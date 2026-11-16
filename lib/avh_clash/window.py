# -*- coding: utf-8 -*-
"""The Clash Detector window: a WPF window holding one WebView2, which
shows lib/avh_clash/web/index.html.

Revit, WPF and WebView2 only, so nothing here runs outside Revit and the
harness puts a stand in for this module.

How the pieces meet:

- The web folder is served to the page as https://avh-clash.example/
  through WebView2's virtual host mapping. A file:// page may not load
  JavaScript modules; a mapped host may.
- The page and Python exchange JSON: `chrome.webview.postMessage` one
  way, `PostWebMessageAsJson` the other.
- The window is modeless, so Revit stays usable beside it. A modeless
  window is outside the Revit API context, so anything that touches the
  API is queued and run from an ExternalEvent. Shift click opens it
  modal instead, where the API can be called directly: the fallback if
  the ExternalEvent route does not work on some machine.

WebView2 keeps a browser profile on disk and will not run without one.
It goes in %TEMP%\\avh_clash_wv2_<process id> and is deleted when the
window closes; one left by a Revit that is no longer running is deleted
at the next start. The assemblies are Revit's own (2024 to 2027 all
ship Microsoft.Web.WebView2.*.dll); nothing is bundled.

Unverified until run in Revit: all of it. In particular loading the
WebView2 assemblies from IronPython, CoreWebView2CreationProperties,
the virtual host mapping, an IExternalEventHandler written in Python,
and pumping the dispatcher during a run so progress can paint.
"""

import json
import os
import shutil
import tempfile
import traceback

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System import Action, Uri                                  # noqa: E402
from System.Diagnostics import Process                          # noqa: E402
from System.Windows import (                                    # noqa: E402
    Window, WindowStartupLocation, Thickness, TextWrapping)
from System.Windows.Controls import TextBlock                   # noqa: E402
from System.Windows.Interop import WindowInteropHelper          # noqa: E402
from System.Windows.Threading import (                          # noqa: E402
    Dispatcher, DispatcherPriority)

from Autodesk.Revit.UI import (                                 # noqa: E402
    IExternalEventHandler, ExternalEvent)

from avh_clash import model                                     # noqa: E402
from avh_schedules.compat import to_text                        # noqa: E402

HOST_NAME = "avh-clash.example"
START_PAGE = "https://" + HOST_NAME + "/index.html"
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PROFILE_PREFIX = "avh_clash_wv2_"

# The open window, so a second click brings it forward instead of
# opening another. Survives between clicks because the button asks
# pyRevit for a persistent engine.
_CURRENT = [None]


def load_webview2():
    """The WebView2 control class and its helpers, from Revit's own
    assemblies. Raises with every route tried when none works."""
    tried = []
    try:
        clr.AddReference("Microsoft.Web.WebView2.Core")
        clr.AddReference("Microsoft.Web.WebView2.Wpf")
    except BaseException as exc:
        tried.append(u"by name ({0})".format(to_text(exc)))
        folder = os.path.dirname(
            Process.GetCurrentProcess().MainModule.FileName)
        try:
            clr.AddReferenceToFileAndPath(
                os.path.join(folder, "Microsoft.Web.WebView2.Core.dll"))
            clr.AddReferenceToFileAndPath(
                os.path.join(folder, "Microsoft.Web.WebView2.Wpf.dll"))
        except BaseException as exc2:
            tried.append(u"from {0} ({1})".format(folder, to_text(exc2)))
            raise Exception(u"WebView2 could not be loaded: " +
                            u"; ".join(tried))
    from Microsoft.Web.WebView2.Wpf import (
        WebView2, CoreWebView2CreationProperties)
    from Microsoft.Web.WebView2.Core import (
        CoreWebView2HostResourceAccessKind)
    return WebView2, CoreWebView2CreationProperties, \
        CoreWebView2HostResourceAccessKind


def _process_is_running(pid):
    try:
        Process.GetProcessById(pid)
        return True
    except BaseException:
        return False


def profile_folder():
    """This process's browser profile folder, after clearing out any
    left behind by a Revit that has since closed."""
    root = tempfile.gettempdir()
    try:
        for name in os.listdir(root):
            if not name.startswith(PROFILE_PREFIX):
                continue
            try:
                pid = int(name[len(PROFILE_PREFIX):])
            except ValueError:
                continue
            if not _process_is_running(pid):
                shutil.rmtree(os.path.join(root, name), True)
    except BaseException:
        pass
    return os.path.join(
        root, PROFILE_PREFIX + "{0}".format(Process.GetCurrentProcess().Id))


class _ApiHandler(IExternalEventHandler):
    """Revit calls Execute inside an API context. Inherits from the
    interface alone: mixed with a plain base class, IronPython's method
    resolution can leave Revit calling nothing."""

    def __init__(self, owner):
        self.owner = owner

    def Execute(self, uiapp):
        self.owner.drain(uiapp)

    def GetName(self):
        return "AVH Clash Detector"


