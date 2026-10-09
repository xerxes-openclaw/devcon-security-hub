"""Devcon 8 India: Security Hub. Public agenda + admin scheduling.

Public: hero, four-day agenda (host shifts with sessions inside), a full
.ics feed. Page design by Rodri (templates/public_base.html, static/rodri/).
Admin (username + password, shared logins, no personal accounts): the one
"team" login edits everything; each of the 12 shift logins (tue3-shift1 ...
fri6-shift3) manages sessions and the host name of its own shift only. A
matchmaking board lists open time and content waiting for a slot.
"""
import datetime as dt
import hashlib
import hmac
import os
import secrets
import threading
import time
import urllib.parse
from collections import defaultdict, deque
from functools import wraps

import click
from flask import (Flask, abort, flash, g, make_response, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import config
import db

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=config.COOKIE_SECURE,
    MAX_CONTENT_LENGTH=256 * 1024,
)
if config.TRUST_PROXY:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

db.init()

# Official hub mark (from Zep's static/img/logo-full.jpg), redrawn as SVG in
# that JPG's own pixel coordinates until Zep sends the source SVG. It is brand
# art, not theme: colors are the logo's own, sampled from the JPG.
# Replace this string with the official SVG when it arrives.
LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="566 242 148 172" '
    'role="img" aria-label="Security Hub">'
    # shield: white fill, dark navy outline
    '<path d="M640 249 705 274V330C705 372 680 395 640 408 600 395 575 372 '
    '575 330V274Z" fill="#fff" stroke="#2d2650" stroke-width="8" '
    'stroke-linejoin="round"/>'
    # top-corner triangles
    '<path d="M581 281 607 292 581 317Z" fill="#c67efe"/>'
    '<path d="M699 281 673 292 699 317Z" fill="#8d5cf4"/>'
    # upper diamond (four faces)
    '<path d="M640 280 611 331 640 319Z" fill="#c27cf8"/>'
    '<path d="M640 280 640 319 669 331Z" fill="#895bef"/>'
    '<path d="M611 331 640 319 640 348Z" fill="#6b45d9"/>'
    '<path d="M640 319 669 331 640 348Z" fill="#49329e"/>'
    # lower chevron
    '<path d="M612 345 640 358 640 377Z" fill="#7d64c0"/>'
    '<path d="M640 358 669 345 640 377Z" fill="#342a5f"/>'
    '</svg>')
# Favicon: the shield from Rodri's logo (static/rodri/shield.svg).
with open(os.path.join(app.static_folder, "rodri", "shield.svg"),
          encoding="utf-8") as _f:
    FAVICON_URI = "data:image/svg+xml," + urllib.parse.quote(_f.read().strip())


# ------------------------------------------------------------ time helpers

def hhmm(minutes):
    return "%02d:%02d" % divmod(int(minutes), 60)


def parse_hhmm(text):
    """'09:30' -> 570. Returns None on anything malformed."""
    try:
        h, m = (text or "").strip().split(":")
        h, m = int(h), int(m)
    except ValueError:
        return None
    if 0 <= h <= 24 and 0 <= m < 60 and h * 60 + m <= 24 * 60:
        return h * 60 + m
    return None


def day_date(day):
    return config.DAYS[day][0]


def local_dt(day, minutes):
    """Aware datetime in IST for a hub day + minutes since midnight."""
    d = day_date(day)
    return (dt.datetime(d.year, d.month, d.day, tzinfo=config.TZ)
            + dt.timedelta(minutes=minutes))


def utc_stamp(aware):
    return aware.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def default_day():
    today = dt.datetime.now(config.TZ).date()
    for n, (d, _t, _b) in config.DAYS.items():
        if d == today:
            return n
    return 1


app.jinja_env.filters["hhmm"] = hhmm


# ------------------------------------------------------------ calendar bits

def location_text():
    return "%s, %s, %s (%s)" % (config.HUB_NAME, config.EVENT_VENUE,
                               config.EVENT_CITY, config.HUB_LOCATION)


