"""PLS-POLE XML import for the TAPP Base Plate web app.

Pure standard library (xml.etree). Reads a PLS-POLE XML export
(read-only) and returns, per pole/leg, the values the app's input form
needs. It never runs a design itself; the caller prefills the form and
the user reviews everything before pressing Calculate.

Proven tag/attribute names come from the pole-shaft-optimizer repo's
pls_pole_xml_parser.py (PLS-POLE Version 21.01 exports):
  - <table tagname=... titledetail=...> blocks; row elements named by
    tagname; field sub-elements carry the values.
  - steel_pole_properties: one row per pole property set (label, shape,
    base_diameter, base_plate flag).
  - steel_tubes_properties: rows keyed by pole_property; tube_no runs
    top-to-bottom, so the base tube is the highest tube_no.
  - steel_pole_connectivity: pole_label -> property_set, plus the base
    joint label pattern '<pole_label>:g'.
  - joint_support_reactions: one table instance per load case
    (titledetail = load case description); the base row is the one whose
    joint_label is the leg's base joint.
  - vector_load_cases: strength factors, keyed by load_case_description.
  - detailed_steel_pole_usages: per-case base-section rows; the bending
    usage m_s at the base joint gives the shaft moment capacity.

Mapping to app inputs (see README.md for the full table):
  - sides: leading digits of the pole shape ('12F' -> 12).
  - base_od: base tube's tube_bot_diameter (cross-checked against the
    property row's base_diameter).
  - wall / pole_yield: base tube's thickness / yield_stress.
  - axial = -z_force (compression positive; PLS reports the downward
    load on the support as negative z).
  - shear = hypot(x_force, y_force) (resultant horizontal shear).
  - mx = x_moment, my = y_moment, torsion = z_moment (PLS ft-k).
  - sf: sf_for_steel_poles_tubular_arms_and_towers for that case;
    cases whose name contains INSULATOR are carried at 0, matching the
    STR017 sheet (both insulator cases there have sf 0).
  - shaft_capacity_ftk: median over cases of
    hypot(trans_mom, long_mom) / (m_s / 100) at the base joint. m_s is
    the bending-only usage, so this ratio is the section moment
    capacity; it is case-independent (verified: ~4205 ft-k for the 002
    sample across cases, ~4964 ft-k for 013).
  - bolt_dia / plate yield: from the existing base_plate_properties
    row, when the PLS model already has a base plate. The existing
    plate diameter/thickness/bolt circle/bolt count are reported as
    information only, never forced in as overrides, so the app designs
    fresh unless the user chooses otherwise.

Multi-leg files: legs are matched to their base reactions by the base
joint label ('<pole_label>:g'). If a leg cannot be matched
unambiguously, its cases come back as null and every reaction set is
returned separately, so the UI must make the user pick one. A leg is
never silently paired with the wrong reactions.
"""

import math
import re
import xml.etree.ElementTree as ET
from statistics import median

MAX_XML_BYTES = 20 * 1024 * 1024


def _f(value, default=None):
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _tables(root):
    """tagname -> list of (titledetail, [row dicts])."""
    out = {}
    for table_el in root.findall("table"):
        tagname = table_el.get("tagname")
        if not tagname:
            continue
        rows = []
        for row_el in table_el.findall(tagname):
            row = {}
            for field_el in row_el:
                row[field_el.tag] = (field_el.text or "").strip()
            rows.append(row)
        out.setdefault(tagname, []).append(
            (table_el.get("titledetail", ""), rows))
    return out


def _first_row(tables, tagname):
    insts = tables.get(tagname, [])
    if insts and insts[0][1]:
        return insts[0][1][0]
    return None


def _sides_from_shape(shape):
    if not shape:
        return None
    m = re.match(r"\s*(\d+)", shape)
    return int(m.group(1)) if m else None


def _base_tube(tubes):
    """Base tube = highest tube_no; fallbacks: lap_length == 0, last row."""
    if not tubes:
        return None

    def tube_no(t):
        try:
            return int(float(t.get("tube_no", "0")))
        except ValueError:
            return 0

    ordered = sorted(tubes, key=tube_no)
    for t in reversed(ordered):
        if _f(t.get("lap_length"), 1) == 0:
            return t
    return ordered[-1]


