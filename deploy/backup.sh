#!/bin/sh
# Nightly SQLite backup with 14-day retention. Cron:
# 0 4 * * *  /home/hub/devcon-security-hub/deploy/backup.sh >> /home/hub/backup.log 2>&1
set -e
cd "$(dirname "$0")/.."
mkdir -p "$HOME/hub-backups"
./.venv/bin/python -c "import sqlite3,sys; s=sqlite3.connect('hub.db'); d=sqlite3.connect(sys.argv[1]); s.backup(d)" \
  "$HOME/hub-backups/hub-$(date +%Y%m%d-%H%M%S).db"
find "$HOME/hub-backups" -name 'hub-*.db' -mtime +14 -delete
