#!/usr/bin/env python3
"""Local ticket tracker — stdlib-only web server.

  python app.py                     ->  http://localhost:5191
  python app.py --export FILE.json  ->  write a portable snapshot
  python app.py --import FILE.json  ->  restore a snapshot (replaces all data)

JSON API under /api/*, static files from web/. Binds 127.0.0.1 — a single-user
local tool, not for exposure. Follows the cr-council-votes server shape.
"""

import hashlib
import json
import mimetypes
import sys
from datetime import date
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

import db
import store

PORT = 5137
WEB = Path(__file__).parent / "web"

_static_cache = {}   # resolved path -> (mtime, body, etag)


class Handler(BaseHTTPRequestHandler):

    # ── verbs ─────────────────────────────────────────────
    def do_GET(self):
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        try:
            if parts and parts[0] == "api":
                self.api_get(parts[1:], parse_qs(url.query))
            else:
                self.serve_static(url.path)
        except BrokenPipeError:
            pass
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def do_POST(self):
        self._write_verb(self.api_post)

    def do_PATCH(self):
        self._write_verb(self.api_patch)

    def do_DELETE(self):
        self._write_verb(self.api_delete)

    def _write_verb(self, fn):
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        if not parts or parts[0] != "api":
            return self.send_json({"error": "not found"}, 404)
        con = db.connect()
        try:
            fn(parts[1:], parse_qs(url.query), con)
        except ValueError as e:                 # bad input from the client
            self.send_json({"error": str(e)}, 400)
        except BrokenPipeError:
            pass
        except Exception as e:
            self.send_json({"error": str(e)}, 500)
        finally:
            con.close()

    # ── GET /api/* ────────────────────────────────────────
    def api_get(self, parts, q):
        con = db.connect()
        try:
            one = lambda k, d=None: q.get(k, [d])[0]
            if parts == ["stats"]:
                return self.send_json(store.stats(con))
            if parts == ["tickets"]:
                statuses = one("status")
                statuses = statuses.split(",") if statuses else None
                data = store.list_tickets(
                    con, status=statuses, tag=one("tag"),
                    overdue=one("overdue") in ("1", "true"),
                    q=one("q"), sort=one("sort", "urgency"))
                return self.send_json({"tickets": data})
            if len(parts) == 2 and parts[0] == "tickets":
                t = store.get_ticket(con, int(parts[1]))
                return self.send_json(t) if t else self.send_json(
                    {"error": "not found"}, 404)
            if parts == ["tags"]:
                return self.send_json({"tags": store.list_tags(con)})
            if parts == ["views"]:
                return self.send_json({"views": store.list_views(con)})
            if len(parts) == 2 and parts[0] == "attachments":
                return self.serve_attachment(con, int(parts[1]))
            if parts == ["export"]:
                return self.send_export(con)
            self.send_json({"error": "unknown endpoint"}, 404)
        finally:
            con.close()

    # ── POST /api/* ───────────────────────────────────────
    def api_post(self, parts, q, con):
        if parts == ["tickets"]:
            b = self._json()
            return self.send_json(store.create_ticket(
                con, b.get("title"), b.get("priority", 3), b.get("body", ""),
                b.get("due_at"), b.get("tags")), 201)
        if len(parts) == 3 and parts[0] == "tickets" and parts[2] == "updates":
            t = store.add_update(con, int(parts[1]), self._json().get("body"))
            return self.send_json(t) if t else self.send_json(
                {"error": "not found"}, 404)
        if len(parts) == 3 and parts[0] == "tickets" and parts[2] == "attachments":
            filename = q.get("filename", ["file"])[0]
            raw = self._bytes()
            if not raw:
                return self.send_json({"error": "empty upload"}, 400)
            mime = self.headers.get("Content-Type") or \
                mimetypes.guess_type(filename)[0]
            a = store.add_attachment(con, int(parts[1]), filename, raw, mime)
            return self.send_json(a, 201) if a else self.send_json(
                {"error": "not found"}, 404)
        if parts == ["tags"]:
            b = self._json()
            return self.send_json(store.create_tag(
                con, b.get("name"), b.get("color")), 201)
        if parts == ["views"]:
            b = self._json()
            return self.send_json(store.create_view(
                con, b.get("name"), b.get("filter")), 201)
        if parts == ["import"]:
            return self.send_json(store.import_data(con, self._json()))
        self.send_json({"error": "unknown endpoint"}, 404)

    # ── PATCH /api/* ──────────────────────────────────────
    def api_patch(self, parts, q, con):
        if len(parts) == 2 and parts[0] == "tickets":
            t = store.update_ticket(con, int(parts[1]), self._json())
            return self.send_json(t) if t else self.send_json(
                {"error": "not found"}, 404)
        self.send_json({"error": "unknown endpoint"}, 404)

    # ── DELETE /api/* ─────────────────────────────────────
    def api_delete(self, parts, q, con):
        table = {"tickets": store.delete_ticket, "attachments": store.delete_attachment,
                 "tags": store.delete_tag, "views": store.delete_view}
        if len(parts) == 2 and parts[0] in table:
            ok = table[parts[0]](con, int(parts[1]))
            return self.send_json({"ok": True}) if ok else self.send_json(
                {"error": "not found"}, 404)
        self.send_json({"error": "unknown endpoint"}, 404)

    # ── bodies ────────────────────────────────────────────
    def _bytes(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    def _json(self):
        raw = self._bytes()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("invalid JSON body")

    # ── files ─────────────────────────────────────────────
    def serve_attachment(self, con, aid):
        a = store.get_attachment(con, aid)
        f = (db.DATA / a["path"]) if a else None
        if not a or not f.is_file():
            return self.send_json({"error": "not found"}, 404)
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", a["mime"] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition",
                         f'inline; filename="{a["filename"]}"')
        self.end_headers()
        self.wfile.write(body)

    def send_export(self, con):
        body = json.dumps(store.export_data(con), indent=2).encode("utf-8")
        stamp = date.today().isoformat()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition",
                         f'attachment; filename="tickets-export-{stamp}.json"')
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, path):
        name = path.strip("/") or "index.html"
        f = (WEB / name).resolve()
        if not f.is_file() or WEB.resolve() not in f.parents:
            if Path(name).suffix:            # a missing asset must 404, not fall back
                return self.send_json({"error": "not found"}, 404)
            f = WEB / "index.html"
        try:
            mtime = f.stat().st_mtime
        except OSError:
            return self.send_json({"error": "not found"}, 404)
        cached = _static_cache.get(f)
        if not cached or cached[0] != mtime:
            data = f.read_bytes()
            etag = '"%s-%d"' % (hashlib.sha1(data).hexdigest()[:16], len(data))
            _static_cache[f] = cached = (mtime, data, etag)
        _, body, etag = cached
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", formatdate(mtime, usegmt=True))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # ── shared ────────────────────────────────────────────
    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass   # keep the console quiet


def serve():
    db.init_db()
    print(f"Ticket tracker  ->  http://localhost:{PORT}   (Ctrl+C to stop)")
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


def main(argv):
    db.init_db()
    if argv and argv[0] == "--export":
        out = Path(argv[1]) if len(argv) > 1 else \
            Path(f"tickets-export-{date.today().isoformat()}.json")
        con = db.connect()
        try:
            out.write_text(json.dumps(store.export_data(con), indent=2), "utf-8")
        finally:
            con.close()
        print(f"exported to {out}")
    elif argv and argv[0] == "--import":
        if len(argv) < 2:
            sys.exit("usage: python app.py --import FILE.json")
        data = json.loads(Path(argv[1]).read_text("utf-8"))
        con = db.connect()
        try:
            st = store.import_data(con, data)
        finally:
            con.close()
        print(f"imported: {st['total']} tickets restored")
    else:
        serve()


if __name__ == "__main__":
    main(sys.argv[1:])
