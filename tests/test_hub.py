"""Hub tests. Run: python -m unittest discover tests -v

Each test gets a fresh SQLite file (HUB_DB_PATH is set before the app is
imported, so the real hub.db is never touched).
"""
import os
import re
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="hubtest-")
os.environ["HUB_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["SECRET_KEY"] = "test-secret"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash  # noqa: E402

import app as hub  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402

PW = "correct-horse-battery"


def reset_db():
    with db.connect() as con:
        for t in ("backlog", "sessions", "shifts", "users"):
            con.execute("DELETE FROM %s" % t)
    db.init()  # lays out the default 3 shifts per day again


class HubTestCase(unittest.TestCase):
    def setUp(self):
        reset_db()
        hub._buckets.clear()
        hub.app.config["TESTING"] = True
        self.c = hub.app.test_client()
        self.admin = db.create_user("admin@example.invalid", "Admin One",
                                    generate_password_hash(PW), "admin")
        self.host = db.create_user("host@example.invalid", "Host One",
                                   generate_password_hash(PW), "host")
        self.other = db.create_user("other@example.invalid", "Host Two",
                                    generate_password_hash(PW), "host")
        d1 = db.shifts(1)
        self.own_shift, self.other_shift = d1[0], d1[1]   # 09-12, 12-15
        db.update_shift(self.own_shift["id"], 540, 720, self.host)
        db.update_shift(self.other_shift["id"], 720, 900, self.other)

    # -- helpers
    def csrf(self):
        self.c.get("/admin/login")
        with self.c.session_transaction() as s:
            return s["_csrf"]

    def login(self, email):
        tok = self.csrf()
        r = self.c.post("/admin/login", data={"email": email, "password": PW,
                                              "_csrf": tok})
        self.assertEqual(r.status_code, 302, r.data[:300])
        return tok

    def add_session(self, tok, shift_id, start, end, title="S", ack=False):
        data = {"_csrf": tok, "shift_id": shift_id, "start": start, "end": end,
                "title": title, "type": "talk", "speakers": "", "description": ""}
        if ack:
            data["ack_gap"] = "1"
        return self.c.post("/admin/sessions/new", data=data)

    def direct_session(self, shift_id, a, b, title="Existing"):
        return db.create_session({"shift_id": shift_id, "start_min": a,
                                  "end_min": b, "title": title, "type": "talk",
                                  "speakers": "", "description": ""}, None)


class TestAuthGating(HubTestCase):
    def test_admin_pages_redirect_to_login(self):
        for path in ("/admin", "/admin/shifts", "/admin/match",
                     "/admin/users", "/admin/sessions/new"):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertIn("/admin/login", r.headers["Location"])

    def test_post_without_csrf_rejected(self):
        r = self.c.post("/admin/login", data={"email": "admin@example.invalid",
                                              "password": PW})
        self.assertEqual(r.status_code, 400)

    def test_wrong_password_rejected(self):
        tok = self.csrf()
        r = self.c.post("/admin/login", data={"email": "admin@example.invalid",
                                              "password": "nope", "_csrf": tok})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"do not match", r.data)
        self.assertEqual(self.c.get("/admin/shifts").status_code, 302)

    def test_host_cannot_reach_admin_only_pages(self):
        tok = self.login("host@example.invalid")
        self.assertEqual(self.c.get("/admin/shifts").status_code, 200)
        self.assertEqual(self.c.get("/admin/users").status_code, 403)
        r = self.c.post("/admin/shifts/%d" % self.own_shift["id"],
                        data={"_csrf": tok, "start": "08:00", "end": "12:00",
                              "host_id": self.host})
        self.assertEqual(r.status_code, 403)

    def test_admin_can_manage_users(self):
        tok = self.login("admin@example.invalid")
        r = self.c.post("/admin/users", data={
            "_csrf": tok, "name": "New Host", "email": "new@example.invalid",
            "role": "host", "password": "long-enough-pw"})
        self.assertEqual(r.status_code, 302)
        self.assertIsNotNone(db.user_by_email("new@example.invalid"))

    def test_login_rate_limited(self):
        tok = self.csrf()
        for _ in range(config.LOGIN_ATTEMPTS_PER_MINUTE_PER_IP):
            self.c.post("/admin/login", data={"email": "x@example.invalid",
                                              "password": "x", "_csrf": tok})
        r = self.c.post("/admin/login", data={"email": "admin@example.invalid",
                                              "password": PW, "_csrf": tok})
        self.assertIn(b"Too many attempts", r.data)


