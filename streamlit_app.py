"""Base Plate calculator — Streamlit version (testing phase).

This is the same calculation engine as the main web app
(base_plate_calc.py, plus xml_import.py for PLS-POLE XML), with a
Streamlit front end instead of the stdlib HTTP server. It is meant
for testing: there is no password gate here, and nothing is logged
to the development evaluation database — on Streamlit Cloud the
filesystem is ephemeral, so a log written there would not survive
a restart anyway.

Run locally:  streamlit run streamlit_app.py
Deploy:       Streamlit Community Cloud, repo base-plate-web,
              branch main, main file streamlit_app.py
              (requirements.txt at the repo root installs Streamlit).
"""

import hashlib
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import streamlit as st

import base_plate_calc
import xml_import
from server import run_validation

GRADE_YIELDS = {"A572-60": 60.0, "A572-50": 50.0, "A572-42": 42.0, "A36": 36.0}

DEFAULT_CASES = [{
    "name": "NESC Heavy (250B) FULL DE AHEAD-OW",
    "axial": 15.25, "shear": 43.302, "mx": 858.87, "my": 2332.0,
    "torsion": 4.97, "sf": 1.0,
}]


def default_inputs():
    """Fresh-form defaults: same as the main web app."""
    return {
        "sides": 12, "base_od": 46.5, "wall": 0.3125, "pole_yield": 65.0,
        "shaft_capacity_ftk": 2622.61, "shaft_capacity_perc": 0.5,
        "bolt_dia": 2.25, "bolt_grade": '2.25"-18J', "bolt_yield": 75.0,
        "bolt_fu": 100.0, "tensile_area": 3.25, "gross_area": 3.9740625,
        "nut_width": 4.0625, "hole_dia": 2.625, "bolt_at_0": "yes",
        "bolt_qty_override": None, "bolt_circle_override": None,
        "plate_shape": "Round", "plate_grade": "A572-50",
        "bp_edge": 6.0, "min_thick": 1.5, "plate_thick_override": None,
        "sf_plate": 1.0, "sf_bolt": 1.0,
        "rod_w_lbft": 14.63, "template_count": 2, "template_w1": 4.38,
        "template_w2": 0.89, "template_t1": 0.375, "template_t2": 0.375,
        "template_id_drop": 12.0, "fc_ksi": 3.0, "projection_in": 12.0,
        "cases": [dict(c) for c in DEFAULT_CASES],
    }


def _num(v, default=0.0):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def cases_from_editor(df):
    out = []
    if df is None:
        return out
    for _, row in df.iterrows():
        name = "" if pd.isna(row.get("name")) else str(row.get("name")).strip()
        vals = {k: _num(row.get(k)) for k in
                ("axial", "shear", "mx", "my", "torsion", "sf")}
        if not name and all(v == 0.0 for v in vals.values()):
            continue
        out.append({"name": name or "Unnamed case", **vals})
    return out


def apply_leg(d, leg, cases):
    """Prefill the defaults dict from one XML leg, like the web UI."""
    if leg.get("sides") is not None:
        d["sides"] = leg["sides"]
    for k in ("base_od", "wall", "pole_yield"):
        if leg.get(k) is not None:
            d[k] = leg[k]
    if leg.get("shaft_capacity_ftk") is not None:
        d["shaft_capacity_ftk"] = round(leg["shaft_capacity_ftk"], 2)
    ep = leg.get("existing_plate") or {}
    if ep.get("bolt_diam") is not None:
        d["bolt_dia"] = ep["bolt_diam"]
    if ep.get("plate_yield") is not None:
        g = {50: "A572-50", 60: "A572-60", 42: "A572-42", 36: "A36"}.get(ep["plate_yield"])
        if g:
            d["plate_grade"] = g
    if ep.get("plate_shape") == "0":
        d["plate_shape"] = "Round"
    d["cases"] = [dict(c) for c in (cases or [])]
    return d, ep