class ClashWindow(object):

    def __init__(self, service, uiapp, modal=False, title=u"Clash Detector"):
        self.service = service
        self.uiapp = uiapp
        self.modal = modal
        self.queue = []
        self.closed = False
        self.core = None
        self.event = None
        self.profile = profile_folder()

        window = Window()
        window.Title = title
        window.Width = 1320
        window.Height = 820
        window.MinWidth = 820
        window.MinHeight = 520
        window.WindowStartupLocation = WindowStartupLocation.CenterScreen
        window.ShowInTaskbar = modal is False
        try:
            WindowInteropHelper(window).Owner = uiapp.MainWindowHandle
        except BaseException:
            pass
        self.window = window

        try:
            WebView2, Properties, self.access_kind = load_webview2()
            view = WebView2()
            properties = Properties()
            properties.UserDataFolder = self.profile
            view.CreationProperties = properties
            view.CoreWebView2InitializationCompleted += self.on_core_ready
            window.Content = view
            self.view = view
        except BaseException:
            self.view = None
            self.show_text(u"The viewer could not start.\n\n" +
                           to_text(traceback.format_exc()))

        window.Closed += self.on_closed
        if not modal:
            self.event = ExternalEvent.Create(_ApiHandler(self))
        service.attach(self.post, self.pump)

    # -- showing ----------------------------------------------------------

    def show(self):
        _CURRENT[0] = self
        if self.view is not None:
            try:
                self.view.EnsureCoreWebView2Async(None)
            except BaseException:
                # An older control without that overload initialises
                # itself as soon as it has somewhere to go.
                try:
                    self.view.Source = Uri("about:blank")
                except BaseException:
                    self.show_text(u"The viewer could not start.\n\n" +
                                   to_text(traceback.format_exc()))
        if self.modal:
            self.window.ShowDialog()
        else:
            self.window.Show()

    def show_text(self, text):
        block = TextBlock()
        block.Text = text
        block.Margin = Thickness(20)
        block.TextWrapping = TextWrapping.Wrap
        self.window.Content = block

    def on_core_ready(self, sender, args):
        try:
            if not args.IsSuccess:
                self.show_text(
                    u"WebView2 did not start, so the viewer cannot be "
                    u"shown. It needs the Microsoft Edge WebView2 "
                    u"Runtime, which Windows 11 includes.\n\n" +
                    to_text(args.InitializationException))
                return
            core = self.view.CoreWebView2
            core.SetVirtualHostNameToFolderMapping(
                HOST_NAME, WEB_DIR, self.access_kind.Allow)
            try:
                settings = core.Settings
                settings.IsStatusBarEnabled = False
                settings.AreDefaultContextMenusEnabled = False
                settings.IsZoomControlEnabled = False
            except BaseException:
                pass
            core.WebMessageReceived += self.on_message
            self.core = core
            core.Navigate(START_PAGE)
        except BaseException:
            self.show_text(u"The viewer could not start.\n\n" +
                           to_text(traceback.format_exc()))

    # -- messages ---------------------------------------------------------

    def post(self, message):
        if self.closed or self.core is None:
            return
        try:
            # Never json.dumps: on IronPython it raises on any name
            # with a non-ASCII character. See model.to_json.
            self.core.PostWebMessageAsJson(model.to_json(message))
            return
        except BaseException:
            detail = to_text(traceback.format_exc())
        # The message could not be sent. Say so, in the window and in
        # the pyRevit log, rather than let the page wait for it forever.
        kind = to_text(message.get("type")) if isinstance(message, dict) \
            else u"?"
        self.service.log(u"Clash Detector could not send '{0}':\n{1}".format(
            kind, detail))
        try:
            self.core.PostWebMessageAsJson(model.to_json({
                "type": "error",
                "message": u"Revit could not send '{0}' to this window.".format(
                    kind),
                "detail": detail}))
        except BaseException:
            pass

    def pump(self):
        """Let the window paint and take a Cancel during a long run."""
        Dispatcher.CurrentDispatcher.Invoke(
            Action(lambda: None), DispatcherPriority.Background)

    def on_message(self, sender, args):
        # json.loads is fine on IronPython, unlike json.dumps: text that
        # is already unicode is never decoded. Not proven in Revit with
        # a non-ASCII message, so a failure is reported, not dropped.
        try:
            message = json.loads(args.WebMessageAsJson)
        except BaseException:
            detail = to_text(traceback.format_exc())
            self.service.log(u"Clash Detector could not read a message "
                             u"from the page:\n" + detail)
            self.post({"type": "error", "message":
                       u"Revit could not read a request from this window.",
                       "detail": detail})
            return
        if not isinstance(message, dict):
            return
        kind = message.get(u"type")
        if kind == u"close":
            self.window.Close()
            return
        if not self.service.needs_api(kind):
            try:
                self.service.handle(message)
            except BaseException:
                detail = to_text(traceback.format_exc())
                self.service.log(detail)
                self.post({"type": "error", "message":
                           u"Revit could not handle '{0}'.".format(
                               to_text(kind)), "detail": detail})
            return
        if self.event is None:
            # Modal: still inside the button's own API context.
            self.service.handle_api(message, self.uiapp)
            return
        self.queue.append(message)
        try:
            self.event.Raise()
        except BaseException:
            self.post({"type": "error", "message":
                       u"Revit would not take the request. Close this "
                       u"window and shift click Clash Detector to open "
                       u"it in its other mode.",
                       "detail": to_text(traceback.format_exc())})

    def drain(self, uiapp):
        """Run everything queued. Called by Revit, in an API context."""
        while self.queue and not self.closed:
            message = self.queue.pop(0)
            self.service.handle_api(message, uiapp)

    # -- closing ----------------------------------------------------------

    def on_closed(self, sender, args):
        self.closed = True
        if _CURRENT[0] is self:
            _CURRENT[0] = None
        try:
            self.service.detach()
        except BaseException:
            pass
        for disposable in (self.view, self.event):
            try:
                if disposable is not None:
                    disposable.Dispose()
            except BaseException:
                pass
        # The browser may still be letting go of the folder; whatever is
        # left is cleared at the next start.
        shutil.rmtree(self.profile, True)


def bring_forward():
    """True when a window is already open, after activating it."""
    current = _CURRENT[0]
    if current is None or current.closed:
        return False
    try:
        current.window.Activate()
        return True
    except BaseException:
        return False


def show(service, uiapp, modal=False, title=u"Clash Detector"):
    ClashWindow(service, uiapp, modal=modal, title=title).show()
