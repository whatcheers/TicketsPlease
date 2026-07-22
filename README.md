# TicketsPlease — a local IT ticket desk

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

By default the server binds `127.0.0.1` (this machine only). Set `HOST` and
`PORT` to change that — e.g. `HOST=0.0.0.0 python app.py` to reach it from
other devices on your network. There is **no login**, so only expose it on a
network you trust.

## Run it as a service (PM2)

To keep it running in the background and restart it on crash/reboot, use
[PM2](https://pm2.keymetrics.io/):

```
pm2 start ecosystem.config.js     # launch the tracker
pm2 logs tickets-please           # tail its output
pm2 restart tickets-please        # after code changes
pm2 stop tickets-please           # stop it
```

The bundled `ecosystem.config.js` runs a single `python3 app.py` process,
flushes its logs to `logs/`, and sets `HOST=0.0.0.0` so it's reachable on the
LAN at **http://&lt;this-host&gt;:5137**. Edit the `env` block there to change the
host/port. After changing `env`, reload with
`pm2 start ecosystem.config.js --update-env` (a plain `pm2 restart` won't
re-read the file).

To relaunch on boot, run `pm2 startup` once (paste the command it prints), then
`pm2 save` while the app is running.

## What it does

- **Quick-add** — type a title, press Enter, done.
- **Priority (P1–P4), status, and due dates**, with **overdue tickets flagged
  and sorted to the top**. The due chooser has an **ASAP** shortcut (= due
  today), or a ticket can be marked **Research** or **Hold** (no date; holds
  sink to the bottom). Default sort is *urgency*: overdue first, then
  priority, then soonest due.
- **Descriptions are saved deliberately** — the *Log description* button won't
  save until the ticket has a due date (ASAP counts — it's today) or is marked
  Research / Hold, so nothing gets written up without being triaged.
- **Work log** — an append-only, timestamped list of notes per ticket. This is
  your record of what you tried, in order.
- **People (end users)** — keep your own directory of requesters (name, email,
  phone, department, notes) on the **People** page. Attach one to a ticket, then
  click their name to see every ticket they've reported.
- **Auto due dates from priority** — in **Settings**, set an SLA per priority
  (e.g. P1 in 4h, P3 in 3 days). New tickets with no date you pick get one
  automatically; a priority's SLA of 0 hours (or the whole feature off) means no
  auto date. Only new tickets are touched — changing priority later won't move a
  due date you've set.
- **Tags + saved views** — label tickets and save filters (e.g. "Waiting on
  vendor") as one-click chips.
- **Attachments** — paste a screenshot, drop a file, or browse. Stored on disk.
- **Search** — full-text over titles and notes.
- **Light / dark / system theme** — a three-way switch in the masthead. Your
  choice is remembered; **System** follows the OS setting and tracks it live.
- **Trash** — deleting a ticket moves it to the Trash view, where it can be
  restored or deleted forever. Nothing is destroyed by a single click.
- **Auto-close** — a *Resolved* ticket closes itself 24 hours later (the
  countdown shows on the ticket). Reopening it cancels the countdown.

## Where your data lives

Everything is under `data/`:

- `data/tickets.db` — the SQLite database (tickets, notes, tags, views, people,
  settings).
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