def leg_note(leg, ep):
    bits = [f"Pole {leg.get('label')} ({leg.get('shape') or '?'}, base OD "
            f"{leg.get('base_od')} in, wall {leg.get('wall')} in, "
            f"yield {leg.get('pole_yield')} ksi)"]
    if leg.get("shaft_capacity_ftk") is not None:
        bits.append(f"shaft capacity {leg['shaft_capacity_ftk']:.1f} ft-k "
                    f"({leg.get('shaft_capacity_note') or ''})")
    else:
        bits.append("shaft capacity not found in the file — left at the "
                    "current form value, check it before calculating "
                    "(it sets the 50% floor)")
    if not leg.get("base_plate_in_model"):
        bits.append("PLS model has no base plate — designing a new one")
    if ep and ep.get("plate_diam") is not None:
        bits.append(f"existing PLS plate: {ep['plate_diam']} in dia x "
                    f"{ep['plate_thick']} in, bolt circle "
                    f"{ep['bolt_pattern_diam']} in, {ep['num_bolts']} bolts — "
                    "shown for comparison only, not applied as overrides")
    if leg.get("sides") is None:
        bits.append("pole shape had no side count — sides left unchanged, check it")
    if leg.get("cases_note"):
        bits.append(str(leg["cases_note"]))
    bits.append("Bolt grade, areas, hole diameter, and plate edge/minimum "
                "thickness are not in the XML and were left at their "
                "current values.")
    return "\n".join("- " + b for b in bits)


def run_calculation(inputs):
    cleaned = base_plate_calc.clean_inputs(inputs)
    summary, steps, case_rows = base_plate_calc.compute(cleaned)
    return {"inputs": cleaned, "summary": summary, "steps": steps,
            "cases": case_rows}


def show_result(res):
    s = res["summary"]
    if s.get("a572_42_applied"):
        st.warning("Switched to A572-42 (42 ksi): calculated thickness was "
                   "over 4 in — see the calculation step below.")
    st.subheader(
        f"Base Plate — {s['plate_thick_use']} in thick, "
        f"{s['plate_width']} in O.D. — ({s['bolt_qty_use']}) anchor bolts "
        f"on {s['bolt_circle_use']} in BCD — Overall: {s['overall']}")
    st.caption(f"Governing case: {s['governing_case']}  |  "
               f"Grade used: {s['plate_grade_used']} "
               f"({s['plate_yield_used']} ksi)")
    cols = st.columns(4)
    metrics = [
        ("Overall", s["overall"]), ("AB usage", round(s["ab_usage"], 4)),
        ("BP usage", round(s["bp_usage"], 4)),
        ("Plate thickness used (in)", s["plate_thick_use"]),
        ("Bolt circle used (in)", s["bolt_circle_use"]),
        ("Bolts used", s["bolt_qty_use"]),
        ("Plate width (in)", s["plate_width"]),
        ("Max rod force (kips)", round(s["max_rod_force"], 2)),
        ("Rod capacity (kips)", round(s["rod_capacity"], 2)),
        ("Plate final weight (lbs)", round(s["weights"]["plate_final"], 2)),
        ("Anchor cage weight (lbs)", round(s["weights"]["cage_total"], 2)),
        ("Governing moment (in-k)", round(s["governing_moment"], 2)),
    ]
    for i, (label, val) in enumerate(metrics):
        cols[i % 4].metric(label, val)
    st.subheader("Per-case results")
    st.dataframe(pd.DataFrame([{
        "Case": c["name"], "Axial": c["axial"], "Shear": c["shear"],
        "Mx used": c["mx_used"], "My used": c["my_used"],
        "Torsion": c["torsion"], "SF": c["sf"],
        "Max comp": round(c["max_comp"], 2),
        "Max ten*": round(c["max_ten"], 2),
        "Max shear": round(c["max_shear"], 2),
        "Max force": round(c["max_force"], 2),
        "t_min": round(c["t_min"], 4),
        "Usage": round(c["usage"], 4),
    } for c in res["cases"]]), width="stretch")
    st.caption("* Max ten follows the sheet's own definition: max "
               "compression minus the axial share per bolt.")
    st.subheader("Checks")
    st.dataframe(pd.DataFrame([{
        "Check": c["name"], "Result": c["result"],
        "Detail": c.get("detail", ""),
    } for c in s["checks"]]), width="stretch")
    with st.expander("Calculation steps — formula, explanation, inputs, result"):
        for stp in res["steps"]:
            with st.expander(f"{stp['step']} — {stp['result']} "
                             f"{stp.get('unit', '')}"):
                st.code(stp["formula"])
                st.write(stp["explain"])
                st.write("Inputs:", stp["inputs"])
                st.write("Result:", stp["result"])
    pos = next((x for x in res["steps"]
                if "Bolt positions" in x["step"]), None)
    if pos and isinstance(pos["inputs"].get("bolt angles"), list):
        bcd = s["bolt_circle_use"]
        rows = []
        for a in pos["inputs"]["bolt angles"]:
            r = math.radians(a)
            rows.append({"Bolt angle": a, "X (in)": round(math.cos(r) * bcd / 2, 4),
                         "Y (in)": round(math.sin(r) * bcd / 2, 4)})
        with st.expander("Bolt angles — X / Y table"):
            st.dataframe(pd.DataFrame(rows), width="stretch")


