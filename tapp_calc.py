"""TAPP Base Plate V2.01 calculation engine (pure stdlib, no I/O).

Reproduces the Drive workbook's per-structure sheet chain, as documented
in TAPP_Basis_of_Design_from_Scratch.md and verified against the STR017
sheet's cached values. Every step returns its formula, a plain-language
explanation, its inputs and its result, so the web UI can show the work.

Conventions (sheet-faithful):
- Bolt forces use the elastic rigid-plate distribution. The sheet's
  bolt-group inertias carry the tensile area (Ixx = As*sum(y^2) +
  n*Ipp, Iyy = As*sum(x^2) + n*Ipp); Ipp = pi*d^4/64 is the bolt's own
  inertia. That small Ipp term is why the sheet's bolt forces sit
  ~0.1% below a plain sum(y^2) calculation.
- "Max_Ten" follows the sheet's own definition: Max_Comp minus the
  axial share per bolt (KG = KH - P/n), i.e. the largest moment-only
  bolt force. It is NOT the largest tensile magnitude when axial is
  compressive; the sheet defines it this way and the app keeps it.
- Load-case moments are floored at the 50% shaft-capacity floor: a case
  whose raw resultant is below the floor is scaled up to the floor in
  the same direction (sheet JY/JZ formulas; same as ASCE 48-19 6.4.2
  as PLS-POLE applies it). Axial force is never changed.
- The applied floor is the PLS-POLE shaft capacity x the percentage
  (sheet BC22 = MAX(IU1:IV1) * Shaft_Capacity_perc). The sheet also
  displays its own 0.411-formula estimate (O48); the app shows both
  and uses the PLS-based one for the floor, exactly as the sheet does.
- Plate thickness per case is scaled from the governing case by the
  max-bolt-load ratio (sheet KK), and usage = (t_min/t_use)^2, zeroed
  when the PLS strength factor is 0 (sheet KN + insulator rows).
- The sheet's "50% OF SHAFT CAPACITY" row (row 201) reports
  MAX(simplified couple estimate, elastic floor case, shear estimate),
  not the plain elastic value (sheet KJ201 = MAX(KG201, KH201, KI201)).
- Rod length (sheet DD65/DG65) is CEILING(MAX(development length +
  projection, 6 ft for 18J bolts, 5 ft otherwise), 0.25).
- SBC (single bolt circle) only. EBC/IBC second-circle sheets are not
  covered; the check block flags a non-SBC type as not implemented.
"""

import math

# ---------------------------------------------------------------- constants
DENSITY_PCF = 490.0          # steel density, lb/ft^3 (sheet O39-O41)
STEEL_DENSITY_LBIN3 = 0.2836  # lb/in^3 (template weight O35)
CEIL_STEP = 0.25             # plate thickness / rod length rounding, in / ft


def ceil_to(x, step):
    return math.ceil(x / step - 1e-9) * step


def round_down_to(x, step):
    return math.floor(x / step + 1e-9) * step


def fmt(x, nd=4):
    if x is None:
        return "—"
    if isinstance(x, str):
        return x
    if abs(x) >= 10000:
        return f"{x:,.2f}"
    return f"{x:,.{nd}f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------- inputs
DEFAULTS = {
    # pole
    "sides": 12,
    "base_od": 46.5,
    "wall": 0.3125,
    "pole_yield": 65.0,
    "shaft_capacity_ftk": 2622.61,   # PLS-POLE shaft moment capacity (IU1/IV1 max)
    "shaft_capacity_perc": 0.5,
    # bolts
    "bolt_dia": 2.25,
    "bolt_grade": '2.25"-18J',
    "bolt_yield": 75.0,
    "bolt_fu": 100.0,
    "tensile_area": 3.25,
    "gross_area": 3.9740625,
    "nut_width": 4.0625,
    "hole_dia": 2.625,
    "bolt_at_0": True,
    "bolt_qty_override": None,
    # plate
    "plate_shape": "Round",
    "plate_grade": "A572-60",
    "plate_yield": 60.0,
    "bp_edge": 6.0,
    "min_thick": 1.5,
    "plate_thick_override": None,
    "bolt_circle_override": None,
    # factors
    "sf_plate": 1.0,
    "sf_bolt": 1.0,
    # weights
    "rod_w_lbft": 14.63,
    "template_count": 2,
    "template_w1": 4.38,
    "template_w2": 0.89,
    "template_t1": 0.375,
    "template_t2": 0.375,
    "template_id_drop": 12.0,
    # development length
    "fc_ksi": 3.0,
    "projection_in": 12.0,
    "alpha_dev": 1.2,
    "beta_dev": 0.8,
    # bolt-circle AD branch
    "ae5_threshold": 13.3046,
}

GRADE_YIELDS = {  # grade -> yield ksi (sheet GRDTABLEC45 rows; extend as needed)
    "A572-60": 60.0,
    "A572-50": 50.0,
    "A36": 36.0,
}


