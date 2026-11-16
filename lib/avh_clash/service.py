# -*- coding: utf-8 -*-
"""What the Clash Detector window can ask Revit for.

The window sends JSON messages and gets JSON messages back; this is the
other end. It knows Revit and nothing about WPF or WebView2, so the
harness drives it directly with a list standing in for the window.

    ready       -> init: the models, their categories and the view
    refresh     -> init again: the models, links and selection as they
                   are now, so a selection made after the click counts
    run         -> progress..., mesh..., result
    select      -> selects the two elements in Revit and zooms to them
    sectionbox  -> puts the active 3D view's section box round a clash
    cancel      -> stops a run at the next pair
    log         -> an error in the page, written to the pyRevit log

`refresh`, `run`, `select` and `sectionbox` touch the Revit API, so the
window must deliver them from inside an API context (`needs_api`). The
section box is the only thing here that writes to the model, and its
commit status is checked.
"""

import time
import traceback

from avh_clash import model
from avh_clash import reader as rd
from avh_schedules.compat import to_text

API_MESSAGES = (u"refresh", u"run", u"select", u"sectionbox")

# Geometry that is in neither element of any clash is only there to show
# where in the building you are. It stops being sent past this many
# triangles; the clashing elements are always sent in full.
CONTEXT_TRIANGLES = 1500000

PROGRESS_EVERY = 0.12       # seconds between progress messages


def _collection(DB, items, kind):
    """A .NET List of ElementId or Reference; a plain list where .NET is
    not there, which is the harness."""
    try:
        from System.Collections.Generic import List
        result = List[getattr(DB, kind)]()
        for item in items:
            result.Add(item)
        return result
    except BaseException:
        return list(items)


def clearance_of(config):
    """(metres, set of category ids) from the run's `clearance`, or
    (0.0, empty) when there is none."""
    clearance = config.get(u"clearance") or {}
    try:
        metres = float(clearance.get(u"mm") or 0) / 1000.0
    except (TypeError, ValueError):
        metres = 0.0
    cats = set()
    for cat in clearance.get(u"cats") or []:
        try:
            cats.add(int(cat))
        except (TypeError, ValueError):
            pass
    if metres <= 0 or not cats:
        return 0.0, set()
    return metres, cats


def soft_clash(gap, clearance):
    """A gap under the clearance, as a clash: where and how close."""
    p = gap["a"]
    q = gap["b"]
    middle = ((p[0] + q[0]) * 0.5, (p[1] + q[1]) * 0.5, (p[2] + q[2]) * 0.5)
    box = model.pad(model.bounds(list(p) + list(q)), 0.001)
    return {"kind": u"soft", "volume": None, "depth": None,
            "center": middle, "box": box, "exact": True,
            "partial": not gap["complete"], "gap": gap["distance"],
            "clear": clearance, "p": p, "q": q}


