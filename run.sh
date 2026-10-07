#!/bin/sh
# Dev server (Flask built-in). Production uses serve-prod.sh under systemd.
cd "$(dirname "$0")" || exit 1
if [ ! -d .venv ]; then
  python3 -m venv .venv && ./.venv/bin/pip install -q -r requirements.txt
fi
exec ./.venv/bin/python app.py
