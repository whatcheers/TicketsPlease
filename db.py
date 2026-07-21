#!/usr/bin/env python3
"""Single source for the tickets DB path and a Row-factory connection.

Mirrors the cr-council-votes db.py pattern: one place owns where the database
lives and how it is opened, so a change to that (WAL, a different data dir for a
portable copy) happens in one spot. Everything under DATA/ is runtime state and
gitignored; the code in this repo is the whole app.
"""

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
  resolved_at TEXT
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
        con.commit()
    finally:
        con.close()


if __name__ == "__main__":
    init_db()
    print(f"initialized {DB}")
