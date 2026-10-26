#!/usr/bin/env python3
"""TAPP Base Plate web app — replicates the TAPP Base Plate V2.01 Excel
calculations with every formula shown and explained.

Zero-dependency: Python standard library only. Run:
    python3 server.py        (then open http://localhost:8512)

Deployment notes (see README.md):
- Binds 127.0.0.1 only. Sharing is via Tailscale Funnel, which terminates
  TLS and proxies to this local port; the app itself needs no LAN port.
- Password gate: the server reads ONLY a PBKDF2-HMAC-SHA256 hash + salt
  from data/config.json. No password value is ever stored in code, docs,
  or data files. If no hash exists, only a setup page is served; use
  set_password.py (getpass, never a CLI argument) to create the hash.
"""
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tapp_calc
import xml_import

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
DATA_DIR = os.path.join(APP_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
PORT = int(os.environ.get("TAPP_PORT", "8512"))
HOST = "127.0.0.1"

SESSIONS = set()


def load_config():
    cfg = {"password_hash": "", "password_salt": ""}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(),
                               bytes.fromhex(salt), 120_000).hex()


def load_validation():
    """Merge all validation_*.json datasets in the app dir."""
    out = {}
    for name in sorted(os.listdir(APP_DIR)):
        if name.startswith("validation_") and name.endswith(".json"):
            try:
                with open(os.path.join(APP_DIR, name)) as f:
                    out.update(json.load(f))
            except Exception:
                pass
    return out


def check_value(app, expected, tol=0.005):
    """Compare two numbers; returns (pass, diff_pct or None)."""
    if expected is None or app is None:
        return False, None
    if expected == 0:
        return (abs(app) < 1e-9), (abs(app) * 100.0 if app else 0.0)
    diff = (app - expected) / abs(expected) * 100.0
    return abs(diff) <= tol * 100.0, diff


