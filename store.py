#!/usr/bin/env python3
"""Data operations for the ticket tracker.

Pure functions over a sqlite3 connection (the HTTP layer in app.py owns the
connection lifecycle), mirroring how vote_queries.py sits behind that project's
app.py. Overdue/age are *derived* here and returned on every ticket so the client
never has to know the rules.
"""

import base64
import json
import re
import shutil
import sqlite3
from datetime import datetime

import db

# ── time ──────────────────────────────────────────────────
# Naive local time throughout: this is a single-machine tool, so "now" and a
# due date the user typed are in the same clock. Stored as second-precision ISO
# (YYYY-MM-DDTHH:MM:SS) — sortable and parseable by both Python and JS Date.


def now():
    return datetime.now().replace(microsecond=0).isoformat()


def _parse(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


# ── serialization ─────────────────────────────────────────

def _tags_for(con, tid):
    rows = con.execute(
        "SELECT t.id, t.name, t.color FROM tags t "
        "JOIN ticket_tags jt ON jt.tag_id = t.id "
        "WHERE jt.ticket_id = ? ORDER BY t.name COLLATE NOCASE", (tid,)).fetchall()
    return [dict(r) for r in rows]


def _ticket_dict(con, row, now_dt, detail=False):
    d = dict(row)
    due = _parse(d.get("due_at"))
    created = _parse(d.get("created_at"))
    d["overdue"] = bool(
        due and d["status"] in db.ACTIVE_STATUSES and due < now_dt)
    d["age_seconds"] = int((now_dt - created).total_seconds()) if created else 0
    d["tags"] = _tags_for(con, d["id"])
    if detail:
        d["updates"] = [dict(r) for r in con.execute(
            "SELECT id, body, created_at FROM updates "
            "WHERE ticket_id = ? ORDER BY created_at, id", (d["id"],)).fetchall()]
        d["attachments"] = [dict(r) for r in con.execute(
            "SELECT id, filename, path, mime, size, created_at FROM attachments "
            "WHERE ticket_id = ? ORDER BY id", (d["id"],)).fetchall()]
    return d


# ── tickets: read ─────────────────────────────────────────

def _fts_query(q):
    # Per-token prefix match, each token quoted so punctuation can't blow up the
    # FTS grammar. "foo bar" -> '"foo"* "bar"*'.
    toks = re.findall(r"\w+", q.lower())
    return " ".join(f'"{t}"*' for t in toks)


def list_tickets(con, status=None, tag=None, overdue=None, q=None, sort="urgency"):
    now_dt = datetime.now()
    where, args = [], []
    if status:                       # list of allowed statuses
        where.append("t.status IN (%s)" % ",".join("?" * len(status)))
        args += list(status)
    if tag:
        where.append("t.id IN (SELECT jt.ticket_id FROM ticket_tags jt "
                     "JOIN tags tg ON tg.id = jt.tag_id WHERE tg.name = ? COLLATE NOCASE)")
        args.append(tag)

    if q and _fts_query(q):
        # Title/body via FTS, plus work-log notes via LIKE (small single-user
        # tables — a scan is fine and keeps the notes out of the FTS triggers).
        sql = ("SELECT t.* FROM tickets t WHERE t.id IN ("
               "SELECT rowid FROM tickets_fts WHERE tickets_fts MATCH ? "
               "UNION SELECT ticket_id FROM updates WHERE body LIKE ?)")
        args = [_fts_query(q), "%" + q.strip() + "%"] + args
        if where:
            sql += " AND " + " AND ".join(where)
    else:
        sql = "SELECT t.* FROM tickets t"
        if where:
            sql += " WHERE " + " AND ".join(where)

    rows = [_ticket_dict(con, r, now_dt) for r in con.execute(sql, args).fetchall()]
    if overdue:
        rows = [r for r in rows if r["overdue"]]
    return _sort(rows, sort)


def _sort(rows, sort):
    far = datetime.max
    keys = {
        # Default: what's on fire first. Overdue on top, then priority, then the
        # soonest due date, then oldest.
        "urgency": lambda r: (not r["overdue"], r["priority"],
                              _parse(r["due_at"]) or far, r["created_at"]),
        "priority": lambda r: (r["priority"], _parse(r["due_at"]) or far),
        "due": lambda r: (_parse(r["due_at"]) or far, r["priority"]),
        "created": lambda r: r["created_at"],
        "updated": lambda r: r["updated_at"],
    }
    if sort == "created" or sort == "updated":
        return sorted(rows, key=keys[sort], reverse=True)
    return sorted(rows, key=keys.get(sort, keys["urgency"]))


def get_ticket(con, tid):
    row = con.execute("SELECT * FROM tickets WHERE id = ?", (tid,)).fetchone()
    if not row:
        return None
    return _ticket_dict(con, row, datetime.now(), detail=True)


# ── tickets: write ────────────────────────────────────────

def _resolve_tags(con, names):
    ids = []
    for name in names:
        name = (name or "").strip()
        if not name:
            continue
        con.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
        r = con.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE",
                        (name,)).fetchone()
        ids.append(r["id"])
    return ids


