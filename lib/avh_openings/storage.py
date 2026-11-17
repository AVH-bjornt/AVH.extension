# -*- coding: utf-8 -*-
"""Reading and writing an opening's tag in Revit's extensible storage.

`DB` (Autodesk.Revit.DB) is passed in rather than imported, so the
harness can hand over a stand in. The tag itself is plain text built
and read by `avh_openings.tags`; this module only puts it on an element
and gets it back.

Extensible storage is data Revit keeps on an element under a schema,
invisible in the interface. It needs no shared parameter file and no
project parameter, it stays with the element through copies and syncs,
and it cannot be edited by hand by accident.

The schema has a fixed GUID, so every run and every model agree on it.
Changing its fields means a new GUID: Revit refuses to redefine one.

## Unverified until run in Revit

Read from the API documentation only:

    ExtensibleStorage.Schema.Lookup, SchemaBuilder (SetReadAccessLevel,
        SetWriteAccessLevel, SetSchemaName, AddSimpleField, Finish)
    ExtensibleStorage.Entity, Entity.Set[String] / Get[String],
        Entity.IsValid
    Element.SetEntity / GetEntity
"""

try:
    from System import Guid, String      # IronPython in Revit
except ImportError:                      # desktop Python: the harness
    Guid = None
    String = str

from avh_schedules.compat import to_text

SCHEMA_GUID = "6f1c2a0e-8d4b-4c3e-9a57-2b1e0d7c4a91"
SCHEMA_NAME = "AVHPipeOpening"
FIELD = "Data"


def _storage(DB):
    return getattr(DB, "ExtensibleStorage")


def _guid():
    return Guid(SCHEMA_GUID) if Guid is not None else SCHEMA_GUID


def schema(DB, create=False):
    """The tag schema, or None when it does not exist and `create` is
    False. Creating it needs no transaction."""
    es = _storage(DB)
    found = es.Schema.Lookup(_guid())
    if found is not None or not create:
        return found
    builder = es.SchemaBuilder(_guid())
    builder.SetReadAccessLevel(es.AccessLevel.Public)
    builder.SetWriteAccessLevel(es.AccessLevel.Public)
    builder.SetSchemaName(SCHEMA_NAME)
    builder.AddSimpleField(FIELD, String)
    return builder.Finish()


def write(DB, element, text):
    """Put the tag text on the element, and read it back. Inside a
    transaction. Raises with the reason when it did not stick."""
    found = schema(DB, create=True)
    if found is None:
        raise Exception(u"the tag schema could not be made")
    entity = _storage(DB).Entity(found)
    entity.Set[String](FIELD, text)
    element.SetEntity(entity)
    if read(DB, element) != to_text(text):
        raise Exception(u"the tag did not read back as written")


def read(DB, element):
    """The tag text on the element, or None when it has none."""
    found = schema(DB)
    if found is None:
        return None
    entity = element.GetEntity(found)
    if entity is None or not entity.IsValid():
        return None
    value = entity.Get[String](FIELD)
    return to_text(value) if value else None