class Service(object):

    def __init__(self, DB, uiapp, version=u"", logger=None):
        self.DB = DB
        self.uiapp = uiapp
        self.version = version
        self.logger = logger
        self.post = lambda message: None
        self.pump = lambda: None
        self.doc = None
        self.sources = {}
        self.source_order = []
        self.view_id = None
        self.init_message = None
        self.last = None
        self._cancel = False
        self._detached = False
        self._last_progress = 0.0
        self._last_stage = None

    # -- wiring ----------------------------------------------------------

    def attach(self, post, pump=None):
        self._detached = False
        self.post = post
        self.pump = pump or (lambda: None)

    def detach(self):
        self.post = lambda message: None
        self.pump = lambda: None
        self._cancel = True
        self._detached = True

    def needs_api(self, kind):
        return kind in API_MESSAGES

    def handle(self, message):
        """A message that needs no Revit API."""
        kind = message.get(u"type")
        if kind == u"ready":
            if self.init_message is not None:
                self.post(self.init_message)
            else:
                self.post({"type": "error", "message":
                           u"Revit has not listed the models yet. Click "
                           u"Refresh from Revit."})
        elif kind == u"cancel":
            self._cancel = True
        elif kind == u"log":
            self.log(u"Clash Detector page: {0}\n{1}".format(
                to_text(message.get(u"message")),
                to_text(message.get(u"detail"))))

    def log(self, text):
        if self.logger is None:
            return
        try:
            self.logger.error(text)
        except BaseException:
            pass

    def handle_api(self, message, uiapp=None):
        """A message that does. Never raises: a failure goes back to the
        window as an error message with the traceback."""
        if self._detached:
            return                          # the window has gone
        if uiapp is not None:
            self.uiapp = uiapp
        kind = message.get(u"type")
        try:
            if not self._document_is_current():
                self.post({"type": "error", "message":
                           u"The model this window was opened on is no "
                           u"longer the active one. Close this window "
                           u"and click Clash Detector again."})
                if kind == u"run":
                    self.post({"type": "aborted"})
                return
            if kind == u"run":
                self.run(message.get(u"config") or {})
            elif kind == u"refresh":
                init = dict(self.build_inventory())
                init["refreshed"] = True
                init["useSelection"] = bool(message.get(u"useSelection"))
                self.post(init)
            elif kind == u"select":
                self.select(message)
            elif kind == u"sectionbox":
                self.section_box(message)
        except BaseException as exc:
            detail = traceback.format_exc()
            self.log(detail)
            self.post({"type": "error",
                       "message": u"{0}: {1}".format(
                           type(exc).__name__, to_text(exc)),
                       "detail": to_text(detail)})
            if kind == u"run":
                self.post({"type": "aborted"})

    def _document_is_current(self):
        try:
            return self._same(self.uiapp.ActiveUIDocument.Document,
                              self.doc)
        except BaseException:
            return False

    @staticmethod
    def _same(active, doc):
        if not getattr(doc, "IsValidObject", True):
            return False
        return bool(active.Equals(doc))

    # -- what there is to test ---------------------------------------------

    def build_inventory(self):
        """Read the models and count their categories. Called from the
        button click before the window opens, and again on refresh."""
        DB = self.DB
        uidoc = self.uiapp.ActiveUIDocument
        doc = uidoc.Document
        # A refresh with another model active never gets here:
        # handle_api checks the model before every API message.
        self.doc = doc
        excluded = rd.excluded_ids(DB)
        self.excluded = excluded
        groups, group_order = rd.discipline_ids(DB)
        clear_ids = rd.enum_ids(DB, rd.CLEARANCE_DEFAULT)

        sources = [rd.Source(u"host", to_text(doc.Title), u"host", doc)]
        sources.extend(rd.link_sources(DB, doc))

        listed = []
        for source in sources:
            counts = {}
            for element, category in rd.model_elements(DB, source.doc,
                                                       excluded):
                self._count(counts, category)
            listed.append((source, counts))

        try:
            selected = list(uidoc.Selection.GetElementIds())
        except BaseException:
            selected = []
        if selected:
            counts = {}
            kept = []
            for element_id in selected:
                element = doc.GetElement(element_id)
                if element is None:
                    continue
                category = rd.qualifies(DB, element, excluded)
                if category is None:
                    continue
                kept.append(element_id)
                self._count(counts, category)
            if kept:
                selection = rd.Source(u"selection", u"Current selection",
                                      u"selection", doc, element_ids=kept)
                listed.insert(0, (selection, counts))

        self.sources = dict((source.key, source) for source, _ in listed)
        self.source_order = [source.key for source, _ in listed]

        view = self._view_info(doc)
        self.init_message = {
            "type": "init",
            "version": self.version,
            "model": to_text(doc.Title),
            "view": view,
            "groups": group_order,
            "sources": [
                {"key": source.key, "label": source.label,
                 "kind": source.kind,
                 "cats": sorted(
                     [{"id": cat_id, "name": name, "count": count,
                       "group": groups.get(cat_id, rd.OTHER),
                       "clear": cat_id in clear_ids}
                      for cat_id, (name, count) in counts.items()],
                     key=lambda cat: cat["name"].lower())}
                for source, counts in listed],
        }
        return self.init_message

    def _count(self, counts, category):
        cat_id = rd.id_value(category.Id)
        entry = counts.get(cat_id)
        if entry is None:
            counts[cat_id] = [to_text(category.Name), 1]
        else:
            entry[1] += 1

    def _view_info(self, doc):
        DB = self.DB
        info = {"name": u"", "usable": False, "is3d": False,
                "sectionBox": False}
        self.view_id = None
        try:
            view = doc.ActiveView
        except BaseException:
            view = None
        if view is None:
            return info
        try:
            info["name"] = to_text(view.Name)
            # Revit refuses a collector on a view that draws no
            # elements (a sheet, a schedule), which is the test.
            DB.FilteredElementCollector(doc, view.Id)
            info["usable"] = True
            self.view_id = view.Id
            info["is3d"] = isinstance(view, DB.View3D)
            info["sectionBox"] = rd.section_box_feet(DB, view) is not None
        except BaseException:
            info["usable"] = False
            self.view_id = None
        return info

    # -- a run ------------------------------------------------------------

    def progress(self, stage, done=None, total=None, force=False):
        now = time.time()
        if not force and stage == self._last_stage and \
                now - self._last_progress < PROGRESS_EVERY:
            return
        self._last_progress = now
        self._last_stage = stage
        self.post({"type": "progress", "stage": stage,
                   "done": done, "total": total})
        try:
            self.pump()
        except BaseException:
            pass

    def _collect(self, config, scope, why):
        """Items by key for both sets, in the order met.

        `why` is filled in per set with what was looked at and what was
        dropped, so a set that comes back empty can say which step
        emptied it instead of just being empty."""
        DB = self.DB
        items = {}
        order = []
        view_id = self.view_id if scope == u"view" else None
        for side, bit in ((u"a", 1), (u"b", 2)):
            tally = why[side]
            for entry in config.get(side) or []:
                tally["entries"] += 1
                source = self.sources.get(entry.get(u"source"))
                if source is None:
                    tally["unknown"].append(to_text(entry.get(u"source")))
                    continue
                wanted = set(int(c) for c in entry.get(u"cats") or [])
                if not wanted:
                    continue
                tally["categories"] += len(wanted)
                for element, category in self._elements(source, view_id):
                    tally["looked"] += 1
                    if rd.id_value(category.Id) not in wanted:
                        continue
                    tally["matched"] += 1
                    element_id = rd.id_value(element.Id)
                    key = u"{0}:{1}".format(source.prefix, element_id)
                    item = items.get(key)
                    if item is None:
                        box = rd.element_box(DB, element, source.transform)
                        if box is None:
                            tally["nobox"] += 1
                            continue
                        item = rd.Item(key, source, element,
                                       to_text(category.Name), box)
                        item.element_id = element_id
                        item.cat_id = rd.id_value(category.Id)
                        items[key] = item
                        order.append(item)
                    item.sets |= bit
                    self.progress(u"Collecting elements", len(order))
                    if self._cancel:
                        return order
        return order

    def _elements(self, source, view_id):
        DB = self.DB
        if source.kind == u"selection":
            for element_id in source.element_ids:
                element = source.doc.GetElement(element_id)
                if element is None:
                    continue
                category = rd.qualifies(DB, element, self.excluded)
                if category is not None:
                    yield element, category
            return
        only_view = view_id if source.kind == u"host" else None
        for pair in rd.model_elements(DB, source.doc, self.excluded,
                                      only_view):
            yield pair

    def run(self, config):
        DB = self.DB
        started = time.time()
        self._cancel = False
        self.last = None
        scope = config.get(u"scope") or u"model"
        detail = config.get(u"detail") or u"medium"
        want_context = bool(config.get(u"context", True))

        if scope == u"view":
            # The view that is active now, not the one at the click.
            view = self._active_view()
            try:
                DB.FilteredElementCollector(self.doc, view.Id)
                self.view_id = view.Id
            except BaseException:
                self.view_id = None
                scope = u"model"
                self.notice(u"The active view shows no model elements, "
                            u"so the whole model was tested.", u"warn")

        why = {}
        for side in (u"a", u"b"):
            why[side] = {"entries": 0, "categories": 0, "looked": 0,
                         "matched": 0, "nobox": 0, "clipped": 0,
                         "unknown": []}
        why["scope"] = scope
        self.progress(u"Collecting elements", force=True)
        items = self._collect(config, scope, why)

        clipped = 0
        if scope == u"view":
            limit = rd.section_box_feet(DB, self._active_view())
            why["sectionBox"] = limit is not None
            if limit is not None:
                inside = []
                for item in items:
                    if model.boxes_overlap(item.box_feet, limit, 0.0):
                        inside.append(item)
                        continue
                    if item.sets & 1:
                        why[u"a"]["clipped"] += 1
                    if item.sets & 2:
                        why[u"b"]["clipped"] += 1
                clipped = len(items) - len(inside)
                items = inside

        set_a = [item for item in items if item.sets & 1]
        set_b = [item for item in items if item.sets & 2]
        stats = {"a": len(set_a), "b": len(set_b), "pairs": 0, "tested": 0,
                 "clipped": clipped, "failed": 0, "unreadable": 0,
                 "cancelled": False, "contextCut": 0, "seconds": 0.0,
                 "hard": 0, "soft": 0, "joined": 0, "clearance": 0.0}

        if self._cancel:
            # Stopped while still collecting: there is nothing to show,
            # and a half collected set is not an empty one.
            stats["cancelled"] = True
            stats["seconds"] = round(time.time() - started, 2)
            self.post({"type": "result", "clashes": [], "elements": [],
                       "levels": [], "stats": stats})
            return
        if not set_a or not set_b:
            stats["seconds"] = round(time.time() - started, 2)
            self.post({"type": "result", "clashes": [], "elements": [],
                       "levels": [], "stats": stats,
                       "empty": u"A" if not set_a else u"B", "why": why})
            return

        whole = None
        for item in items:
            whole = model.union(whole, item.box_feet)
        origin = model.center(whole)
        for item in items:
            item.box = model.to_local(item.box_feet, origin)
        by_key = dict((item.key, item) for item in items)

        # A clearance zone grows the box of each element that carries
        # one, so an element near it, not touching, still makes a pair.
        clearance, clear_cats = clearance_of(config)
        stats["clearance"] = clearance
        for item in items:
            if clearance > 0 and item.cat_id in clear_cats:
                item.clear = clearance

        def reach(item):
            return model.pad(item.box, item.clear) if item.clear \
                else item.box

        self.progress(u"Finding candidate pairs", force=True)
        pairs = model.candidate_pairs(
            [(item.key, reach(item)) for item in set_a],
            [(item.key, reach(item)) for item in set_b])
        stats["pairs"] = len(pairs)

        reading = rd.Reader(DB, origin, detail)
        clashes = []
        records = []
        volumes = []
        for number, (key_a, key_b) in enumerate(pairs):
            if self._cancel:
                stats["cancelled"] = True
                break
            self.progress(u"Testing pairs", number, len(pairs))
            a = by_key[key_a]
            b = by_key[key_b]
            stats["tested"] += 1
            if reading.related(a, b):
                stats["joined"] += 1            # a pipe and its fitting
                continue
            found = None
            if model.boxes_overlap(a.box, b.box):
                found = reading.test(a, b)
            if found is None:
                limit = max(a.clear, b.clear)
                gap = reading.gap(a, b, limit) if limit > 0 else None
                if gap is None:
                    continue
                found = soft_clash(gap, limit)
                stats["soft"] += 1
            else:
                stats["hard"] += 1
            index = len(clashes)
            clash = {
                "n": index, "a": a.key, "b": b.key, "kind": found["kind"],
                "vol": found["volume"], "depth": found["depth"],
                "c": list(found["center"]), "box": list(found["box"]),
                "exact": found["exact"], "partial": found["partial"]}
            if found["kind"] == u"soft":
                clash.update({"gap": found["gap"], "clear": found["clear"],
                              "p": list(found["p"]), "q": list(found["q"]),
                              "zone": {"a": a.clear, "b": b.clear}})
            clashes.append(clash)
            records.append({
                "a": (a.source.key, a.element_id),
                "b": (b.source.key, b.element_id),
                "box_feet": model.to_feet(found["box"], origin)})
            if found.get("pos") is not None:      # a soft clash has none
                volumes.append((index, found["pos"], found["idx"]))
            a.clashing = True
            b.clashing = True

        # Geometry out: clashing elements first and in full, then the
        # overlaps themselves, then as much of the rest as the budget
        # allows.
        batch = model.MeshBatch(self.post)
        sent = []
        clashing = [item for item in items
                    if getattr(item, "clashing", False)]
        for number, item in enumerate(clashing):
            self.progress(u"Sending geometry", number, len(clashing))
            geometry = reading.geometry(item)
            if len(geometry.idx):
                batch.add(u"e", item.key, geometry.pos, geometry.idx)
                sent.append(item)
            item.geometry = None
        for index, pos, idx in volumes:
            batch.add(u"c", index, pos, idx)
        del volumes[:]

        if want_context and not self._cancel:
            rest = [item for item in items
                    if not getattr(item, "clashing", False)]
            for number, item in enumerate(rest):
                if self._cancel:
                    break
                if batch.triangles >= CONTEXT_TRIANGLES:
                    stats["contextCut"] = len(rest) - number
                    break
                self.progress(u"Reading surrounding geometry",
                              number, len(rest))
                geometry = reading.geometry(item)
                if len(geometry.idx):
                    batch.add(u"e", item.key, geometry.pos, geometry.idx)
                    sent.append(item)
                item.geometry = None
        batch.flush()
        for item in items:
            item.geometry = None

        stats["failed"] = reading.boolean_failures
        stats["unreadable"] = reading.unreadable
        stats["seconds"] = round(time.time() - started, 2)

        self.last = {"records": records, "origin": origin}
        self.post({
            "type": "result",
            "clashes": clashes,
            "elements": [self._describe(item) for item in sent],
            "levels": self._levels(origin),
            "stats": stats,
        })

    def _describe(self, item):
        try:
            name = to_text(item.element.Name)
        except BaseException:
            name = u""
        return {"key": item.key, "src": item.source.prefix,
                "id": item.element_id, "cat": item.category,
                "name": name, "set": item.sets}

    def _levels(self, origin):
        DB = self.DB
        found = []
        try:
            for level in DB.FilteredElementCollector(self.doc) \
                    .OfClass(DB.Level):
                found.append({
                    "name": to_text(level.Name),
                    "z": (level.Elevation - origin[2]) * model.FEET_TO_M})
        except BaseException:
            return []
        found.sort(key=lambda level: level["z"])
        return found

    # -- acting on a clash in Revit ----------------------------------------

    def _active_view(self):
        try:
            return self.uiapp.ActiveUIDocument.ActiveView
        except BaseException:
            try:
                return self.doc.ActiveView
            except BaseException:
                return None

    def _record(self, message):
        if self.last is None:
            return None
        try:
            return self.last["records"][int(message.get(u"index"))]
        except BaseException:
            return None

    def _element(self, source_key, element_id):
        source = self.sources.get(source_key)
        if source is None:
            return None, None
        try:
            element = source.doc.GetElement(rd.make_id(self.DB, element_id))
        except BaseException:
            element = None
        return source, element

    def notice(self, text, kind=u"info"):
        self.post({"type": "notice", "text": text, "kind": kind})

    def select(self, message):
        """Select both elements of a clash in Revit and zoom to it."""
        DB = self.DB
        record = self._record(message)
        if record is None:
            self.notice(u"Run a clash test first.", u"warn")
            return
        uidoc = self.uiapp.ActiveUIDocument
        host_ids = []
        references = []
        link_ids = []
        missing = 0
        for side in (u"a", u"b"):
            source, element = self._element(*record[side])
            if element is None:
                missing += 1
                continue
            if source.instance is None:
                host_ids.append(element.Id)
                continue
            link_ids.append(source.instance.Id)
            try:
                references.append(
                    DB.Reference(element).CreateLinkReference(
                        source.instance))
            except BaseException:
                pass

        selection = uidoc.Selection
        done = False
        if references and len(references) == len(link_ids) and \
                hasattr(selection, "SetReferences"):
            # Revit 2023 on: the element inside the link itself.
            try:
                every = [DB.Reference(self.doc.GetElement(element_id))
                         for element_id in host_ids] + references
                selection.SetReferences(_collection(DB, every, "Reference"))
                done = True
            except BaseException:
                done = False
        if not done:
            # Otherwise the link instance stands in for its element.
            selection.SetElementIds(
                _collection(DB, host_ids + link_ids, "ElementId"))

        zoomed = False
        if message.get(u"zoom", True):
            zoomed = self._zoom(uidoc, record["box_feet"])
        if missing:
            self.notice(u"{0} of the two elements no longer exists. Run "
                        u"the test again.".format(missing), u"warn")
        elif message.get(u"zoom", True) and not zoomed:
            self.notice(u"Selected in Revit. The active view could not "
                        u"be zoomed.", u"info")
        else:
            self.notice(u"Selected in Revit.", u"ok")

    def _zoom(self, uidoc, box_feet):
        DB = self.DB
        try:
            view = uidoc.ActiveView
            box = model.pad(box_feet, 1.0 / model.FEET_TO_M, 1.0)
            for ui_view in uidoc.GetOpenUIViews():
                if ui_view.ViewId == view.Id:
                    ui_view.ZoomAndCenterRectangle(
                        DB.XYZ(box[0], box[1], box[2]),
                        DB.XYZ(box[3], box[4], box[5]))
                    return True
        except BaseException:
            pass
        return False

    def section_box(self, message):
        """Put the active 3D view's section box round a clash. Writes to
        the model, in its own transaction."""
        DB = self.DB
        record = self._record(message)
        if record is None:
            self.notice(u"Run a clash test first.", u"warn")
            return
        uidoc = self.uiapp.ActiveUIDocument
        view = uidoc.ActiveView
        if not isinstance(view, DB.View3D) or \
                getattr(view, "IsTemplate", False):
            self.notice(u"Open a 3D view in Revit first: a section box "
                        u"belongs to a 3D view.", u"warn")
            return
        box = model.pad(record["box_feet"], 0.75 / model.FEET_TO_M, 0.75)
        transaction = DB.Transaction(self.doc, "Clash Detector: section box")
        transaction.Start()
        try:
            bounding = DB.BoundingBoxXYZ()
            bounding.Min = DB.XYZ(box[0], box[1], box[2])
            bounding.Max = DB.XYZ(box[3], box[4], box[5])
            view.SetSectionBox(bounding)
        except BaseException as exc:
            transaction.RollBack()
            self.notice(u"Revit refused the section box: {0}".format(
                to_text(exc)), u"warn")
            return
        status = transaction.Commit()
        committed = getattr(DB.TransactionStatus, "Committed", None)
        if committed is not None and status != committed:
            self.notice(u"Revit rejected the section box ({0}); the view "
                        u"is unchanged.".format(to_text(status)), u"warn")
            return
        self._zoom(uidoc, record["box_feet"])
        self.notice(u"Section box set on \"{0}\". Undo in Revit puts it "
                    u"back.".format(to_text(view.Name)), u"ok")