def _set_tags(con, tid, names):
    con.execute("DELETE FROM ticket_tags WHERE ticket_id = ?", (tid,))
    for tag_id in _resolve_tags(con, names):
        con.execute("INSERT OR IGNORE INTO ticket_tags(ticket_id, tag_id) "
                    "VALUES (?, ?)", (tid, tag_id))


def create_ticket(con, title, priority=3, body="", due_at=None, tags=None):
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    priority = int(priority) if priority else 3
    if priority not in db.PRIORITIES:
        priority = 3
    ts = now()
    cur = con.execute(
        "INSERT INTO tickets(title, body, priority, status, created_at, updated_at, due_at) "
        "VALUES (?, ?, ?, 'open', ?, ?, ?)",
        (title, body or "", priority, ts, ts, due_at or None))
    tid = cur.lastrowid
    if tags:
        _set_tags(con, tid, tags)
    con.commit()
    return get_ticket(con, tid)


# Fields a PATCH may touch, and how to coerce each.
_EDITABLE = {"title", "body", "priority", "status", "due_at"}


def update_ticket(con, tid, fields):
    row = con.execute("SELECT * FROM tickets WHERE id = ?", (tid,)).fetchone()
    if not row:
        return None
    sets, args = [], []
    for key in _EDITABLE:
        if key not in fields:
            continue
        val = fields[key]
        if key == "priority":
            val = int(val)
            if val not in db.PRIORITIES:
                continue
        if key == "status" and val not in db.STATUSES:
            continue
        if key == "title":
            val = (val or "").strip()
            if not val:
                continue
        if key == "due_at":
            val = val or None            # "" clears the due date
        sets.append(f"{key} = ?")
        args.append(val)

    # resolved_at follows the status transition.
    if "status" in fields and fields["status"] in db.STATUSES:
        if fields["status"] in ("resolved", "closed"):
            if not row["resolved_at"]:
                sets.append("resolved_at = ?")
                args.append(now())
        else:
            sets.append("resolved_at = NULL")

    if sets:
        sets.append("updated_at = ?")
        args.append(now())
        con.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id = ?",
                    args + [tid])
    if "tags" in fields:
        _set_tags(con, tid, fields["tags"] or [])
    con.commit()
    return get_ticket(con, tid)


def delete_ticket(con, tid):
    row = con.execute("SELECT id FROM tickets WHERE id = ?", (tid,)).fetchone()
    if not row:
        return False
    con.execute("DELETE FROM tickets WHERE id = ?", (tid,))  # cascades children
    con.commit()
    d = db.ATTACH / str(tid)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    return True


def add_update(con, tid, body):
    body = (body or "").strip()
    if not body:
        raise ValueError("note body is required")
    if not con.execute("SELECT 1 FROM tickets WHERE id = ?", (tid,)).fetchone():
        return None
    con.execute("INSERT INTO updates(ticket_id, body, created_at) VALUES (?, ?, ?)",
                (tid, body, now()))
    # A work-note is activity — bump the ticket so it surfaces as recently worked.
    con.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now(), tid))
    con.commit()
    return get_ticket(con, tid)


# ── attachments ───────────────────────────────────────────

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(name):
    name = (name or "file").replace("\\", "/").split("/")[-1]
    name = _SAFE.sub("_", name).strip("_.") or "file"
    return name[:120]


def add_attachment(con, tid, filename, raw, mime=None):
    if not con.execute("SELECT 1 FROM tickets WHERE id = ?", (tid,)).fetchone():
        return None
    safe = _safe_name(filename)
    cur = con.execute(
        "INSERT INTO attachments(ticket_id, filename, path, mime, size, created_at) "
        "VALUES (?, ?, '', ?, ?, ?)", (tid, safe, mime, len(raw), now()))
    aid = cur.lastrowid
    rel = f"attachments/{tid}/{aid}_{safe}"          # forward slashes: portable
    dest = db.DATA / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    con.execute("UPDATE attachments SET path = ? WHERE id = ?", (rel, aid))
    con.commit()
    return dict(con.execute("SELECT id, filename, path, mime, size, created_at "
                            "FROM attachments WHERE id = ?", (aid,)).fetchone())


def get_attachment(con, aid):
    row = con.execute("SELECT * FROM attachments WHERE id = ?", (aid,)).fetchone()
    if not row:
        return None
    return dict(row)