class TestHostPermissions(HubTestCase):
    def test_host_can_add_inside_own_shift(self):
        tok = self.login("host@example.invalid")
        r = self.add_session(tok, self.own_shift["id"], "09:30", "10:00")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(db.sessions_in_shift(self.own_shift["id"])), 1)

    def test_host_cannot_add_in_other_shift(self):
        tok = self.login("host@example.invalid")
        r = self.add_session(tok, self.other_shift["id"], "12:30", "13:00")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(db.sessions_in_shift(self.other_shift["id"]), [])

    def test_host_cannot_edit_or_delete_other_shift_session(self):
        xid = self.direct_session(self.other_shift["id"], 750, 780)
        tok = self.login("host@example.invalid")
        self.assertEqual(self.c.get("/admin/sessions/%d/edit" % xid).status_code, 403)
        r = self.c.post("/admin/sessions/%d/edit" % xid, data={
            "_csrf": tok, "shift_id": self.other_shift["id"], "start": "12:30",
            "end": "13:00", "title": "Hijack", "type": "talk"})
        self.assertEqual(r.status_code, 403)
        r = self.c.post("/admin/sessions/%d/delete" % xid, data={"_csrf": tok})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(db.session_row(xid)["title"], "Existing")

    def test_host_cannot_move_own_session_into_other_shift(self):
        xid = self.direct_session(self.own_shift["id"], 600, 630)
        tok = self.login("host@example.invalid")
        r = self.c.post("/admin/sessions/%d/edit" % xid, data={
            "_csrf": tok, "shift_id": self.other_shift["id"], "start": "12:30",
            "end": "13:00", "title": "Moved", "type": "talk"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(db.session_row(xid)["shift_id"], self.own_shift["id"])

    def test_admin_can_edit_any_session(self):
        xid = self.direct_session(self.other_shift["id"], 750, 780)
        tok = self.login("admin@example.invalid")
        r = self.c.post("/admin/sessions/%d/edit" % xid, data={
            "_csrf": tok, "shift_id": self.other_shift["id"], "start": "12:30",
            "end": "13:30", "title": "Edited", "type": "workshop"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(db.session_row(xid)["title"], "Edited")


class TestValidation(HubTestCase):
    def test_must_fit_inside_shift(self):
        tok = self.login("host@example.invalid")
        r = self.add_session(tok, self.own_shift["id"], "11:30", "12:30")
        self.assertEqual(r.status_code, 422)
        self.assertIn(b"fit inside its shift", r.data)

    def test_overlap_hard_blocked_even_with_override(self):
        self.direct_session(self.own_shift["id"], 600, 660)  # 10:00-11:00
        tok = self.login("host@example.invalid")
        r = self.add_session(tok, self.own_shift["id"], "10:30", "11:30", ack=True)
        self.assertEqual(r.status_code, 422)
        self.assertIn(b"Overlaps", r.data)
        self.assertEqual(len(db.sessions_in_shift(self.own_shift["id"])), 1)

    def test_overlap_across_shift_boundary_blocked(self):
        # Admin stretches the second shift back; overlap is checked day-wide.
        self.direct_session(self.own_shift["id"], 690, 720)  # 11:30-12:00
        errors, _ = hub.validate_session(
            dict(self.other_shift, start_min=660), 700, 740)
        self.assertTrue(any("Overlaps" in e for e in errors))

    def test_short_gap_warns_then_override_saves(self):
        self.direct_session(self.own_shift["id"], 600, 660)  # ends 11:00
        tok = self.login("host@example.invalid")
        r = self.add_session(tok, self.own_shift["id"], "11:03", "11:30")
        self.assertEqual(r.status_code, 422)
        self.assertIn(b"Only 3 min between", r.data)
        self.assertIn(b'name="ack_gap"', r.data)
        r = self.add_session(tok, self.own_shift["id"], "11:03", "11:30", ack=True)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(db.sessions_in_shift(self.own_shift["id"])), 2)

    def test_back_to_back_warns_but_five_minutes_does_not(self):
        self.direct_session(self.own_shift["id"], 600, 660)
        _, w0 = hub.validate_session(self.own_shift, 660, 690)
        _, w5 = hub.validate_session(self.own_shift, 665, 690)
        self.assertEqual(len(w0), 1)
        self.assertEqual(w5, [])

    def test_shift_move_blocked_if_sessions_fall_out(self):
        self.direct_session(self.own_shift["id"], 560, 600)  # 09:20-10:00
        tok = self.login("admin@example.invalid")
        self.c.post("/admin/shifts/%d" % self.own_shift["id"],
                    data={"_csrf": tok, "start": "09:30", "end": "12:00",
                          "host_id": self.host})
        self.assertEqual(db.shift(self.own_shift["id"])["start_min"], 540)

    def test_admin_moves_shift_and_reassigns_host(self):
        tok = self.login("admin@example.invalid")
        r = self.c.post("/admin/shifts/%d" % self.own_shift["id"],
                        data={"_csrf": tok, "start": "09:00", "end": "11:30",
                              "host_id": self.other})
        self.assertEqual(r.status_code, 302)
        s = db.shift(self.own_shift["id"])
        self.assertEqual((s["end_min"], s["host_id"]), (690, self.other))

    def test_open_slots(self):
        rows = [{"start_min": 570, "end_min": 600}, {"start_min": 602, "end_min": 700}]
        self.assertEqual(hub.open_slots(self.own_shift, rows),
                         [(540, 570), (700, 720)])


class TestMatchmaking(HubTestCase):
    def test_place_entry_creates_session(self):
        bid = db.create_backlog("Forensics", "Someone", "workshop", 60,
                                "x@example.invalid", "", self.admin)
        tok = self.login("admin@example.invalid")
        r = self.c.post("/admin/match/place", data={
            "_csrf": tok, "entry_id": bid, "shift_id": self.other_shift["id"],
            "start": "13:00"})
        self.assertEqual(r.status_code, 302)
        rows = db.sessions_in_shift(self.other_shift["id"])
        self.assertEqual([(x["title"], x["start_min"], x["end_min"]) for x in rows],
                         [("Forensics", 780, 840)])
        self.assertIsNotNone(db.backlog_entry(bid)["session_id"])

    def test_host_cannot_place_into_other_shift(self):
        bid = db.create_backlog("CTF", "", "game", 30, "", "", self.host)
        tok = self.login("host@example.invalid")
        r = self.c.post("/admin/match/place", data={
            "_csrf": tok, "entry_id": bid, "shift_id": self.other_shift["id"],
            "start": "13:00"})
        self.assertEqual(r.status_code, 403)

    def test_match_board_renders_open_time(self):
        db.create_backlog("CTF warm-up", "", "game", 30, "", "", self.host)
        self.login("admin@example.invalid")
        r = self.c.get("/admin/match")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"CTF warm-up", r.data)
        self.assertIn(b"09:00 to 12:00 <span>(180 min open)", r.data)


ICS_LINE = re.compile(r"^[A-Z][A-Z0-9-]*(;[^:]*)?:")


class TestICS(HubTestCase):
    def setUp(self):
        super().setUp()
        self.xid = db.create_session({
            "shift_id": self.own_shift["id"], "start_min": 570, "end_min": 615,
            "title": "Threat modeling, part 1; with notes",
            "type": "workshop", "speakers": "Ana, Bo",
            "description": "Long description " * 12}, None)

    def check_calendar(self, body, n_events):
        self.assertTrue(body.endswith("\r\n"))
        self.assertNotIn("\n", body.replace("\r\n", ""))  # CRLF only
        lines = body.split("\r\n")[:-1]
        for l in lines:
            self.assertLessEqual(len(l.encode("utf-8")), 75, l)
        unfolded = []
        for l in lines:
            if l.startswith(" "):
                unfolded[-1] += l[1:]
            else:
                unfolded.append(l)
        for l in unfolded:
            self.assertRegex(l, ICS_LINE)
        self.assertEqual(unfolded[0], "BEGIN:VCALENDAR")
        self.assertEqual(unfolded[-1], "END:VCALENDAR")
        self.assertIn("VERSION:2.0", unfolded)
        self.assertTrue(any(l.startswith("PRODID:") for l in unfolded))
        self.assertEqual(unfolded.count("BEGIN:VEVENT"), n_events)
        self.assertEqual(unfolded.count("END:VEVENT"), n_events)
        for key in ("UID:", "DTSTAMP:", "DTSTART:", "DTEND:", "SUMMARY:"):
            self.assertEqual(sum(l.startswith(key) for l in unfolded), n_events, key)
        return unfolded

    def test_session_ics(self):
        r = self.c.get("/session/%d.ics" % self.xid)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["Content-Type"].startswith("text/calendar"))
        u = self.check_calendar(r.get_data(as_text=True), 1)
        # Day 1 09:30 IST == 04:00 UTC on the configured first day
        d = config.DAYS[1][0].strftime("%Y%m%d")
        self.assertIn("DTSTART:%sT040000Z" % d, u)
        self.assertIn("DTEND:%sT044500Z" % d, u)
        self.assertTrue(any("Threat modeling\\, part 1\\; with notes" in l for l in u))

    def test_agenda_feed(self):
        self.direct_session(self.other_shift["id"], 780, 840, "Second")
        r = self.c.get("/agenda.ics")
        self.assertEqual(r.status_code, 200)
        self.check_calendar(r.get_data(as_text=True), 2)

    def test_unknown_session_404(self):
        self.assertEqual(self.c.get("/session/99999.ics").status_code, 404)

    def test_fold_handles_multibyte(self):
        folded = hub.ics_fold("DESCRIPTION:" + "é" * 100)
        for part in folded.split("\r\n"):
            self.assertLessEqual(len(part.encode("utf-8")), 75)
        self.assertEqual(folded.replace("\r\n ", ""), "DESCRIPTION:" + "é" * 100)

    def test_google_calendar_link(self):
        x = db.session_row(self.xid)
        link = hub.gcal_link(x)
        self.assertTrue(link.startswith(
            "https://calendar.google.com/calendar/render?action=TEMPLATE"))
        d = config.DAYS[1][0].strftime("%Y%m%d")
        self.assertIn("dates=%sT040000Z%%2F%sT044500Z" % (d, d), link)
        self.assertIn("ctz=Asia%2FKolkata", link)


class TestPublic(HubTestCase):
    def test_agenda_renders(self):
        db.update_shift(self.own_shift["id"], 540, 720, self.host)
        self.direct_session(self.own_shift["id"], 570, 600, "Opening circle")
        r = self.c.get("/?day=1")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn("Security Hub", body)
        self.assertIn("Opening circle", body)
        self.assertIn("Host: <strong>Host One</strong>", body)
        for n, (_d, theme, _b) in config.DAYS.items():
            self.assertIn(theme, body)
        self.assertEqual(body.count('class="shift"'), 12)
        self.assertIn("calendar.google.com/calendar/render?action=TEMPLATE", body)
        self.assertIn('rel="icon" type="image/svg+xml" href="data:image/svg+xml,', body)
        self.assertNotIn("\u2014", body)  # no em dashes in copy

    def test_no_inline_styles_or_scripts(self):
        """The CSP blocks inline style attributes and inline scripts, so any
        that slip into a template would silently do nothing."""
        self.direct_session(self.own_shift["id"], 570, 600)
        db.create_backlog("CTF", "", "game", 30, "", "", self.admin)
        pages = [self.c.get("/").get_data(as_text=True)]
        self.login("admin@example.invalid")
        for path in ("/admin/shifts", "/admin/match", "/admin/users",
                     "/admin/sessions/new?shift=%d" % self.own_shift["id"]):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 200, path)
            pages.append(r.get_data(as_text=True))
        self.c.post("/admin/logout", data={"_csrf": self.csrf()})
        pages.append(self.c.get("/admin/login").get_data(as_text=True))
        for body in pages:
            self.assertNotIn('style="', body)
            self.assertNotRegex(body, r"<script>|<script [^>]*>(?!</script>)\s*\S")
            self.assertNotIn("\u2014", body)

    def test_bad_day_param_falls_back(self):
        self.assertEqual(self.c.get("/?day=9").status_code, 200)
        self.assertEqual(self.c.get("/?day=x").status_code, 200)

    def test_healthz(self):
        r = self.c.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])

    def test_seed_demo_command(self):
        runner = hub.app.test_cli_runner()
        res = runner.invoke(args=["seed-demo"])
        self.assertEqual(res.exit_code, 0, res.output)
        demo = [x for x in db.sessions() if x["is_demo"]]
        self.assertEqual(len(demo), 12)
        self.assertEqual({x["day"] for x in demo}, {1, 2, 3, 4})
        res = runner.invoke(args=["seed-demo"])
        self.assertNotEqual(res.exit_code, 0)  # refuses to double-seed
        res = runner.invoke(args=["seed-demo", "--reset"])
        self.assertEqual(res.exit_code, 0, res.output)


if __name__ == "__main__":
    unittest.main()
