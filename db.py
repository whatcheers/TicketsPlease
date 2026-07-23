#!/usr/bin/env python3
"""Single source for the tickets DB path and a Row-factory connection.

Mirrors the cr-council-votes db.py pattern: one place owns where the database
lives and how it is opened, so a change to that (WAL, a different data dir for a
portable copy) happens in one spot. Everything under DATA/ is runtime state and
gitignored; the code in this repo is the whole app.
"""

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"                 # runtime state (gitignored)
DB = DATA / "tickets.db"
ATTACH = DATA / "attachments"        # uploaded files, one dir per ticket

# priority: 1=critical .. 4=low. status: the ticket lifecycle.
PRIORITIES = (1, 2, 3, 4)
STATUSES = ("open", "in_progress", "waiting", "resolved", "closed")
ACTIVE_STATUSES = ("open", "in_progress", "waiting")   # "not done yet"

# due_mode: the no-date triage states. Mutually exclusive with due_at —
# '' means "use due_at (or nothing)". ASAP is not a mode: it's a UI
# shortcut that sets due_at to today.
DUE_MODES = ("", "research", "hold")

# Default SLA per priority, in hours: how long after creation a ticket is due
# when auto-due is on. Editable in Settings; these are just the seed values.
DEFAULT_AUTODUE_HOURS = {1: 4, 2: 24, 3: 72, 4: 168}
DEFAULT_SETTINGS = {
    "autodue_enabled": "1",
    "autodue_hours": json.dumps({str(p): DEFAULT_AUTODUE_HOURS[p] for p in PRIORITIES}),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  title       TEXT    NOT NULL,
  body        TEXT    NOT NULL DEFAULT '',
  priority    INTEGER NOT NULL DEFAULT 3,
  status      TEXT    NOT NULL DEFAULT 'open',
  created_at  TEXT    NOT NULL,
  updated_at  TEXT    NOT NULL,
  due_at      TEXT,
  due_mode    TEXT    NOT NULL DEFAULT '',
  due_auto    INTEGER NOT NULL DEFAULT 0,      -- 1 = due_at came from priority SLA, not the user
  resolved_at TEXT,
  deleted_at  TEXT,                         -- set = in the trash
  user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL   -- the requester
);

-- End users / requesters — you enter these yourself. A ticket may point at one
-- (tickets.user_id); deleting a user just clears that pointer, never a ticket.
CREATE TABLE IF NOT EXISTS users (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL,
  email      TEXT NOT NULL DEFAULT '',
  phone      TEXT NOT NULL DEFAULT '',
  dept       TEXT NOT NULL DEFAULT '',      -- department / location
  notes      TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

-- Simple key/value app settings (e.g. auto-due SLA hours per priority).
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
  id    INTEGER PRIMARY KEY AUTOINCREMENT,
  name  TEXT NOT NULL UNIQUE COLLATE NOCASE,
  color TEXT NOT NULL DEFAULT '#8b949e'
);

CREATE TABLE IF NOT EXISTS ticket_tags (
  ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  tag_id    INTEGER NOT NULL REFERENCES tags(id)    ON DELETE CASCADE,
  PRIMARY KEY (ticket_id, tag_id)
);

CREATE TABLE IF NOT EXISTS updates (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id  INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  body       TEXT    NOT NULL,
  created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id  INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  filename   TEXT    NOT NULL,
  path       TEXT    NOT NULL,        -- relative to data/, e.g. attachments/3/7_shot.png
  mime       TEXT,
  size       INTEGER,
  created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS views (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  filter_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_updates_ticket     ON updates(ticket_id);
CREATE INDEX IF NOT EXISTS idx_attachments_ticket ON attachments(ticket_id);
-- NB: idx_tickets_user is created after the migration below, not here — on a
-- pre-existing DB the tickets table has no user_id column until we ALTER it in.

-- Full-text over the searchable ticket fields (matches the FTS5 pattern the
-- votes DB uses). Kept in sync by triggers so a plain INSERT/UPDATE just works.
CREATE VIRTUAL TABLE IF NOT EXISTS tickets_fts
  USING fts5(title, body, content='tickets', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS tickets_ai AFTER INSERT ON tickets BEGIN
  INSERT INTO tickets_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS tickets_ad AFTER DELETE ON tickets BEGIN
  INSERT INTO tickets_fts(tickets_fts, rowid, title, body)
    VALUES ('delete', old.id, old.title, old.body);
END;
CREATE TRIGGER IF NOT EXISTS tickets_au AFTER UPDATE ON tickets BEGIN
  INSERT INTO tickets_fts(tickets_fts, rowid, title, body)
    VALUES ('delete', old.id, old.title, old.body);
  INSERT INTO tickets_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
"""


def connect():
    """A tickets.db connection with Row rows and foreign keys enforced.

    Foreign keys are off by default in SQLite and must be re-enabled per
    connection — the ON DELETE CASCADE rules above depend on it.
    """
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db():
    """Create tables/triggers if absent. Safe to call on every startup."""
    DATA.mkdir(exist_ok=True)
    ATTACH.mkdir(exist_ok=True)
    con = connect()
    try:
        con.executescript(SCHEMA)
        # Migration for databases created before due_mode existed.
        cols = [r["name"] for r in con.execute("PRAGMA table_info(tickets)")]
        if "due_mode" not in cols:
            con.execute("ALTER TABLE tickets "
                        "ADD COLUMN due_mode TEXT NOT NULL DEFAULT ''")
        if "deleted_at" not in cols:
            con.execute("ALTER TABLE tickets ADD COLUMN deleted_at TEXT")
        # Migration for databases created before auto-due was tracked. Existing
        # rows default to 0 (manual) so a priority change won't clobber their date.
        if "due_auto" not in cols:
            con.execute("ALTER TABLE tickets "
                        "ADD COLUMN due_auto INTEGER NOT NULL DEFAULT 0")
        # Migration for databases created before requesters existed. (SQLite
        # can't add a column with a FK, so it's a plain nullable INTEGER — the
        # ON DELETE SET NULL rule only applies to freshly created DBs, which is
        # fine: user deletes go through the store, which clears the pointer.)
        if "user_id" not in cols:
            con.execute("ALTER TABLE tickets ADD COLUMN user_id INTEGER")
        # Now that user_id is guaranteed present (fresh or migrated), index it.
        con.execute("CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id)")
        # Seed default settings without clobbering any the user has changed.
        for k, v in DEFAULT_SETTINGS.items():
            con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                        (k, v))
        # 'asap' was briefly a mode; it now means "due today".
        from datetime import datetime
        eod = datetime.now().replace(hour=23, minute=59, second=0,
                                     microsecond=0).isoformat()
        con.execute("UPDATE tickets SET due_at = ?, due_mode = '' "
                    "WHERE due_mode = 'asap'", (eod,))
        con.commit()
    finally:
        con.close()


if __name__ == "__main__":
    init_db()
    print(f"initialized {DB}")
