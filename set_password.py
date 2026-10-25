#!/usr/bin/env python3
"""Set the TAPP Base Plate app password (deploy-time helper).

Reads the password via getpass (never as a CLI argument, never echoed)
and writes ONLY the PBKDF2-HMAC-SHA256 hash + salt to data/config.json.
No password value is ever stored in code, docs, or data files.

Usage:  python3 set_password.py
"""
import getpass
import hashlib
import json
import os
import secrets
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")


def main():
    pw = getpass.getpass("New app password: ")
    pw2 = getpass.getpass("Confirm app password: ")
    if pw != pw2:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    if len(pw) < 4:
        print("Password too short (min 4 characters).", file=sys.stderr)
        return 1
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(),
                                 bytes.fromhex(salt), 120_000).hex()
    cfg["password_salt"] = salt
    cfg["password_hash"] = digest
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)
    print("Password hash saved to data/config.json (hash + salt only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
