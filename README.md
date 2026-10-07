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
- **Admin** (`/admin`, email + password): global admins manage users, assign
  hosts to shifts, move shift times and edit any session. Hosts add and edit
  sessions only inside their own shifts.
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
3 shifts per day. Create the global admins from the command line (emails are
never hardcoded):

    flask --app app create-user --role admin      # prompts for email, name, password
    flask --app app create-user --role host
    flask --app app set-password --email someone@example.org
    flask --app app seed-demo                     # demo hosts + 12 demo sessions
    flask --app app seed-demo --reset             # replace the demo data

Demo users, sessions and matchmaking entries carry an `is_demo` flag and a
visible Demo badge; the public page shows a banner while any demo session
exists. `seed-demo --reset` removes them.

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

Covers auth gating, CSRF, host-vs-admin permissions, overlap blocking, the
5-minute gap warning and override, shift moves, matchmaking placement, ICS
validity (CRLF, 75-octet folding, required properties, UTC times) and the
public agenda render.

Deploying: see **DEPLOY.md**.