# ---------------------------------------------------------------- state
st.set_page_config(page_title="Base Plate — Web Calculator",
                   layout="wide")
if "defaults" not in st.session_state:
    st.session_state["defaults"] = default_inputs()
if "form_version" not in st.session_state:
    st.session_state["form_version"] = 0
if "result" not in st.session_state:
    st.session_state["result"] = None
if "validation_report" not in st.session_state:
    st.session_state["validation_report"] = None

d = st.session_state["defaults"]
v = st.session_state["form_version"]
K = lambda name: f"{name}_v{v}"  # noqa: E731

st.title("Base Plate — Web Calculator")
st.caption("Streamlit testing version of the base plate calculator. "
           "Same engine as the main app. No password gate here, and "
           "uploads are not logged to the evaluation database on "
           "Streamlit Cloud (its filesystem does not persist).")

# ---------------------------------------------------------------- XML
st.subheader("Import from PLS-POLE XML")
uploaded = st.file_uploader("Choose a .xml file", type=["xml"])
if uploaded is not None:
    key = (uploaded.name, uploaded.size)
    if st.session_state.get("xml_key") != key:
        try:
            data = uploaded.getvalue()
            parsed = xml_import.parse_pls_xml(data)
            st.session_state["xml_data"] = parsed
            st.session_state["xml_key"] = key
            st.session_state["xml_hash"] = hashlib.sha256(data).hexdigest()
            st.session_state["xml_leg_idx"] = 0
            st.session_state["xml_rxn_idx"] = 0
        except Exception as e:
            st.error(f"Could not import XML: {e}")
            st.session_state["xml_data"] = None
    parsed = st.session_state.get("xml_data")
    if parsed:
        legs = parsed.get("legs") or []
        if not legs:
            st.error("No poles found in the file.")
        else:
            leg_idx = st.selectbox(
                "Pole / leg",
                range(len(legs)),
                format_func=lambda i: (
                    f"{legs[i].get('label')}"
                    + (f" — {legs[i].get('base_od')} in base"
                       if legs[i].get("base_od") is not None else "")),
                key="xml_leg_picker")
            if st.session_state.get("xml_note"):
                st.info(st.session_state["xml_note"])
            leg = legs[leg_idx]
            cases, rxn_idx = leg.get("cases"), 0
            if cases is None:
                rxn_sets = parsed.get("reaction_sets") or []
                if rxn_sets:
                    rxn_idx = st.selectbox(
                        "Reaction set (base joint)",
                        range(len(rxn_sets)),
                        format_func=lambda i: (
                            f"{rxn_sets[i].get('joint_label')} "
                            f"({len(rxn_sets[i].get('cases') or [])} cases)"),
                        key="xml_rxn_picker")
                    cases = rxn_sets[rxn_idx].get("cases")
                else:
                    st.error("No reaction set could be matched to this pole. "
                             "Pick a different leg or check the file.")
            if cases is not None and (
                    st.session_state.get("xml_leg_idx") != leg_idx
                    or st.session_state.get("xml_rxn_idx") != rxn_idx
                    or st.session_state.get("xml_applied_for") != key):
                # New leg / reaction choice (or first upload): prefill
                # and calculate automatically, like the main app.
                # apply_leg only overwrites what the XML actually
                # carries; everything else keeps the values from the
                # form as last rendered (live_inputs, below).
                base = dict(st.session_state.get("live_inputs")
                            or st.session_state["defaults"])
                base["cases"] = [dict(c) for c in base.get("cases", [])]
                new_d, ep = apply_leg(base, leg, cases)
                st.session_state["defaults"] = new_d
                st.session_state["xml_leg_idx"] = leg_idx
                st.session_state["xml_rxn_idx"] = rxn_idx
                st.session_state["xml_applied_for"] = key
                st.session_state["xml_note"] = leg_note(leg, ep)
                st.session_state["form_version"] += 1
                st.session_state["pending_auto_calc"] = True
                st.rerun()

