# Deploying the Security Hub site

Same shape as thedao-rfps: a small Linux VPS, gunicorn under systemd, Caddy
in front for HTTPS, SQLite on disk. Steps only you can do are marked **[you]**.

## 0. Decisions **[you]**

- **Domain**: pick one and point its `A` record at the server.
- **Who gets which login**: the organizers share the `team` login; each
  shift host gets their shift's login (`tue3-shift1` ... `fri6-shift3`).
  No emails or personal accounts.
- **Placeholders**: Telegram invite link and hub floor/room, once known.

## 1. Server setup (Ubuntu/Debian)

```sh
adduser --disabled-password --gecos "" hub
apt update && apt install -y python3-venv git caddy
```

## 2. Code + configuration

```sh
su - hub
git clone <repo-url> devcon-security-hub
cd devcon-security-hub
cp .env.example .env
nano .env        # SITE_URL, TELEGRAM_URL, HUB_LOCATION
chmod 600 .env
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
```

## 3. Create the logins

```sh
./.venv/bin/flask --app app init-logins
```

This prints `team` and the 12 shift usernames, each with a random password,
ONCE. Copy them straight into your password manager and hand each shift its
own; they are stored only as hashes. Running it again creates only missing
logins. To replace one password (for example after a host leaves):

```sh
./.venv/bin/flask --app app reset-login wed4-shift2
```

The team can also reset any shift login from **Shift logins** in the admin.
A reset logs out everyone still using the old password.

Upgrading a server that ran the older email-account version: just deploy;
the app backs up `hub.db` to `hub-pre-logins-<unixtime>.db`, migrates it on
start (shifts, sessions and host names kept, old accounts dropped), then run
`init-logins`.

Do NOT run `seed-demo` on production unless you want demo content visible.
If you did, `flask --app app seed-demo --reset` and then delete the demo
rows via the admin UI, or start from a fresh `hub.db`.

## 4. systemd

```sh
sudo cp /home/hub/devcon-security-hub/deploy/devcon-security-hub.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now devcon-security-hub
journalctl -u devcon-security-hub -f
```

## 5. HTTPS with Caddy

```sh
sudo cp /home/hub/devcon-security-hub/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile      # set your domain
sudo systemctl reload caddy
```

## 6. Backups

```sh
crontab -e -u hub
0 4 * * *  /home/hub/devcon-security-hub/deploy/backup.sh >> /home/hub/backup.log 2>&1
```

## 7. Check

- `https://your-domain/healthz` returns `{"ok": true, ...}`.
- Log in at `/admin/login` as `team` and fill in the host name on all 12
  shifts (or let each shift login set its own).
- Import `https://your-domain/agenda.ics` into a calendar and confirm one
  session lands at the right IST time.

## Updating

```sh
su - hub && cd devcon-security-hub
git pull && sudo systemctl restart devcon-security-hub
```

No CI yet (no remote exists). When the repo gets one, copy the thedao-rfps
workflow: tests, then SSH deploy with automatic rollback.