def _num(v, default):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def clean_inputs(raw):
    """Merge user input over defaults; returns a dict of typed values."""
    p = dict(DEFAULTS)
    if not raw:
        return p
    for k in ("sides",):
        if raw.get(k) not in (None, ""):
            p[k] = int(raw[k])
    for k in ("base_od", "wall", "pole_yield", "shaft_capacity_ftk",
              "shaft_capacity_perc", "bolt_dia", "bolt_yield", "bolt_fu",
              "tensile_area", "gross_area", "nut_width", "hole_dia",
              "plate_yield", "bp_edge", "min_thick", "sf_plate", "sf_bolt",
              "rod_w_lbft", "template_w1", "template_w2", "template_t1",
              "template_t2", "template_id_drop", "fc_ksi", "projection_in",
              "alpha_dev", "beta_dev", "ae5_threshold"):
        p[k] = _num(raw.get(k), p[k])
    for k in ("template_count",):
        if raw.get(k) not in (None, ""):
            p[k] = int(raw[k])
    for k in ("bolt_grade", "plate_shape", "plate_grade"):
        if raw.get(k) not in (None, ""):
            p[k] = str(raw[k])
    p["bolt_at_0"] = str(raw.get("bolt_at_0", "yes")).lower() in ("yes", "true", "1", "y")
    for k, out in (("bolt_qty_override", "bolt_qty_override"),
                   ("plate_thick_override", "plate_thick_override"),
                   ("bolt_circle_override", "bolt_circle_override")):
        v = raw.get(k)
        p[out] = None if v in (None, "") else _num(v, None)
    cases = []
    for c in raw.get("cases", []) or []:
        name = (c.get("name") or "").strip()
        if not name and all(c.get(k) in (None, "") for k in
                            ("axial", "shear", "mx", "my", "torsion")):
            continue
        cases.append({
            "name": name or "Unnamed case",
            "axial": _num(c.get("axial"), 0.0),
            "shear": _num(c.get("shear"), 0.0),
            "mx": _num(c.get("mx"), 0.0),
            "my": _num(c.get("my"), 0.0),
            "torsion": _num(c.get("torsion"), 0.0),
            "sf": _num(c.get("sf"), 1.0),
        })
    p["cases"] = cases
    return p


# ---------------------------------------------------------------- engine
def ffa(angle_deg):
    """Distance from a bolt angle to the nearest 30-degree axis (sheet Z)."""
    a = angle_deg % 360.0
    if a <= 15:
        return abs(a - 0)
    if a <= 45:
        return abs(30 - a)
    if a <= 75:
        return abs(60 - a)
    if a <= 90:
        return abs(90 - a)
    return min(abs(a - 30 * round(a / 30.0)), 15.0)


def bolt_circle_calc(p, n, first_quadrant_angles):
    """Sheet MinBC_Tapp = MAX(BZ branch, AD branch). Returns steps."""
    steps = []
    od, t, d = p["base_od"], p["wall"], p["bolt_dia"]
    max_poledia = od + 0.5 if t >= 0.5 else od + 0.25
    boltedge = 2.0 * (t + 1.0 / 16.0) + p["nut_width"] + 0.5
    steps.append({"step": "Bolt-circle: pole diameter term",
                  "formula": "MaxPoledia = OD + 0.5 if wall >= 0.5 else OD + 0.25",
                  "explain": "The shaft's outside diameter plus a small clearance so the "
                             "bolt circle clears the pole wall.",
                  "inputs": {"OD": od, "wall": t},
                  "result": max_poledia, "unit": "in"})
    steps.append({"step": "Bolt-circle: edge term",
                  "formula": "Boltedge = 2 x (wall + 1/16) + nut width + 0.5",
                  "explain": "Room for the pole wall, a sixteenth of clearance, the nut "
                             "across-flats, and a half inch of spare room.",
                  "inputs": {"wall": t, "nut width": p["nut_width"]},
                  "result": boltedge, "unit": "in"})
    # BZ branch: per first-quadrant bolt angle
    bz_vals = []
    for bg in first_quadrant_angles:
        if bg <= 15:
            v = max_poledia / math.cos(math.radians(bg)) + boltedge
        elif bg <= 45:
            v = max_poledia / math.sin(math.radians(120 - bg)) + boltedge
        elif bg <= 75:
            v = max_poledia / math.sin(math.radians(150 - bg)) + boltedge
        elif bg <= 90:
            v = max_poledia / math.sin(math.radians(180 - bg)) + boltedge
        else:
            v = 0.0
        bz_vals.append(ceil_to(v, 0.25))
    bz_max = max(bz_vals) if bz_vals else 0.0
    steps.append({"step": "Bolt-circle: angle branch (BZ)",
                  "formula": "BZ = CEILING(MaxPoledia / trig(angle) + Boltedge, 0.25), "
                             "trig = cos(angle) to 15 deg, sin(120-angle) to 45 deg, "
                             "sin(150-angle) to 75 deg, sin(180-angle) to 90 deg",
                  "explain": "For each bolt in the first quadrant, the circle must be big "
                             "enough that the bolt clears the 12-sided shaft face at that "
                             "bolt's angle. The worst angle governs this branch.",
                  "inputs": {"first-quadrant angles": first_quadrant_angles,
                             "BZ per angle": bz_vals},
                  "result": bz_max, "unit": "in"})
    # AD branch: per bolt
    clr = 0.0625 + (0.125 if od < 36 else 0.25) + 0.25
    ad_vals = []
    for bg in first_quadrant_angles:
        f = ffa(bg)
        ae = 1.0 if f >= p["ae5_threshold"] else math.cos(math.radians(f))
        ad = 2.0 * ((od / 2.0) / math.cos(math.radians(f)) +
                    (clr + t + d * 1.778 / 2.0) / ae)
        ad_vals.append(ad)
    ad_max = ceil_to(max(ad_vals), 0.25) if ad_vals else 0.0
    steps.append({"step": "Bolt-circle: clearance branch (AD)",
                  "formula": "AD = 2 x ((OD/2)/cos(FFA) + (0.0625 + gap + 0.25 + wall + "
                             "bolt_dia x 1.778/2) / AE), gap = 0.125 if OD < 36 else 0.25; "
                             "FFA = distance to nearest 30-deg axis; "
                             "AE = 1 if FFA >= threshold else cos(FFA)",
                  "explain": "The circle must also clear the shaft face plus nut and "
                             "washer room, measured square to the nearest flat of the "
                             "12-sided shaft. FFA is how far the bolt sits from a flat.",
                  "inputs": {"FFA per angle": [ffa(a) for a in first_quadrant_angles],
                             "AD per angle": ad_vals},
                  "result": ad_max, "unit": "in"})
    minbc = max(bz_max, ad_max)
    steps.append({"step": "Bolt circle calculated (MinBC)",
                  "formula": "MinBC = MAX(BZ branch, AD branch)",
                  "explain": "The calculated bolt circle is the larger of the two "
                             "requirements above.",
                  "inputs": {"BZ branch": bz_max, "AD branch": ad_max},
                  "result": minbc, "unit": "in"})
    return minbc, steps


