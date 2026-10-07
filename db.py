"""SQLite storage. stdlib sqlite3, WAL mode, prepared statements throughout.

Times are stored as minutes since midnight IST plus a day number (1-4); the
calendar date for each day lives in config.DAYS.
"""
import os
import sqlite3
import time
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS shifts(
  id INTEGER PRIMARY KEY,
  day INTEGER NOT NULL CHECK(day BETWEEN 1 AND 4),
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  host_name TEXT NOT NULL DEFAULT '',
  CHECK(end_min > start_min)
);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('team','shift')),
  shift_id INTEGER UNIQUE REFERENCES shifts(id) ON DELETE SET NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY,
  shift_id INTEGER NOT NULL REFERENCES shifts(id) ON DELETE RESTRICT,
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  title TEXT NOT NULL,
  type TEXT NOT NULL,
  speakers TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  is_demo INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  updated_at INTEGER NOT NULL,
  CHECK(end_min > start_min)
);
CREATE TABLE IF NOT EXISTS backlog(
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  who TEXT NOT NULL DEFAULT '',
  type TEXT NOT NULL,
  duration_min INTEGER NOT NULL CHECK(duration_min > 0),
  contact TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  is_demo INTEGER NOT NULL DEFAULT 0,
  session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at INTEGER NOT NULL
);
"""
# users holds shared logins, not people: one 'team' login (global admin) and
# one 'shift' login per host shift, linked to that shift by shift_id.

USERS_DDL = [x for x in SCHEMA.split(";") if "EXISTS users(" in x][0]


@contextmanager
def connect():
    """Short-lived connection: commit on success, always close."""
    con = sqlite3.connect(config.DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _columns(con, table):
    return [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]


def migrate_personal_accounts():
    """One-way migration from the old per-person schema (users with email,
    shifts.host_id) to shared logins (shifts.host_name, users.username).

    Backs the file up first (<db>-pre-logins-<unixtime>.db next to it), keeps
    every shift, session and matchmaking entry, copies each shift's old host
    name into host_name, and drops the old personal accounts. Re-create the
    logins afterwards with `flask --app app init-logins`. Returns the backup
    path, or None when there was nothing to migrate."""
    if not os.path.exists(config.DB_PATH):
        return None
    con = sqlite3.connect(config.DB_PATH, timeout=10)
    con.isolation_level = None  # explicit BEGIN/COMMIT below
    try:
        if "email" not in _columns(con, "users"):
            return None
        stem, ext = os.path.splitext(config.DB_PATH)
        backup = "%s-pre-logins-%d%s" % (stem, now(), ext or ".db")
        dst = sqlite3.connect(backup)
        con.backup(dst)
        dst.close()
        con.execute("PRAGMA foreign_keys=OFF")  # only takes effect outside a transaction
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(
                "CREATE TABLE shifts_new(id INTEGER PRIMARY KEY,"
                " day INTEGER NOT NULL CHECK(day BETWEEN 1 AND 4),"
                " start_min INTEGER NOT NULL, end_min INTEGER NOT NULL,"
                " host_name TEXT NOT NULL DEFAULT '', CHECK(end_min > start_min))")
            con.execute(
                "INSERT INTO shifts_new(id,day,start_min,end_min,host_name) "
                "SELECT s.id, s.day, s.start_min, s.end_min, COALESCE("
                "(SELECT u.name FROM users u WHERE u.id = s.host_id), '') "
                "FROM shifts s")
            con.execute("UPDATE sessions SET created_by=NULL")
            con.execute("UPDATE backlog SET created_by=NULL")
            con.execute("DROP TABLE shifts")
            con.execute("ALTER TABLE shifts_new RENAME TO shifts")
            con.execute("DROP TABLE users")
            con.execute(USERS_DDL)
            bad = con.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise RuntimeError("Migration left broken references: %r" % bad)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return backup
    finally:
        con.close()


def init():
    """Migrate an old DB if needed, create tables, and on an empty DB lay out
    the default 3 shifts per day."""
    migrate_personal_accounts()
    with connect() as con:
        con.executescript(SCHEMA)
        if con.execute("SELECT COUNT(*) FROM shifts").fetchone()[0] == 0:
            for day in config.DAYS:
                for s, e in config.DEFAULT_SHIFTS:
                    con.execute("INSERT INTO shifts(day,start_min,end_min) "
                                "VALUES(?,?,?)", (day, s, e))


def now():
    return int(time.time())


# ------------------------------------------------------------------ logins

def user_by_id(uid):
    with connect() as con:
        return con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def user_by_username(username):
    with connect() as con:
        return con.execute("SELECT * FROM users WHERE username=?",
                           ((username or "").strip(),)).fetchone()


def shift_logins():
    """Shift logins with their shift, in agenda order."""
    with connect() as con:
        return con.execute(
            "SELECT u.id, u.username, u.shift_id, s.day, s.start_min, s.end_min,"
            " s.host_name FROM users u LEFT JOIN shifts s ON s.id = u.shift_id "
            "WHERE u.role='shift' ORDER BY s.day, s.start_min, u.username"
        ).fetchall()


def create_user(username, password_hash, role, shift_id=None):
    with connect() as con:
        return con.execute(
            "INSERT INTO users(username,password_hash,role,shift_id,created_at)"
            " VALUES(?,?,?,?,?)",
            (username.strip(), password_hash, role, shift_id, now())).lastrowid


def set_password(uid, password_hash):
    with connect() as con:
        con.execute("UPDATE users SET password_hash=? WHERE id=?",
                    (password_hash, uid))


def link_shift(uid, shift_id):
    with connect() as con:
        con.execute("UPDATE users SET shift_id=? WHERE id=?", (shift_id, uid))


# ----------------------------------------------------------------- shifts

SHIFT_SELECT = "SELECT s.* FROM shifts s "


def shifts(day=None):
    with connect() as con:
        if day is None:
            return con.execute(SHIFT_SELECT + "ORDER BY day, start_min").fetchall()
        return con.execute(SHIFT_SELECT + "WHERE day=? ORDER BY start_min",
                           (day,)).fetchall()


def shift(sid):
    with connect() as con:
        return con.execute(SHIFT_SELECT + "WHERE s.id=?", (sid,)).fetchone()


def create_shift(day, start_min, end_min, host_name=""):
    with connect() as con:
        return con.execute(
            "INSERT INTO shifts(day,start_min,end_min,host_name) VALUES(?,?,?,?)",
            (day, start_min, end_min, host_name.strip())).lastrowid


def update_shift(sid, start_min, end_min, host_name):
    with connect() as con:
        con.execute("UPDATE shifts SET start_min=?, end_min=?, host_name=? "
                    "WHERE id=?", (start_min, end_min, host_name.strip(), sid))


def set_host_name(sid, host_name):
    with connect() as con:
        con.execute("UPDATE shifts SET host_name=? WHERE id=?",
                    (host_name.strip(), sid))


def delete_shift(sid):
    with connect() as con:
        con.execute("DELETE FROM shifts WHERE id=?", (sid,))


# --------------------------------------------------------------- sessions

SESSION_SELECT = ("SELECT x.*, s.day, s.host_name "
                  "FROM sessions x JOIN shifts s ON s.id = x.shift_id ")


def sessions(day=None):
    with connect() as con:
        if day is None:
            return con.execute(SESSION_SELECT +
                               "ORDER BY s.day, x.start_min").fetchall()
        return con.execute(SESSION_SELECT + "WHERE s.day=? ORDER BY x.start_min",
                           (day,)).fetchall()


def session_row(xid):
    with connect() as con:
        return con.execute(SESSION_SELECT + "WHERE x.id=?", (xid,)).fetchone()


def sessions_in_shift(sid):
    with connect() as con:
        return con.execute(SESSION_SELECT + "WHERE x.shift_id=? "
                           "ORDER BY x.start_min", (sid,)).fetchall()


SESSION_FIELDS = ("shift_id", "start_min", "end_min", "title", "type",
                  "speakers", "description")


def create_session(data, created_by, is_demo=0):
    with connect() as con:
        return con.execute(
            "INSERT INTO sessions(%s, is_demo, created_by, updated_at) "
            "VALUES(%s,?,?,?)" % (",".join(SESSION_FIELDS),
                                  ",".join("?" * len(SESSION_FIELDS))),
            tuple(data[f] for f in SESSION_FIELDS) +
            (is_demo, created_by, now())).lastrowid


def update_session(xid, data):
    with connect() as con:
        con.execute(
            "UPDATE sessions SET %s, updated_at=? WHERE id=?" %
            ", ".join("%s=?" % f for f in SESSION_FIELDS),
            tuple(data[f] for f in SESSION_FIELDS) + (now(), xid))


def delete_session(xid):
    with connect() as con:
        con.execute("DELETE FROM sessions WHERE id=?", (xid,))


# ---------------------------------------------------------------- backlog

def backlog(open_only=False):
    q = ("SELECT b.*, u.username AS added_by FROM backlog b "
         "LEFT JOIN users u ON u.id = b.created_by ")
    if open_only:
        q += "WHERE b.session_id IS NULL "
    with connect() as con:
        return con.execute(q + "ORDER BY b.created_at, b.id").fetchall()


def backlog_entry(bid):
    with connect() as con:
        return con.execute("SELECT * FROM backlog WHERE id=?", (bid,)).fetchone()


def create_backlog(title, who, type_, duration_min, contact, description,
                   created_by, is_demo=0):
    with connect() as con:
        return con.execute(
            "INSERT INTO backlog(title,who,type,duration_min,contact,"
            "description,is_demo,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (title, who, type_, duration_min, contact, description, is_demo,
             created_by, now())).lastrowid


def place_backlog(bid, session_id):
    with connect() as con:
        con.execute("UPDATE backlog SET session_id=? WHERE id=?",
                    (session_id, bid))


def delete_backlog(bid):
    with connect() as con:
        con.execute("DELETE FROM backlog WHERE id=?", (bid,))
