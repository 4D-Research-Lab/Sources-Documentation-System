# sources_documentation.py

bl_info = {
    "name": "Historical Source Documentation",
    "author": "Tijm Lanjouw/Claude Sonnet 4.6",
    "version": (2, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Hist. Sources",
    "description": "Document historical sources for architectural reconstruction",
    "category": "Object",
}

import bpy
import os
import json
import uuid
import csv
import math
import webbrowser
from bpy.types import PropertyGroup, Panel, Operator, UIList
from bpy.props import (
    StringProperty, EnumProperty, CollectionProperty,
    IntProperty, PointerProperty, BoolProperty
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCE_TYPES = [
    ("PHOTO",      "Archival Photo",        "Historical photograph"),
    ("DRAWING",    "Architectural Drawing", "Plan, section, or elevation"),
    ("MAP",        "Map / Cadastral",       "Historical map or cadastral record"),
    ("WRITTEN",    "Written Source",        "Text, inventory, or description"),
    ("3D_SCAN",    "3D Scan / Survey",      "Photogrammetry or laser scan"),
    ("PAINTING",   "Painting",              "Oil, watercolour, or other painted depiction"),
    ("SKETCH",     "Sketch / Drawing",      "Freehand sketch or artistic drawing"),
    ("ENGRAVING",  "Engraving / Print",     "Engraving, etching, lithograph, or other print"),
    ("OTHER",      "Other",                 "Other source type"),
]

RELIABILITY = [
    ("HIGH",   "High",   "Primary source, directly depicts the element"),
    ("MEDIUM", "Medium", "Indirect or partially legible source"),
    ("LOW",    "Low",    "Speculative or secondary source"),
]

EXPORT_FORMAT = [
    ("GLB",           "GLB",           "Single binary .glb file"),
    ("GLTF_SEPARATE", "GLTF",          "Separate .gltf + .bin + textures"),
    ("GLTF_EMBEDDED", "GLTF Embedded", "Single .gltf with embedded data"),
]

EXPORT_SCOPE = [
    ("ALL",             "All Objects",           "Export every object that has sources"),
    ("SELECTED",        "Selected Objects",       "Export each selected object as a separate file"),
    ("SELECTED_SINGLE", "Selected — Single File", "Export all selected objects into one file"),
]

SORT_OPTIONS = [
    ("NONE",    "Original Order", "Keep original import/creation order"),
    ("TITLE",   "Title",          "Sort alphabetically by title"),
    ("DATE",    "Date",           "Sort alphabetically by date string"),
    ("TOPONYM", "Toponym",        "Sort alphabetically by toponym"),
]

SOURCE_TYPE_ICONS = {
    "PHOTO":     "IMAGE_DATA",
    "DRAWING":   "DOCUMENTS",
    "MAP":       "WORLD",
    "WRITTEN":   "TEXT",
    "3D_SCAN":   "MESH_DATA",
    "PAINTING":  "BRUSH_DATA",
    "SKETCH":    "GREASEPENCIL",
    "ENGRAVING": "FORCE_TEXTURE",
    "OTHER":     "QUESTION",
}

RELIABILITY_ICONS = {
    "HIGH":   "KEYTYPE_KEYFRAME_VEC",
    "MEDIUM": "KEYTYPE_BREAKDOWN_VEC",
    "LOW":    "KEYTYPE_JITTER_VEC",
}

# Key used for historical sources in glTF extras
EXTRAS_KEY = "historical_sources"

CSV_HEADER = [
    "source_id", "title", "source_type", "date", "toponym",
    "repository", "inventory_nr", "url", "reliability", "description", "notes",
]

TYPE_MAP = [
    ("photo",      "PHOTO"),
    ("foto",       "PHOTO"),
    ("drawing",    "DRAWING"),
    ("tekening",   "DRAWING"),
    ("plan",       "DRAWING"),
    ("map",        "MAP"),
    ("kaart",      "MAP"),
    ("cadastral",  "MAP"),
    ("written",    "WRITTEN"),
    ("text",       "WRITTEN"),
    ("tekst",      "WRITTEN"),
    ("scan",       "3D_SCAN"),
    ("survey",     "3D_SCAN"),
    ("painting",   "PAINTING"),
    ("schilderij", "PAINTING"),
    ("sketch",     "SKETCH"),
    ("schets",     "SKETCH"),
    ("engraving",  "ENGRAVING"),
    ("print",      "ENGRAVING"),
    ("gravure",    "ENGRAVING"),
]

def map_source_type(raw):
    if not raw:
        return "OTHER"
    low = str(raw).lower().strip()
    for keyword, enum_val in TYPE_MAP:
        if keyword in low:
            return enum_val
    return "OTHER"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def generate_id():
    return str(uuid.uuid4())

def get_library(context):
    return context.scene.hist_source_library

def find_source_by_id(library, source_id):
    return next((s for s in library.sources if s.source_id == source_id), None)

def resolve_object_sources(obj, library):
    return [
        (ref, find_source_by_id(library, ref.source_id))
        for ref in obj.hist_source_refs.refs
    ]

def is_url(value):
    return value.startswith("http://") or value.startswith("https://")

def file_extension_for_format(fmt):
    return ".glb" if fmt == "GLB" else ".gltf"

def source_passes_filter(src, lib):
    """Return True if src matches all active filters on the library."""
    f_inv     = lib.filter_inventory_nr.strip().lower()
    f_title   = lib.filter_title.strip().lower()
    f_toponym = lib.filter_toponym.strip().lower()
    f_date    = lib.filter_date.strip().lower()
    f_type    = lib.filter_type
    f_rel     = lib.filter_reliability

    if f_inv     and f_inv     not in src.inventory_nr.lower(): return False
    if f_title   and f_title   not in src.title.lower():        return False
    if f_toponym and f_toponym not in src.toponym.lower():      return False
    if f_date    and f_date    not in src.date.lower():         return False
    if f_type != "ALL" and src.source_type != f_type:           return False
    if f_rel  != "ALL" and src.reliability  != f_rel:           return False
    return True

def filters_active(lib):
    return (
        lib.filter_inventory_nr.strip() != "" or
        lib.filter_title.strip()        != "" or
        lib.filter_toponym.strip()      != "" or
        lib.filter_date.strip()         != "" or
        lib.filter_type                 != "ALL" or
        lib.filter_reliability          != "ALL"
    )

# ---------------------------------------------------------------------------
# Shared helper: write all fields of a HistoricalSource in one call
# ---------------------------------------------------------------------------

def _copy_source_fields(src, sid, title, stype, date, topo, repo, inv, url, rel, desc, notes):
    src.source_id    = sid
    src.title        = title
    src.source_type  = stype
    src.date         = date
    src.toponym      = topo
    src.repository   = repo
    src.inventory_nr = inv
    src.url          = url
    src.reliability  = rel
    src.description  = desc
    src.notes        = notes

# ---------------------------------------------------------------------------
# Shared helper: import rows into library
# ---------------------------------------------------------------------------

def _import_rows_into_library(lib, rows, skip_duplicates):
    """
    rows: list of dicts keyed by HistoricalSource field names.
    Returns (added, skipped).
    """
    existing_titles = {s.title for s in lib.sources} if skip_duplicates else set()
    added = skipped = 0
    for row in rows:
        title = (row.get("title") or "").strip()
        if not title:
            skipped += 1
            continue
        if skip_duplicates and title in existing_titles:
            skipped += 1
            continue
        rel = (row.get("reliability") or "MEDIUM").strip().upper()
        _copy_source_fields(
            lib.sources.add(),
            sid   = (row.get("source_id")    or generate_id()).strip(),
            title = title,
            stype = (row.get("source_type")  or "OTHER").strip(),
            date  = (row.get("date")         or "").strip(),
            topo  = (row.get("toponym")      or "").strip(),
            repo  = (row.get("repository")   or "").strip(),
            inv   = (row.get("inventory_nr") or "").strip(),
            url   = (row.get("url")          or "").strip(),
            rel   = rel if rel in {"HIGH", "MEDIUM", "LOW"} else "MEDIUM",
            desc  = (row.get("description")  or "").strip(),
            notes = (row.get("notes")        or "").strip(),
        )
        existing_titles.add(title)
        added += 1
    return added, skipped

# ---------------------------------------------------------------------------
# Extras helpers
# ---------------------------------------------------------------------------

def sources_to_dict_for_export(obj, library):
    """Build the historical_sources list for glTF extras."""
    result = []
    for ref, src in resolve_object_sources(obj, library):
        entry = {"source_id": ref.source_id, "part_note": ref.part_note}
        if src:
            entry.update({
                "title":        src.title,
                "source_type":  src.source_type,
                "date":         src.date,
                "toponym":      src.toponym,
                "repository":   src.repository,
                "inventory_nr": src.inventory_nr,
                "url":          src.url,
                "reliability":  src.reliability,
                "description":  src.description,
                "notes":        src.notes,
            })
        result.append(entry)
    return result

def build_extras_for_object(obj, library):
    """
    Build the full extras dict for an object, combining historical sources
    and uncertainty classification (from the Uncertainty Index addon, if present).
    In Blender 4.x this dict is written as a single custom property so the
    built-in glTF exporter picks it up automatically via export_extras=True.
    """
    extras = {}

    sources = sources_to_dict_for_export(obj, library)
    if sources:
        extras[EXTRAS_KEY] = sources

    # Uncertainty Index addon stores these as plain custom properties
    uncertainty_index = obj.get("uncertainty_index")
    uncertainty_label = obj.get("uncertainty_label")
    if uncertainty_index is not None:
        extras["uncertainty_index"] = uncertainty_index
    if uncertainty_label is not None:
        extras["uncertainty_label"] = uncertainty_label

    return extras

def write_extras_to_object(obj, extras):
    """
    Write the extras dict as a JSON custom property on the object so Blender 4.x
    glTF exporter picks it up when export_extras=True.
    We store the payload under a single key to avoid polluting the namespace.
    """
    if extras:
        obj["_hist_extras"] = json.dumps(extras)
    elif "_hist_extras" in obj:
        del obj["_hist_extras"]

def clear_extras_from_object(obj):
    if "_hist_extras" in obj:
        del obj["_hist_extras"]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class HistoricalSource(PropertyGroup):
    source_id:    StringProperty(name="ID",                   default="")
    title:        StringProperty(name="Title",                default="Untitled Source")
    source_type:  EnumProperty(  name="Type",                 items=SOURCE_TYPES, default="PHOTO")
    date:         StringProperty(name="Date / Period",        default="")
    toponym:      StringProperty(name="Toponym",              default="")
    repository:   StringProperty(name="Repository / Archive", default="")
    inventory_nr: StringProperty(name="Inventory / Call No.", default="")
    url:          StringProperty(name="URL",                  default="")
    reliability:  EnumProperty(  name="Reliability",          items=RELIABILITY, default="HIGH")
    description:  StringProperty(name="Description",          default="")
    notes:        StringProperty(name="Notes",                default="")

FILTER_TYPE_ITEMS = [("ALL", "All Types", "")] + list(SOURCE_TYPES)
FILTER_REL_ITEMS  = [("ALL", "All",       "")] + list(RELIABILITY)

class HistoricalSourceLibrary(PropertyGroup):
    sources:             CollectionProperty(type=HistoricalSource)
    active_index:        IntProperty(name="Active Library Index", default=0)
    sort_order:          EnumProperty(
        name="Sort By", items=SORT_OPTIONS, default="NONE",
        description="Sort the source library list"
    )
    filter_inventory_nr: StringProperty(
        name="Inventory No.", default="",
        description="Filter by inventory number (substring, case-insensitive)"
    )
    filter_title:        StringProperty(
        name="Title", default="",
        description="Filter by title (substring, case-insensitive)"
    )
    filter_toponym:      StringProperty(
        name="Toponym", default="",
        description="Filter by toponym (substring, case-insensitive)"
    )
    filter_date:         StringProperty(
        name="Date", default="",
        description="Filter by date string (substring, case-insensitive)"
    )
    filter_type:         EnumProperty(
        name="Type", items=FILTER_TYPE_ITEMS, default="ALL",
        description="Filter by source type"
    )
    filter_reliability:  EnumProperty(
        name="Reliability", items=FILTER_REL_ITEMS, default="ALL",
        description="Filter by reliability"
    )
    show_filters:        BoolProperty(name="Show Filters", default=False)

class ObjectSourceRef(PropertyGroup):
    source_id: StringProperty(name="Source ID", default="")
    part_note: StringProperty(
        name="Part Note",
        description="Which part of this object the source applies to",
        default="",
    )

class ObjectSourceRefs(PropertyGroup):
    refs:         CollectionProperty(type=ObjectSourceRef)
    active_index: IntProperty(name="Active Ref Index", default=0)

class HistExportSettings(PropertyGroup):
    directory:         StringProperty(name="Export Directory", default="//exports/", subtype="DIR_PATH")
    file_format:       EnumProperty(name="Format", items=EXPORT_FORMAT, default="GLB")
    export_scope:      EnumProperty(name="Scope",  items=EXPORT_SCOPE,  default="ALL")
    only_with_sources: BoolProperty(name="Only Objects with Sources", default=True)
    export_textures:   BoolProperty(name="Include Textures",          default=True)
    apply_modifiers:   BoolProperty(
        name="Apply Modifiers",
        description="Apply modifiers to exported meshes",
        default=False,
    )

class HistImportSettings(PropertyGroup):
    filepath: StringProperty(
        name="File",
        description="Path to an Excel (.xlsx) or CSV (.csv) file containing sources",
        default="",
        subtype="FILE_PATH",
    )
    skip_duplicates: BoolProperty(
        name="Skip Duplicate Titles",
        description="Skip rows whose Title already exists in the library",
        default=True,
    )
    default_reliability: EnumProperty(
        name="Default Reliability",
        description="Reliability assigned to all imported sources",
        items=RELIABILITY,
        default="MEDIUM",
    )

# ---------------------------------------------------------------------------
# UI Lists
# ---------------------------------------------------------------------------

class HIST_UL_LibraryList(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            row.label(text="", icon=SOURCE_TYPE_ICONS.get(item.source_type, "QUESTION"))
            title_col = row.row(align=True)
            title_col.ui_units_x = 7
            title_col.label(text=item.title if item.title else "-")
            inv_col = row.row(align=True)
            inv_col.ui_units_x = 3
            inv_col.label(text=item.inventory_nr if item.inventory_nr else "-")
            date_col = row.row(align=True)
            date_col.ui_units_x = 1
            date_col.label(text=item.date if item.date else "-")
            row.label(text="", icon=RELIABILITY_ICONS.get(item.reliability, "QUESTION"))
            if is_url(item.url):
                op = row.operator("hist.open_url", text="", icon="URL", emboss=False)
                op.url = item.url
            else:
                row.label(text="", icon="BLANK1")
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text="", icon=SOURCE_TYPE_ICONS.get(item.source_type, "QUESTION"))

    def filter_items(self, context, data, propname):
        lib     = context.scene.hist_source_library
        sources = getattr(data, propname)
        flags   = [
            self.bitflag_filter_item if source_passes_filter(src, lib) else 0
            for src in sources
        ]
        return flags, []

class HIST_UL_ObjectRefList(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        lib = context.scene.hist_source_library
        src = find_source_by_id(lib, item.source_id)
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            if src:
                row.label(text="", icon=SOURCE_TYPE_ICONS.get(src.source_type, "QUESTION"))
                row.label(text=src.title)
                if item.part_note:
                    row.label(text=f"({item.part_note})")
                row.label(text="", icon=RELIABILITY_ICONS.get(src.reliability, "QUESTION"))
            else:
                row.label(text=f"[missing] {item.source_id[:8]}...", icon="ERROR")
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(
                text="",
                icon=SOURCE_TYPE_ICONS.get(src.source_type, "QUESTION") if src else "ERROR",
            )

# ---------------------------------------------------------------------------
# Operators — filter clear
# ---------------------------------------------------------------------------

class HIST_OT_ClearFilters(Operator):
    bl_idname      = "hist.clear_filters"
    bl_label       = "Clear Filters"
    bl_description = "Reset all library filters"

    def execute(self, context):
        lib = get_library(context)
        lib.filter_inventory_nr = ""
        lib.filter_title        = ""
        lib.filter_toponym      = ""
        lib.filter_date         = ""
        lib.filter_type         = "ALL"
        lib.filter_reliability  = "ALL"
        self.report({"INFO"}, "Filters cleared.")
        return {"FINISHED"}

# ---------------------------------------------------------------------------
# Operators — URL
# ---------------------------------------------------------------------------

class HIST_OT_OpenURL(Operator):
    bl_idname      = "hist.open_url"
    bl_label       = "Open URL"
    bl_description = "Open this URL in your web browser"
    url: StringProperty(name="URL", default="")

    def execute(self, context):
        if not self.url:
            self.report({"WARNING"}, "No URL provided.")
            return {"CANCELLED"}
        if not is_url(self.url):
            self.report({"WARNING"}, f"Not a valid URL: '{self.url}'")
            return {"CANCELLED"}
        webbrowser.open(self.url)
        self.report({"INFO"}, f"Opened: {self.url}")
        return {"FINISHED"}

# ---------------------------------------------------------------------------
# Operators — Library management
# ---------------------------------------------------------------------------

class HIST_OT_LibAddSource(Operator):
    bl_idname      = "hist.lib_add_source"
    bl_label       = "Add Source to Library"
    bl_description = "Add a new source to the scene library"

    def execute(self, context):
        lib = get_library(context)
        src = lib.sources.add()
        src.source_id    = generate_id()
        src.title        = "New Source"
        lib.active_index = len(lib.sources) - 1
        return {"FINISHED"}

class HIST_OT_LibRemoveSource(Operator):
    bl_idname      = "hist.lib_remove_source"
    bl_label       = "Remove Source from Library"
    bl_description = "Remove the selected source from the scene library"

    def execute(self, context):
        lib = get_library(context)
        idx = lib.active_index
        if not (0 <= idx < len(lib.sources)):
            return {"CANCELLED"}
        removed_id = lib.sources[idx].source_id
        lib.sources.remove(idx)
        lib.active_index = max(0, idx - 1)
        orphaned = [
            obj.name for obj in context.scene.objects
            if any(ref.source_id == removed_id for ref in obj.hist_source_refs.refs)
        ]
        if orphaned:
            self.report({"WARNING"}, f"Source removed but still referenced by: {', '.join(orphaned)}")
        return {"FINISHED"}

class HIST_OT_LibDuplicateSource(Operator):
    bl_idname      = "hist.lib_duplicate_source"
    bl_label       = "Duplicate Source"
    bl_description = "Duplicate the selected library source (assigns a new ID)"

    def execute(self, context):
        lib = get_library(context)
        idx = lib.active_index
        if not (0 <= idx < len(lib.sources)):
            return {"CANCELLED"}
        o = lib.sources[idx]
        _copy_source_fields(
            lib.sources.add(),
            sid   = generate_id(),
            title = o.title + " (copy)",
            stype = o.source_type,
            date  = o.date,
            topo  = o.toponym,
            repo  = o.repository,
            inv   = o.inventory_nr,
            url   = o.url,
            rel   = o.reliability,
            desc  = o.description,
            notes = o.notes,
        )
        lib.active_index = len(lib.sources) - 1
        return {"FINISHED"}

class HIST_OT_LibClearAll(Operator):
    bl_idname      = "hist.lib_clear_all"
    bl_label       = "Delete Entire Library"
    bl_description = "Remove ALL sources from the scene library (asks for confirmation)"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        lib = get_library(context)
        n   = len(lib.sources)
        lib.sources.clear()
        lib.active_index = 0
        self.report({"INFO"}, f"Library cleared ({n} sources removed).")
        return {"FINISHED"}

class HIST_OT_SortSources(Operator):
    bl_idname      = "hist.sort_sources"
    bl_label       = "Apply Sort"
    bl_description = "Physically reorder the source list by the chosen sort key"

    def execute(self, context):
        lib = get_library(context)
        if lib.sort_order == "NONE":
            self.report({"INFO"}, "Sort order is 'Original' — nothing to do.")
            return {"FINISHED"}

        items = [
            (
                src.source_id, src.title, src.source_type, src.date,
                src.toponym, src.repository, src.inventory_nr, src.url,
                src.reliability, src.description, src.notes,
            )
            for src in lib.sources
        ]

        if lib.sort_order == "TITLE":
            key = lambda x: x[1].lower()
        elif lib.sort_order == "DATE":
            key = lambda x: x[3].lower()
        else:  # TOPONYM
            key = lambda x: x[4].lower()

        items.sort(key=key)

        active_id = lib.sources[lib.active_index].source_id if lib.sources else ""
        lib.sources.clear()

        for sid, title, stype, date, topo, repo, inv, url, rel, desc, notes in items:
            _copy_source_fields(
                lib.sources.add(),
                sid, title, stype, date, topo, repo, inv, url, rel, desc, notes,
            )

        for i, src in enumerate(lib.sources):
            if src.source_id == active_id:
                lib.active_index = i
                break

        self.report({"INFO"}, f"Library sorted by {lib.sort_order.lower()}.")
        return {"FINISHED"}

# ---------------------------------------------------------------------------
# Operators — Library CSV export / import
# ---------------------------------------------------------------------------

class HIST_OT_LibraryExportCSV(Operator):
    bl_idname      = "hist.library_export_csv"
    bl_label       = "Export Library to CSV"
    bl_description = "Export the entire source library to a CSV file"

    filepath:    StringProperty(name="File Path", subtype="FILE_PATH", default="library.csv")
    filter_glob: StringProperty(default="*.csv", options={"HIDDEN"})

    def execute(self, context):
        lib  = get_library(context)
        path = bpy.path.abspath(self.filepath)
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_HEADER)
                for s in lib.sources:
                    writer.writerow([
                        s.source_id, s.title, s.source_type, s.date,
                        s.toponym, s.repository, s.inventory_nr, s.url,
                        s.reliability, s.description, s.notes,
                    ])
        except Exception as e:
            self.report({"ERROR"}, f"Failed to write CSV: {e}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Exported {len(lib.sources)} sources to '{path}'.")
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

class HIST_OT_LibraryImportCSV(Operator):
    bl_idname      = "hist.library_import_csv"
    bl_label       = "Import Library from CSV"
    bl_description = "Import sources from a library CSV file"

    filepath:        StringProperty(name="File Path", subtype="FILE_PATH")
    filter_glob:     StringProperty(default="*.csv", options={"HIDDEN"})
    clear_before:    BoolProperty(
        name="Clear Library First",
        description="Remove all existing sources before importing",
        default=False,
    )
    skip_duplicates: BoolProperty(
        name="Skip Duplicate Titles",
        description="Skip rows whose Title already exists in the library",
        default=True,
    )

    def execute(self, context):
        lib  = get_library(context)
        path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(path):
            self.report({"ERROR"}, f"File not found: {path}")
            return {"CANCELLED"}
        if self.clear_before:
            lib.sources.clear()
            lib.active_index = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                missing = [h for h in CSV_HEADER if h not in (reader.fieldnames or [])]
                if missing:
                    self.report({"ERROR"}, f"CSV missing columns: {', '.join(missing)}")
                    return {"CANCELLED"}
                rows = list(reader)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to read CSV: {e}")
            return {"CANCELLED"}

        added, skipped = _import_rows_into_library(lib, rows, self.skip_duplicates)
        lib.active_index = max(0, len(lib.sources) - 1)
        self.report({"INFO"}, f"Imported {added} source(s) from CSV, skipped {skipped}.")
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

# ---------------------------------------------------------------------------
# Operators — Object references
# ---------------------------------------------------------------------------

class HIST_OT_LinkToSelected(Operator):
    bl_idname      = "hist.link_to_selected"
    bl_label       = "Link to Selected Objects"
    bl_description = (
        "Link the active library source to all selected objects. "
        "Objects that already have this source linked are skipped."
    )

    def execute(self, context):
        lib = get_library(context)
        if not lib.sources:
            self.report({"WARNING"}, "No sources in library.")
            return {"CANCELLED"}
        src     = lib.sources[lib.active_index]
        targets = [
            o for o in context.selected_objects
            if o.type in {"MESH", "CURVE", "SURFACE", "META", "FONT", "GREASEPENCIL"}
        ]
        if not targets:
            self.report({"WARNING"}, "No valid objects selected.")
            return {"CANCELLED"}
        linked, skipped = [], []
        for obj in targets:
            if any(ref.source_id == src.source_id for ref in obj.hist_source_refs.refs):
                skipped.append(obj.name)
            else:
                ref = obj.hist_source_refs.refs.add()
                ref.source_id = src.source_id
                obj.hist_source_refs.active_index = len(obj.hist_source_refs.refs) - 1
                linked.append(obj.name)
        msg = f"Linked '{src.title}' to {len(linked)} object(s)"
        if skipped:
            msg += f" | already linked on {len(skipped)}: {', '.join(skipped)}"
        self.report({"INFO"}, msg)
        return {"FINISHED"}

class HIST_OT_RefRemoveSource(Operator):
    bl_idname      = "hist.ref_remove_source"
    bl_label       = "Unlink Source from Object"
    bl_description = "Remove the selected source reference from the active object"

    def execute(self, context):
        refs = context.object.hist_source_refs
        idx  = refs.active_index
        if 0 <= idx < len(refs.refs):
            refs.refs.remove(idx)
            refs.active_index = max(0, idx - 1)
        return {"FINISHED"}

# ---------------------------------------------------------------------------
# Operators — Excel / external CSV import into library
# ---------------------------------------------------------------------------

def _cell(row, key):
    val = row.get(key, "")
    if val is None:
        return ""
    try:
        if math.isnan(float(val)):
            return ""
    except (TypeError, ValueError):
        pass
    return str(val).strip()

def _build_date(row):
    date  = _cell(row, "Date")
    early = _cell(row, "Date early")
    late  = _cell(row, "Date late")
    if early or late:
        range_str = f"{early}-{late}".strip("-")
        return f"{date} ({range_str})" if date else range_str
    return date

class HIST_OT_ImportSources(Operator):
    bl_idname      = "hist.import_sources"
    bl_label       = "Import Sources"
    bl_description = "Import sources from an Excel (.xlsx) or CSV (.csv) file into the library"

    def execute(self, context):
        settings = context.scene.hist_import_settings
        filepath = bpy.path.abspath(settings.filepath)
        if not filepath:
            self.report({"ERROR"}, "No file path set.")
            return {"CANCELLED"}
        if not os.path.isfile(filepath):
            self.report({"ERROR"}, f"File not found: {filepath}")
            return {"CANCELLED"}
        try:
            import pandas as pd
            ext = os.path.splitext(filepath)[1].lower()
            if ext in {".xlsx", ".xls"}:
                df = pd.read_excel(filepath, dtype=str)
            elif ext == ".csv":
                df = pd.read_csv(filepath, dtype=str)
            else:
                self.report({"ERROR"}, f"Unsupported file type: '{ext}'. Use .xlsx or .csv")
                return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, f"Could not read file: {e}")
            return {"CANCELLED"}

        if "Title" not in df.columns:
            self.report({"ERROR"}, "File must contain a 'Title' column.")
            return {"CANCELLED"}

        lib  = get_library(context)
        rows = []
        for raw in df.to_dict(orient="records"):
            rows.append({
                "title":        _cell(raw, "Title"),
                "source_type":  map_source_type(_cell(raw, "Type")),
                "date":         _build_date(raw),
                "toponym":      _cell(raw, "Toponym"),
                "repository":   _cell(raw, "Origin"),
                "inventory_nr": _cell(raw, "File Name"),
                "url":          _cell(raw, "Link"),
                "reliability":  settings.default_reliability,
                "description":  _cell(raw, "Description"),
                "notes":        "",
            })

        added, skipped = _import_rows_into_library(lib, rows, settings.skip_duplicates)
        lib.active_index = max(0, len(lib.sources) - 1)
        self.report({"INFO"}, f"Imported {added} source(s), skipped {skipped}.")
        return {"FINISHED"}

# ---------------------------------------------------------------------------
# Operators — glTF batch export + text report
# ---------------------------------------------------------------------------

class HIST_OT_ExportReport(Operator):
    bl_idname      = "hist.export_report"
    bl_label       = "Export Source Report"
    bl_description = "Write a full source report for all objects to a text block"

    def execute(self, context):
        lib   = get_library(context)
        lines = ["HISTORICAL SOURCE REPORT", "=" * 60, ""]
        for obj in context.scene.objects:
            pairs = resolve_object_sources(obj, lib)
            if not pairs:
                continue
            lines += [f"Object: {obj.name}", "-" * 40]
            for i, (ref, src) in enumerate(pairs, 1):
                label = src.title if src else f"[missing id: {ref.source_id[:8]}...]"
                lines.append(f"  [{i}] {label}")
                if ref.part_note:
                    lines.append(f"      Part:         {ref.part_note}")
                if src:
                    lines.append(f"      ID:           {src.source_id}")
                    lines.append(f"      Type:         {src.source_type}")
                    lines.append(f"      Date:         {src.date or '-'}")
                    lines.append(f"      Toponym:      {src.toponym or '-'}")
                    lines.append(f"      Repository:   {src.repository or '-'}")
                    lines.append(f"      Inventory No: {src.inventory_nr or '-'}")
                    lines.append(f"      URL:          {src.url or '-'}")
                    lines.append(f"      Reliability:  {src.reliability}")
                    if src.description:
                        lines.append(f"      Description:  {src.description}")
                    if src.notes:
                        lines.append(f"      Notes:        {src.notes}")
                else:
                    lines.append("      [Source not found in library]")
                lines.append("")
            lines.append("")
        name = "Historical_Source_Report.txt"
        if name in bpy.data.texts:
            bpy.data.texts.remove(bpy.data.texts[name])
        bpy.data.texts.new(name).write("\n".join(lines))
        self.report({"INFO"}, f"Report written to Text Editor: '{name}'")
        return {"FINISHED"}

class HIST_OT_BatchExport(Operator):
    bl_idname      = "hist.batch_export"
    bl_label       = "Batch Export glTF / GLB"
    bl_description = (
        "Export objects as glTF/GLB. Historical sources and uncertainty classifications "
        "are embedded as extras via Blender's built-in custom property export."
    )

    def _prepare_extras(self, context, candidates):
        """
        Write extras as custom properties on each candidate object so the
        built-in glTF exporter picks them up via export_extras=True.
        Returns a list of objects that had extras written (for cleanup).
        """
        lib     = get_library(context)
        written = []
        for obj in candidates:
            extras = build_extras_for_object(obj, lib)
            if extras:
                write_extras_to_object(obj, extras)
                written.append(obj)
        return written

    def _cleanup_extras(self, written):
        for obj in written:
            clear_extras_from_object(obj)

    def execute(self, context):
        settings  = context.scene.hist_export_settings
        directory = bpy.path.abspath(settings.directory)
        if not directory:
            self.report({"ERROR"}, "No export directory set.")
            return {"CANCELLED"}
        os.makedirs(directory, exist_ok=True)

        exportable_types = {"MESH", "CURVE", "SURFACE", "META", "FONT", "GREASEPENCIL"}

        # ------------------------------------------------------------------ #
        # SELECTED — SINGLE FILE                                            #
        # ------------------------------------------------------------------ #
        if settings.export_scope == "SELECTED_SINGLE":
            candidates = [o for o in context.selected_objects if o.type in exportable_types]
            if settings.only_with_sources:
                candidates = [o for o in candidates if o.hist_source_refs.refs]
            if not candidates:
                self.report({"WARNING"}, "No exportable objects found in selection.")
                return {"CANCELLED"}

            filepath = os.path.join(
                directory, "selection" + file_extension_for_format(settings.file_format)
            )
            written = self._prepare_extras(context, candidates)
            try:
                bpy.ops.export_scene.gltf(
                    filepath=filepath,
                    use_selection=True,
                    export_format=settings.file_format,
                    export_extras=True,
                    export_apply=settings.apply_modifiers,
                    export_materials="EXPORT" if settings.export_textures else "NONE",
                )
            except Exception as e:
                self._cleanup_extras(written)
                self.report({"ERROR"}, f"Export failed: {e}")
                return {"CANCELLED"}
            self._cleanup_extras(written)
            self.report({"INFO"}, f"Exported {len(candidates)} object(s) to '{filepath}'.")
            return {"FINISHED"}

        # ------------------------------------------------------------------ #
        # ALL or SELECTED — one file per object                             #
        # ------------------------------------------------------------------ #
        candidates = (
            list(context.selected_objects) if settings.export_scope == "SELECTED"
            else list(context.scene.objects)
        )
        if settings.only_with_sources:
            candidates = [o for o in candidates if o.hist_source_refs.refs]
        candidates = [o for o in candidates if o.type in exportable_types]
        if not candidates:
            self.report({"WARNING"}, "No exportable objects found.")
            return {"CANCELLED"}

        original_active    = context.view_layer.objects.active
        original_selection = list(context.selected_objects)
        exported, skipped  = [], []

        for obj in candidates:
            written = self._prepare_extras(context, [obj])
            try:
                bpy.ops.object.select_all(action="DESELECT")
                obj.select_set(True)
                context.view_layer.objects.active = obj
                filepath = os.path.join(
                    directory,
                    bpy.path.clean_name(obj.name) + file_extension_for_format(settings.file_format),
                )
                bpy.ops.export_scene.gltf(
                    filepath=filepath,
                    use_selection=True,
                    export_format=settings.file_format,
                    export_extras=True,
                    export_apply=settings.apply_modifiers,
                    export_materials="EXPORT" if settings.export_textures else "NONE",
                )
                exported.append(obj.name)
            except Exception as e:
                skipped.append(obj.name)
                self.report({"WARNING"}, f"Failed '{obj.name}': {e}")
            finally:
                self._cleanup_extras(written)

        bpy.ops.object.select_all(action="DESELECT")
        for o in original_selection:
            o.select_set(True)
        if original_active:
            context.view_layer.objects.active = original_active

        msg = f"Exported {len(exported)} object(s) to '{directory}'"
        if skipped:
            msg += f" | {len(skipped)} failed: {', '.join(skipped)}"
        self.report({"INFO"}, msg)
        return {"FINISHED"}

# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def draw_url_field(layout, src, editable=True):
    row      = layout.row(align=True)
    text_col = row.row(align=True)
    text_col.enabled = editable
    text_col.prop(src, "url")
    if is_url(src.url):
        op     = row.operator("hist.open_url", text="", icon="URL")
        op.url = src.url

def draw_source_fields(layout, src, editable=True):
    """Unified editable/readonly source field drawing."""
    col         = layout.column(align=True)
    col.enabled = editable
    col.prop(src, "title")
    row = col.row(align=True)
    row.prop(src, "source_type")
    row.prop(src, "reliability")
    col.prop(src, "date")
    col.prop(src, "toponym")
    col.prop(src, "repository")
    col.prop(src, "inventory_nr")
    col.separator()
    col.prop(src, "description")
    col.prop(src, "notes")
    draw_url_field(layout, src, editable=editable)

# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------

SIDEBAR_CATEGORY = "Hist. Sources"

class HIST_PT_LibraryPanel(Panel):
    bl_label       = "Source Library"
    bl_idname      = "HIST_PT_library_panel"
    bl_space_type  = "VIEW_3D"
    bl_region_type = "UI"
    bl_category    = SIDEBAR_CATEGORY

    def draw(self, context):
        layout = self.layout
        lib    = get_library(context)

        row = layout.row(align=True)
        row.prop(lib, "sort_order", text="Sort")
        row.operator("hist.sort_sources", text="", icon="FILE_REFRESH")

        active = filters_active(lib)
        box    = layout.box()
        row    = box.row(align=True)
        row.prop(lib, "show_filters",
                 icon="TRIA_DOWN" if lib.show_filters else "TRIA_RIGHT",
                 icon_only=True, emboss=False)
        if active:
            row.label(text="Filter  [active]", icon="FILTER")
            row.operator("hist.clear_filters", text="", icon="X")
        else:
            row.label(text="Filter", icon="FILTER")

        if lib.show_filters:
            col = box.column(align=True)
            col.prop(lib, "filter_inventory_nr", icon="SHORTDISPLAY")
            col.prop(lib, "filter_title",        icon="SORTALPHA")
            col.prop(lib, "filter_toponym",      icon="WORLD")
            col.prop(lib, "filter_date",         icon="TIME")
            col.prop(lib, "filter_type")
            col.prop(lib, "filter_reliability")
            if active:
                col.separator()
                col.operator("hist.clear_filters", icon="X", text="Clear All Filters")

        if active:
            total   = len(lib.sources)
            visible = sum(1 for s in lib.sources if source_passes_filter(s, lib))
            layout.label(text=f"Showing {visible} of {total} sources", icon="INFO")

        row = layout.row()
        row.template_list("HIST_UL_LibraryList", "", lib, "sources", lib, "active_index", rows=6)
        col = row.column(align=True)
        col.operator("hist.lib_add_source",       icon="ADD",       text="")
        col.operator("hist.lib_remove_source",    icon="REMOVE",    text="")
        col.separator()
        col.operator("hist.lib_duplicate_source", icon="DUPLICATE", text="")
        col.separator()
        col.operator("hist.lib_clear_all",        icon="TRASH",     text="")

        if lib.sources and 0 <= lib.active_index < len(lib.sources):
            src = lib.sources[lib.active_index]
            box = layout.box()
            box.label(text="Edit Source", icon="GREASEPENCIL")
            draw_source_fields(box, src, editable=True)
            row         = box.row()
            row.enabled = False
            row.prop(src, "source_id", text="ID")
            layout.separator()
            layout.operator("hist.link_to_selected", icon="LINKED")

        layout.separator()
        row = layout.row(align=True)
        row.operator("hist.library_export_csv", icon="EXPORT", text="Export Library CSV")
        row.operator("hist.library_import_csv", icon="IMPORT", text="Import Library CSV")

class HIST_PT_ObjectPanel(Panel):
    bl_label       = "Object Sources"
    bl_idname      = "HIST_PT_object_panel"
    bl_space_type  = "VIEW_3D"
    bl_region_type = "UI"
    bl_category    = SIDEBAR_CATEGORY

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def draw(self, context):
        layout = self.layout
        obj    = context.object
        lib    = get_library(context)
        refs   = obj.hist_source_refs
        layout.label(text=f"Object: {obj.name}", icon="OBJECT_DATA")
        row = layout.row()
        row.template_list("HIST_UL_ObjectRefList", "", refs, "refs", refs, "active_index", rows=4)
        col = row.column(align=True)
        col.operator("hist.ref_remove_source", icon="UNLINKED", text="")

        if refs.refs and 0 <= refs.active_index < len(refs.refs):
            active_ref = refs.refs[refs.active_index]
            src        = find_source_by_id(lib, active_ref.source_id)
            box        = layout.box()
            box.label(text="Reference detail", icon="GREASEPENCIL")
            box.prop(active_ref, "part_note")
            if src:
                draw_source_fields(box, src, editable=False)
            else:
                box.label(text="Source not found in library.", icon="ERROR")

class HIST_PT_ImportPanel(Panel):
    bl_label       = "Import Sources from File"
    bl_idname      = "HIST_PT_import_panel"
    bl_space_type  = "VIEW_3D"
    bl_region_type = "UI"
    bl_category    = SIDEBAR_CATEGORY
    bl_options     = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        s      = context.scene.hist_import_settings
        layout.prop(s, "filepath")
        col = layout.column(align=True)
        col.prop(s, "default_reliability")
        col.prop(s, "skip_duplicates")
        layout.separator()
        box = layout.box()
        box.label(text="Column mapping:", icon="INFO")
        col = box.column(align=True)
        col.scale_y = 0.8
        for excel_col, addon_field in [
            ("Title",       "title  (required)"),
            ("Type",        "source_type"),
            ("Date",        "date"),
            ("Toponym",     "toponym"),
            ("Origin",      "repository"),
            ("File Name",   "inventory_nr"),
            ("Link",        "url"),
            ("Description", "description"),
        ]:
            col.label(text=f"  {excel_col}  ->  {addon_field}")
        layout.separator()
        layout.operator("hist.import_sources", icon="IMPORT", text="Import into Library")

class HIST_PT_ExportPanel(Panel):
    bl_label       = "Export"
    bl_idname      = "HIST_PT_export_panel"
    bl_space_type  = "VIEW_3D"
    bl_region_type = "UI"
    bl_category    = SIDEBAR_CATEGORY
    bl_options     = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        s      = context.scene.hist_export_settings
        layout.prop(s, "directory")
        layout.row(align=True).prop(s, "file_format",  expand=True)
        layout.row(align=True).prop(s, "export_scope", expand=True)
        col = layout.column(align=True)
        col.prop(s, "only_with_sources")
        col.prop(s, "export_textures")
        col.prop(s, "apply_modifiers")
        layout.separator()
        box = layout.box()
        box.label(text="Extras included in export:", icon="INFO")
        col = box.column(align=True)
        col.scale_y = 0.8
        col.label(text="  historical_sources  (this addon)")
        col.label(text="  uncertainty_index   (if assigned)")
        col.label(text="  uncertainty_label   (if assigned)")
        layout.separator()
        layout.operator("hist.batch_export",  icon="EXPORT", text="Batch Export")
        layout.operator("hist.export_report", icon="TEXT")

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    HistoricalSource,
    HistoricalSourceLibrary,
    ObjectSourceRef,
    ObjectSourceRefs,
    HistExportSettings,
    HistImportSettings,
    HIST_UL_LibraryList,
    HIST_UL_ObjectRefList,
    HIST_OT_ClearFilters,
    HIST_OT_OpenURL,
    HIST_OT_LibAddSource,
    HIST_OT_LibRemoveSource,
    HIST_OT_LibDuplicateSource,
    HIST_OT_LibClearAll,
    HIST_OT_SortSources,
    HIST_OT_LibraryExportCSV,
    HIST_OT_LibraryImportCSV,
    HIST_OT_LinkToSelected,
    HIST_OT_RefRemoveSource,
    HIST_OT_ImportSources,
    HIST_OT_ExportReport,
    HIST_OT_BatchExport,
    HIST_PT_LibraryPanel,
    HIST_PT_ObjectPanel,
    HIST_PT_ImportPanel,
    HIST_PT_ExportPanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.hist_source_library  = PointerProperty(type=HistoricalSourceLibrary)
    bpy.types.Scene.hist_export_settings = PointerProperty(type=HistExportSettings)
    bpy.types.Scene.hist_import_settings = PointerProperty(type=HistImportSettings)
    bpy.types.Object.hist_source_refs    = PointerProperty(type=ObjectSourceRefs)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.hist_source_library
    del bpy.types.Scene.hist_export_settings
    del bpy.types.Scene.hist_import_settings
    del bpy.types.Object.hist_source_refs

if __name__ == "__main__":
    register()