"""Gunicorn config for production. Launched by serve-prod.sh.

Light traffic and SQLite: one worker with a few threads is plenty.
"""
# Leading underscore: gunicorn treats bare top-level names as settings and has
# a setting called `config`.
import config as _cfg

_host = _cfg.ENV.get("BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
bind = "%s:%d" % (_host, _cfg.PORT)
workers = 1
threads = 4
timeout = 30
graceful_timeout = 20
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = "info"
