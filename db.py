"""SQLite storage. stdlib sqlite3, WAL mode, prepared statements throughout.

Times are stored as minutes since midnight IST plus a day number (1-4); the
calendar date for each day lives in config.DAYS.
"""
import sqlite3
import time
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  email TEXT UNIQUE NOT NULL COLLATE NOCASE,
  name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','host')),
  is_demo INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS shifts(
  id INTEGER PRIMARY KEY,
  day INTEGER NOT NULL CHECK(day BETWEEN 1 AND 4),
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  host_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  CHECK(end_min > start_min)
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


def init():
    """Create tables; on an empty DB lay out the default 3 shifts per day."""
    with connect() as con:
        con.executescript(SCHEMA)
        if con.execute("SELECT COUNT(*) FROM shifts").fetchone()[0] == 0:
            for day in config.DAYS:
                for s, e in config.DEFAULT_SHIFTS:
                    con.execute("INSERT INTO shifts(day,start_min,end_min) "
                                "VALUES(?,?,?)", (day, s, e))


def now():
    return int(time.time())


# ------------------------------------------------------------------ users

def user_by_id(uid):
    with connect() as con:
        return con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def user_by_email(email):
    with connect() as con:
        return con.execute("SELECT * FROM users WHERE email=?",
                           (email.strip(),)).fetchone()


def users():
    with connect() as con:
        return con.execute("SELECT * FROM users ORDER BY role, name").fetchall()


def create_user(email, name, password_hash, role, is_demo=0):
    with connect() as con:
        cur = con.execute(
            "INSERT INTO users(email,name,password_hash,role,is_demo,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (email.strip(), name.strip(), password_hash, role, is_demo, now()))
        return cur.lastrowid


def set_password(uid, password_hash):
    with connect() as con:
        con.execute("UPDATE users SET password_hash=? WHERE id=?",
                    (password_hash, uid))


def delete_user(uid):
    with connect() as con:
        con.execute("DELETE FROM users WHERE id=?", (uid,))


# ----------------------------------------------------------------- shifts

SHIFT_SELECT = ("SELECT s.*, u.name AS host_name FROM shifts s "
                "LEFT JOIN users u ON u.id = s.host_id ")


def shifts(day=None):
    with connect() as con:
        if day is None:
            return con.execute(SHIFT_SELECT + "ORDER BY day, start_min").fetchall()
        return con.execute(SHIFT_SELECT + "WHERE day=? ORDER BY start_min",
                           (day,)).fetchall()


def shift(sid):
    with connect() as con:
        return con.execute(SHIFT_SELECT + "WHERE s.id=?", (sid,)).fetchone()


def create_shift(day, start_min, end_min, host_id):
    with connect() as con:
        return con.execute(
            "INSERT INTO shifts(day,start_min,end_min,host_id) VALUES(?,?,?,?)",
            (day, start_min, end_min, host_id)).lastrowid


def update_shift(sid, start_min, end_min, host_id):
    with connect() as con:
        con.execute("UPDATE shifts SET start_min=?, end_min=?, host_id=? "
                    "WHERE id=?", (start_min, end_min, host_id, sid))


def delete_shift(sid):
    with connect() as con:
        con.execute("DELETE FROM shifts WHERE id=?", (sid,))


# --------------------------------------------------------------- sessions

SESSION_SELECT = ("SELECT x.*, s.day, s.host_id, u.name AS host_name "
                  "FROM sessions x JOIN shifts s ON s.id = x.shift_id "
                  "LEFT JOIN users u ON u.id = s.host_id ")


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
    q = ("SELECT b.*, u.name AS added_by FROM backlog b "
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