def session_details(x):
    parts = []
    if x["speakers"]:
        parts.append("With: " + x["speakers"])
    parts.append("Type: " + x["type"])
    if x["description"]:
        parts.append(x["description"])
    parts.append("%s at %s" % (config.HUB_NAME, config.EVENT_NAME))
    return "\n\n".join(parts)


def gcal_link(x):
    q = urllib.parse.urlencode({
        "action": "TEMPLATE",
        "text": "%s (%s)" % (x["title"], config.HUB_NAME),
        "dates": "%s/%s" % (utc_stamp(local_dt(x["day"], x["start_min"])),
                            utc_stamp(local_dt(x["day"], x["end_min"]))),
        "details": session_details(x),
        "location": location_text(),
        "ctz": config.TIMEZONE_NAME,
    })
    return "https://calendar.google.com/calendar/render?" + q


def ics_escape(text):
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n"))


def ics_fold(line):
    """RFC 5545 3.1: lines over 75 octets continue on a line starting with a
    space. Splits on UTF-8 character boundaries."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74  # continuation lines carry a leading space
        if size + n > limit:
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


def ics_calendar(rows, name):
    stamp = utc_stamp(dt.datetime.now(dt.timezone.utc))
    host = urllib.parse.urlparse(config.SITE_URL).hostname or "devcon-security-hub.local"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
             "PRODID:-//Devcon 8 India Security Hub//Agenda//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:" + ics_escape(name),
             "X-WR-TIMEZONE:" + config.TIMEZONE_NAME]
    for x in rows:
        lines += [
            "BEGIN:VEVENT",
            "UID:session-%d@%s" % (x["id"], host),
            "DTSTAMP:" + stamp,
            "DTSTART:" + utc_stamp(local_dt(x["day"], x["start_min"])),
            "DTEND:" + utc_stamp(local_dt(x["day"], x["end_min"])),
            "SUMMARY:" + ics_escape("%s (%s)" % (x["title"], config.HUB_NAME)),
            "DESCRIPTION:" + ics_escape(session_details(x)),
            "LOCATION:" + ics_escape(location_text()),
            "CATEGORIES:" + ics_escape(x["type"]),
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(ics_fold(l) for l in lines) + "\r\n"


def ics_response(body, filename):
    resp = make_response(body)
    resp.headers["Content-Type"] = "text/calendar; charset=utf-8"
    resp.headers["Content-Disposition"] = 'attachment; filename="%s"' % filename
    return resp


# ------------------------------------------------------------ validation

def validate_session(shift, start, end, exclude_id=None):
    """Return (errors, warnings). Errors block saving; warnings need the
    override checkbox. Overlap is checked across the whole day, not just the
    shift, so two shifts can never double-book the room."""
    errors, warnings = [], []
    if start is None or end is None:
        return ["Enter start and end times as HH:MM."], []
    if end <= start:
        return ["The session must end after it starts."], []
    if start < shift["start_min"] or end > shift["end_min"]:
        errors.append("The session must fit inside its shift (%s to %s)."
                      % (hhmm(shift["start_min"]), hhmm(shift["end_min"])))
    for other in db.sessions(shift["day"]):
        if other["id"] == exclude_id:
            continue
        if other["start_min"] < end and start < other["end_min"]:
            errors.append('Overlaps "%s" (%s to %s).' % (
                other["title"], hhmm(other["start_min"]), hhmm(other["end_min"])))
            continue
        gap = (start - other["end_min"] if other["end_min"] <= start
               else other["start_min"] - end)
        if 0 <= gap < config.MIN_GAP_MINUTES:
            warnings.append(
                'Only %d min between this and "%s" (%s to %s). Under %d min '
                'leaves no time to switch speakers.' % (
                    gap, other["title"], hhmm(other["start_min"]),
                    hhmm(other["end_min"]), config.MIN_GAP_MINUTES))
    return errors, warnings


def validate_shift(day, start, end, exclude_id=None):
    if start is None or end is None:
        return ["Enter start and end times as HH:MM."]
    if end <= start:
        return ["The shift must end after it starts."]
    errors = []
    for other in db.shifts(day):
        if other["id"] != exclude_id and other["start_min"] < end and start < other["end_min"]:
            errors.append("Overlaps the %s to %s shift." % (
                hhmm(other["start_min"]), hhmm(other["end_min"])))
    if exclude_id:
        for x in db.sessions_in_shift(exclude_id):
            if x["start_min"] < start or x["end_min"] > end:
                errors.append('"%s" (%s to %s) would fall outside the shift. '
                              'Move it first.' % (x["title"], hhmm(x["start_min"]),
                                                  hhmm(x["end_min"])))
    return errors


def open_slots(shift, rows):
    """Uncovered intervals inside a shift, at least MIN_GAP minutes long."""
    slots, cursor = [], shift["start_min"]
    for x in sorted(rows, key=lambda r: r["start_min"]):
        if x["start_min"] - cursor >= config.MIN_GAP_MINUTES:
            slots.append((cursor, x["start_min"]))
        cursor = max(cursor, x["end_min"])
    if shift["end_min"] - cursor >= config.MIN_GAP_MINUTES:
        slots.append((cursor, shift["end_min"]))
    return slots


# ------------------------------------------------------------ security bits

_buckets = defaultdict(deque)
_bucket_lock = threading.Lock()


def rate_limit(key, limit, window_secs):
    now = time.time()
    with _bucket_lock:
        q = _buckets[key]
        while q and q[0] < now - window_secs:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


@app.before_request
def load_user_and_check_csrf():
    g.user = None
    uid = session.get("uid")
    if uid:
        g.user = db.user_by_id(uid)
        # A password reset changes the hash, which logs out every browser
        # still holding the old shared password.
        if g.user is None or session.get("pwv") != password_version(g.user):
            g.user = None
            session.pop("uid", None)
            session.pop("pwv", None)
    if request.method == "POST":
        tok = request.form.get("_csrf", "")
        if not (tok and hmac.compare_digest(tok, session.get("_csrf", "-"))):
            abort(400, "Your form expired. Reload the page and try again.")


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data:; "
        "frame-ancestors 'none'; form-action 'self'; base-uri 'self'")
    if request.path.startswith("/admin"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.context_processor
def inject():
    return {"csrf_token": csrf_token, "cfg": config, "user": g.get("user"),
            "favicon": FAVICON_URI, "logo_svg": LOGO_SVG,
            "shift_label": shift_label}


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    """Team login only."""
    @wraps(fn)
    @login_required
    def wrapper(*a, **kw):
        if g.user["role"] != "team":
            abort(403)
        return fn(*a, **kw)
    return wrapper


def is_admin():
    return g.user is not None and g.user["role"] == "team"


def can_manage_shift(shift):
    """The team login manages every shift; a shift login only its own."""
    return shift is not None and g.user is not None and (
        g.user["role"] == "team" or shift["id"] == g.user["shift_id"])


def password_version(u):
    return hashlib.sha256(u["password_hash"].encode()).hexdigest()[:16]


def new_password():
    """Strong random password for a shared login (about 120 bits)."""
    return secrets.token_urlsafe(15)


def shift_label(day, start_min=None, end_min=None):
    d = day_date(day)
    text = "%s %d %s" % (d.strftime("%a"), d.day, d.strftime("%b"))
    if start_min is not None:
        text += ", %s to %s" % (hhmm(start_min), hhmm(end_min))
    return text


def shift_username(day, index):
    """Day 1, index 0 -> 'tue3-shift1'."""
    d = day_date(day)
    return "%s%d-shift%d" % (d.strftime("%a").lower(), d.day, index + 1)


def manageable_shifts():
    return [s for s in db.shifts() if can_manage_shift(s)]


# ------------------------------------------------------------ public

def agenda_days():
    """[{n, date, theme, blurb, shifts:[{shift, sessions}]}] for templates."""
    by_shift = defaultdict(list)
    for x in db.sessions():
        by_shift[x["shift_id"]].append(x)
    days = []
    for n, (date, theme, blurb) in config.DAYS.items():
        days.append({
            "n": n, "date": date, "theme": theme, "blurb": blurb,
            "shifts": [{"shift": s, "sessions": by_shift.get(s["id"], [])}
                       for s in db.shifts(n)],
        })
    return days


@app.route("/")
def index():
    try:
        active = int(request.args.get("day", default_day()))
    except ValueError:
        active = 1
    if active not in config.DAYS:
        active = 1
    days = agenda_days()
    has_demo = any(x["is_demo"] for d in days for s in d["shifts"]
                   for x in s["sessions"])
    first, last = day_date(1), day_date(max(config.DAYS))
    return render_template("index.html", days=days, active=active,
                           has_demo=has_demo, gcal_link=gcal_link,
                           first=first, last=last)


@app.route("/agenda.ics")
def agenda_ics():
    return ics_response(ics_calendar(db.sessions(), "Devcon 8 India Security Hub"),
                        "security-hub-agenda.ics")


@app.route("/session/<int:xid>.ics")
def session_ics(xid):
    x = db.session_row(xid)
    if x is None:
        abort(404)
    return ics_response(ics_calendar([x], x["title"]),
                        "security-hub-session-%d.ics" % xid)


@app.route("/healthz")
def healthz():
    with db.connect() as con:
        n = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    return {"ok": True, "sessions": n}


# ------------------------------------------------------------ auth

@app.route("/admin/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if not rate_limit("login:" + ip, config.LOGIN_ATTEMPTS_PER_MINUTE_PER_IP, 60):
            error = "Too many attempts. Wait a minute and try again."
        else:
            u = db.user_by_username(request.form.get("username", "")[:64])
            if u and check_password_hash(u["password_hash"],
                                         request.form.get("password", "")):
                csrf = session.get("_csrf")
                session.clear()
                session["uid"] = u["id"]
                session["pwv"] = password_version(u)
                if csrf:
                    session["_csrf"] = csrf
                nxt = request.args.get("next", "")
                if not (nxt.startswith("/admin") and "//" not in nxt):
                    nxt = url_for("admin_shifts")
                return redirect(nxt)
            error = "That username and password do not match."
    return render_template("admin/login.html", error=error)


@app.route("/admin/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/admin")
@login_required
def admin_home():
    return redirect(url_for("admin_shifts"))


# ------------------------------------------------------------ shifts

@app.route("/admin/shifts")
@login_required
def admin_shifts():
    days = agenda_days()
    own = db.shift(g.user["shift_id"]) if g.user["shift_id"] else None
    return render_template("admin/shifts.html", days=days, own=own,
                           can_manage_shift=can_manage_shift)


def host_name_field():
    return request.form.get("host_name", "").strip()[:120]


@app.route("/admin/shifts/<int:sid>", methods=["POST"])
@admin_required
def admin_shift_update(sid):
    s = db.shift(sid)
    if s is None:
        abort(404)
    start = parse_hhmm(request.form.get("start"))
    end = parse_hhmm(request.form.get("end"))
    errors = validate_shift(s["day"], start, end, exclude_id=sid)
    if errors:
        for e in errors:
            flash(e, "error")
    else:
        db.update_shift(sid, start, end, host_name_field())
        flash("Shift on day %d saved (%s to %s)." % (s["day"], hhmm(start), hhmm(end)), "ok")
    return redirect(url_for("admin_shifts") + "#day-%d" % s["day"])


@app.route("/admin/shifts/<int:sid>/host", methods=["POST"])
@login_required
def admin_shift_host(sid):
    """Shift logins set the displayed host name of their own shift."""
    s = db.shift(sid)
    if s is None:
        abort(404)
    if not can_manage_shift(s):
        abort(403)
    db.set_host_name(sid, host_name_field())
    flash("Host name for %s saved." % shift_label(s["day"], s["start_min"],
                                                  s["end_min"]), "ok")
    return redirect(url_for("admin_shifts") + "#shift-%d" % sid)


@app.route("/admin/shifts/new", methods=["POST"])
@admin_required
def admin_shift_new():
    try:
        day = int(request.form.get("day", ""))
    except ValueError:
        abort(400)
    if day not in config.DAYS:
        abort(400)
    start = parse_hhmm(request.form.get("start"))
    end = parse_hhmm(request.form.get("end"))
    errors = validate_shift(day, start, end)
    if errors:
        for e in errors:
            flash(e, "error")
    else:
        db.create_shift(day, start, end, host_name_field())
        flash("Shift added.", "ok")
    return redirect(url_for("admin_shifts") + "#day-%d" % day)


@app.route("/admin/shifts/<int:sid>/delete", methods=["POST"])
@admin_required
def admin_shift_delete(sid):
    s = db.shift(sid)
    if s is None:
        abort(404)
    if db.sessions_in_shift(sid):
        flash("Move or delete this shift's sessions before deleting it.", "error")
    elif any(x["shift_id"] == sid for x in db.shift_logins()):
        flash("This shift has its own login. Keep it and change its times "
              "instead.", "error")
    else:
        db.delete_shift(sid)
        flash("Shift deleted.", "ok")
    return redirect(url_for("admin_shifts") + "#day-%d" % s["day"])


# ------------------------------------------------------------ sessions

def session_form_data():
    f = request.form
    try:
        shift_id = int(f.get("shift_id", ""))
    except ValueError:
        shift_id = None
    typ = f.get("type", "")
    return {
        "shift_id": shift_id,
        "start_min": parse_hhmm(f.get("start")),
        "end_min": parse_hhmm(f.get("end")),
        "title": f.get("title", "").strip()[:160],
        "type": typ if typ in config.SESSION_TYPES else None,
        "speakers": f.get("speakers", "").strip()[:300],
        "description": f.get("description", "").strip()[:1200],
    }


def save_session(existing=None):
    """Shared create/update handler. Returns a response."""
    data = session_form_data()
    shift = db.shift(data["shift_id"]) if data["shift_id"] else None
    if not can_manage_shift(shift):
        abort(403)
    errors, warnings = [], []
    if not data["title"]:
        errors.append("Give the session a title.")
    if not data["type"]:
        errors.append("Pick a session type.")
    e2, warnings = validate_session(shift, data["start_min"], data["end_min"],
                                    exclude_id=existing["id"] if existing else None)
    errors += e2
    if not errors and warnings and not request.form.get("ack_gap"):
        errors.append("Tick the box below to save anyway, or adjust the times.")
    if errors:
        return render_template("admin/session_form.html", data=data,
                               existing=existing, errors=errors,
                               warnings=warnings, shifts=manageable_shifts(),
                               form=request.form), 422
    if existing:
        db.update_session(existing["id"], data)
        flash('Saved "%s".' % data["title"], "ok")
    else:
        db.create_session(data, g.user["id"])
        flash('Added "%s".' % data["title"], "ok")
    return redirect(url_for("admin_shifts") + "#day-%d" % shift["day"])


@app.route("/admin/sessions/new", methods=["GET", "POST"])
@login_required
def admin_session_new():
    if request.method == "POST":
        return save_session()
    shift = db.shift(request.args.get("shift", type=int) or 0)
    if shift is not None and not can_manage_shift(shift):
        abort(403)
    data = {"shift_id": shift["id"] if shift else None,
            "start_min": shift["start_min"] if shift else None,
            "end_min": None, "title": "", "type": "talk", "speakers": "",
            "description": ""}
    return render_template("admin/session_form.html", data=data, existing=None,
                           errors=[], warnings=[], shifts=manageable_shifts(),
                           form={})


@app.route("/admin/sessions/<int:xid>/edit", methods=["GET", "POST"])
@login_required
def admin_session_edit(xid):
    x = db.session_row(xid)
    if x is None:
        abort(404)
    if not can_manage_shift(db.shift(x["shift_id"])):
        abort(403)
    if request.method == "POST":
        return save_session(existing=x)
    return render_template("admin/session_form.html", data=dict(x), existing=x,
                           errors=[], warnings=[], shifts=manageable_shifts(),
                           form={})


@app.route("/admin/sessions/<int:xid>/delete", methods=["POST"])
@login_required
def admin_session_delete(xid):
    x = db.session_row(xid)
    if x is None:
        abort(404)
    if not can_manage_shift(db.shift(x["shift_id"])):
        abort(403)
    db.delete_session(xid)
    flash('Deleted "%s".' % x["title"], "ok")
    return redirect(url_for("admin_shifts") + "#day-%d" % x["day"])


# ------------------------------------------------------------ shift logins

@app.route("/admin/logins")
@admin_required
def admin_logins():
    return render_template("admin/logins.html", logins=db.shift_logins(),
                           revealed=None)


@app.route("/admin/logins/<int:uid>/reset", methods=["POST"])
@admin_required
def admin_login_reset(uid):
    """New random password for a shift login, shown once in this response
    (never flashed: flashes travel in the readable session cookie)."""
    u = db.user_by_id(uid)
    if u is None or u["role"] != "shift":
        abort(404)
    pw = new_password()
    db.set_password(uid, generate_password_hash(pw))
    return render_template("admin/logins.html", logins=db.shift_logins(),
                           revealed=(u["username"], pw))


# ------------------------------------------------------------ matchmaking

@app.route("/admin/match")
@login_required
def admin_match():
    days = agenda_days()
    for d in days:
        for s in d["shifts"]:
            s["open"] = open_slots(s["shift"], s["sessions"])
            s["manage"] = can_manage_shift(s["shift"])
    return render_template("admin/match.html", days=days,
                           entries=db.backlog(open_only=True),
                           placed=[b for b in db.backlog() if b["session_id"]])


@app.route("/admin/match/entries", methods=["POST"])
@login_required
def admin_backlog_new():
    f = request.form
    title = f.get("title", "").strip()[:160]
    typ = f.get("type", "")
    try:
        duration = int(f.get("duration", ""))
    except ValueError:
        duration = 0
    if not title or typ not in config.SESSION_TYPES or not 5 <= duration <= 9 * 60:
        flash("Entries need a title, a type and a duration of 5 to 540 minutes.", "error")
    else:
        db.create_backlog(title, f.get("who", "").strip()[:300], typ, duration,
                          f.get("contact", "").strip()[:200],
                          f.get("description", "").strip()[:1200], g.user["id"])
        flash('Added "%s" to the list.' % title, "ok")
    return redirect(url_for("admin_match"))


@app.route("/admin/match/entries/<int:bid>/delete", methods=["POST"])
@login_required
def admin_backlog_delete(bid):
    b = db.backlog_entry(bid)
    if b is None:
        abort(404)
    if not (is_admin() or b["created_by"] == g.user["id"]):
        abort(403)
    db.delete_backlog(bid)
    flash("Entry removed.", "ok")
    return redirect(url_for("admin_match"))


@app.route("/admin/match/place", methods=["POST"])
@login_required
def admin_backlog_place():
    f = request.form
    b = db.backlog_entry(f.get("entry_id", type=int) or 0)
    shift = db.shift(f.get("shift_id", type=int) or 0)
    if b is None or shift is None:
        abort(404)
    if not can_manage_shift(shift):
        abort(403)
    if b["session_id"]:
        flash("That entry already has a slot.", "error")
        return redirect(url_for("admin_match"))
    start = parse_hhmm(f.get("start"))
    end = start + b["duration_min"] if start is not None else None
    errors, warnings = validate_session(shift, start, end)
    if not errors and warnings and not f.get("ack_gap"):
        errors = warnings + ["Tick \"Save anyway\" to place it with the short gap."]
    if errors:
        for e in errors:
            flash('"%s": %s' % (b["title"], e), "error")
        return redirect(url_for("admin_match") + "#shift-%d" % shift["id"])
    xid = db.create_session({
        "shift_id": shift["id"], "start_min": start, "end_min": end,
        "title": b["title"], "type": b["type"], "speakers": b["who"],
        "description": b["description"]}, g.user["id"], is_demo=b["is_demo"])
    db.place_backlog(b["id"], xid)
    flash('Placed "%s" on day %d, %s to %s.' % (b["title"], shift["day"],
                                                hhmm(start), hhmm(end)), "ok")
    return redirect(url_for("admin_match") + "#shift-%d" % shift["id"])


# ------------------------------------------------------------ CLI

TEAM_USERNAME = "team"


def ensure_day_shifts(day):
    """The day's first len(DEFAULT_SHIFTS) shifts in time order, creating
    any default shift that is missing and fits."""
    rows = list(db.shifts(day))
    for s, e in config.DEFAULT_SHIFTS[len(rows):]:
        if validate_shift(day, s, e):
            raise click.ClickException(
                "Day %d has no room for the default %s to %s shift. Fix its "
                "shifts in the admin first." % (day, hhmm(s), hhmm(e)))
        db.create_shift(day, s, e)
    return list(db.shifts(day))[:len(config.DEFAULT_SHIFTS)]


@app.cli.command("init-logins")
def init_logins_cmd():
    """Create the team login and one login per host shift (idempotent).

    Prints each NEW login's random password once, on stdout only. Existing
    logins keep their password (use reset-login)."""
    created = []
    if db.user_by_username(TEAM_USERNAME) is None:
        pw = new_password()
        db.create_user(TEAM_USERNAME, generate_password_hash(pw), "team")
        created.append((TEAM_USERNAME, pw, "team (edits everything)"))
    linked = {x["shift_id"] for x in db.shift_logins() if x["shift_id"]}
    for day in config.DAYS:
        for i, s in enumerate(ensure_day_shifts(day)):
            name = shift_username(day, i)
            u = db.user_by_username(name)
            if u is not None and u["role"] != "shift":
                raise click.ClickException("%s exists but is not a shift login." % name)
            if u is not None and u["shift_id"]:
                continue
            if s["id"] in linked:
                raise click.ClickException(
                    "Shift %s already belongs to another login." %
                    shift_label(day, s["start_min"], s["end_min"]))
            if u is None:
                pw = new_password()
                db.create_user(name, generate_password_hash(pw), "shift", s["id"])
                created.append((name, pw, shift_label(day, s["start_min"],
                                                      s["end_min"])))
            else:
                db.link_shift(u["id"], s["id"])
            linked.add(s["id"])
    if not created:
        click.echo("All logins already exist. Passwords unchanged "
                   "(flask --app app reset-login <username>).")
        return
    click.echo("New logins. Passwords are shown ONCE and are not stored in "
               "plain text; hand them out now.")
    for name, pw, what in created:
        click.echo("%-14s %s   %s" % (name, pw, what))


@app.cli.command("reset-login")
@click.argument("username")
def reset_login_cmd(username):
    """Give a login a new random password and print it once."""
    u = db.user_by_username(username)
    if u is None:
        raise click.ClickException("No login named %r. Run init-logins first."
                                   % username)
    pw = new_password()
    db.set_password(u["id"], generate_password_hash(pw))
    click.echo("%s %s" % (u["username"], pw))


DEMO_HOSTS = ["Demo Host A", "Demo Host B", "Demo Host C"]

# (day, start, end, title, type, speakers, description)
DEMO_SESSIONS = [
    (1, "09:15", "09:45", "Welcome to the Security Hub", "talk", "Demo speaker",
     "How the hub works this week, where things are, and how to join in."),
    (1, "12:30", "13:30", "Who is in the room", "roundtable", "Demo moderator",
     "Contributor introductions: researchers, auditors, responders and newcomers."),
    (1, "15:30", "16:30", "Security community speed-meet", "game", "Demo host",
     "Short rotating conversations to find people working on your problem."),
    (2, "09:30", "11:00", "Threat modeling your first protocol", "workshop",
     "Demo auditor", "Bring a design; leave with a list of what can go wrong."),
    (2, "12:15", "13:00", "Ask an auditor", "office hours", "Demo auditors",
     "Drop in with questions about your contracts or your audit plans."),
    (2, "15:15", "16:15", "Tools we actually use", "talk", "Demo builder",
     "A walkthrough of everyday security tooling for smart contract teams."),
    (3, "09:30", "11:30", "Tabletop: the bridge is draining", "game",
     "Demo responders", "A live incident-response exercise in small teams."),
    (3, "12:30", "13:30", "Lessons from real incidents", "fishbowl",
     "Demo panel", "Open-chair conversation on what worked and what did not."),
    (3, "15:00", "16:00", "Coordinating across teams in a crisis", "roundtable",
     "Demo moderator", "Who calls whom, and how to make that faster next time."),
    (4, "09:30", "10:30", "Paths into Ethereum security", "talk", "Demo speaker",
     "Contributor pathways for students, developers and researchers."),
    (4, "12:00", "13:30", "Open-source security sprint", "workshop",
     "Demo maintainers", "Pick an issue from a security tool and ship a fix."),
    (4, "16:00", "17:00", "Closing circle", "fishbowl", "Demo organizers",
     "What we learned this week and what we keep working on together."),
]

DEMO_BACKLOG = [
    ("Wallet drainer forensics", "Demo investigator", "workshop", 60,
     "demo-contact@example.invalid", "Tracing funds from a phishing kit."),
    ("Post-quantum readiness for Ethereum", "Demo researcher", "talk", 30,
     "demo-contact@example.invalid", "Where migration governance stands."),
    ("CTF warm-up", "Demo CTF team", "game", 45,
     "demo-contact@example.invalid", "Beginner-friendly capture-the-flag."),
]


@app.cli.command("seed-demo")
@click.option("--reset", is_flag=True, help="Delete existing demo data first.")
def seed_demo_cmd(reset):
    """Fill empty shift host names with demo names, add ~12 demo sessions and
    demo matchmaking entries."""
    with db.connect() as con:
        if reset:
            con.execute("DELETE FROM backlog WHERE is_demo=1")
            con.execute("DELETE FROM sessions WHERE is_demo=1")
            con.execute("UPDATE shifts SET host_name='' WHERE host_name IN (%s)"
                        % ",".join("?" * len(DEMO_HOSTS)), DEMO_HOSTS)
        elif (con.execute("SELECT 1 FROM sessions WHERE is_demo=1").fetchone()
              or con.execute("SELECT 1 FROM backlog WHERE is_demo=1").fetchone()):
            raise click.ClickException("Demo data already exists. Use --reset.")
    named = 0
    for i, s in enumerate(db.shifts()):
        if not s["host_name"]:
            db.set_host_name(s["id"], DEMO_HOSTS[i % len(DEMO_HOSTS)])
            named += 1
    made = 0
    for day, start, end, title, typ, who, desc in DEMO_SESSIONS:
        a, b = parse_hhmm(start), parse_hhmm(end)
        shift = next((s for s in db.shifts(day)
                      if s["start_min"] <= a and b <= s["end_min"]), None)
        if shift is None or validate_session(shift, a, b)[0]:
            click.echo("Skipped %r: no free room in a shift." % title)
            continue
        db.create_session({"shift_id": shift["id"], "start_min": a,
                           "end_min": b, "title": title, "type": typ,
                           "speakers": who, "description": desc},
                          None, is_demo=1)
        made += 1
    for t, who, typ, dur, contact, desc in DEMO_BACKLOG:
        db.create_backlog(t, who, typ, dur, contact, desc, None, is_demo=1)
    click.echo("Demo data: %d demo host names, %d sessions, %d matchmaking "
               "entries." % (named, made, len(DEMO_BACKLOG)))


if __name__ == "__main__":
    print("Devcon 8 India Security Hub on http://127.0.0.1:%d" % config.PORT)
    app.run(host=config.ENV.get("BIND_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=config.PORT, debug=False)
