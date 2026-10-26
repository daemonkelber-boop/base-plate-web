# Base Plate — Web Calculator

A zero-dependency web app (Python standard library only, no pip installs,
no build step) that replicates the Base Plate Excel
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
- **Engine** (`base_plate_calc.py`, separate from the server): bolt-circle
  sizing rules, bolt positions, elastic bolt-group force distribution,
  governing case plus the 50% shaft-capacity floor, bend-line effective
  width, governing required thickness rounded up to 0.25 in and floored
  at 1.5 in, per-case usage = (t_min_case / t_use)² zeroed when the
  strength factor is 0, the Valid/Error check block, and weights.
  Where the Basis document flags a workbook-specific rule vs ASCE 48-19,
  the app follows the workbook.
- **A572-42 thick-plate rule (user rule, not the Excel sheet's):**
  when the "Use A572-42 for plates over 4 in" box is on (the
  default), and the calculated thickness is over 4 in, the design
  is redone at 42 ksi as A572-42 (t_req scales by SQRT(old yield /
  42)), and the summary shows the grade actually used. It does not
  apply when the selected yield is already 42 ksi or lower, and
  exactly 4.0 in does not trigger it. Turn the box off to keep the
  original grade's thickness. A572-42 is also selectable directly
  in the grade list.
- **Layout:** sheet-style, like the Excel structure sheet — Pole
  Geometry | Anchor Bolt Data | Base Plate Properties | Checks &
  Weights side by side, a summary sentence, then the load-case table.
  Yellow cells are inputs, grey cells are calculated. Header fields
  (Quote / Customer / Project / Item) are labels only.
- **Formulas visible:** every calculation step is shown collapsed
  (like clicking a cell in Excel) with the actual formula used AND a
  plain-language explanation, with its input values and computed
  result beside it. No black-box numbers.
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

## PLS-POLE XML import

Upload a PLS-POLE `.xml` export and the form is prefilled with all
load cases from the file, then the base plate design is calculated
automatically — no Calculate press is needed after an upload.
Changing the pole/leg or reaction-set picker recalculates the same
way. The Calculate button remains for after manual edits.

- **Endpoint:** `POST /api/import-xml` (login required), body is
  `multipart/form-data` with the file in the field named `file`.
  Example: `curl -b cookies.txt -F file=@pole.xml
  http://127.0.0.1:8512/api/import-xml`. Files over 20 MB, files
  without a `.xml` name, unparseable files, and files with no steel
  pole properties or no base reactions are rejected with a plain
  error message, never a crash.
- **What is extracted, per pole/leg:** shape/sides (leading digits of
  the PLS shape, e.g. `12F` → 12), base OD (base tube's bottom
  diameter), base wall and yield (base tube = highest tube number),
  shaft moment capacity (see below), bolt diameter and plate yield
  from the model's existing base plate, if it has one, and the full
  load-case table from the base support reactions.
- **Load-case mapping** (forces in kips, moments in ft-k, PLS-POLE
  convention): case name = the reaction table's load case description;
  axial = −z_force (compression positive); shear =
  hypot(x_force, y_force); mx = x_moment, my = y_moment,
  torsion = z_moment; strength factor =
  `sf_for_steel_poles_tubular_arms_and_towers` for that case.
  Cases named INSULATOR are carried at SF 0, matching the STR017
  sheet (both insulator cases there have SF 0); deflection cases keep
  their PLS strength factor.
- **Shaft moment capacity:** the median over cases of
  hypot(trans_mom, long_mom) / (m_s / 100) at the base joint in the
  detailed steel pole usages table. m_s is the bending-only usage,
  so the ratio is the section moment capacity and is
  case-independent (verified: ~4205 ft-k for the 002 sample,
  ~4964 ft-k for 013). If the file has no usable base usage rows,
  the capacity is left for the user to enter — it sets the 50%
  floor, so check it before calculating.
- **Multi-leg files:** legs are matched to their base reactions by
  the base joint label (`<pole_label>:g`, via the connectivity
  table). The UI shows a pole picker; if a leg cannot be matched
  unambiguously, it also shows a reaction-set picker and the user
  must choose — a leg is never silently paired with the wrong
  reactions. A 3-pole file therefore designs one leg at a time,
  chosen by the user.
- **Not taken from the XML (left at their current form values):**
  bolt grade, bolt yield/Fu, tensile/gross areas, nut width, hole
  diameter, bolt-at-0°, plate edge/minimum thickness, and template
  weights. The model's existing plate diameter, thickness, bolt
  circle, and bolt count are shown for comparison only and are
  never applied as overrides, so the app designs fresh unless the
  user sets an override. Plate shape is only mapped when the PLS
  value is `0` (→ Round); other values are left unchanged.
- **Units assumption:** PLS-POLE exports do not label units on the
  reaction rows used here; kips and ft-k are assumed per PLS-POLE
  convention and are consistent with the STR017 sheet's case table.

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

Note: you may clone the repo anywhere (e.g.
`~/Documents/Projects/base-plate-web`). `install-mini-services.sh`
writes that actual folder into the launchd jobs — do not hand-edit
the plist templates, and re-run the install script if you ever move
the folder, or launchd will keep pointing at the old location.

- **Sharing:** `tailscale funnel --bg 8512` on the mini (verify Funnel
  is allowed on the tailnet), then share the Funnel link. Anyone on
  the internet can reach the login page; the password gate is the
  protection.
- **Later code changes:** push to GitHub and the mini picks them up
  within ~5 minutes. To pull immediately: `./scripts/update-from-github.sh`
- **Logs on the mini:** `<repo-folder>/logs/app.log` and
  `<repo-folder>/logs/update.log`

The server binds **127.0.0.1:8512** (override with `BASE_PLATE_PORT`).
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
  `/api/validation`, `/api/sample/str017`, `/api/import-xml`).
- `base_plate_calc.py` — the calculation engine (pure stdlib, no I/O).
- `xml_import.py` — PLS-POLE XML parsing (stdlib xml.etree only);
  maps an export onto the app's input fields, per leg.
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