def parse_pls_xml(data):
    """Parse PLS-POLE XML bytes into legs + reaction sets.

    Returns a dict with 'legs' (list) and 'reaction_sets' (list).
    Raises ValueError with a plain-language message for anything that
    is not a usable PLS-POLE export.
    """
    if data is None or len(data) == 0:
        raise ValueError("empty file: nothing to import")
    if len(data) > MAX_XML_BYTES:
        raise ValueError("file is larger than the 20 MB import limit")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        try:
            root = ET.fromstring(data.decode("cp1252"))
        except Exception:
            raise ValueError(
                "not a readable XML file: could not parse it as XML")
    tables = _tables(root)
    if "steel_pole_properties" not in tables:
        raise ValueError(
            "not a PLS-POLE export: no steel pole properties table found")

    # --- strength factors by load case description
    sf_map = {}
    for _td, rows in tables.get("vector_load_cases", []):
        for row in rows:
            desc = row.get("load_case_description", "")
            sf = _f(row.get("sf_for_steel_poles_tubular_arms_and_towers"))
            if desc and sf is not None:
                sf_map[desc] = sf

    # --- support reactions grouped by load case
    reactions = {}  # case name -> [row dicts]
    for titledetail, rows in tables.get("joint_support_reactions", []):
        reactions.setdefault(titledetail, []).extend(rows)
    if not reactions:
        raise ValueError(
            "no base reactions found: the file has no joint support "
            "reactions table, so there are no load cases to import")

    # --- base-section bending usages (for shaft capacity), by joint label
    base_usage = {}  # joint_label -> [(moment, m_s)]
    for _td, rows in tables.get("detailed_steel_pole_usages", []):
        for row in rows:
            jl = row.get("joint_label", "")
            ms = _f(row.get("m_s"))
            if jl and ms is not None and ms > 1:
                mom = math.hypot(_f(row.get("trans_mom_local_mx"), 0.0),
                                 _f(row.get("long_mom_local_my"), 0.0))
                base_usage.setdefault(jl, []).append((mom, ms))

    def shaft_capacity(joint_label):
        pts = base_usage.get(joint_label, [])
        caps = [mom / (ms / 100.0) for mom, ms in pts if ms > 0]
        if not caps:
            return None, "no base-section bending usage in the file"
        cap = median(caps)
        return cap, ("median of %d case values (min %.1f, max %.1f)"
                     % (len(caps), min(caps), max(caps)))

    def make_case(case_name, row):
        sf = sf_map.get(case_name, 1.0)
        note = None
        if "insulator" in case_name.lower():
            sf = 0.0
            note = "sheet convention: insulator cases carried at SF 0"
        return {
            "name": case_name,
            "axial": -_f(row.get("z_force"), 0.0),
            "shear": math.hypot(_f(row.get("x_force"), 0.0),
                                _f(row.get("y_force"), 0.0)),
            "mx": _f(row.get("x_moment"), 0.0),
            "my": _f(row.get("y_moment"), 0.0),
            "torsion": _f(row.get("z_moment"), 0.0),
            "sf": sf,
            "sf_note": note,
        }

    # --- legs: connectivity maps pole labels to property sets
    props = {}
    for _td, rows in tables.get("steel_pole_properties", []):
        for row in rows:
            label = row.get("steel_pole_property_label", "")
            if label:
                props[label] = row
    tubes_by_prop = {}
    for _td, rows in tables.get("steel_tubes_properties", []):
        for row in rows:
            tubes_by_prop.setdefault(row.get("pole_property", ""), []).append(row)
    existing_plate = {}
    for _td, rows in tables.get("base_plate_properties", []):
        for row in rows:
            existing_plate[row.get("pole_property", "")] = row

    conn_rows = []
    for _td, rows in tables.get("steel_pole_connectivity", []):
        conn_rows.extend(rows)
    if conn_rows:
        leg_defs = [(r.get("pole_label", ""), r.get("property_set", ""))
                    for r in conn_rows]
    else:
        # No connectivity table: each property set is its own leg.
        leg_defs = [("", label) for label in props]

    legs = []
    for pole_label, prop_label in leg_defs:
        prop = props.get(prop_label)
        if prop is None:
            continue
        tube = _base_tube(tubes_by_prop.get(prop_label, []))
        base_joint = (pole_label + ":g") if pole_label else None
        leg = {
            "label": pole_label or prop_label,
            "property_label": prop_label,
            "shape": prop.get("shape", ""),
            "sides": _sides_from_shape(prop.get("shape", "")),
            "base_od": _f(tube.get("tube_bot_diameter")) if tube else None,
            "base_od_property": _f(prop.get("base_diameter")),
            "wall": _f(tube.get("thickness")) if tube else None,
            "pole_yield": _f(tube.get("yield_stress")) if tube else None,
            "base_plate_in_model": (prop.get("base_plate", "").lower()
                                    == "yes"),
            "shaft_capacity_ftk": None,
            "shaft_capacity_note": None,
            "existing_plate": None,
            "cases": None,
            "cases_note": None,
        }
        if leg["base_od"] is not None and leg["base_od_property"] is not None \
                and abs(leg["base_od"] - leg["base_od_property"]) > 0.01:
            leg["cases_note"] = (
                "base diameter differs between tube (%.3f) and property "
                "row (%.3f); tube value used"
                % (leg["base_od"], leg["base_od_property"]))
        bp = existing_plate.get(prop_label)
        if bp is not None:
            leg["existing_plate"] = {
                "plate_diam": _f(bp.get("plate_diam")),
                "plate_thick": _f(bp.get("plate_thick")),
                "plate_shape": bp.get("plate_shape", ""),
                "plate_yield": _f(bp.get("steel_yield_stress")),
                "bolt_diam": _f(bp.get("bolt_diam")),
                "bolt_pattern_diam": _f(bp.get("bolt_pattern_diam")),
                "num_bolts": _f(bp.get("num_of_bolts")),
            }
        cap, cap_note = shaft_capacity(base_joint) if base_joint else (None, None)
        leg["shaft_capacity_ftk"] = cap
        leg["shaft_capacity_note"] = cap_note
        # Match this leg's base reactions by base joint label.
        if base_joint:
            leg_cases = []
            missing = 0
            for case_name in reactions:
                rows = [r for r in reactions[case_name]
                        if r.get("joint_label") == base_joint]
                if rows:
                    leg_cases.append(make_case(case_name, rows[0]))
                else:
                    missing += 1
            if leg_cases:
                leg["cases"] = leg_cases
                if missing:
                    leg["cases_note"] = (
                        "%d load case(s) have no %s reaction row and were "
                        "skipped" % (missing, base_joint))
        legs.append(leg)

    # Fallback: single leg, single reaction row per case (no usable labels).
    if legs and all(leg["cases"] is None for leg in legs):
        if len(legs) == 1 and all(len(rows) == 1
                                  for rows in reactions.values()):
            leg = legs[0]
            leg["cases"] = [make_case(name, rows[0])
                            for name, rows in reactions.items()]
            cap, cap_note = shaft_capacity(
                next(iter(reactions.values()))[0].get("joint_label", ""))
            leg["shaft_capacity_ftk"] = cap
            leg["shaft_capacity_note"] = cap_note
            leg["cases_note"] = ("reactions matched by single-row-per-case "
                                 "fallback (no connectivity labels)")

    # Reaction sets for manual picking when legs could not be matched.
    reaction_sets = []
    if any(leg["cases"] is None for leg in legs):
        joint_labels = []
        for rows in reactions.values():
            for row in rows:
                jl = row.get("joint_label", "")
                if jl and jl not in joint_labels:
                    joint_labels.append(jl)
        for jl in joint_labels:
            set_cases = []
            for case_name, rows in reactions.items():
                hit = [r for r in rows if r.get("joint_label") == jl]
                if hit:
                    set_cases.append(make_case(case_name, hit[0]))
            if set_cases:
                reaction_sets.append({"joint_label": jl, "cases": set_cases})

    if not legs:
        raise ValueError("no pole property rows found in the file")
    if all(leg["cases"] is None for leg in legs) and not reaction_sets:
        raise ValueError(
            "base reactions exist but none could be matched to a pole; "
            "the file may use joint labels this importer does not recognise")
    return {"legs": legs, "reaction_sets": reaction_sets}