def compute(p):
    """Run the full chain. Returns (summary dict, steps list, case rows)."""
    steps = []
    od, t, fy_pole = p["base_od"], p["wall"], p["pole_yield"]
    d, As, Ag = p["bolt_dia"], p["tensile_area"], p["gross_area"]
    ipp = math.pi * d ** 4 / 64.0
    sides = p["sides"]

    # ---- shaft capacity floor -------------------------------------------
    floor_moment = p["shaft_capacity_perc"] * p["shaft_capacity_ftk"]
    tapp_est_full = 0.411 * (od - t) ** 3 * t * fy_pole / (od / 2.0) / 12.0
    tapp_est = p["shaft_capacity_perc"] * tapp_est_full
    steps.append({"step": "50% shaft-capacity floor (applied)",
                  "formula": "Floor = percentage x PLS shaft moment capacity",
                  "explain": "The plate is never sized for less than half the shaft's own "
                             "moment capacity, even if no load case reaches it. The sheet "
                             "takes this capacity from the PLS-POLE model (its IU/IV "
                             "columns), not from the estimate below.",
                  "inputs": {"PLS shaft capacity": p["shaft_capacity_ftk"],
                             "percentage": p["shaft_capacity_perc"]},
                  "result": floor_moment, "unit": "ft-k"})
    steps.append({"step": "TAPP 0.411 estimate (reference only)",
                  "formula": "0.411 x (OD - wall)^3 x wall x pole_yield / (OD/2) / 12, "
                             "then x percentage",
                  "explain": "TAPP's own closed-form estimate of the shaft capacity, shown "
                             "on the sheet (cell O48). The sheet displays it but applies "
                             "the PLS-based floor above for the case moments.",
                  "inputs": {"OD": od, "wall": t, "pole yield": fy_pole},
                  "result": tapp_est, "unit": "ft-k"})

    # ---- bolt quantity (RODS) -------------------------------------------
    cases = p["cases"]
    raw_moments = [math.hypot(c["mx"], c["my"]) for c in cases]
    gov_moment = max([floor_moment] + raw_moments) if raw_moments else floor_moment
    gov_idx = None
    if raw_moments:
        m = max(raw_moments)
        if m >= floor_moment:
            gov_idx = raw_moments.index(m)
    max_poledia = od + 0.5 if t >= 0.5 else od + 0.25
    boltedge = 2.0 * (t + 1.0 / 16.0) + p["nut_width"] + 0.5
    bc_1 = ceil_to(max_poledia / math.cos(math.radians(180.0 / sides)) + boltedge, 0.25)
    ab_cap_table = p["bolt_yield"] * As  # sheet AR1 lookup = Fy x As
    p_gov = cases[gov_idx]["axial"] if gov_idx is not None else 0.0
    rods_raw = ((4.0 * gov_moment / (bc_1 / 12.0) + abs(p_gov)) /
                (ab_cap_table * p["sf_bolt"]))
    rods = int(4 * math.floor(rods_raw / 4.0 + 1.0)) if rods_raw > 0 else 4
    steps.append({"step": "Bolt quantity calculated (RODS)",
                  "formula": "RODS = 4 x INT(((4 x governing moment / (BC_1/12) + "
                             "|governing axial|) / (bolt capacity x SF)) / 4 + 1)",
                  "explain": "Enough bolts so the overturning couple plus the axial load "
                             "fits within one bolt's capacity, rounded up to a multiple "
                             "of 4. BC_1 is a first-pass circle from the pole diameter "
                             "term and edge term.",
                  "inputs": {"governing moment": gov_moment, "BC_1": bc_1,
                             "governing axial": p_gov,
                             "bolt capacity (Fy x As)": ab_cap_table},
                  "result": rods, "unit": "bolts"})
    n = int(p["bolt_qty_override"]) if p["bolt_qty_override"] else rods
    steps.append({"step": "Bolt quantity used",
                  "formula": "Use = override if given, else RODS",
                  "explain": "An override replaces the calculated count; the checks then "
                             "flag an override that falls short.",
                  "inputs": {"calculated": rods, "override": p["bolt_qty_override"]},
                  "result": n, "unit": "bolts"})

    # ---- bolt circle ------------------------------------------------------
    bolts_q = n / 4.0
    if p["bolt_at_0"]:
        fq = [i * (90.0 / bolts_q) for i in range(int(bolts_q) + 1)]
    else:
        fq = [(i + 0.5) * (90.0 / bolts_q) for i in range(int(bolts_q))]
    fq = [a for a in fq if a <= 90.0 + 1e-9]
    minbc, bc_steps = bolt_circle_calc(p, n, fq)
    steps.extend(bc_steps)
    bcd = p["bolt_circle_override"] if p["bolt_circle_override"] else minbc
    steps.append({"step": "Bolt circle used",
                  "formula": "Use = override if given, else MinBC",
                  "explain": "An override replaces the calculated circle; the bolt-circle "
                             "check then flags an override smaller than calculated.",
                  "inputs": {"calculated": minbc, "override": p["bolt_circle_override"]},
                  "result": bcd, "unit": "in"})

    # ---- bolt positions + inertias ---------------------------------------
    start = 0.0 if p["bolt_at_0"] else (360.0 / n) / 2.0
    angles = [start + k * 360.0 / n for k in range(n)]
    xs = [bcd / 2.0 * math.cos(math.radians(a)) for a in angles]
    ys = [bcd / 2.0 * math.sin(math.radians(a)) for a in angles]
    sum_x2 = sum(x * x for x in xs)
    sum_y2 = sum(y * y for y in ys)
    ixx = As * sum_y2 + n * ipp
    iyy = As * sum_x2 + n * ipp
    steps.append({"step": "Bolt positions and group inertias",
                  "formula": "x = cos(angle) x BCD/2, y = sin(angle) x BCD/2; "
                             "Ixx = As x sum(y^2) + n x Ipp, "
                             "Iyy = As x sum(x^2) + n x Ipp, Ipp = pi x d^4/64",
                  "explain": "Each bolt's spot on the circle, and the group's resistance "
                             "to tipping: bolts farther from the centre carry more of the "
                             "moment. The tiny Ipp term is each bolt's own stiffness.",
                  "inputs": {"bolt angles": [round(a, 2) for a in angles],
                             "BCD": bcd, "As": As, "Ipp": round(ipp, 5)},
                  "result": {"Ixx": round(ixx, 2), "Iyy": round(iyy, 2)},
                  "unit": "in^2 / in^4 x in^2"})

    # ---- plate width / ID -------------------------------------------------
    if p["plate_shape"] == "Square":
        width = max(math.ceil((bcd * math.cos(math.radians(45)) +
                               ceil_to((p["bp_edge"] / 2.25) * d, 0.25)) / 0.25) * 0.25,
                    od + 3.0)
        width_formula = ("Square: MAX(ROUNDUP((BCD x cos45 + CEILING(edge/2.25 x d, "
                         "0.25))/0.25, 0) x 0.25, OD + 3)")
    else:
        width = ceil_to(bcd + (p["bp_edge"] / 2.25) * d, 0.25)
        width_formula = ("Round / 12-sided: CEILING(BCD + edge/2.25 x bolt_dia, 0.25)")
    plate_id = round_down_to(od * 0.6 / 0.25, 1) * 0.25
    steps.append({"step": "Base plate width",
                  "formula": width_formula,
                  "explain": "The plate must reach past the bolt circle by the edge "
                             "allowance (6 in. for the reference 2.25 in. bolt, scaled "
                             "with bolt size), rounded up to a quarter inch.",
                  "inputs": {"BCD": bcd, "edge": p["bp_edge"], "bolt dia": d},
                  "result": width, "unit": "in"})
    steps.append({"step": "Base plate inside diameter",
                  "formula": "ID = ROUNDDOWN(OD x 0.6 / 0.25) x 0.25",
                  "explain": "The centre hole the pole shaft passes through, taken as "
                             "60% of the shaft diameter and rounded down to a quarter "
                             "inch.",
                  "inputs": {"OD": od},
                  "result": plate_id, "unit": "in"})

    # ---- per-case bolt forces ---------------------------------------------
    def bolt_forces(case):
        mx_raw, my_raw = case["mx"], case["my"]
        m_raw = math.hypot(mx_raw, my_raw)
        if m_raw < floor_moment:
            if m_raw > 1e-9:
                f = floor_moment / m_raw
                mx_u, my_u = mx_raw * f, my_raw * f
            else:
                mx_u, my_u = floor_moment, 0.0
        else:
            mx_u, my_u = mx_raw, my_raw
        p_ax = case["axial"]
        forces = []
        for x, y in zip(xs, ys):
            mom = (mx_u * 12.0 * x / ixx + my_u * 12.0 * y / iyy) * As
            forces.append(mom + p_ax / n)
        max_comp = max(forces)
        max_ten = max_comp - p_ax / n  # sheet definition (KG = KH - P/n)
        max_shear = case["shear"] / n + 2.0 * case["torsion"] * 12.0 / (bcd * n)
        max_force = max(max_ten, max_comp, max_shear)
        return {"mx_used": mx_u, "my_used": my_u, "m_raw": m_raw,
                "forces": forces, "max_comp": max_comp, "max_ten": max_ten,
                "max_shear": max_shear, "max_force": max_force}

    case_rows = []
    for c in cases:
        r = bolt_forces(c)
        r.update(c)
        case_rows.append(r)

    # ---- sheet floor row (row 201): MAX of three estimates ---------------
    # The sheet's "50% OF SHAFT CAPACITY" row does not report the plain
    # elastic value. KJ201 = MAX(KG201, KH201, KI201), where KG is a
    # simplified couple estimate, KH is the elastic floor case (moment =
    # floor on X, axial = max axial over cases, shear = 0), and KI is the
    # shear estimate. For STR017 the elastic term wins; for STR057 LPOL
    # the simplified term wins (81.71 vs 76.26 kips).
    floor_rows = [r for r in case_rows
                  if "shaft capacity" in str(r["name"]).lower()]
    if floor_rows and case_rows:
        gov_cand = max(case_rows,
                       key=lambda r: math.hypot(r["mx_used"], r["my_used"]))
        max_axial = max(c["axial"] for c in cases)
        max_torsion = max(c["torsion"] for c in cases)
        kg = (4.0 * floor_moment / (bcd / 12.0) - gov_cand["axial"]) / n if n else 0.0
        kh_row = bolt_forces({"mx": floor_moment, "my": 0.0, "axial": max_axial,
                              "shear": 0.0, "torsion": max_torsion})
        kh = kh_row["max_force"]
        ki_torsion = gov_cand["torsion"] if gov_cand["torsion"] >= 1.0 else 1.0
        ki = (2.0 * ki_torsion * 12.0 / (bcd * n) + gov_cand["shear"] / rods) \
            if n and rods else 0.0
        floor_max = max(kg, kh, ki)
        for r in floor_rows:
            r["max_comp"] = kh_row["max_comp"]
            r["max_ten"] = kh_row["max_ten"]
            r["max_shear"] = ki
            r["max_force"] = floor_max
        steps.append({"step": "Floor-row max bolt force (sheet row 201)",
                      "formula": "KJ201 = MAX(KG201, KH201, KI201); "
                                 "KG = (4 x floor / (BCD/12) - governing axial) / n_use; "
                                 "KH = elastic floor case (moment = floor on X, axial = "
                                 "max axial); "
                                 "KI = 2 x MAX(governing torsion, 1) x 12 / (BCD x n_use) "
                                 "+ governing shear / n_calc",
                      "explain": "The sheet's floor row takes the worst of three "
                                 "estimates: a quick couple calculation, the full "
                                 "elastic bolt-group calculation for the floor case, "
                                 "and the shear estimate. Whichever is largest is the "
                                 "floor row's reported max bolt force.",
                      "inputs": {"floor moment": floor_moment, "BCD": bcd,
                                 "governing axial": gov_cand["axial"],
                                 "governing shear": gov_cand["shear"],
                                 "governing torsion": gov_cand["torsion"],
                                 "max axial": max_axial, "n_use": n, "n_calc": rods,
                                 "KG": round(kg, 3), "KH": round(kh, 3),
                                 "KI": round(ki, 3)},
                      "result": floor_max, "unit": "kips"})

    # governing case (max resultant incl. floor; floor case if floor wins)
    if case_rows:
        gov_row = max(case_rows, key=lambda r: math.hypot(r["mx_used"], r["my_used"]))
        gov_bolt = gov_row["max_force"]
        gov_name = gov_row["name"]
    else:
        gov_row, gov_bolt, gov_name = None, 0.0, "—"

    # ---- rod capacity + AB usage ------------------------------------------
    shear_u = gov_row["shear"] if gov_row else 0.0
    fv = (shear_u / n) / Ag if n else 0.0
    fv_allow = 0.35 * p["bolt_fu"]
    rod_cap = p["bolt_yield"] * math.sqrt(max(0.0, 1.0 - (fv / fv_allow) ** 2)) * As * p["sf_bolt"]
    max_force_all = max([r["max_force"] for r in case_rows], default=0.0)
    ab_usage = max_force_all / rod_cap if rod_cap else 0.0
    steps.append({"step": "Rod capacity (revised)",
                  "formula": "RodCap = bolt_yield x SQRT(1 - (fv/Fv)^2) x As x SF_bolt, "
                             "fv = (governing shear / n) / Ag, Fv = 0.35 x Fu",
                  "explain": "One bolt's pull capacity, reduced a touch when it also "
                             "carries shear: the combined tension-plus-shear rule. "
                             "fv is the shear stress on one bolt.",
                  "inputs": {"governing shear": shear_u, "n": n, "Ag": Ag,
                             "fv": round(fv, 4), "Fv": fv_allow,
                             "bolt yield": p["bolt_yield"], "As": As},
                  "result": rod_cap, "unit": "kips"})
    steps.append({"step": "Anchor-bolt usage (AB usage)",
                  "formula": "AB usage = max rod force over all cases / rod capacity",
                  "explain": "The hardest-working bolt against what one bolt is allowed "
                             "to carry. Must be 1.0 or less, and not zero.",
                  "inputs": {"max rod force": max_force_all, "rod capacity": rod_cap},
                  "result": ab_usage, "unit": "ratio"})

    # ---- bend lines ---------------------------------------------------------
    def bend_line_moments():
        # governing-case bolt forces at first-quadrant angles
        if gov_row is None:
            return [0.0, 0.0, 0.0, 0.0], []
        mx_u, my_u, p_ax = gov_row["mx_used"], gov_row["my_used"], gov_row["axial"]
        detail = []
        bl = [0.0, 0.0, 0.0, 0.0]
        od_ee = od / math.cos(math.radians(180.0 / sides))
        for i, bg in enumerate(fq):
            x = bcd / 2.0 * math.cos(math.radians(bg))
            y = bcd / 2.0 * math.sin(math.radians(bg))
            f_bolt = (mx_u * 12.0 * x / ixx + my_u * 12.0 * y / iyy) * As + p_ax / n
            if p["plate_shape"] == "Round":
                c1 = 0.5 * (bcd * math.cos(math.radians(bg)) - od) if bg <= 30 else 0.0
                c2 = 0.5 * (bcd - od / math.sin(math.radians(120 - bg))) * \
                    math.cos(math.radians(30 - bg)) if bg <= 60 else 0.0
                c3 = 0.5 * (bcd - od / math.sin(math.radians(30 + bg))) * \
                    math.cos(math.radians(60 - bg)) if bg <= 90 else 0.0
            else:
                c1 = 0.5 * (bcd * math.cos(math.radians(bg)) - od) if bg <= 30 else 0.0
                c2 = 0.5 * (bcd - od_ee / math.sin(math.radians(120 - bg))) * \
                    math.cos(math.radians(30 - bg)) if bg <= 60 else 0.0
                c3 = 0.5 * (bcd - od_ee / math.sin(math.radians(30 + bg))) * \
                    math.cos(math.radians(60 - bg)) if bg <= 90 else 0.0
            c4 = 0.5 * (bcd * math.sin(math.radians(bg)) - od) if bg > 60 else 0.0
            cs = [c1, c2, c3, c4]
            contribs = [max(c, 0.0) * f_bolt for c in cs]
            detail.append({"angle": bg, "bolt_force": f_bolt, "c": cs,
                           "contrib": contribs})
        # sheet totals: Bl1 doubles all but the 0-degree row (or all if no
        # 0-degree row); Bl4 doubles all but the 90-degree row; Bl2/Bl3 plain.
        for j, (w0, w90) in enumerate([(True, False), (False, False),
                                       (False, False), (False, True)]):
            tot = 0.0
            for k, dt in enumerate(detail):
                w = 2.0
                if w0 and k == 0 and abs(dt["angle"]) < 1e-9:
                    w = 1.0
                if w90 and abs(dt["angle"] - 90.0) < 1e-9:
                    w = 1.0
                if not w0 and not w90:
                    w = 1.0
                tot += w * dt["contrib"][j]
            bl[j] = tot
        return bl, detail

    bl_moments, bl_detail = bend_line_moments()
    m_gov = max(bl_moments) if bl_moments else 0.0
    if p["plate_shape"] == "Square":
        bl_width = od * math.tan(math.radians(180.0 / sides))
        bl_formula = "Bl = OD x TAN(180 / sides)  [square plate]"
    else:
        bl_width = 0.9 * math.sqrt(max(bcd ** 2 - od ** 2, 0.0))
        bl_formula = "Bl = 0.9 x SQRT(BCD^2 - OD^2)  [round / 12-sided plate]"
    steps.append({"step": "Bend-line moments (governing case)",
                  "formula": "For each first-quadrant bolt: contribution = "
                             "MAX(distance to bend line, 0) x bolt force; "
                             "Bl1 = first bolt + 2 x rest, Bl2 = sum, Bl3 = sum, "
                             "Bl4 = 90-deg bolt + 2 x rest",
                  "explain": "The plate bends along lines across the shaft faces. Each "
                             "bolt pulls on the plate with a lever arm equal to how far "
                             "it sits past the shaft face; bolts behind the line pull "
                             "nothing. Only the first quadrant is summed (the ring "
                             "repeats it), with mirror doubling on lines 1 and 4.",
                  "inputs": {"governing case": gov_name,
                             "first-quadrant detail": [
                                 {"angle": d["angle"],
                                  "bolt force": round(d["bolt_force"], 3),
                                  "distances": [round(c, 4) for c in d["c"]],
                                  "contributions": [round(c, 3) for c in d["contrib"]]}
                                 for d in bl_detail]},
                  "result": {"Bl1": round(bl_moments[0], 3),
                             "Bl2": round(bl_moments[1], 3),
                             "Bl3": round(bl_moments[2], 3),
                             "Bl4": round(bl_moments[3], 3),
                             "governing": round(m_gov, 3)},
                  "unit": "in-k"})
    steps.append({"step": "Bend-line effective width",
                  "formula": bl_formula,
                  "explain": "How wide a strip of plate is assumed to resist the bending "
                             "at the worst bend line.",
                  "inputs": {"BCD": bcd, "OD": od},
                  "result": bl_width, "unit": "in"})

    # ---- plate thickness ----------------------------------------------------
    fy_plate = p["plate_yield"]
    t_req = math.sqrt(m_gov * 6.0 / (bl_width * fy_plate * p["sf_plate"])) \
        if bl_width > 0 and fy_plate > 0 else 0.0
    t_calc = max(p["min_thick"], ceil_to(t_req, 0.25)) if t_req > 0 else p["min_thick"]
    t_use = p["plate_thick_override"] if p["plate_thick_override"] else t_calc
    steps.append({"step": "Required plate thickness (governing)",
                  "formula": "t_req = SQRT(governing bend-line moment x 6 / "
                             "(Bl x plate_yield x SF_plate))",
                  "explain": "Plate bending stress is 6M/(width x thickness^2); setting "
                             "it equal to the plate yield and solving for thickness "
                             "gives the thickness the worst bend line demands.",
                  "inputs": {"governing moment": round(m_gov, 3), "Bl": round(bl_width, 4),
                             "plate yield": fy_plate, "SF": p["sf_plate"]},
                  "result": t_req, "unit": "in"})
    steps.append({"step": "Plate thickness calculated / used",
                  "formula": "t_calc = MAX(min_thick, CEILING(t_req, 0.25)); "
                             "t_use = override if given, else t_calc",
                  "explain": "Plates come in quarter-inch steps and never thinner than "
                             "the 1.5 in. minimum, so the required thickness is rounded "
                             "up to the next quarter inch.",
                  "inputs": {"t_req": round(t_req, 4), "min": p["min_thick"],
                             "override": p["plate_thick_override"]},
                  "result": {"t_calc": t_calc, "t_use": t_use}, "unit": "in"})

    # ---- per-case plate usage ------------------------------------------------
    for r in case_rows:
        t_min = t_req * (r["max_force"] / gov_bolt) if gov_bolt else 0.0
        bend_stress = fy_plate * (t_min / t_use) ** 2 if t_use else 0.0
        usage = 0.0 if r["sf"] == 0 else (bend_stress / fy_plate if fy_plate else 0.0)
        r["t_min"] = t_min
        r["bend_stress"] = bend_stress
        r["usage"] = usage
    bp_usage = max([r["usage"] for r in case_rows], default=0.0)
    steps.append({"step": "Plate usage (BP usage)",
                  "formula": "Per case: t_min_case = t_req x (case max bolt force / "
                             "governing max bolt force); bend stress = plate_yield x "
                             "(t_min_case / t_use)^2; usage = bend stress / plate_yield; "
                             "usage = 0 when the case strength factor is 0. "
                             "BP usage = MAX over cases.",
                  "explain": "Instead of re-running the bend-line sum per case, the sheet "
                             "scales the governing thickness by how hard each case pulls "
                             "its worst bolt. Because stress goes as thickness squared, "
                             "usage is the squared thickness ratio. Cases with strength "
                             "factor 0 (insulator, deflection) report zero by TAPP's "
                             "reporting choice.",
                  "inputs": {"governing max bolt force": round(gov_bolt, 3),
                             "t_req": round(t_req, 4), "t_use": t_use},
                  "result": bp_usage, "unit": "ratio"})

    # ---- checks ---------------------------------------------------------------
    def check(name, formula, explain, ok, detail=""):
        return {"name": name, "formula": formula, "explain": explain,
                "result": "Valid" if ok else "Error", "detail": detail}

    spacing = bcd * math.pi / n if n else 0.0
    spacing_allow = ceil_to(2.625 * d, 0.25)
    checks = []
    checks.append(check("Rod spacing",
                        "spacing = BCD x pi / n > CEILING(2.625 x bolt_dia, 0.25)",
                        "Bolts must sit far enough apart around the circle for the "
                        "concrete and the wrench.",
                        spacing > spacing_allow,
                        f"spacing {spacing:.4f} in vs allowed {spacing_allow:.2f} in"))
    checks.append(check("Max rod compression",
                        "max rod force < bolt_yield x As",
                        "No bolt may be squeezed past its yield capacity.",
                        max_force_all < p["bolt_yield"] * As,
                        f"{max_force_all:.2f} kips vs {p['bolt_yield'] * As:.2f} kips"))
    checks.append(check("Fy rod check",
                        "bolt_yield > max rod force / As",
                        "The worst bolt's stress must stay under the bolt yield.",
                        p["bolt_yield"] > (max_force_all / As if As else 0),
                        f"{max_force_all / As:.2f} ksi vs {p['bolt_yield']:.0f} ksi"))
    checks.append(check("Bolt circle check",
                        "MinBC <= BCD used",
                        "The circle actually used must be at least the calculated one.",
                        minbc <= bcd + 1e-9,
                        f"calculated {minbc:.2f} in vs used {bcd:.2f} in"))
    checks.append(check("Plate width check",
                        "MinBC <= BCD used + 6",
                        "The plate must still cover the calculated circle plus the "
                        "edge allowance.",
                        minbc <= bcd + 6.0 + 1e-9,
                        f"calculated {minbc:.2f} in vs {bcd + 6.0:.2f} in"))
    if t_use < t_calc - 1e-9 or t_calc == 0:
        thick_result, thick_note = "Error", "used thinner than calculated"
    elif t_use > t_calc + 1e-9:
        thick_result, thick_note = "Valid", "used thicker than calculated (note: 'Less Bp thick use?' in sheet)"
    else:
        thick_result, thick_note = "Valid", "used equals calculated"
    checks.append({"name": "Base plate thickness check",
                   "formula": "t_use < t_calc -> Error; t_use > t_calc -> advisory; else Valid",
                   "explain": "An override thinner than calculated fails; thicker is "
                              "allowed but flagged by the sheet.",
                   "result": thick_result, "detail": thick_note})
    orient_ok = not (not p["bolt_at_0"] and n == 12)
    checks.append(check("Bolt orientation check",
                        'NOT (bolt_at_0 = No AND n = 12), else "Bolt@0?"',
                        "With no bolt at zero degrees and exactly 12 bolts, the sheet "
                        "asks whether a bolt at zero was intended.",
                        orient_ok, ""))
    grade_ok = p["plate_grade"] in GRADE_YIELDS
    checks.append(check("Material grade check",
                        "plate grade must be in the allowed grade list",
                        "Only grades the sheet's table knows can be looked up.",
                        grade_ok, f"grade {p['plate_grade']}"))
    shape_ok = not ((n == 4 and p["plate_shape"] == "Round") or
                    (n > 4 and p["plate_shape"] == "Square"))
    checks.append(check("Plate shape check",
                        "NOT (n = 4 on round) and NOT (n > 4 on square)",
                        "Four bolts do not belong on a round plate, and more than four "
                        "do not belong on a square plate, per the sheet's rule.",
                        shape_ok, f"n = {n}, shape = {p['plate_shape']}"))
    checks.append(check("AB usage check",
                        "AB usage <= 1 and != 0",
                        "The anchor bolts must be within capacity and actually loaded.",
                        0 < ab_usage <= 1.0, f"AB usage {ab_usage:.4f}"))
    checks.append(check("BP usage check",
                        "BP usage <= 1 and != 0",
                        "The plate bending must be within yield and actually loaded.",
                        0 < bp_usage <= 1.0, f"BP usage {bp_usage:.4f}"))
    m27 = max([floor_moment] + [math.hypot(c["mx"], c["my"]) for c in cases]) \
        if cases else floor_moment
    # sheet uses MAX(B48:B49) = max axial of floor case and governing case;
    # floor-case axial = max axial over cases, so this equals max axial.
    p27 = max([c["axial"] for c in cases], default=0.0) if cases else 0.0
    if n == 4:
        q = (4 * m27 * 12) / (bcd * n) + p27 / n if bcd and n else 0
        qty_result = "Valid" if q < rod_cap else ("Error" if q > rod_cap else "Valid")
        qty_detail = f"couple check {q:.2f} kips vs capacity {rod_cap:.2f} kips"
    elif n > 4:
        q1 = (4 * m27 * 12) / (bcd * (n - 4)) + p27 / (n - 4) if bcd else 0
        q2 = (4 * m27 * 12) / (bcd * n) + p27 / n if bcd else 0
        if q1 < rod_cap:
            qty_result, qty_detail = "Valid", \
                f"fewer-bolt check {q1:.2f} kips < capacity (sheet: 'Use less bolts?')"
        elif q2 > rod_cap:
            qty_result, qty_detail = "Error", \
                f"capacity check {q2:.2f} kips > capacity {rod_cap:.2f} kips"
        else:
            qty_result, qty_detail = "Valid", \
                f"checks {q1:.2f} / {q2:.2f} kips vs capacity {rod_cap:.2f} kips"
    else:
        qty_result, qty_detail = "Error", "bolt count below 4 not covered"
    checks.append({"name": "Bolt quantities check",
                   "formula": "n = 4: (4 x M x 12)/(BCD x n) + P/n vs capacity; "
                              "n > 4: same with (n-4) and with n",
                   "explain": "A second, independent couple-based count check: with four "
                              "fewer bolts the load must still fit, and with the bolts "
                              "used it must not exceed capacity.",
                   "result": qty_result, "detail": qty_detail})
    width_req = width  # sheet AO41 uses the same width formula
    checks.append(check("Plate width check (2)",
                        "plate width used == required width",
                        "The width actually used must match the width the rules "
                        "require; smaller fails, larger is flagged.",
                        abs(width_req - width) < 1e-9,
                        f"used {width:.2f} in vs required {width_req:.2f} in"))
    sf_ok = all(isinstance(c["sf"], (int, float)) for c in cases)
    checks.append(check("PLS SF check",
                        "strength-factor column must be clean (no blanks/errors)",
                        "Every load case must carry a usable PLS strength factor.",
                        sf_ok, ""))
    overall = "OK" if all(c["result"] == "Valid" for c in checks) else "Error"

    # ---- weights ---------------------------------------------------------------
    gross_w = math.pi * ((width / 12.0) * 0.5) ** 2 * (t_use / 12.0) * DENSITY_PCF
    id_w = math.pi * ((plate_id / 12.0) * 0.5) ** 2 * (t_use / 12.0) * DENSITY_PCF
    holes_w = n * math.pi * ((p["hole_dia"] / 12.0) * 0.5) ** 2 * (t_use / 12.0) * DENSITY_PCF
    plate_final = gross_w - (id_w + holes_w)
    # development length (ASCE 9.3.5 as the sheet applies it, DD65)
    ft_allow = p["bolt_yield"]  # sheet capacity basis (Fy = 75 = 0.75*Fu here)
    gamma = min((max_force_all / ft_allow) / Ag, 1.0) if Ag and ft_allow else 1.0
    ld_in = 3.52 * p["bolt_yield"] / math.sqrt(p["fc_ksi"]) * p["alpha_dev"] * \
        p["beta_dev"] * gamma
    ld_in = max(ld_in, 25.0 * d)
    # sheet DD65/DG65: rod length = CEILING(MAX(Ld/12 + projection/12,
    # 6 ft for 18J bolts, 5 ft otherwise), 0.25). The 6 ft floor is what
    # STR057 LPOL hits (5.69 ft by development length alone -> 6 ft).
    bolt_type = str(p["bolt_grade"]).split("-")[-1].strip()
    min_rod_ft = 6.0 if bolt_type == "18J" else 5.0
    rod_len_ft = ceil_to(max(ld_in / 12.0 + p["projection_in"] / 12.0,
                             min_rod_ft), 0.25)
    ab_w = ((p["rod_w_lbft"] * rod_len_ft +
             p["template_count"] * (p["template_w1"] + p["template_w2"])) * n) * 1.02
    tpl_od, tpl_id = bcd + 6.0, bcd + 6.0 - p["template_id_drop"]
    tpl_w = (math.pi / 4.0 * STEEL_DENSITY_LBIN3 *
             (p["template_t1"] + p["template_t2"]) * (tpl_od ** 2 - tpl_id ** 2)) * 1.02
    cage_w = ab_w + tpl_w
    weights = {
        "gross_plate": gross_w, "id_hole": id_w, "bolt_holes": holes_w,
        "plate_final": plate_final, "rod_length_ft": rod_len_ft,
        "dev_length_in": ld_in, "gamma": gamma, "ab_weight": ab_w,
        "template_weight": tpl_w, "cage_total": cage_w,
    }
    steps.append({"step": "Weights",
                  "formula": "Gross plate = pi x (W/24)^2 x (t/12) x 490; "
                             "less ID hole and bolt holes; "
                             "rod length = CEILING(MAX(development length/12 + "
                             "projection/12, 6 ft for 18J bolts else 5 ft), 0.25); "
                             "AB weight = (rod lb/ft x length + templates x (4.38+0.89)) "
                             "x n x 1.02; template = pi/4 x 0.2836 x (t1+t2) x "
                             "(ODt^2 - IDt^2) x 1.02",
                  "explain": "Plate weight is the disc minus the centre hole and the bolt "
                             "holes. Anchor-cage weight is the rods (with development "
                             "length plus the 12 in. projection) plus the template ring, "
                             "with a 2% allowance.",
                  "inputs": {"plate width": width, "t_use": t_use, "ID": plate_id,
                             "hole dia": p["hole_dia"], "n": n,
                             "development length": round(ld_in, 2),
                             "minimum rod length (ft)": min_rod_ft,
                             "rod length": rod_len_ft},
                  "result": {"plate final": round(plate_final, 2),
                             "AB weight": round(ab_w, 2),
                             "template weight": round(tpl_w, 2),
                             "cage total": round(cage_w, 2)},
                  "unit": "lbs"})

    summary = {
        "bolt_circle_calc": minbc, "bolt_circle_use": bcd,
        "bolt_qty_calc": rods, "bolt_qty_use": n,
        "plate_width": width, "plate_id": plate_id,
        "plate_thick_calc": t_calc, "plate_thick_use": t_use,
        "t_req": t_req, "bend_line_width": bl_width,
        "bend_line_moments": bl_moments, "governing_moment": m_gov,
        "governing_case": gov_name, "floor_moment": floor_moment,
        "tapp_floor_estimate": tapp_est,
        "max_rod_force": max_force_all, "rod_capacity": rod_cap,
        "ab_usage": ab_usage, "bp_usage": bp_usage,
        "overall": overall, "checks": checks, "weights": weights,
        "spacing": spacing, "spacing_allow": spacing_allow,
    }
    return summary, steps, case_rows
