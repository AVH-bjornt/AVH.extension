# -*- coding: utf-8 -*-
"""Create one workset per linked model and assign each link instance and
its link type to that workset.

Naming convention:
    Link_RVT_<filename>   for Revit links
    Link_IFC_<filename>   for IFC links
"""

__title__ = "Create Worksets\nfrom Links"
__author__ = "AVH"
__doc__ = ("Creates a workset per linked model in the active project and "
           "assigns the link instance and link type to it. "
           "Naming: Link_RVT_<name> or Link_IFC_<name>.")

from pyrevit import revit, DB, forms, script

doc = revit.doc
output = script.get_output()
logger = script.get_logger()


def get_file_type_prefix(name):
    if name.lower().endswith(".ifc"):
        return "Link_IFC"
    return "Link_RVT"


def strip_extension(name):
    for ext in (".rvt", ".ifc"):
        if name.lower().endswith(ext):
            return name[:-len(ext)].strip()
    return name.strip()


if not doc.IsWorkshared:
    forms.alert(
        "The current model is not workshared. Worksets cannot be created.",
        exitscript=True
    )

link_instances = (DB.FilteredElementCollector(doc)
                  .OfClass(DB.RevitLinkInstance)
                  .ToElements())

workset_map = {}
skipped_unknown = []

for link in link_instances:
    try:
        type_id = link.GetTypeId()
        link_type = doc.GetElement(type_id)
    except Exception:
        link_type = None

    if link_type is None:
        skipped_unknown.append(str(link.Id))
        continue

    try:
        name_param = link_type.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
        if name_param is not None:
            type_name = name_param.AsString()
        else:
            name_param = link_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
            type_name = name_param.AsString() if name_param is not None else None

        if not type_name:
            skipped_unknown.append("{} (no name parameter)".format(str(link.Id)))
            continue
    except Exception as e:
        skipped_unknown.append("{} (param read: {})".format(str(link.Id), str(e)))
        continue

    prefix = get_file_type_prefix(type_name)

    link_doc = link.GetLinkDocument()
    if link_doc is not None:
        clean_name = strip_extension(link_doc.Title)
    else:
        clean_name = strip_extension(type_name)

    workset_name = prefix + "_" + clean_name

    if workset_name not in workset_map:
        workset_map[workset_name] = {
            "instance": link,
            "link_type": link_type,
        }

existing_worksets = (DB.FilteredWorksetCollector(doc)
                     .OfKind(DB.WorksetKind.UserWorkset)
                     .ToWorksets())
existing_map = {ws.Name: ws.Id for ws in existing_worksets}

created = []
skipped_existing = []
errors = []
assigned = []
assign_errors = []

with revit.Transaction("Create worksets from links"):
    for ws_name in workset_map.keys():
        if ws_name in existing_map:
            skipped_existing.append(ws_name)
        else:
            try:
                new_ws = DB.Workset.Create(doc, ws_name)
                existing_map[ws_name] = new_ws.Id
                created.append(ws_name)
            except Exception as e:
                errors.append("{} => {}".format(ws_name, str(e)))

    for ws_name, elements in workset_map.items():
        if ws_name not in existing_map:
            continue

        ws_id = existing_map[ws_name]
        ws_id_int = ws_id.IntegerValue

        try:
            instance = elements["instance"]
            instance_param = instance.get_Parameter(DB.BuiltInParameter.ELEM_PARTITION_PARAM)
            if instance_param is not None and not instance_param.IsReadOnly:
                instance_param.Set(ws_id_int)
                assigned.append("Instance: " + ws_name)
            else:
                assign_errors.append("Instance read only: " + ws_name)
        except Exception as e:
            assign_errors.append("Instance {}: {}".format(ws_name, str(e)))

        try:
            link_type = elements["link_type"]
            type_param = link_type.get_Parameter(DB.BuiltInParameter.ELEM_PARTITION_PARAM)
            if type_param is not None and not type_param.IsReadOnly:
                type_param.Set(ws_id_int)
                assigned.append("Type: " + ws_name)
            else:
                assign_errors.append("Type read only: " + ws_name)
        except Exception as e:
            assign_errors.append("Type {}: {}".format(ws_name, str(e)))


# Report.
output.print_md("# Create Worksets from Links")

if not created and not skipped_existing and not assigned:
    output.print_md("No linked models found in the current document.")
else:
    if created:
        output.print_md("## Created ({})".format(len(created)))
        for n in created:
            output.print_md("* `{}`".format(n))

    if skipped_existing:
        output.print_md("## Skipped, already exists ({})".format(len(skipped_existing)))
        for n in skipped_existing:
            output.print_md("* `{}`".format(n))

    if assigned:
        output.print_md("## Assigned ({})".format(len(assigned)))
        for n in assigned:
            output.print_md("* `{}`".format(n))

if skipped_unknown:
    output.print_md("## Skipped, unknown links ({})".format(len(skipped_unknown)))
    for i in skipped_unknown:
        output.print_md("* Element id: `{}`".format(i))

if errors:
    output.print_md("## Workset creation errors ({})".format(len(errors)))
    for n in errors:
        output.print_md("* `{}`".format(n))

if assign_errors:
    output.print_md("## Assignment errors ({})".format(len(assign_errors)))
    for n in assign_errors:
        output.print_md("* `{}`".format(n))