# ---------------------------------------------------------------- buttons
col_a, col_b, _ = st.columns([1, 1, 4])
with col_a:
    if st.button("Load STR017 sample"):
        try:
            from server import load_validation
            ds = load_validation().get("str017", {})
            nd = default_inputs()
            nd.update({k: val for k, val in ds.get("inputs", {}).items()
                       if k in nd})
            # Sample inputs carry plate_yield; grade wins in the UI,
            # and the sample grade is A572-60, so keep it consistent.
            nd["plate_grade"] = ds["inputs"].get("plate_grade", "A572-60")
            st.session_state["defaults"] = nd
            st.session_state["form_version"] += 1
            st.session_state["result"] = None
            st.rerun()
        except Exception as e:
            st.error(f"Could not load sample: {e}")
with col_b:
    if st.button("Run validation"):
        try:
            st.session_state["validation_report"] = run_validation()
        except Exception as e:
            st.error(f"Validation failed: {e}")

# ---------------------------------------------------------------- inputs
st.subheader("Inputs")
c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown("**Pole Geometry**")
    sides = st.number_input("Pole shape (sides)", value=int(d["sides"]),
                            step=1, key=K("sides"))
    base_od = st.number_input("Pole base dia (in)", value=float(d["base_od"]),
                              key=K("base_od"))
    wall = st.number_input("Pole base thickness (in)", value=float(d["wall"]),
                           key=K("wall"))
    pole_yield = st.number_input("Pole material yield (ksi)",
                                 value=float(d["pole_yield"]), key=K("pole_yield"))
    shaft_perc = st.number_input("Shaft capacity check",
                                 value=float(d["shaft_capacity_perc"]), key=K("shaft_perc"))
    shaft_ftk = st.number_input("PLS shaft moment capacity (ft-k)",
                                value=float(d["shaft_capacity_ftk"]), key=K("shaft_ftk"))
    sf_plate = st.number_input("Strength reduction factor BPL",
                               value=float(d["sf_plate"]), key=K("sf_plate"))
    sf_bolt = st.number_input("Strength reduction factor AB",
                              value=float(d["sf_bolt"]), key=K("sf_bolt"))
with c2:
    st.markdown("**Anchor Bolt Data**")
    bolt_grade = st.text_input("Anchor bolt", value=str(d["bolt_grade"]),
                               key=K("bolt_grade"))
    bolt_dia = st.number_input("Bolt dia (in)", value=float(d["bolt_dia"]),
                               key=K("bolt_dia"))
    bolt_yield = st.number_input("Bolt Fy (ksi)", value=float(d["bolt_yield"]),
                                 key=K("bolt_yield"))
    bolt_fu = st.number_input("Bolt Fu (ksi)", value=float(d["bolt_fu"]),
                              key=K("bolt_fu"))
    tensile_area = st.number_input("Tensile area As (in²)",
                                   value=float(d["tensile_area"]), key=K("tensile_area"))
    gross_area = st.number_input("Gross area Ag (in²)",
                                 value=float(d["gross_area"]), key=K("gross_area"))
    nut_width = st.number_input("Nut width (in)", value=float(d["nut_width"]),
                                key=K("nut_width"))
    bolt_qty_override = st.number_input("Bolts qty override (blank = calculated)",
                                        value=d.get("bolt_qty_override"), key=K("bolt_qty_override"))
    bolt_at_0 = st.selectbox("Bolt @ 0 deg", ["yes", "no"],
                             index=0 if d["bolt_at_0"] == "yes" else 1,
                             key=K("bolt_at_0"))
