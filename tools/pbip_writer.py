"""Write a Power BI Project (PBIP) over a folder of pipeline CSVs.

Output is the format Power BI Desktop saves natively: a TMDL semantic model (one Power Query per CSV,
types only, no transformation logic) and a PBIR report (one JSON file per page and visual). Opening the
.pbip in Power BI Desktop and pressing Refresh loads the CSVs; File > Save as .pbix gives a single file.

IDs and lineage tags are derived from names, so rebuilding produces no diff unless the spec changed.
Schema versions match those written by Power BI Desktop 2.159 (Oct 2026).
"""
import hashlib
import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric"
BASE_THEME = "Fluent2-CY26SU10"
THEME_SRC = Path("C:/Program Files/WindowsApps/Microsoft.MicrosoftPowerBIDesktop_2.159.883.0_x64__8wekyb3d8bbwe"
                 "/bin/WebView2Resources/minerva/sharedresources/BaseThemes") / f"{BASE_THEME.split('-', 1)[1]}.json"
PAGE_W, PAGE_H = 1280, 720

# dataviz reference palette: categorical order is fixed; status colours are reserved for PASS/WARN/FAIL
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, INK_2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e1", "#fcfcfb"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( 00:00:00)?$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$")


def _hex(*parts, n=20):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:n]


def _guid(*parts):
    return str(uuid.UUID(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()))


def _q(name):
    """TMDL object name, quoted when it is not a plain identifier."""
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else "'" + name.replace("'", "''") + "'"


# ---------------------------------------------------------------- semantic model spec

@dataclass
class Measure:
    name: str
    dax: str
    fmt: str = ""
    folder: str = ""


@dataclass
class Table:
    name: str
    csv: str | None = None              # file in the data folder; None for a calculated table
    dax: str | None = None              # calculated table expression
    columns: dict | None = None         # calculated tables: {column: dataType}
    measures: list = field(default_factory=list)
    hidden: bool = False
    description: str = ""
    formats: dict = field(default_factory=dict)   # column -> format string, e.g. {"share": "0.0%"}


