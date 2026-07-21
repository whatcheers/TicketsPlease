#!/usr/bin/env python3
"""Smoke tests for the ticket tracker HTTP API.

Runs the real server against a throwaway data dir, then drives it with
http.client — the same path the browser takes. No third-party deps.

    python test_api.py
"""

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import db

# Redirect all persistence to a temp dir BEFORE the app/store touch it.
_TMP = Path(tempfile.mkdtemp(prefix="triage-test-"))
db.DATA = _TMP
db.DB = _TMP / "tickets.db"
db.ATTACH = _TMP / "attachments"

import app  # noqa: E402  (imported after db paths are redirected)


class ApiTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db.init_db()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    # ── helper ────────────────────────────────────────────
    def req(self, method, path, body=None, raw=None, ctype="application/json"):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        headers = {}
        payload = raw
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = ctype
        elif raw is not None:
            headers["Content-Type"] = ctype
        c.request(method, path, payload, headers)
        r = c.getresponse()
        data = r.read()
        c.close()
        ct = r.getheader("Content-Type", "")
        return r.status, (json.loads(data) if "json" in ct else data)

    # ── the walk-through ──────────────────────────────────
    def test_full_lifecycle(self):
        # create
        st, t = self.req("POST", "/api/tickets", {"title": "Modem drops packets on WAN1"})
        self.assertEqual(st, 201)
        tid = t["id"]
        self.assertEqual(t["status"], "open")
        self.assertEqual(t["priority"], 3)

        # list shows it
        st, lst = self.req("GET", "/api/tickets")
        self.assertEqual(st, 200)
        self.assertIn(tid, [x["id"] for x in lst["tickets"]])

        # patch status + priority
        st, t = self.req("PATCH", f"/api/tickets/{tid}", {"status": "waiting", "priority": 1})
        self.assertEqual(t["status"], "waiting")
        self.assertEqual(t["priority"], 1)

        # work-note timeline
        st, t = self.req("POST", f"/api/tickets/{tid}/updates", {"body": "Tried new cable, no change."})
        self.assertEqual(len(t["updates"]), 1)

        # tags via PATCH (created on the fly)
        st, t = self.req("PATCH", f"/api/tickets/{tid}", {"tags": ["network", "tmobile"]})
        self.assertEqual({tg["name"] for tg in t["tags"]}, {"network", "tmobile"})
        st, tags = self.req("GET", "/api/tags")
        self.assertIn("network", [x["name"] for x in tags["tags"]])

        # attachment: raw body upload, then fetch the bytes back
        blob = b"\x89PNG\r\n\x1a\n-fake-screenshot-bytes"
        st, a = self.req("POST", f"/api/tickets/{tid}/attachments?filename=shot.png",
                         raw=blob, ctype="image/png")
        self.assertEqual(st, 201)
        st, t = self.req("GET", f"/api/tickets/{tid}")
        self.assertEqual(len(t["attachments"]), 1)
        st, got = self.req("GET", f"/api/attachments/{a['id']}")
        self.assertEqual(got, blob)

    def test_due_modes(self):
        # Research / Hold live in due_mode, mutually exclusive with a concrete
        # due date — setting one clears the other. ASAP is not a mode (the UI
        # turns it into a due date of today).
        st, t = self.req("POST", "/api/tickets", {"title": "due-mode ticket"})
        tid = t["id"]
        st, t = self.req("PATCH", f"/api/tickets/{tid}", {"due_mode": "research"})
        self.assertEqual(t["due_mode"], "research")
        self.assertIsNone(t["due_at"])
        st, t = self.req("PATCH", f"/api/tickets/{tid}", {"due_at": "2030-01-02T09:00"})
        self.assertEqual(t["due_mode"], "")
        self.assertEqual(t["due_at"], "2030-01-02T09:00")
        st, t = self.req("PATCH", f"/api/tickets/{tid}", {"due_mode": "hold"})
        self.assertEqual(t["due_mode"], "hold")
        self.assertIsNone(t["due_at"])
        st, t = self.req("PATCH", f"/api/tickets/{tid}", {"due_mode": "asap"})
        self.assertEqual(t["due_mode"], "hold")   # not a mode — ignored

    def test_autoclose_after_24h(self):
        # Resolved tickets close themselves once resolved_at is 24h old.
        st, t = self.req("POST", "/api/tickets", {"title": "autoclose me"})
        tid = t["id"]
        st, t = self.req("PATCH", f"/api/tickets/{tid}", {"status": "resolved"})
        self.assertEqual(t["status"], "resolved")
        from datetime import datetime, timedelta
        stale = (datetime.now() - timedelta(hours=25)).isoformat()
        con = db.connect()
        con.execute("UPDATE tickets SET resolved_at = ? WHERE id = ?", (stale, tid))
        con.commit(); con.close()
        st, t = self.req("GET", f"/api/tickets/{tid}")
        self.assertEqual(t["status"], "closed")

    def test_trash(self):
        # Delete moves to trash (recoverable); purge is the only hard delete.
        st, t = self.req("POST", "/api/tickets", {"title": "trash me"})
        tid = t["id"]
        st, _ = self.req("DELETE", f"/api/tickets/{tid}")
        self.assertEqual(st, 200)
        st, lst = self.req("GET", "/api/tickets")
        self.assertNotIn(tid, [x["id"] for x in lst["tickets"]])
        st, lst = self.req("GET", "/api/tickets?trash=1")
        self.assertIn(tid, [x["id"] for x in lst["tickets"]])
        st, t = self.req("POST", f"/api/tickets/{tid}/restore")
        self.assertEqual(st, 200)
        self.assertIsNone(t["deleted_at"])
        st, lst = self.req("GET", "/api/tickets")
        self.assertIn(tid, [x["id"] for x in lst["tickets"]])
        st, _ = self.req("DELETE", f"/api/tickets/{tid}")
        st, _ = self.req("DELETE", f"/api/tickets/{tid}?purge=1")
        self.assertEqual(st, 200)
        st, _ = self.req("GET", f"/api/tickets/{tid}")
        self.assertEqual(st, 404)

    def test_restore_writes_safety_backup(self):
        # A restore is destructive, so it must first snapshot the current data
        # to data/backups/ — that snapshot is the undo.
        st, _ = self.req("POST", "/api/tickets", {"title": "pre-restore data"})
        self.assertEqual(st, 201)
        st, snap = self.req("GET", "/api/export")
        self.assertEqual(st, 200)
        st, res = self.req("POST", "/api/import", snap)
        self.assertEqual(st, 200)
        backups = list((db.DATA / "backups").glob("pre-restore-*.json"))
        self.assertTrue(backups, "restore should write a safety snapshot first")
        restored = json.loads(backups[-1].read_text("utf-8"))
        self.assertIn("tickets", restored)

    def test_overdue_computation(self):
        st, past = self.req("POST", "/api/tickets", {"title": "past due"})
        self.req("PATCH", f"/api/tickets/{past['id']}", {"due_at": "2000-01-01T00:00:00"})
        st, t = self.req("GET", f"/api/tickets/{past['id']}")
        self.assertTrue(t["overdue"], "a past due date on an open ticket is overdue")

        # once resolved it is no longer overdue
        self.req("PATCH", f"/api/tickets/{past['id']}", {"status": "resolved"})
        st, t = self.req("GET", f"/api/tickets/{past['id']}")
        self.assertFalse(t["overdue"])
        self.assertTrue(t["resolved_at"])

        # the overdue filter returns the open past-due one, not the resolved one
        st, other = self.req("POST", "/api/tickets", {"title": "still open past due"})
        self.req("PATCH", f"/api/tickets/{other['id']}", {"due_at": "2000-01-01T00:00:00"})
        st, lst = self.req("GET", "/api/tickets?overdue=1")
        ids = [x["id"] for x in lst["tickets"]]
        self.assertIn(other["id"], ids)
        self.assertNotIn(past["id"], ids)

    def test_saved_view(self):
        st, v = self.req("POST", "/api/views",
                         {"name": "Waiting on vendor", "filter": {"status": "waiting"}})
        self.assertEqual(st, 201)
        st, views = self.req("GET", "/api/views")
        self.assertIn("Waiting on vendor", [x["name"] for x in views["views"]])
        self.assertEqual(
            next(x for x in views["views"] if x["id"] == v["id"])["filter"]["status"], "waiting")

    def test_export_import_roundtrip(self):
        st, snap = self.req("GET", "/api/export")
        before = snap["tickets"]
        # add a ticket that is NOT in the snapshot
        self.req("POST", "/api/tickets", {"title": "extra after snapshot"})
        st, lst = self.req("GET", "/api/tickets?status=open,in_progress,waiting,resolved,closed")
        self.assertGreater(len(lst["tickets"]), len(before))
        # importing the snapshot restores the earlier state exactly
        st, stats = self.req("POST", "/api/import", snap)
        self.assertEqual(stats["total"], len(before))


if __name__ == "__main__":
    unittest.main(verbosity=2)
