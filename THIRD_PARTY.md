# Third party code in this extension

`AVH.extension` is distributed under the **GNU General Public License
v3**. The full text is in `LICENSE`. It carries that licence because
parts of it are copied from, or derived from, other pyRevit extensions
that are themselves GPL v3.

Nothing in this restricts AVH's own use of these tools, internally or on
paid work. What it means is that anyone who receives the extension
receives the same rights over it, and that AVH cannot redistribute it as
closed source.

## pyRevit

<https://github.com/pyrevitlabs/pyRevit>, GPL v3.

**AVH > Tools > Diagnose Invisibility** is a copy of pyRevit's own tool,
contributed by Wurschdhaud as pull request 3600 and merged on
12 September 2026. Taken from commit
`316f0b98487c6b53687c4b0c63cd8af8b3fdef75` on the `develop` branch, from

```
extensions/pyRevitTools.extension/pyRevit.tab/Analysis.panel/
Tools.stack/Inspect.pulldown/Diagnose Invisibility.pushbutton/
```

Two changes to `script.py`, both marked in the file. A provenance notice
at the top, and a local stand in for
`applocales.get_locale_string_from_xaml`, which landed on `develop`
alongside the tool and is not in the pyRevit release AVH runs. Without it
the first click raised `AttributeError` and the button never opened. `_t`
looks the function up rather than calling it, so pyRevit's own is used
where it exists and no edit is needed when the release catches up.

A third change adds `diagnose_partial_visibility`, which reports a
visible element whose geometry draws on a hidden category. pyRevit's tool
answers "is this element visible", which is a different question from "is
all of it drawn", and the second is what a toilet built from an imported
DWG fails.

`test_diagnose_additions_harness.py` covers both AVH additions, 33
checks. The tool itself is not tested here: it is not AVH's code.

The icon is AVH's, because the original sat inside a pulldown and had
none of its own.

It was copied because the tool was merged but had not reached a pyRevit
release.

**AVH maintains this copy from here on.** It is a snapshot of that commit,
not a tracked fork: a later pyRevit release carrying its own version does
not replace this one, and fixes they make to theirs will not arrive here.
Decided on 18 September 2026, and the price is that this file's faults
are AVH's to find. If somebody later wants to swap back to pyRevit's,
that is a fresh decision with the AVH additions to carry across by hand.

Every other file in this extension imports pyRevit's libraries, which is
ordinary use of the platform rather than copying from it.

## pyApex

<https://apex-project.github.io/pyApex>, GPL v3.

**AVH > Tools > Flip Grid Ends** and **AVH > Tools > Remove Level** were
both rewritten from pyApex scripts in August 2026. They are not verbatim
copies: the offset arithmetic in Remove Level was corrected, the picker
was rewritten against the current `pyrevit.forms` API, locale unsafe
string conversion was removed throughout, and commit status checking was
added. They remain derivative works of pyApex all the same, and Remove
Level links AVH's own `avh_levels` library into that derivative work.

This attribution was missing until 18 September 2026, which was an
oversight rather than a decision.
