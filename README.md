# Triage — a local IT ticket desk

A single-operator ticket tracker for time-sensitive IT work. No cloud, no login,
no accounts, no dependencies — just Python's standard library and a SQLite file.
Built to do the one thing Planner and project tools do badly: **show what's on
fire and keep a paper trail of everything you tried.**

## Run it

```
python app.py
```

Then open **http://localhost:5137**. On Windows you can double-click
`start.bat` (it opens the browser for you); on Linux/Mac use `./start.sh`.

Requires Python 3.8+. Nothing to install — `pip` is never involved.

## What it does

- **Quick-add** — type a title, press Enter, done.
- **Priority (P1–P4), status, and due dates**, with **overdue tickets flagged
  and sorted to the top**. Instead of a date, a ticket can be marked **ASAP**
  (sorts just under overdue), **Research**, or **Hold** (sinks to the bottom).
  Default sort is *urgency*: overdue first, then ASAP, then priority, then
  soonest due.
- **Descriptions are saved deliberately** — the *Log description* button won't
  save until the ticket has a due date or one of ASAP / Research / Hold, so
  nothing gets written up without being triaged.
- **Work log** — an append-only, timestamped list of notes per ticket. This is
  your record of what you tried, in order.
- **Tags + saved views** — label tickets and save filters (e.g. "Waiting on
  vendor") as one-click chips.
- **Attachments** — paste a screenshot, drop a file, or browse. Stored on disk.
- **Search** — full-text over titles and notes.

## Where your data lives

Everything is under `data/`:

- `data/tickets.db` — the SQLite database (tickets, notes, tags, views).
- `data/attachments/` — uploaded files, one folder per ticket.

`data/` is **gitignored** — it's your live state, not code.

## Backup & portability

Three ways to move or back it up, in increasing git-friendliness:

1. **Copy the folder.** Copy `data/` somewhere safe — that's a complete backup,
   attachments included.
2. **Run it anywhere.** The app is stdlib-only with no build step and no
   hardcoded paths, so `git clone` (or copy the repo) onto any machine with
   Python and `python app.py` just works — Windows, or a Linux box.
3. **Export to JSON** — a text snapshot you can commit to git or carry between
   machines. Attachments are embedded (base64), so it's self-contained.

   In the app: the **Backup** button (masthead) downloads a snapshot, and
   **Restore** loads one back in. From the command line:

   ```
   python app.py --export my-tickets.json     # write a snapshot
   python app.py --import my-tickets.json      # restore it (replaces all data)
   ```

   > **Restore replaces everything.** It's "restore this backup," not "merge."
   > As a safety net, every restore first writes a snapshot of the data it's
   > about to replace to `data/backups/pre-restore-<timestamp>.json` — so a
   > wrong restore is always undoable.

## Optional: always-on at login (Windows)

If you want it waiting at `localhost:5137` without launching it each time, drop a
shortcut to `start.bat` into your Startup folder:

1. Press `Win+R`, type `shell:startup`, Enter.
2. Right-click → New → Shortcut → point it at
   `...\projects\tickets\start.bat`.

(For a console-free launch, make a `.pyw` wrapper that calls `app.serve()` — ask
and it's a two-line file.)

## Layout

```
app.py        HTTP server + CLI (--export / --import)
store.py      data operations (tickets, notes, tags, views, attachments)
db.py         SQLite connection + schema
test_api.py   smoke tests:  python test_api.py
web/          the no-build UI (index.html, style.css, app.js)
data/         runtime state (gitignored)
```

## Tests

```
python test_api.py
```

Spins up the real server against a throwaway database and drives it over HTTP:
create → list → status change → work note → tags → attachment upload/download →
overdue logic → export/import round-trip.
