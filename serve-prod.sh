#!/bin/sh
# Production launcher: gunicorn behind Caddy. Run by the systemd unit.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
./.venv/bin/pip install -q -r requirements.txt
exec ./.venv/bin/gunicorn -c gunicorn.conf.py app:app