def run_validation():
    datasets = load_validation()
    report = {"samples": []}
    for key, ds in datasets.items():
        entry = {"key": key, "label": ds.get("label", key)}
        if "inputs" in ds and "expected" in ds:
            # full recompute
            summary, _steps, case_rows = tapp_calc.compute(
                tapp_calc.clean_inputs(ds["inputs"]))
            exp = ds["expected"]
            checks = []
            mapping = [
                ("bolt_circle_calc", summary["bolt_circle_calc"]),
                ("bolt_circle_use", summary["bolt_circle_use"]),
                ("bolt_qty_calc", summary["bolt_qty_calc"]),
                ("bolt_qty_use", summary["bolt_qty_use"]),
                ("plate_width", summary["plate_width"]),
                ("plate_id", summary["plate_id"]),
                ("plate_thick_calc", summary["plate_thick_calc"]),
                ("plate_thick_use", summary["plate_thick_use"]),
                ("t_req", summary["t_req"]),
                ("bend_line_width", summary["bend_line_width"]),
                ("governing_moment", summary["governing_moment"]),
                ("floor_moment", summary["floor_moment"]),
                ("max_rod_force", summary["max_rod_force"]),
                ("rod_capacity", summary["rod_capacity"]),
                ("ab_usage", summary["ab_usage"]),
                ("bp_usage", summary["bp_usage"]),
                ("spacing", summary["spacing"]),
                ("plate_final_weight", summary["weights"]["plate_final"]),
                ("ab_weight", summary["weights"]["ab_weight"]),
                ("template_weight", summary["weights"]["template_weight"]),
                ("cage_total", summary["weights"]["cage_total"]),
                ("rod_length_ft", summary["weights"]["rod_length_ft"]),
            ]
            for name, app_val in mapping:
                if name in exp:
                    ok, diff = check_value(app_val, exp[name])
                    checks.append({"name": name, "app": app_val,
                                   "expected": exp[name], "diff_pct": diff,
                                   "pass": ok})
            # overall verdict string
            if "overall" in exp:
                checks.append({"name": "overall", "app": summary["overall"],
                               "expected": exp["overall"],
                               "diff_pct": None,
                               "pass": summary["overall"] == exp["overall"]})
            # per-case comparison (max bolt / t_min / usage)
            exp_cases = {c["name"]: c for c in ds["inputs"].get("cases", [])
                         if "expected_max_bolt" in c}
            case_checks = []
            worst = 0.0
            for r in case_rows:
                e = exp_cases.get(r["name"])
                if not e:
                    continue
                for field, app_v, exp_v in (
                        ("max_bolt", r["max_force"], e["expected_max_bolt"]),
                        ("t_min", r["t_min"], e["expected_t_min"]),
                        ("usage", r["usage"], e["expected_usage"])):
                    ok, diff = check_value(app_v, exp_v)
                    if diff is not None and abs(diff) > worst:
                        worst = abs(diff)
                    if not ok:
                        case_checks.append({"case": r["name"], "field": field,
                                            "app": app_v, "expected": exp_v,
                                            "diff_pct": diff})
            entry["type"] = "recompute"
            entry["checks"] = checks
            entry["case_mismatches"] = case_checks[:25]
            entry["case_count"] = len(exp_cases)
            entry["worst_case_diff_pct"] = worst
            entry["passed"] = sum(1 for c in checks if c["pass"])
            entry["total"] = len(checks)
        else:
            entry["type"] = "reference"
            entry["cached"] = ds.get("cached", {})
            entry["note"] = ds.get("note", "")
        report["samples"].append(entry)
    return report


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[tapp] " + (fmt % args) + "\n")

    # -- plumbing
    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, body, status=200, ctype="text/html"):
        raw = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_file(self, path):
        if not os.path.exists(path):
            return self.send_json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def read_multipart_file(self):
        """Return (filename, bytes) for the form field named 'file'.

        Small hand-rolled parser: no new dependencies, and it never
        writes the upload to disk. Raises ValueError on bad input.
        """
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
        if not m:
            raise ValueError("expected a multipart file upload")
        boundary = ("--" + (m.group(1) or m.group(2)).strip()).encode()
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            raise ValueError("empty upload: no file received")
        body = self.rfile.read(length)
        parts = body.split(boundary)
        for part in parts:
            if b'name="file"' not in part:
                continue
            head, _, payload = part.partition(b"\r\n\r\n")
            if not payload:
                continue
            fm = re.search(br'filename="([^"]*)"', head)
            filename = (fm.group(1).decode("utf-8", "replace")
                        if fm else "")
            # Strip the trailing CRLF that precedes the next boundary.
            if payload.endswith(b"\r\n"):
                payload = payload[:-2]
            if not filename:
                raise ValueError("no file selected for upload")
            if not filename.lower().endswith(".xml"):
                raise ValueError(
                    "expected a .xml file, got '%s'" % filename)
            if not payload.strip():
                raise ValueError("uploaded file '%s' is empty" % filename)
            return filename, payload
        raise ValueError("no file part found in the upload")

    def authed(self):
        cfg = load_config()
        if not cfg.get("password_hash"):
            return False
        cookie = self.headers.get("Cookie", "")
        m = re.search(r"tapp_session=([a-f0-9]+)", cookie)
        return bool(m and m.group(1) in SESSIONS)

    def require_auth(self):
        if self.authed():
            return True
        self.send_json({"error": "login required", "login": True}, 401)
        return False

    # -- routes
    def do_GET(self):
        path = self.path.split("?")[0]
        cfg = load_config()
        if path == "/" or path == "/index.html":
            if not cfg.get("password_hash"):
                return self.send_file(os.path.join(STATIC_DIR, "setup.html"))
            if not self.authed():
                return self.send_file(os.path.join(STATIC_DIR, "login.html"))
            return self.send_file(os.path.join(STATIC_DIR, "index.html"))
        if path == "/api/state":
            return self.send_json({"has_password": bool(cfg.get("password_hash")),
                                   "authed": self.authed()})
        if path == "/api/validation":
            if not self.require_auth():
                return
            try:
                return self.send_json(run_validation())
            except Exception as e:
                return self.send_json({"error": str(e)}, 500)
        if path == "/api/sample/str017":
            if not self.require_auth():
                return
            ds = load_validation().get("str017", {})
            return self.send_json({"inputs": ds.get("inputs", {})})
        return self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/login":
            body = self.read_json()
            cfg = load_config()
            if not cfg.get("password_hash"):
                return self.send_json({"ok": True, "setup": True})
            digest = hash_password(str(body.get("password", "")),
                                   cfg["password_salt"])
            if hmac.compare_digest(digest, cfg["password_hash"]):
                token = secrets.token_hex(24)
                SESSIONS.add(token)
                out = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", "tapp_session=" + token +
                                 "; Path=/; HttpOnly; SameSite=Lax")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return
            return self.send_json({"error": "wrong password"}, 403)
        if path == "/api/setup":
            cfg = load_config()
            if cfg.get("password_hash"):
                return self.send_json({"error": "password already set"}, 403)
            body = self.read_json()
            pw = str(body.get("password", ""))
            if len(pw) < 4:
                return self.send_json({"error": "password too short"}, 400)
            salt = secrets.token_hex(16)
            cfg["password_salt"] = salt
            cfg["password_hash"] = hash_password(pw, salt)
            save_config(cfg)
            return self.send_json({"ok": True})
        if not self.require_auth():
            return
        if path == "/api/import-xml":
            try:
                filename, data = self.read_multipart_file()
                result = xml_import.parse_pls_xml(data)
                result["file_name"] = filename
                return self.send_json(result)
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
            except Exception as e:
                return self.send_json(
                    {"error": "could not import XML: " + str(e)}, 400)
        if path == "/api/calculate":
            try:
                body = self.read_json()
                summary, steps, case_rows = tapp_calc.compute(
                    tapp_calc.clean_inputs(body))
                return self.send_json({"summary": summary, "steps": steps,
                                       "cases": case_rows})
            except Exception as e:
                return self.send_json({"error": str(e)}, 500)
        return self.send_json({"error": "not found"}, 404)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("TAPP Base Plate app running at http://%s:%d" % (HOST, PORT), flush=True)
    print("Share via Tailscale Funnel pointing at this local port.", flush=True)
    print("Data folder: " + DATA_DIR, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