def infer_types(path):
    """Map each CSV column to a TMDL data type: int64, double, boolean, dateTime (date or datetime) or string."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    types = {}
    for c in df.columns:
        v = df[c][df[c] != ""]
        if v.empty:
            types[c] = "string"
        elif v.str.lower().isin(["true", "false"]).all():
            types[c] = "boolean"
        elif v.str.fullmatch(r"-?\d+").all() and not c.endswith("_id"):
            types[c] = "int64"
        elif pd.to_numeric(v, errors="coerce").notna().all():
            types[c] = "double"
        elif v.str.match(DATE_RE).all():
            types[c] = "date"
        elif v.str.match(DATETIME_RE).all():
            types[c] = "datetime"
        else:
            types[c] = "string"
    return types


M_TYPE = {"int64": "Int64.Type", "double": "type number", "boolean": "type logical",
          "date": "type datetime", "datetime": "type datetime", "string": "type text"}


def _m_query(t, types):
    pairs = ", ".join(f'{{"{c}", {M_TYPE[k]}}}' for c, k in types.items())
    dates = ", ".join(f'{{"{c}", type date}}' for c, k in types.items() if k == "date")
    lines = [
        "let",
        f'    Source = Csv.Document(File.Contents(DataFolder & "\\{t.csv}"), '
        '[Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),',
        "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
        f'    Typed = Table.TransformColumnTypes(Promoted, {{{pairs}}}, "en-US")' + ("," if dates else ""),
    ]
    if dates:
        lines.append(f"    Dated = Table.TransformColumnTypes(Typed, {{{dates}}})")
    lines += ["in", "    Dated" if dates else "    Typed"]
    return lines


def _measure_tmdl(t, m):
    out = []
    if "\n" in m.dax.strip():
        out.append(f"\tmeasure {_q(m.name)} = ```")
        out += ["\t\t\t" + ln for ln in m.dax.strip().splitlines()]
        out.append("\t\t\t```")
    else:
        out.append(f"\tmeasure {_q(m.name)} = {m.dax.strip()}")
    if m.fmt:
        out.append(f"\t\tformatString: {m.fmt}")
    if m.folder:
        out.append(f"\t\tdisplayFolder: {m.folder}")
    out += [f"\t\tlineageTag: {_guid(t.name, 'measure', m.name)}", ""]
    return out


def _column_tmdl(t, c, kind, source):
    dtype = {"date": "dateTime", "datetime": "dateTime"}.get(kind, kind)
    out = [f"\tcolumn {_q(c)}", f"\t\tdataType: {dtype}"]
    if c in t.formats:
        out.append(f"\t\tformatString: {t.formats[c]}")
    elif kind == "date":
        out.append("\t\tformatString: d mmm yyyy")
    elif kind == "datetime":
        out.append("\t\tformatString: d mmm yyyy hh:nn")
    elif kind == "int64":
        out.append("\t\tformatString: 0")
    out += [f"\t\tlineageTag: {_guid(t.name, 'column', c)}", "\t\tsummarizeBy: none", f"\t\tsourceColumn: {source}",
            "", "\t\tannotation SummarizationSetBy = User"]
    if kind == "date":
        out.append("\t\tannotation UnderlyingDateTimeDataType = Date")
    out.append("")
    return out


def table_tmdl(t, data_dir):
    out = []
    if t.description:
        out.append(f"/// {t.description}")
    out += [f"table {_q(t.name)}", f"\tlineageTag: {_guid(t.name, 'table')}"]
    if t.hidden:
        out.append("\tisHidden")
    out.append("")
    for m in t.measures:
        out += _measure_tmdl(t, m)
    if t.csv:
        types = infer_types(Path(data_dir) / t.csv)
        for c, k in types.items():
            out += _column_tmdl(t, c, k, c)
        out += [f"\tpartition {_q(t.name)} = m", "\t\tmode: import", "\t\tsource ="]
        out += ["\t\t\t\t" + ln for ln in _m_query(t, types)]
    else:
        for c, k in t.columns.items():
            out += _column_tmdl(t, c, k, f"[{c}]")
        out += [f"\tpartition {_q(t.name)} = calculated", "\t\tmode: import", f"\t\tsource = {t.dax}"]
    out += ["", "\tannotation PBI_ResultType = Table", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- report helpers

def col(t, c):
    return {"Column": {"Expression": {"SourceRef": {"Entity": t}}, "Property": c}}


def mea(t, m):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": t}}, "Property": m}}


def lit(v):
    """Power BI literal: bool -> true/false, int -> 12L, float -> 1.5D, str -> 'text'."""
    if isinstance(v, bool):
        s = "true" if v else "false"
    elif isinstance(v, int):
        s = f"{v}L"
    elif isinstance(v, float):
        s = f"{v}D"
    else:
        s = "'" + str(v).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": s}}}


def _proj(f, display=None):
    kind = next(iter(f))
    ent, prop = f[kind]["Expression"]["SourceRef"]["Entity"], f[kind]["Property"]
    p = {"field": f, "queryRef": f"{ent}.{prop}", "nativeQueryRef": prop}
    if display:
        p["displayName"] = display
    return p


def where_in(f, values, negate=False):
    """Categorical filter (visual or page level) keeping (or, if negate, dropping) the listed values."""
    ent, prop = f["Column"]["Expression"]["SourceRef"]["Entity"], f["Column"]["Property"]
    alias = ent[0].lower()
    cond = {"In": {"Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": alias}}, "Property": prop}}],
                   "Values": [[lit(v)["expr"]] for v in values]}}
    if negate:
        cond = {"Not": {"Expression": cond}}
    return {"Version": 2, "From": [{"Name": alias, "Entity": ent, "Type": 0}], "Where": [{"Condition": cond}]}


def _filter_config(filters):
    return {"filters": [{"name": _hex("filter", json.dumps(fl, sort_keys=True)), "field": f, "type": "Categorical",
                         "filter": fl} for f, fl in filters]}


@dataclass
class Visual:
    kind: str                           # card | table | bar | column | line | slicer | text
    x: int
    y: int
    w: int
    h: int
    title: str = ""
    fields: dict = field(default_factory=dict)   # role -> [field or (field, display name)]
    sort: list = field(default_factory=list)     # [(field, "Ascending"|"Descending")]
    filters: list = field(default_factory=list)  # [(field, where_in(...))]
    text: list = field(default_factory=list)     # textbox paragraphs: [(text, style dict)]
    single: bool = False                # slicer: single select
    default: list | None = None         # slicer: pre-selected values
    dropdown: bool = True
    totals: bool = False                # table: totals row (off: most tables list rates, which do not add up)
    font: float | None = None           # card: value font size (pt)


VISUAL_TYPE = {"card": "card", "table": "tableEx", "bar": "clusteredBarChart", "column": "clusteredColumnChart",
               "line": "lineChart", "slicer": "slicer", "text": "textbox"}


def _visual_json(v, name, z):
    vis = {"visualType": VISUAL_TYPE[v.kind]}
    if v.kind == "text":
        vis["objects"] = {"general": [{"properties": {"paragraphs": [
            {"textRuns": [{"value": txt, **({"textStyle": st} if st else {})}]} for txt, st in v.text]}}]}
    else:
        state = {}
        for role, fs in v.fields.items():
            state[role] = {"projections": [_proj(*f) if isinstance(f, tuple) else _proj(f) for f in fs]}
        vis["query"] = {"queryState": state}
        if v.sort:
            vis["query"]["sortDefinition"] = {"sort": [{"field": f, "direction": d} for f, d in v.sort],
                                              "isDefaultSort": True}
        objects = {}
        if v.kind == "slicer":
            objects["data"] = [{"properties": {"mode": lit("Dropdown" if v.dropdown else "Basic")}}]
            if v.single:
                objects["selection"] = [{"properties": {"singleSelect": lit(True), "strictSingleSelect": lit(True)}}]
            if v.default:
                f = next(iter(v.fields.values()))[0]
                objects["general"] = [{"properties": {"filter": {"filter": where_in(f, v.default)}}}]
            objects["header"] = [{"properties": {"show": lit(False)}}]
        if v.kind in ("bar", "column"):
            objects["labels"] = [{"properties": {"show": lit(True)}}]
        if v.kind == "table" and not v.totals:
            objects["total"] = [{"properties": {"totals": lit(False)}}]
        if v.kind == "card":
            props = {"labelDisplayUnits": lit(1.0)}          # exact values, not 42K
            if v.font:
                props["fontSize"] = lit(float(v.font))
                objects["categoryLabels"] = [{"properties": {"show": lit(False)}}]
            objects["labels"] = [{"properties": props}]
        if objects:
            vis["objects"] = objects
    if v.title:
        vis["visualContainerObjects"] = {"title": [{"properties": {"show": lit(True), "text": lit(v.title)}}],
                                         "subTitle": [{"properties": {"show": lit(False)}}]}
    vis["drillFilterOtherVisuals"] = True
    out = {"$schema": f"{SCHEMA}/item/report/definition/visualContainer/2.14.0/schema.json", "name": name,
           "position": {"x": v.x, "y": v.y, "z": z, "height": v.h, "width": v.w, "tabOrder": z},
           "visual": vis}
    if v.filters:
        out["filterConfig"] = _filter_config(v.filters)
    return out


@dataclass
class Page:
    name: str
    visuals: list
    filters: list = field(default_factory=list)


def heading(title, subtitle, w=PAGE_W - 40):
    return Visual("text", 20, 8, w, 64, text=[(title, {"fontSize": "18pt", "fontWeight": "bold", "color": INK}),
                                              (subtitle, {"fontSize": "10pt", "color": INK_2})])


def footer(note):
    return Visual("text", 20, PAGE_H - 34, PAGE_W - 40, 30, text=[(note, {"fontSize": "9pt", "color": MUTED})])


def _theme(name):
    return {"name": name, "dataColors": PALETTE, "foreground": INK, "foregroundNeutralSecondary": INK_2,
            "foregroundNeutralTertiary": MUTED, "background": SURFACE, "backgroundLight": "#f9f9f7",
            "backgroundNeutral": GRID, "tableAccent": PALETTE[0],
            "good": "#0ca30c", "neutral": "#fab219", "bad": "#d03b3b", "maximum": PALETTE[0],
            "center": "#f0efec", "minimum": PALETTE[1],
            "textClasses": {"title": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": INK},
                            "label": {"fontFace": "Segoe UI", "fontSize": 10, "color": INK_2}}}


# ---------------------------------------------------------------- writer

def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    path.write_text(content, encoding="utf-8", newline="\n")


def build(out_dir, name, data_dir, tables, relationships, pages, culture="en-AU"):
    """Write <out_dir>/<name>.pbip with its .SemanticModel and .Report folders (replacing earlier output)."""
    out_dir, data_dir = Path(out_dir), Path(data_dir).resolve()
    # Power BI rejects a measure named like any column (case-insensitive); fail here, not on open
    taken = {c.lower() for t in tables
             for c in (infer_types(data_dir / t.csv) if t.csv else t.columns)}
    clash = [m.name for t in tables for m in t.measures if m.name.lower() in taken]
    assert not clash, f"measure names clash with columns: {clash}"
    sm, rp = out_dir / f"{name}.SemanticModel", out_dir / f"{name}.Report"
    for d in (sm / "definition", rp / "definition"):
        shutil.rmtree(d, ignore_errors=True)

    _write(out_dir / f"{name}.pbip", {"$schema": f"{SCHEMA}/pbip/pbipProperties/1.0.0/schema.json", "version": "1.0",
                                      "artifacts": [{"report": {"path": f"{name}.Report"}}],
                                      "settings": {"enableAutoRecovery": True}})
    _write(out_dir / ".gitignore", "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n")

    # semantic model
    _write(sm / ".platform", {"$schema": f"{SCHEMA}/gitIntegration/platformProperties/2.0.0/schema.json",
                              "metadata": {"type": "SemanticModel", "displayName": name},
                              "config": {"version": "2.0", "logicalId": _guid(name, "sm")}})
    _write(sm / "definition.pbism", {"$schema": f"{SCHEMA}/item/semanticModel/definitionProperties/1.0.0/schema.json",
                                     "version": "4.2", "settings": {}})
    d = sm / "definition"
    _write(d / "database.tmdl", "database\n\tcompatibilityLevel: 1606\n")
    _write(d / f"cultures/{culture}.tmdl", f"cultureInfo {culture}\n")
    order = json.dumps([t.name for t in tables] + ["DataFolder"])
    _write(d / "model.tmdl", "\n".join([
        "model Model", f"\tculture: {culture}", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        f"\tsourceQueryCulture: {culture}", "\tvalueFilterBehavior: independent", "\tdataAccessOptions",
        "\t\tlegacyRedirects", "\t\treturnErrorValuesAsNull", "",
        "annotation __PBI_TimeIntelligenceEnabled = 0", "", f"annotation PBI_QueryOrder = {order}", "",
        'annotation PBI_ProTooling = ["DevMode"]', "",
        *[f"ref table {_q(t.name)}" for t in tables], "", f"ref cultureInfo {culture}", ""]))
    _write(d / "expressions.tmdl", "\n".join([
        "/// Folder holding the pipeline's CSV exports. Change it in Transform data > Manage parameters.",
        f'expression DataFolder = "{data_dir}" meta [IsParameterQuery = true, Type = "Text", '
        "IsParameterQueryRequired = true]",
        f"\tlineageTag: {_guid(name, 'DataFolder')}", "", "\tannotation PBI_ResultType = Text", ""]))
    rels = []
    for frm, to in relationships:
        (ft, fc), (tt, tc) = frm.split("."), to.split(".")
        rels += [f"relationship {_guid(frm, to)}", f"\tfromColumn: {_q(ft)}.{_q(fc)}",
                 f"\ttoColumn: {_q(tt)}.{_q(tc)}", ""]
    _write(d / "relationships.tmdl", "\n".join(rels))
    for t in tables:
        _write(d / f"tables/{t.name}.tmdl", table_tmdl(t, data_dir))

    # report
    _write(rp / ".platform", {"$schema": f"{SCHEMA}/gitIntegration/platformProperties/2.0.0/schema.json",
                              "metadata": {"type": "Report", "displayName": name},
                              "config": {"version": "2.0", "logicalId": _guid(name, "report")}})
    _write(rp / "definition.pbir", {"$schema": f"{SCHEMA}/item/report/definitionProperties/2.0.0/schema.json",
                                    "version": "4.0", "datasetReference": {"byPath": {"path": f"../{name}.SemanticModel"}}})
    res = rp / "StaticResources"
    shutil.rmtree(res, ignore_errors=True)
    (res / "SharedResources/BaseThemes").mkdir(parents=True)
    shutil.copy(THEME_SRC, res / f"SharedResources/BaseThemes/{BASE_THEME}.json")
    theme_file = f"{name.replace(' ', '_')}_theme.json"
    _write(res / f"RegisteredResources/{theme_file}", _theme(f"{name} theme"))
    version = {"visual": "2.14.0", "report": "3.4.0", "page": "2.3.1"}
    d = rp / "definition"
    _write(d / "version.json", {"$schema": f"{SCHEMA}/item/report/definition/versionMetadata/1.0.0/schema.json",
                                "version": "2.0.0"})
    _write(d / "report.json", {
        "$schema": f"{SCHEMA}/item/report/definition/report/3.3.0/schema.json",
        "themeCollection": {
            "baseTheme": {"name": BASE_THEME, "reportVersionAtImport": version, "type": "SharedResources"},
            "customTheme": {"name": theme_file, "reportVersionAtImport": version, "type": "RegisteredResources"}},
        "objects": {"section": [{"properties": {"verticalAlignment": lit("Top")}}]},
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": BASE_THEME, "path": f"BaseThemes/{BASE_THEME}.json", "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": theme_file, "path": theme_file, "type": "CustomTheme"}]}],
        "settings": {"useStylableVisualContainerHeader": True, "exportDataMode": "AllowSummarized",
                     "defaultDrillFilterOtherVisuals": True, "allowChangeFilterTypes": True,
                     "useEnhancedTooltips": True, "useDefaultAggregateDisplayName": True}})
    ids = [_hex(name, "page", p.name) for p in pages]
    _write(d / "pages/pages.json", {"$schema": f"{SCHEMA}/item/report/definition/pagesMetadata/1.1.0/schema.json",
                                    "pageOrder": ids, "activePageName": ids[0]})
    for pid, p in zip(ids, pages):
        page = {"$schema": f"{SCHEMA}/item/report/definition/page/2.1.0/schema.json", "name": pid,
                "displayName": p.name, "displayOption": "FitToPage", "height": PAGE_H, "width": PAGE_W}
        if p.filters:
            page["filterConfig"] = _filter_config(p.filters)
        _write(d / f"pages/{pid}/page.json", page)
        for z, v in enumerate(p.visuals):
            vid = _hex(pid, z, v.kind, v.title)
            _write(d / f"pages/{pid}/visuals/{vid}/visual.json", _visual_json(v, vid, z * 1000))
    return out_dir / f"{name}.pbip"