def delete_attachment(con, aid):
    row = con.execute("SELECT path FROM attachments WHERE id = ?", (aid,)).fetchone()
    if not row:
        return False
    con.execute("DELETE FROM attachments WHERE id = ?", (aid,))
    con.commit()
    f = db.DATA / row["path"]
    if f.exists():
        f.unlink()
    return True


# ── tags & saved views ────────────────────────────────────

def list_tags(con):
    return [dict(r) for r in con.execute(
        "SELECT t.id, t.name, t.color, "
        "(SELECT COUNT(*) FROM ticket_tags jt WHERE jt.tag_id = t.id) AS count "
        "FROM tags t ORDER BY t.name COLLATE NOCASE").fetchall()]


def create_tag(con, name, color=None):
    name = (name or "").strip()
    if not name:
        raise ValueError("tag name is required")
    con.execute("INSERT OR IGNORE INTO tags(name, color) VALUES (?, ?)",
                (name, color or "#8b949e"))
    if color:
        con.execute("UPDATE tags SET color = ? WHERE name = ? COLLATE NOCASE",
                    (color, name))
    con.commit()
    return dict(con.execute("SELECT id, name, color FROM tags "
                            "WHERE name = ? COLLATE NOCASE", (name,)).fetchone())


def delete_tag(con, tid):
    con.execute("DELETE FROM tags WHERE id = ?", (tid,))
    con.commit()
    return True


def list_views(con):
    out = []
    for r in con.execute("SELECT id, name, filter_json FROM views ORDER BY id").fetchall():
        out.append({"id": r["id"], "name": r["name"],
                    "filter": json.loads(r["filter_json"])})
    return out


def create_view(con, name, flt):
    name = (name or "").strip()
    if not name:
        raise ValueError("view name is required")
    con.execute("INSERT INTO views(name, filter_json) VALUES (?, ?)",
                (name, json.dumps(flt or {})))
    con.commit()
    return {"id": con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"],
            "name": name, "filter": flt or {}}


def delete_view(con, vid):
    con.execute("DELETE FROM views WHERE id = ?", (vid,))
    con.commit()
    return True


# ── stats (the summary strip) ─────────────────────────────

def stats(con):
    now_dt = datetime.now()
    rows = [_ticket_dict(con, r, now_dt) for r in
            con.execute("SELECT * FROM tickets").fetchall()]
    active = [r for r in rows if r["status"] in db.ACTIVE_STATUSES]
    today = now_dt.date().isoformat()
    due_today = sum(1 for r in active
                    if r["due_at"] and r["due_at"][:10] == today and not r["overdue"])
    return {
        "open": len(active),
        "overdue": sum(1 for r in active if r["overdue"]),
        "waiting": sum(1 for r in active if r["status"] == "waiting"),
        "due_today": due_today,
        "total": len(rows),
    }


# ── export / import (git-friendly JSON portability) ───────

_TABLES = ("tickets", "tags", "ticket_tags", "updates", "attachments", "views")


def export_data(con):
    out = {"version": 1, "exported_at": now()}
    for tbl in _TABLES:
        out[tbl] = [dict(r) for r in con.execute(f"SELECT * FROM {tbl}").fetchall()]
    # Fold the file bytes into the JSON so an export is a complete, self-contained
    # snapshot — screenshots travel with the tickets.
    for a in out["attachments"]:
        f = db.DATA / a["path"]
        a["data_b64"] = base64.b64encode(f.read_bytes()).decode() if f.exists() else ""
    return out


def import_data(con, data):
    """Replace all current data with the snapshot. Destructive by design — this
    is 'restore this backup', not 'merge'. Wrapped in one transaction."""
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        for tbl in reversed(_TABLES):
            con.execute(f"DELETE FROM {tbl}")
        # sqlite_sequence only exists once an AUTOINCREMENT insert has happened,
        # so a restore into a freshly created DB must tolerate its absence.
        try:
            con.execute("DELETE FROM sqlite_sequence")
        except sqlite3.OperationalError:
            pass
        for tbl in _TABLES:
            for row in data.get(tbl, []):
                cols = [c for c in row if c != "data_b64"]
                con.execute(
                    f"INSERT INTO {tbl} ({','.join(cols)}) "
                    f"VALUES ({','.join('?' * len(cols))})",
                    [row[c] for c in cols])
        con.execute("INSERT INTO tickets_fts(tickets_fts) VALUES('rebuild')")
        con.commit()
    finally:
        con.execute("PRAGMA foreign_keys = ON")

    # Rewrite attachment files from the embedded bytes.
    if db.ATTACH.exists():
        shutil.rmtree(db.ATTACH, ignore_errors=True)
    db.ATTACH.mkdir(parents=True, exist_ok=True)
    for a in data.get("attachments", []):
        blob = a.get("data_b64")
        if not blob:
            continue
        dest = db.DATA / a["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(blob))
    return stats(con)