with c3:
    st.markdown("**Base Plate Properties**")
    bolt_circle_override = st.number_input("Bolt circle override (blank = calculated)",
                                           value=d.get("bolt_circle_override"), key=K("bolt_circle_override"))
    plate_shape = st.selectbox("Plate shape", ["Round", "12-Sided", "Square"],
                               index=["Round", "12-Sided", "Square"].index(d["plate_shape"]),
                               key=K("plate_shape"))
    plate_grade = st.selectbox("Material grade",
                               list(GRADE_YIELDS.keys()),
                               index=list(GRADE_YIELDS.keys()).index(d["plate_grade"]),
                               key=K("plate_grade"))
    st.caption("Yield follows the grade: A572-60 = 60, A572-50 = 50, "
               "A572-42 = 42, A36 = 36 ksi.")
    use_a572_42 = st.checkbox("Use A572-42 (42 ksi) for plates over 4 in — default",
                              value=True, key=K("use_a572_42"))
    bp_edge = st.number_input("BP edge", value=float(d["bp_edge"]), key=K("bp_edge"))
    min_thick = st.number_input("Min thick (in)", value=float(d["min_thick"]),
                                key=K("min_thick"))
    plate_thick_override = st.number_input("Plate thick override (blank = calculated)",
                                           value=d.get("plate_thick_override"), key=K("plate_thick_override"))
    hole_dia = st.number_input("AB hole in plate (in)", value=float(d["hole_dia"]),
                               key=K("hole_dia"))
with c4:
    st.markdown("**Checks & Weights**")
    st.caption("Press Calculate to run the checks. Results appear below.")
    if st.session_state["result"] is not None:
        s = st.session_state["result"]["summary"]
        st.metric("Overall", s["overall"])
        st.metric("AB usage", round(s["ab_usage"], 4))
        st.metric("BP usage", round(s["bp_usage"], 4))

with st.expander("More inputs — weights & development length"):
    mc1, mc2 = st.columns(2)
    with mc1:
        rod_w = st.number_input("Rod weight (lb/ft)", value=float(d["rod_w_lbft"]), key=K("rod_w"))
        template_count = st.number_input("Template count", value=int(d["template_count"]),
                                         step=1, key=K("template_count"))
        template_w1 = st.number_input("Template weight 1 (lb)", value=float(d["template_w1"]), key=K("template_w1"))
        template_w2 = st.number_input("Template weight 2 (lb)", value=float(d["template_w2"]), key=K("template_w2"))
    with mc2:
        template_t1 = st.number_input("Template thickness 1 (in)", value=float(d["template_t1"]), key=K("template_t1"))
        template_t2 = st.number_input("Template thickness 2 (in)", value=float(d["template_t2"]), key=K("template_t2"))
        template_id_drop = st.number_input("Template ID drop (in)", value=float(d["template_id_drop"]), key=K("template_id_drop"))
        fc_ksi = st.number_input("Concrete f'c (ksi)", value=float(d["fc_ksi"]), key=K("fc_ksi"))
        projection_in = st.number_input("Rod projection (in)", value=float(d["projection_in"]), key=K("projection_in"))

st.subheader("Base Reactions — All Load Cases")
st.caption("Moments in ft-k, forces in kips. A case whose raw resultant "
           "moment is below the 50% floor is scaled up to the floor in "
           "the same direction. A strength factor of 0 zeroes that "
           "case's plate usage.")
cases_df = st.data_editor(
    pd.DataFrame(d["cases"]),
    num_rows="dynamic", width="stretch", key=K("cases_editor"))

