"""Development evaluation log for XML-upload calculations.

Purpose: during development/testing, every calculation that follows an
XML upload is recorded — the inputs that were actually evaluated and
the results produced — so a later test session can ask, per file /
pole / load case, what was tested and what came out.

Storage: SQLite, Python standard library only. The database lives in
data/evaluations.db next to data/config.json. data/ is git-ignored,
so evaluations are never committed, never pushed to GitHub, and never
touched by a pull — they stay on the machine that ran the uploads.
Delete data/evaluations.db to start the log over.

Schema:
  evaluations      one row per upload-triggered calculation
  evaluation_cases one row per load case of that calculation

Logging must never break a calculation: the server catches any
database error, reports it to stderr, and still returns the result.
Only upload-triggered calculations are logged. Manual calculations,
the STR017 sample, and validation runs are not logged, so the log
stays a clean record of real XML files.
"""

import json
import os
import sqlite3
import time

DB_NAME = "evaluations.db"

# Summary columns kept as real columns (not just JSON) so a test
# session can query them directly, e.g.:
#   SELECT overall, COUNT(*) FROM evaluations GROUP BY overall;
#   SELECT file_name, leg_label, plate_thick_use, bp_usage
#     FROM evaluations WHERE bp_usage > 1 ORDER BY bp_usage DESC;
#   SELECT case_name, AVG(usage) FROM evaluation_cases GROUP BY case_name;
EVAL_COLUMNS = (
    "created_at", "source", "file_name", "file_sha256", "file_size",
    "leg_label", "reaction_label",
    "sides", "base_od", "wall", "pole_yield", "shaft_capacity_ftk",
    "bolt_dia", "bolt_grade",
    "plate_grade_used", "plate_yield_used", "a572_42_applied",
    "bolt_qty_use", "bolt_circle_use", "plate_width", "plate_id",
    "plate_thick_calc", "plate_thick_use", "t_req",
    "governing_case", "governing_moment", "floor_moment",
    "max_rod_force", "rod_capacity", "ab_usage", "bp_usage",
    "overall", "error",
)

CASE_COLUMNS = (
    "case_name", "axial", "shear", "mx", "my", "torsion", "sf",
    "mx_used", "my_used", "m_raw",
    "max_comp", "max_ten", "max_shear", "max_force",
    "t_min", "usage", "bend_stress",
)


def db_path(data_dir):
    return os.path.join(data_dir, DB_NAME)


