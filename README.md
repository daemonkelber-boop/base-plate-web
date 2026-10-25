# TAPP Base Plate — Web Calculator

A zero-dependency web app (Python standard library only, no pip installs,
no build step) that replicates the TAPP Base Plate V2.01 Excel
calculations: given a pole's base loads, it designs/checks the base plate
and anchor bolts and gives a YES/NO verdict per pole.

## What it does

- **Inputs:** pole geometry (shape, base OD, wall, yield, PLS shaft
  moment capacity and floor %), anchor bolts (size, grade, yield, Fu,
  tensile/gross area, nut width, hole dia, bolt-at-0°, optional count and
  bolt-circle overrides), base plate (round / 12-sided / square, grade,
  yield, edge allowance, minimum thickness, optional thickness override),
  strength factors, and an editable load-case table (axial, shear,
  transverse/longitudinal moment, torsion, PLS strength factor per case).
- **Engine** (`tapp_calc.py`, separate from the server): bolt-circle
  sizing rules, bolt positions, elastic bolt-group force distribution,
  governing case plus the 50% shaft-capacity floor, bend-line effective
  width, governing required thickness rounded up to 0.25 in and floored
  at 1.5 in, per-case usage = (t_min_case / t_use)² zeroed when the
  strength factor is 0, the Valid/Error check block, and weights.
  Where the Basis document flags a TAPP-specific rule vs ASCE 48-19,
  the app follows TAPP.
- **Formulas visible:** every calculation step on the results page
  shows the actual formula used AND a plain-language explanation, with
  its input values and computed result beside it. No black-box numbers.
- **Validation panel:** compares app outputs against the Drive
  workbook's cached values (tolerance 0.5%) and reports pass/fail per
  value, honestly — mismatches are listed, never fudged.

## Validation status

- **STR017 (full recompute):** all 23 summary values and all 47
  per-case rows match the workbook essentially exactly (worst
  difference ~1e-13%). Plate usage 0.838 at 1.75 in plate, max rod
  force 187.65 kips, rod capacity 243.67 kips, overall OK.
- **STR057 LPOL (full recompute):** see the validation panel in the
  app for the per-value results. Workbook targets: plate usage 0.78
  at 1.5 in plate, bolt usage 0.42.
- **Other 23 structure sheets (reference only):** the panel shows
  each sheet's cached top-block values. Their full input sets
  (load-case tables, shaft capacities) were not extracted, so they
  are not recomputed — that is a data-extraction gap, not a pass.

## Not yet covered

- **EBC / IBC second bolt circle:** single-bolt-circle (SBC) sheets
  only. All 25 Drive sheets are SBC, so this does not affect the
  validation set.
- The AE5 threshold (13.3046°) in the bolt-circle AD branch was
  taken from the STR017 sheet; it is treated as a constant.
- Rod-length minimum branches were implemented via the ASCE
  development-length route validated on STR017; see the validation
  panel for how it lands on STR057 LPOL.

## Run it

```sh
cd base-plate-web
python3 server.py
```

## Launch from Finder (Mac mini)

Double-click **`Launch Base Plate Web.command`** in the `base-plate-web`
folder. If the app is already running (e.g. via the launchd service
below), it just opens the app in your browser. If it is not running,
it starts the app in a Terminal window and opens the browser — keep
that Terminal window open while you use the app, closing it stops the
app. You can also drag the `.command` file to the Dock for one-click
access. First time macOS may ask you to confirm opening it; that is
normal for a script file.

## Deploy on the Mac mini (GitHub pull flow)

Code never goes directly to the mini. It lives in the private GitHub
repo `daemonkelber-boop/base-plate-web`, and the mini pulls it:

- **One-time setup, in a Terminal on the mini** (first clone needs
  GitHub sign-in; the keychain then serves the automatic pulls):

```sh
cd ~
git clone https://github.com/daemonkelber-boop/base-plate-web.git
cd base-plate-web
python3 set_password.py
./scripts/install-mini-services.sh
```

That loads two launchd jobs: the app itself (always-on, port 8512)
and an updater that runs every 5 minutes, pulls fast-forward-only
from GitHub, syntax-checks the new code (rolling back on failure),
and restarts the app when code changed. Local edits to tracked files
block a pull; `data/config.json` (the password hash) and `logs/` are
git-ignored and are never touched by a pull.

- **Sharing:** `tailscale funnel --bg 8512` on the mini (verify Funnel
  is allowed on the tailnet), then share the Funnel link. Anyone on
  the internet can reach the login page; the password gate is the
  protection.
- **Later code changes:** push to GitHub and the mini picks them up
  within ~5 minutes. To pull immediately: `./scripts/update-from-github.sh`
- **Logs on the mini:** `~/base-plate-web/logs/app.log` and
  `~/base-plate-web/logs/update.log`

The server binds **127.0.0.1:8512** (override with `TAPP_PORT`).
It is meant to be shared via **Tailscale Funnel**: Funnel
terminates TLS and proxies to this local port, so the app needs no
LAN exposure. Example (on the host running the app):

```sh
tailscale funnel --bg 8512
```

## Password gate

The app requires a password before serving any page. **No password
value exists anywhere in this code, docs, or data files.** The server
reads only a PBKDF2-HMAC-SHA256 hash + salt from `data/config.json`
(which is git-ignored and must never be committed).

- First run with no `data/config.json`: only a setup page is
  served, letting the deployer set a password (hash + salt only).
- Preferred: set it from the terminal with the helper (reads the
  password via getpass — never as a CLI argument, never echoed):

```sh
python3 set_password.py
```

To change the password later, just run the helper again; it
merges the new hash + salt into `data/config.json` without
touching other keys.

## Files

- `server.py` — HTTP server, password gate, API routes
  (`/api/state`, `/api/login`, `/api/setup`, `/api/calculate`,
  `/api/validation`, `/api/sample/str017`).
- `tapp_calc.py` — the calculation engine (pure stdlib, no I/O).
- `static/index.html` — input form, results with visible formulas,
  per-case table, checks table, validation panel.
- `static/login.html`, `static/setup.html` — gate pages.
- `validation_str017.json`, `validation_str057_lpol.json` —
  full-recompute datasets (inputs + workbook cached values).
- `validation_others.json` — reference-only cached values for the
  other structure sheets.
- `set_password.py` — deploy-time password helper (hash + salt only).
- `data/` — runtime only (git-ignored); holds `config.json`.

## Source of truth

The calculation chain was implemented from the Drive workbook's
per-structure sheets (cached values + formulas, read-only — no
macros were ever run) and the Basis of Design document. The
workbook itself is not modified by this app.