# Remember the form as last rendered, so a later XML upload prefills
# on top of the user's edits instead of resetting them.
st.session_state["live_inputs"] = {
    "sides": int(sides), "base_od": float(base_od), "wall": float(wall),
    "pole_yield": float(pole_yield),
    "shaft_capacity_ftk": float(shaft_ftk),
    "shaft_capacity_perc": float(shaft_perc),
    "bolt_dia": float(bolt_dia), "bolt_grade": bolt_grade,
    "bolt_yield": float(bolt_yield), "bolt_fu": float(bolt_fu),
    "tensile_area": float(tensile_area), "gross_area": float(gross_area),
    "nut_width": float(nut_width), "hole_dia": float(hole_dia),
    "bolt_at_0": bolt_at_0,
    "bolt_qty_override": bolt_qty_override,
    "bolt_circle_override": bolt_circle_override,
    "plate_shape": plate_shape, "plate_grade": plate_grade,
    "bp_edge": float(bp_edge), "min_thick": float(min_thick),
    "plate_thick_override": plate_thick_override,
    "sf_plate": float(sf_plate), "sf_bolt": float(sf_bolt),
    "rod_w_lbft": float(rod_w), "template_count": int(template_count),
    "template_w1": float(template_w1), "template_w2": float(template_w2),
    "template_t1": float(template_t1), "template_t2": float(template_t2),
    "template_id_drop": float(template_id_drop), "fc_ksi": float(fc_ksi),
    "projection_in": float(projection_in),
    "cases": cases_from_editor(cases_df),
}

calc = st.button("Calculate base plate", type="primary")
if calc or st.session_state.get("pending_auto_calc"):
    st.session_state["pending_auto_calc"] = False
    inputs = {
        "sides": int(sides), "base_od": float(base_od), "wall": float(wall),
        "pole_yield": float(pole_yield),
        "shaft_capacity_ftk": float(shaft_ftk),
        "shaft_capacity_perc": float(shaft_perc),
        "bolt_dia": float(bolt_dia), "bolt_grade": bolt_grade,
        "bolt_yield": float(bolt_yield), "bolt_fu": float(bolt_fu),
        "tensile_area": float(tensile_area), "gross_area": float(gross_area),
        "nut_width": float(nut_width), "hole_dia": float(hole_dia),
        "bolt_at_0": bolt_at_0,
        "bolt_qty_override": bolt_qty_override,
        "bolt_circle_override": bolt_circle_override,
        "plate_shape": plate_shape, "plate_grade": plate_grade,
        "plate_yield": GRADE_YIELDS[plate_grade],
        "use_a572_42_thick": bool(use_a572_42),
        "bp_edge": float(bp_edge), "min_thick": float(min_thick),
        "plate_thick_override": plate_thick_override,
        "sf_plate": float(sf_plate), "sf_bolt": float(sf_bolt),
        "rod_w_lbft": float(rod_w), "template_count": int(template_count),
        "template_w1": float(template_w1), "template_w2": float(template_w2),
        "template_t1": float(template_t1), "template_t2": float(template_t2),
        "template_id_drop": float(template_id_drop), "fc_ksi": float(fc_ksi),
        "projection_in": float(projection_in),
        "cases": cases_from_editor(cases_df),
    }
    try:
        st.session_state["result"] = run_calculation(inputs)
    except Exception as e:
        st.error(f"Calculation failed: {e}")

if st.session_state["result"] is not None:
    show_result(st.session_state["result"])

if st.session_state["validation_report"] is not None:
    st.subheader("Validation against the workbook")
    st.caption("Tolerance: 0.5% relative. Mismatches are listed honestly — "
               "nothing is fudged.")
    for s in st.session_state["validation_report"]["samples"]:
        if s["type"] == "reference":
            st.markdown(f"**{s['label']}** — {s.get('note', '')}")
            st.dataframe(pd.DataFrame(
                [{"Field": k, "Cached value": str(v)}
                 for k, v in s["cached"].items()]), width="stretch")
        else:
            st.markdown(f"**{s['label']}** — Summary values: "
                        f"{s['passed']}/{s['total']} pass. Per-case rows "
                        f"compared: {s['case_count']}; worst per-case "
                        f"difference: {s['worst_case_diff_pct']:.4f}%.")
            st.dataframe(pd.DataFrame([{
                "Field": c["name"], "App": str(c["app"]),
                "Expected": str(c["expected"]),
                "Diff %": c["diff_pct"], "Pass": c["pass"],
            } for c in s["checks"]]), width="stretch")
            if s["case_mismatches"]:
                st.dataframe(pd.DataFrame(s["case_mismatches"]),
                             width="stretch")
            else:
                st.success("All per-case max-bolt, thickness and usage "
                           "values match within tolerance.")