def init_db(data_dir):
    os.makedirs(data_dir, exist_ok=True)
    conn = sqlite3.connect(db_path(data_dir), timeout=10)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS evaluations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "created_at TEXT NOT NULL, source TEXT NOT NULL,"
            "file_name TEXT, file_sha256 TEXT, file_size INTEGER,"
            "leg_label TEXT, reaction_label TEXT,"
            "sides INTEGER, base_od REAL, wall REAL, pole_yield REAL,"
            "shaft_capacity_ftk REAL, bolt_dia REAL, bolt_grade TEXT,"
            "plate_grade_used TEXT, plate_yield_used REAL,"
            "a572_42_applied INTEGER,"
            "bolt_qty_use INTEGER, bolt_circle_use REAL,"
            "plate_width REAL, plate_id REAL,"
            "plate_thick_calc REAL, plate_thick_use REAL, t_req REAL,"
            "governing_case TEXT, governing_moment REAL, floor_moment REAL,"
            "max_rod_force REAL, rod_capacity REAL,"
            "ab_usage REAL, bp_usage REAL, overall TEXT, error TEXT,"
            "inputs_json TEXT, summary_json TEXT)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS evaluation_cases ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "evaluation_id INTEGER NOT NULL REFERENCES evaluations(id),"
            "case_name TEXT, axial REAL, shear REAL, mx REAL, my REAL,"
            "torsion REAL, sf REAL, mx_used REAL, my_used REAL, m_raw REAL,"
            "max_comp REAL, max_ten REAL, max_shear REAL, max_force REAL,"
            "t_min REAL, usage REAL, bend_stress REAL)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_eval_cases_eval "
            "ON evaluation_cases(evaluation_id)")
        conn.commit()
    finally:
        conn.close()


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def log_evaluation(data_dir, context, inputs, summary, cases, error=None):
    """Insert one evaluation; returns its id. inputs is the cleaned
    input dict the engine actually used; summary/cases are the engine
    outputs (either may be None when the calculation failed)."""
    init_db(data_dir)
    ctx = context if isinstance(context, dict) else {}
    s = summary if isinstance(summary, dict) else {}
    w = s.get("weights") if isinstance(s.get("weights"), dict) else {}
    row = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "xml_upload",
        "file_name": ctx.get("file_name"),
        "file_sha256": ctx.get("file_sha256"),
        "file_size": ctx.get("file_size"),
        "leg_label": ctx.get("leg_label"),
        "reaction_label": ctx.get("reaction_label"),
        "sides": inputs.get("sides") if isinstance(inputs, dict) else None,
        "base_od": _num(inputs.get("base_od")) if isinstance(inputs, dict) else None,
        "wall": _num(inputs.get("wall")) if isinstance(inputs, dict) else None,
        "pole_yield": _num(inputs.get("pole_yield")) if isinstance(inputs, dict) else None,
        "shaft_capacity_ftk": _num(inputs.get("shaft_capacity_ftk")) if isinstance(inputs, dict) else None,
        "bolt_dia": _num(inputs.get("bolt_dia")) if isinstance(inputs, dict) else None,
        "bolt_grade": inputs.get("bolt_grade") if isinstance(inputs, dict) else None,
        "plate_grade_used": s.get("plate_grade_used"),
        "plate_yield_used": _num(s.get("plate_yield_used")),
        "a572_42_applied": 1 if s.get("a572_42_applied") else 0,
        "bolt_qty_use": s.get("bolt_qty_use"),
        "bolt_circle_use": _num(s.get("bolt_circle_use")),
        "plate_width": _num(s.get("plate_width")),
        "plate_id": _num(s.get("plate_id")),
        "plate_thick_calc": _num(s.get("plate_thick_calc")),
        "plate_thick_use": _num(s.get("plate_thick_use")),
        "t_req": _num(s.get("t_req")),
        "governing_case": s.get("governing_case"),
        "governing_moment": _num(s.get("governing_moment")),
        "floor_moment": _num(s.get("floor_moment")),
        "max_rod_force": _num(s.get("max_rod_force")),
        "rod_capacity": _num(s.get("rod_capacity")),
        "ab_usage": _num(s.get("ab_usage")),
        "bp_usage": _num(s.get("bp_usage")),
        "overall": s.get("overall"),
        "error": error,
    }
    # weights are not columns; they travel inside summary_json.
    _ = w
    conn = sqlite3.connect(db_path(data_dir), timeout=10)
    try:
        cols = ", ".join(EVAL_COLUMNS + ("inputs_json", "summary_json"))
        placeholders = ", ".join("?" * (len(EVAL_COLUMNS) + 2))
        cur = conn.execute(
            "INSERT INTO evaluations (%s) VALUES (%s)" % (cols, placeholders),
            [row[c] for c in EVAL_COLUMNS] + [
                json.dumps(inputs) if inputs is not None else None,
                json.dumps(summary) if summary is not None else None])
        eval_id = cur.lastrowid
        for c in cases or []:
            conn.execute(
                "INSERT INTO evaluation_cases (evaluation_id, %s) VALUES (%s)"
                % (", ".join(CASE_COLUMNS),
                   ", ".join(["?"] * (len(CASE_COLUMNS) + 1))),
                [eval_id] + [
                    c.get("name"),
                    _num(c.get("axial")), _num(c.get("shear")),
                    _num(c.get("mx")), _num(c.get("my")),
                    _num(c.get("torsion")), _num(c.get("sf")),
                    _num(c.get("mx_used")), _num(c.get("my_used")),
                    _num(c.get("m_raw")),
                    _num(c.get("max_comp")), _num(c.get("max_ten")),
                    _num(c.get("max_shear")), _num(c.get("max_force")),
                    _num(c.get("t_min")), _num(c.get("usage")),
                    _num(c.get("bend_stress"))])
        conn.commit()
        return eval_id
    finally:
        conn.close()


def list_evaluations(data_dir, limit=200):
    """Newest first, summary columns only (no JSON blobs)."""
    init_db(data_dir)
    conn = sqlite3.connect(db_path(data_dir), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        cols = "id, " + ", ".join(EVAL_COLUMNS)
        rows = conn.execute(
            "SELECT %s FROM evaluations ORDER BY id DESC LIMIT ?" % cols,
            (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_evaluation(data_dir, eval_id):
    """Full record: summary columns plus inputs, summary and cases."""
    init_db(data_dir)
    conn = sqlite3.connect(db_path(data_dir), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM evaluations WHERE id = ?", (int(eval_id),)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["inputs"] = json.loads(out.pop("inputs_json")) if out.get("inputs_json") else None
        out["summary"] = json.loads(out.pop("summary_json")) if out.get("summary_json") else None
        out["cases"] = [dict(r) for r in conn.execute(
            "SELECT %s FROM evaluation_cases WHERE evaluation_id = ? "
            "ORDER BY id" % ", ".join(CASE_COLUMNS),
            (int(eval_id),)).fetchall()]
        return out
    finally:
        conn.close()
