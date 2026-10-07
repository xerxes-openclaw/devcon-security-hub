# Devcon 8 India: Security Hub

Website for the community-run Security Hub at Devcon 8 India (Jio World
Centre, Mumbai, 3 to 6 November 2026, 09:00 to 18:00 IST daily). Organized by
TheDAO Security Fund with the Ethereum security community. Proposal:
https://forum.devcon.org/t/devcon-8-india-community-hub-security-hub/8786

Community-run hub. Not an official Devcon or Ethereum Foundation page.

## What it does

- **Public agenda**: four day tabs, three host shifts per day, sessions inside
  each shift. Every session has an "Add to Google Calendar" link and a
  `.ics` download; `/agenda.ics` is the full feed. Times are IST.
- **Admin** (`/admin`, username + password; shared logins, no personal
  accounts, no email):
  - `team`: the organizers' shared login. Edits everything: shift times,
    host names, every session, matchmaking, and resets shift-login
    passwords on the Shift logins page (the new password is shown once).
  - 12 shift logins, one per host shift: `tue3-shift1` ... `fri6-shift3`
    (shift 1/2/3 = 09-12, 12-15, 15-18 IST). Each can add, edit and delete
    sessions only inside its own shift and set that shift's host name.
- **Host names**: free text per shift, shown publicly as "Host: <name>", or
  "Host: to be announced" while empty.
- **Validation**: sessions must fit inside their shift; overlaps anywhere on
  the same day are blocked; gaps under 5 minutes warn and need an explicit
  "save anyway" tick.
- **Matchmaking board** (`/admin/match`): open time per shift, plus content
  looking for a slot (added by admins and hosts, never public). Placing an
  entry creates the session.

## Run it

    ./run.sh                      # macOS/Linux
    .venv\Scripts\python app.py   # Windows, after creating the venv

Then open http://127.0.0.1:4590 (change with `PORT` in `.env`).

First run creates `.env` with a `SECRET_KEY` and `hub.db` with the default
3 shifts per day. Create the logins from the command line:

    flask --app app init-logins             # team + 12 shift logins
    flask --app app reset-login tue3-shift1 # new password for one login
    flask --app app seed-demo               # demo host names + 12 demo sessions
    flask --app app seed-demo --reset       # replace the demo data

`init-logins` is idempotent: it creates any missing login (and any missing
default shift), prints each NEW username with a random password once on
stdout, and leaves existing logins alone. Passwords are stored only as
hashes; nothing is hardcoded. A password reset logs out every browser still
using the old password.

Demo sessions and matchmaking entries carry an `is_demo` flag and a visible
Demo badge; the public page shows a banner while any demo session exists.
`seed-demo` fills empty host names with "Demo Host A/B/C";
`seed-demo --reset` removes the demo rows and those demo names.

**Upgrading an older `hub.db`** (personal accounts with email): the app
migrates it on start. It first copies the file to
`hub-pre-logins-<unixtime>.db`, keeps every shift, session and matchmaking
entry, copies each shift's old host name into the new host name field and
drops the old accounts. Then run `init-logins`. For a demo-only database
you can instead delete `hub.db` and run `init-logins` and `seed-demo`.

## Where to change things

- **Event facts** (dates, venue, day themes, links, organizers): `config.py`
  only. `EVENT_FACTS_VERIFIED` and its comment record where the dates came
  from.
- **Placeholders**: `TELEGRAM_URL`, `HUB_LOCATION`, `HANDOVER_NOTE` in `.env`.
- **Colors and fonts**: `static/theme.css` only (Devcon 8 palette from the
  devcon.org site code, Poppins + Inter).
- **Logo**: the official hub logo is `static/img/logo-full.jpg`. The header
  and favicon use a faithful SVG redraw of its shield mark (`LOGO_SVG` in
  `app.py`) until the source SVG arrives; swap that string when it does. The
  wordmark is rendered as text: "Security" 700, "Hub" 300.

## Tests

    .venv/bin/python -m unittest discover tests -v      # Windows: .venv\Scripts\python

Covers auth gating, CSRF, username login, team-vs-shift-login permissions
(sessions and host names), password reset shown once, the login CLI, the
old-schema migration, overlap blocking, the
5-minute gap warning and override, shift moves, matchmaking placement, ICS
validity (CRLF, 75-octet folding, required properties, UTC times) and the
public agenda render.

Deploying: see **DEPLOY.md**.
