/* Triage — SPA over the /api endpoints.
   All data-bearing text goes through textContent / value (never innerHTML). */

"use strict";

const $view = document.getElementById("view");

/* ── DOM factory (same shape as cr-council-votes) ─────── */
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined) continue;
    if (k === "text") n.textContent = v;
    else if (k === "html") n.innerHTML = v;          // only for our own trusted SVG
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const kid of kids) if (kid !== null && kid !== undefined && kid !== false) n.append(kid);
  return n;
}

/* ── data access ──────────────────────────────────────── */
async function api(path) {
  const r = await fetch(new URL("api/" + path, document.baseURI));
  if (!r.ok) throw new Error(((await r.json().catch(() => ({}))).error) || r.statusText);
  return r.json();
}
async function mut(method, path, body) {
  const opt = { method };
  if (body !== undefined) {
    opt.headers = { "Content-Type": "application/json" };
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(new URL("api/" + path, document.baseURI), opt);
  if (!r.ok) throw new Error(((await r.json().catch(() => ({}))).error) || r.statusText);
  return r.status === 204 ? null : r.json().catch(() => null);
}

async function usersList(force) {
  if (force || !USER_CACHE) USER_CACHE = (await api("users")).users;
  return USER_CACHE;
}

/* ── vocab ────────────────────────────────────────────── */
const STATUS = [
  ["open", "Open"], ["in_progress", "In progress"], ["waiting", "Waiting"],
  ["resolved", "Resolved"], ["closed", "Closed"],
];
const PRIO = [[1, "P1 · Critical"], [2, "P2 · High"], [3, "P3 · Normal"], [4, "P4 · Low"]];
const statusText = (s) => s.replace("_", " ");
const DUE_MODES = [["research", "Research"], ["hold", "Hold"]];
const DUE_MODE_LABEL = { research: "research", hold: "hold" };
function todayEOD() {           // "due today" = end of today, local clock
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T23:59";
}

// SLA durations are stored as hours on the server but shown as a value + unit.
const SLA_UNITS = [["hours", 1], ["days", 24], ["weeks", 168]];
function hoursToHuman(h) {                      // pick the largest whole unit
  h = Math.max(0, Math.round(h));
  if (!h) return { value: 0, unit: "hours" };   // 0 = off; keep a sane unit
  for (const [unit, mult] of [...SLA_UNITS].reverse()) {
    if (h % mult === 0) return { value: h / mult, unit };
  }
  return { value: h, unit: "hours" };
}
function humanToHours(value, unit) {
  const mult = (SLA_UNITS.find(([u]) => u === unit) || ["hours", 1])[1];
  return Math.max(0, Math.round(value)) * mult;
}
function slaPhrase(value, unit) {              // "3 days", "1 hour", "no auto date"
  value = Math.max(0, Math.round(value));
  if (!value) return "no automatic due date";
  const word = value === 1 ? unit.replace(/s$/, "") : unit;
  return "due " + value + " " + word + " after it's logged";
}

const VIEWS = [
  { key: "open", label: "Open", params: { status: "open,in_progress,waiting" }, stat: "open" },
  { key: "overdue", label: "Overdue", params: { overdue: "1" }, stat: "overdue" },
  { key: "waiting", label: "Waiting", params: { status: "waiting" }, stat: "waiting" },
  { key: "done", label: "Done", params: { status: "resolved,closed" } },
  { key: "all", label: "All", params: {} },
  { key: "trash", label: "Trash", params: { trash: "1" }, stat: "trash" },
];

let F = { view: "open", tag: null, user: null, q: "", sort: "urgency" };
let SAVED = [];   // custom saved views from the server
let USER_CACHE = null;   // [{id,name,...}] — lazily loaded for pickers

/* ── formatting ───────────────────────────────────────── */
function fmtAge(s) {
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + "m";
  if (s < 86400) return Math.round(s / 3600) + "h";
  return Math.round(s / 86400) + "d";
}
function fmtDue(iso) {
  const d = new Date(iso);
  const opt = { month: "short", day: "numeric" };
  const hasTime = iso.length >= 16 && iso.slice(11, 16) !== "00:00";
  if (hasTime) { opt.hour = "numeric"; opt.minute = "2-digit"; }
  return d.toLocaleString("en-US", opt);
}
function fmtStamp(iso) {
  return new Date(iso).toLocaleString("en-US",
    { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
function fmtCloseIn(resolvedIso) {   // resolved tickets auto-close 24h later
  const ms = new Date(resolvedIso).getTime() + 24 * 3600e3 - Date.now();
  if (ms <= 0) return "closing…";
  const h = Math.floor(ms / 3600e3);
  return "closes in " + (h >= 1 ? h + "h" : Math.max(1, Math.round(ms / 60e3)) + "m");
}

/* ── mount helper ─────────────────────────────────────── */
function mount(node) {
  $view.replaceChildren(node);
  $view.focus();
}
function toast(err) {
  console.error(err);
  alert(err.message || String(err));   // a local single-user tool; a native alert is enough
}

/* ── list view ────────────────────────────────────────── */
function resolveParams() {
  let base = {};
  if (F.view.startsWith("custom:")) {
    const v = SAVED.find((s) => "custom:" + s.id === F.view);
    base = v ? { ...v.filter } : {};
  } else {
    base = { ...(VIEWS.find((v) => v.key === F.view) || {}).params };
  }
  if (F.tag) base.tag = F.tag;
  if (F.user) base.user = F.user;
  if (F.q) base.q = F.q;
  base.sort = F.sort;
  const qs = Object.entries(base)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => k + "=" + encodeURIComponent(v)).join("&");
  return qs;
}

async function renderList() {
  const loading = el("div", { class: "notice loading", text: "Loading…" });
  mount(loading);
  let stats, res, views;
  try {
    [stats, res, views] = await Promise.all([
      api("stats"), api("tickets?" + resolveParams()), api("views"),
    ]);
  } catch (e) { return toast(e); }
  SAVED = views.views;

  const root = el("div", {});

  // vitals — the "what's on fire" thesis
  const vital = (num, label, opts = {}) => {
    const attrs = { class: "vital" + (opts.cls || "") };
    const inner = [el("div", { class: "vital-num", text: String(num) }),
                   el("div", { class: "vital-label", text: label })];
    if (opts.view) {
      attrs.class += " " + (F.view === opts.view && !F.tag ? "is-on" : "");
      attrs.onclick = () => { F.view = opts.view; F.tag = null; renderList(); };
      return el("button", attrs, ...inner);
    }
    return el("div", attrs, ...inner);
  };
  root.append(el("div", { class: "vitals" },
    vital(stats.open, "Open", { view: "open" }),
    vital(stats.overdue, "Overdue", { view: "overdue", cls: " alert" + (stats.overdue ? " hot" : "") }),
    vital(stats.waiting, "Waiting", { view: "waiting" }),
    vital(stats.due_today, "Due today")));

  // quick-add
  const qa = el("input", {
    class: "qa-title", type: "text", "aria-label": "New ticket title",
    placeholder: "Log a ticket — type a title and press Enter…",
    onkeydown: async (e) => {
      if (e.key !== "Enter" || !qa.value.trim()) return;
      const title = qa.value.trim(); qa.value = "";
      try { await mut("POST", "tickets", { title }); renderList(); }
      catch (err) { toast(err); }
    },
  });
  root.append(el("div", { class: "quickadd" },
    el("span", { class: "qa-prompt", text: "＋" }), qa));

  // toolbar: view chips + saved views + save + sort
  const bar = el("div", { class: "toolbar" });
  for (const v of VIEWS) {
    const on = F.view === v.key && !F.tag;
    const count = v.stat ? el("span", { class: "chip-n", text: String(stats[v.stat]) })
      : (v.key === "all" ? el("span", { class: "chip-n", text: String(stats.total) }) : null);
    bar.append(el("button", {
      class: "chip" + (on ? " is-on" : ""),
      onclick: () => { F.view = v.key; F.tag = null; renderList(); },
    }, document.createTextNode(v.label), count));
  }
  for (const v of SAVED) {
    const key = "custom:" + v.id;
    const on = F.view === key;
    bar.append(el("button", {
      class: "chip" + (on ? " is-on" : ""),
      onclick: () => { F.view = key; F.tag = null; renderList(); },
    }, document.createTextNode(v.name),
      el("span", {
        class: "x", title: "Delete this view", "aria-label": "Delete view",
        style: "margin-left:7px;color:var(--muted)",
        onclick: async (e) => {
          e.stopPropagation();
          try { await mut("DELETE", "views/" + v.id); if (F.view === key) F.view = "open"; renderList(); }
          catch (err) { toast(err); }
        },
      }, "✕")));
  }
  bar.append(el("button", {
    class: "chip chip-save", title: "Save the current filter as a view",
    onclick: saveCurrentView,
  }, "＋ save view"));

  if (F.tag) {
    bar.append(el("button", {
      class: "chip is-on", title: "Clear tag filter",
      onclick: () => { F.tag = null; renderList(); },
    }, document.createTextNode("#" + F.tag), el("span", { class: "x", style: "margin-left:7px", text: "✕" })));
  }
  if (F.user) {
    bar.append(el("button", {
      class: "chip is-on", title: "Clear requester filter",
      onclick: () => { F.user = null; F.userName = null; renderList(); },
    }, document.createTextNode("👤 " + (F.userName || "requester")),
      el("span", { class: "x", style: "margin-left:7px", text: "✕" })));
  }

  bar.append(el("span", { class: "spacer" }));
  const sortSel = el("select", {
    class: "sort-select", "aria-label": "Sort tickets",
    onchange: () => { F.sort = sortSel.value; renderList(); },
  },
    el("option", { value: "urgency", text: "Sort: urgency" }),
    el("option", { value: "due", text: "Sort: due date" }),
    el("option", { value: "priority", text: "Sort: priority" }),
    el("option", { value: "updated", text: "Sort: last worked" }),
    el("option", { value: "created", text: "Sort: newest" }));
  sortSel.value = F.sort;
  bar.append(sortSel);
  root.append(bar);

  // queue
  const list = res.tickets;
  if (!list.length) {
    root.append(el("div", { class: "notice" },
      el("div", { class: "big", text: F.q ? "No tickets match that search." : "Nothing here." }),
      el("div", { class: "sub", text: F.q ? "Try a different term, or clear the search." : "Log one above to get started." })));
  } else {
    const q = el("div", { class: "queue" });
    for (const t of list) q.append(ticketRow(t));
    root.append(q);
  }
  mount(root);
}

function ticketRow(t) {
  const done = t.status === "resolved" || t.status === "closed";
  const meta = el("div", { class: "t-meta" },
    el("span", { class: "ptag p" + t.priority, text: "P" + t.priority }),
    el("span", { class: "pill " + t.status, text: statusText(t.status) }));
  if (t.user) {
    meta.append(el("span", {
      class: "who", title: "Filter to " + t.user.name,
      onclick: (e) => {
        e.stopPropagation();
        F.user = t.user.id; F.userName = t.user.name; F.view = "all"; renderList();
      },
    }, el("span", { class: "who-ico", text: "👤" }), document.createTextNode(t.user.name)));
  }
  for (const tag of t.tags) meta.append(tagChip(tag, () => { F.tag = tag.name; F.view = "all"; renderList(); }));

  const side = el("div", { class: "t-side" });
  if (t.due_at) side.append(el("span", { class: "t-due" + (t.overdue ? " over" : ""), text: (t.overdue ? "overdue · " : "due ") + fmtDue(t.due_at) }));
  else if (t.due_mode) side.append(el("span", { class: "t-due mode-" + t.due_mode, text: DUE_MODE_LABEL[t.due_mode] || t.due_mode }));
  if (t.status === "resolved" && t.resolved_at) {
    side.append(el("span", { class: "t-close", text: fmtCloseIn(t.resolved_at) }));
  }
  side.append(el("span", { class: "t-age", text: fmtAge(t.age_seconds) }));

  return el("button", {
    class: "trow" + (t.overdue ? " is-overdue" : "")
      + (done || t.deleted_at ? " is-done" : ""),
    onclick: () => { location.hash = "#/t/" + t.id; },
  },
    el("span", { class: "spine p" + t.priority }),
    el("span", { class: "t-id", text: "#" + String(t.id).padStart(4, "0") }),
    el("span", { class: "t-main" }, el("span", { class: "t-title", text: t.title }), meta),
    side);
}

function tagChip(tag, onClick) {
  const c = el("span", { class: "tag", title: onClick ? "Filter by " + tag.name : null },
    el("span", { class: "dot", style: "background:" + (tag.color || "var(--muted)") }),
    document.createTextNode(tag.name));
  if (onClick) { c.style.cursor = "pointer"; c.addEventListener("click", (e) => { e.stopPropagation(); onClick(); }); }
  return c;
}

async function saveCurrentView() {
  const name = prompt("Name this view:");
  if (!name || !name.trim()) return;
  const filter = {};
  if (F.view.startsWith("custom:")) {
    const v = SAVED.find((s) => "custom:" + s.id === F.view);
    Object.assign(filter, v ? v.filter : {});
  } else {
    const p = (VIEWS.find((v) => v.key === F.view) || {}).params || {};
    if (p.status) filter.status = p.status;
    if (p.overdue) filter.overdue = p.overdue;
  }
  if (F.tag) filter.tag = F.tag;
  try {
    const v = await mut("POST", "views", { name: name.trim(), filter });
    F.view = "custom:" + v.id; F.tag = null; renderList();
  } catch (e) { toast(e); }
}

/* ── detail view ──────────────────────────────────────── */
async function renderDetail(id) {
  mount(el("div", { class: "notice loading", text: "Loading…" }));
  let t;
  try { t = await api("tickets/" + id); }
  catch (e) { return toast(e); }
  if (t.error) { location.hash = "#/"; return; }

  const patch = async (fields) => {
    try { await mut("PATCH", "tickets/" + id, fields); Object.assign(t, fields); }
    catch (e) { toast(e); }
  };

  const root = el("div", {});
  root.append(el("div", { class: "detail-top" },
    el("a", { class: "back", href: "#/" }, el("span", { text: "←" }), document.createTextNode("All tickets"))));

  if (t.deleted_at) {
    root.append(el("div", { class: "trash-banner" },
      el("span", { text: "This ticket is in the trash." }),
      el("span", { class: "spacer" }),
      el("button", { class: "btn", text: "Restore",
        onclick: async () => {
          try { await mut("POST", "tickets/" + id + "/restore"); renderDetail(id); }
          catch (e) { toast(e); }
        } }),
      el("button", { class: "btn btn-danger", text: "Delete forever",
        onclick: async () => {
          if (!confirm("Permanently delete this ticket, its notes, and attachments? This can't be undone.")) return;
          try { await mut("DELETE", "tickets/" + id + "?purge=1"); location.hash = "#/"; }
          catch (e) { toast(e); }
        } })));
  }

  // ── main column ──
  const titleInput = el("input", {
    class: "d-title", value: t.title, "aria-label": "Ticket title",
    onkeydown: (e) => { if (e.key === "Enter") titleInput.blur(); },
    onblur: () => { const v = titleInput.value.trim(); if (v && v !== t.title) patch({ title: v }); },
  });
  const main = el("div", {},
    el("div", { class: "d-head" },
      el("span", { class: "d-idtag", text: "#" + String(t.id).padStart(4, "0") }),
      titleInput));

  // description — saved explicitly, and only once the ticket has a due set
  // (a date, or ASAP / Research / Hold). Editable any time.
  let dueCard;                     // assigned when the sidebar builds, below
  const desc = el("textarea", {
    class: "field", "aria-label": "Description",
    placeholder: "What's the situation? (Ctrl+Enter to save)",
    onkeydown: (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") saveDesc(); },
  });
  desc.value = t.body || "";
  const saveDesc = async () => {
    if (desc.value === (t.body || "")) return;      // nothing changed
    if (!t.due_at && !t.due_mode) {
      if (dueCard) dueCard.classList.add("needs-due");
      return toast(new Error(
        "Set a due date first (ASAP = today), or mark the ticket Research / Hold — then save the description."));
    }
    await patch({ body: desc.value });
    descBtn.textContent = "Saved ✓";
    setTimeout(() => { descBtn.textContent = "Log description"; }, 1400);
  };
  const descBtn = el("button", { class: "btn btn-accent", text: "Log description", onclick: saveDesc });
  main.append(el("div", { class: "section-label", text: "Description" }), desc,
    el("div", { style: "margin-top:8px;display:flex;justify-content:flex-end" }, descBtn));

  // work-log tape (the signature)
  main.append(el("div", { class: "section-label", text: "Work log" }));
  const log = el("div", { class: "log" });
  if (!t.updates.length) log.append(el("div", { class: "log-empty", text: "No notes yet. Log what you try below — this is your paper trail." }));
  for (const u of t.updates) {
    log.append(el("div", { class: "log-entry" },
      el("div", { class: "log-stamp", text: fmtStamp(u.created_at) }),
      el("div", { class: "log-text", text: u.body })));
  }
  main.append(log);

  const note = el("textarea", {
    placeholder: "Append to log — what did you try? (Ctrl+Enter to save)",
    "aria-label": "New work note",
    onkeydown: async (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && note.value.trim()) {
        try { await mut("POST", "tickets/" + id + "/updates", { body: note.value.trim() }); renderDetail(id); }
        catch (err) { toast(err); }
      }
    },
  });
  const logBtn = el("button", {
    class: "btn btn-accent", text: "Log note",
    onclick: async () => {
      if (!note.value.trim()) return;
      try { await mut("POST", "tickets/" + id + "/updates", { body: note.value.trim() }); renderDetail(id); }
      catch (err) { toast(err); }
    },
  });
  main.append(el("div", { class: "log-add" },
    el("span", { class: "prompt", text: "›" }), note),
    el("div", { style: "margin-top:8px;display:flex;justify-content:flex-end" }, logBtn));

  // attachments
  main.append(el("div", { class: "section-label", text: "Attachments" }));
  const grid = el("div", { class: "attach-grid" });
  for (const a of t.attachments) grid.append(attachTile(id, a));
  main.append(grid);

  const fileInput = el("input", { type: "file", multiple: "", style: "display:none",
    onchange: () => { uploadFiles(id, fileInput.files); } });
  const dz = el("div", {
    class: "dropzone", role: "button", tabindex: "0",
    text: "Paste a screenshot, drop files here, or click to browse",
    onclick: () => fileInput.click(),
    onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } },
    ondragover: (e) => { e.preventDefault(); dz.classList.add("hot"); },
    ondragleave: () => dz.classList.remove("hot"),
    ondrop: (e) => { e.preventDefault(); dz.classList.remove("hot"); uploadFiles(id, e.dataTransfer.files); },
  });
  main.append(dz, fileInput);

  // paste-to-attach anywhere in the detail view
  root._onPaste = (e) => {
    const items = [...(e.clipboardData?.items || [])].filter((i) => i.kind === "file");
    if (!items.length) return;
    e.preventDefault();
    uploadFiles(id, items.map((i) => i.getAsFile()).filter(Boolean));
  };

  // ── sidebar (controls + meta + tags) ──
  const side = el("div", { class: "d-side" });

  const statusSel = el("select", { class: "field", "aria-label": "Status",
    onchange: () => patch({ status: statusSel.value }) },
    ...STATUS.map(([v, l]) => el("option", { value: v, text: l })));
  statusSel.value = t.status;
  const prioSel = el("select", { class: "field", "aria-label": "Priority",
    onchange: () => patch({ priority: +prioSel.value }) },
    ...PRIO.map(([v, l]) => el("option", { value: v, text: l })));
  prioSel.value = t.priority;
  // due: a triage bucket (ASAP / Research / Hold) or a concrete date — one or
  // the other; picking either clears its counterpart.
  const modeBar = el("div", { class: "due-modes" });
  const refreshModes = () => {
    for (const b of modeBar.children) {
      if (b.dataset.mode === "asap") {       // lit while the due date is today
        b.classList.toggle("is-on", !!t.due_at && t.due_at.slice(0, 10) === todayEOD().slice(0, 10));
      } else {
        b.classList.toggle("is-on", b.dataset.mode === t.due_mode);
      }
    }
  };
  const dueInput = el("input", { type: "datetime-local", class: "field", "aria-label": "Due date",
    value: t.due_at ? t.due_at.slice(0, 16) : "",
    onchange: async () => {
      await patch({ due_at: dueInput.value ? dueInput.value : "" });
      if (dueInput.value) t.due_mode = "";
      t.due_at = dueInput.value || null;
      if (dueCard) dueCard.classList.remove("needs-due");
      refreshModes();
    } });
  // ASAP is a date shortcut — it just means "due today".
  modeBar.append(el("button", { class: "chip chip-mode", type: "button", "data-mode": "asap",
    title: "Due today",
    onclick: async () => {
      const eod = todayEOD();
      await patch({ due_at: eod });
      t.due_at = eod; t.due_mode = "";
      dueInput.value = eod;
      if (dueCard) dueCard.classList.remove("needs-due");
      refreshModes();
    } }, "ASAP"));
  for (const [v, label] of DUE_MODES) {
    modeBar.append(el("button", { class: "chip chip-mode", type: "button", "data-mode": v,
      onclick: async () => {
        const next = t.due_mode === v ? "" : v;         // click again to clear
        await patch({ due_mode: next });
        t.due_mode = next;
        if (next) { t.due_at = null; dueInput.value = ""; }
        if (dueCard) dueCard.classList.remove("needs-due");
        refreshModes();
      } }, label));
  }
  refreshModes();

  dueCard = el("div", { class: "card" },
    el("div", { class: "ctl" }, el("label", { text: "Status" }), statusSel),
    el("div", { class: "ctl" }, el("label", { text: "Priority" }), prioSel),
    el("div", { class: "ctl" }, el("label", { text: "Due" }), modeBar, dueInput));
  side.append(dueCard);

  // tags editor
  const tagWrap = el("div", { class: "tag-edit" });
  const renderTags = () => {
    tagWrap.replaceChildren();
    for (const tag of t.tags) {
      tagWrap.append(el("span", { class: "tag removable" },
        el("span", { class: "dot", style: "background:" + (tag.color || "var(--muted)") }),
        document.createTextNode(tag.name),
        el("span", { class: "x", title: "Remove", text: "✕",
          onclick: async () => {
            const names = t.tags.filter((x) => x.id !== tag.id).map((x) => x.name);
            await patch({ tags: names }); t = await api("tickets/" + id); renderTags();
          } })));
    }
    const add = el("input", { class: "tag-add", placeholder: "+ tag", "aria-label": "Add tag",
      onkeydown: async (e) => {
        if (e.key !== "Enter" || !add.value.trim()) return;
        const names = [...t.tags.map((x) => x.name), add.value.trim()];
        await patch({ tags: names }); t = await api("tickets/" + id); renderTags();
      } });
    tagWrap.append(add);
  };
  renderTags();
  side.append(el("div", { class: "card" },
    el("div", { class: "section-label", text: "Tags" }), tagWrap));

  // requester — who reported this. Pick from the People database, or add one.
  const reqCard = el("div", { class: "card" },
    el("div", { class: "section-label", text: "Requester" }));
  const reqSel = el("select", { class: "field", "aria-label": "Requester" });
  const fillReq = (users) => {
    reqSel.replaceChildren(el("option", { value: "", text: "— none —" }));
    for (const u of users) reqSel.append(el("option", { value: String(u.id), text: u.name }));
    reqSel.append(el("option", { value: "__new", text: "＋ Add a new person…" }));
    reqSel.value = t.user ? String(t.user.id) : "";
  };
  reqCard.append(reqSel);
  const reqMeta = el("div", { class: "req-meta" });
  const showReqMeta = () => {
    reqMeta.replaceChildren();
    if (!t.user) return;
    if (t.user.email) reqMeta.append(metaRow("Email", t.user.email));
    if (t.user.phone) reqMeta.append(metaRow("Phone", t.user.phone));
    if (t.user.dept) reqMeta.append(metaRow("Dept", t.user.dept));
  };
  reqSel.addEventListener("change", async () => {
    if (reqSel.value === "__new") { location.hash = "#/people"; return; }
    const uid = reqSel.value ? +reqSel.value : null;
    await patch({ user_id: uid });
    const users = await usersList(true);
    t.user = users.find((u) => u.id === uid) || null;
    fillReq(users);
    showReqMeta();
  });
  reqCard.append(reqMeta);
  side.append(reqCard);
  usersList().then((users) => { fillReq(users); showReqMeta(); });

  // meta
  side.append(el("div", { class: "card" },
    el("div", { class: "section-label", text: "Details" }),
    metaRow("Created", fmtStamp(t.created_at)),
    metaRow("Updated", fmtStamp(t.updated_at)),
    metaRow("Age", fmtAge(t.age_seconds), t.overdue),
    t.resolved_at ? metaRow("Resolved", fmtStamp(t.resolved_at)) : null,
    t.status === "resolved" && t.resolved_at
      ? metaRow("Auto-close", fmtCloseIn(t.resolved_at)) : null,
    t.deleted_at ? null : el("button", {
      class: "btn btn-danger", style: "margin-top:12px;width:100%",
      text: "Move to trash",
      onclick: async () => {
        try { await mut("DELETE", "tickets/" + id); location.hash = "#/"; }
        catch (e) { toast(e); }
      },
    })));

  root.append(el("div", { class: "detail-grid" }, main, side));
  mount(root);

  // wire paste for this detail render
  document.onpaste = root._onPaste;
}

function metaRow(k, v, over) {
  return el("div", { class: "meta-row" },
    el("span", { class: "k", text: k }),
    el("span", { class: "v" + (over ? " over" : ""), text: v }));
}

function attachTile(ticketId, a) {
  const isImg = (a.mime || "").startsWith("image/");
  const href = new URL("api/attachments/" + a.id, document.baseURI).href;
  const preview = isImg
    ? el("img", { src: href, alt: a.filename, loading: "lazy" })
    : el("div", { class: "file-ico", text: (a.filename.split(".").pop() || "file").toUpperCase().slice(0, 5) });
  return el("div", { class: "attach" },
    el("a", { href, target: "_blank", rel: "noopener", title: a.filename }, preview),
    el("span", { class: "att-name", text: a.filename }),
    el("button", { class: "att-del", title: "Remove attachment", "aria-label": "Remove attachment", text: "✕",
      onclick: async (e) => {
        e.preventDefault();
        try { await mut("DELETE", "attachments/" + a.id); renderDetail(ticketId); }
        catch (err) { toast(err); }
      } }));
}

async function uploadFiles(ticketId, files) {
  const arr = [...files];
  if (!arr.length) return;
  try {
    for (const f of arr) {
      const name = f.name || ("pasted-" + Date.now() + (f.type === "image/png" ? ".png" : ""));
      await fetch(new URL("api/tickets/" + ticketId + "/attachments?filename=" +
        encodeURIComponent(name), document.baseURI), {
        method: "POST",
        headers: { "Content-Type": f.type || "application/octet-stream" },
        body: await f.arrayBuffer(),
      }).then((r) => { if (!r.ok) throw new Error("upload failed"); });
    }
    renderDetail(ticketId);
  } catch (e) { toast(e); }
}

/* ── people (end users / requesters) ──────────────────── */
async function renderPeople() {
  mount(el("div", { class: "notice loading", text: "Loading…" }));
  let users;
  try { users = await usersList(true); }
  catch (e) { return toast(e); }

  const root = el("div", {});
  root.append(el("div", { class: "detail-top" },
    el("a", { class: "back", href: "#/" }, el("span", { text: "←" }),
      document.createTextNode("All tickets"))));
  root.append(el("h1", { class: "page-title", text: "People" }),
    el("p", { class: "page-sub",
      text: "Your end users. Add them as you go, then attach one to a ticket to track who's affected." }));

  // add form
  const f = {};
  const input = (key, ph, type) => (f[key] = el("input", {
    class: "field", type: type || "text", placeholder: ph, "aria-label": ph,
  }));
  const addBtn = el("button", { class: "btn btn-accent", text: "Add person",
    onclick: async () => {
      const body = {};
      for (const k of ["name", "email", "phone", "dept", "notes"]) body[k] = f[k].value.trim();
      if (!body.name) return toast(new Error("A name is required."));
      try { await mut("POST", "users", body); renderPeople(); }
      catch (e) { toast(e); }
    } });
  root.append(el("div", { class: "card person-form" },
    el("div", { class: "section-label", text: "Add a person" }),
    el("div", { class: "person-grid" },
      input("name", "Name *"), input("email", "Email", "email"),
      input("phone", "Phone", "tel"), input("dept", "Department / location")),
    input("notes", "Notes"),
    el("div", { style: "margin-top:10px;display:flex;justify-content:flex-end" }, addBtn)));

  // roster
  if (!users.length) {
    root.append(el("div", { class: "notice" },
      el("div", { class: "big", text: "No people yet." }),
      el("div", { class: "sub", text: "Add your first end user above." })));
  }
  for (const u of users) root.append(personCard(u));
  mount(root);
}

function personCard(u) {
  const fields = {};
  const row = (key, label, type) => {
    const inp = fields[key] = el("input", {
      class: "field", type: type || "text", value: u[key] || "", "aria-label": label,
    });
    return el("div", { class: "ctl" }, el("label", { text: label }), inp);
  };
  const count = u.ticket_count || 0;
  const card = el("div", { class: "card person-card" },
    el("div", { class: "person-head" },
      el("span", { class: "person-name", text: u.name }),
      el("button", { class: "chip", title: "See this person's tickets",
        text: count + (count === 1 ? " ticket" : " tickets"),
        onclick: () => { F.user = u.id; F.userName = u.name; F.view = "all"; location.hash = "#/"; } })),
    el("div", { class: "person-grid" },
      row("name", "Name"), row("email", "Email", "email"),
      row("phone", "Phone", "tel"), row("dept", "Department / location")),
    row("notes", "Notes"),
    el("div", { class: "person-actions" },
      el("button", { class: "btn btn-accent", text: "Save",
        onclick: async () => {
          const body = {};
          for (const k of ["name", "email", "phone", "dept", "notes"]) body[k] = fields[k].value.trim();
          if (!body.name) return toast(new Error("A name is required."));
          try {
            await mut("PATCH", "users/" + u.id, body);
            const btn = card.querySelector(".btn-accent");
            btn.textContent = "Saved ✓"; setTimeout(() => { btn.textContent = "Save"; }, 1400);
            USER_CACHE = null;
          } catch (e) { toast(e); }
        } }),
      el("button", { class: "btn btn-danger", text: "Delete",
        onclick: async () => {
          if (!confirm("Delete " + u.name + "? Their tickets stay, just unassigned.")) return;
          try { await mut("DELETE", "users/" + u.id); renderPeople(); }
          catch (e) { toast(e); }
        } })));
  return card;
}

/* ── settings ─────────────────────────────────────────── */
async function renderSettings() {
  mount(el("div", { class: "notice loading", text: "Loading…" }));
  let s;
  try { s = await api("settings"); }
  catch (e) { return toast(e); }

  const root = el("div", {});
  root.append(el("div", { class: "detail-top" },
    el("a", { class: "back", href: "#/" }, el("span", { text: "←" }),
      document.createTextNode("All tickets"))));
  root.append(el("h1", { class: "page-title", text: "Settings" }));

  // auto-due
  const enable = el("input", { type: "checkbox", "aria-label": "Enable auto-due dates" });
  enable.checked = !!s.autodue_enabled;
  const slaRows = {};   // p -> { value input, unit select }
  const grid = el("div", { class: "sla-grid" });
  for (const [p, label] of PRIO) {
    const start = hoursToHuman(s.autodue_hours[p] ?? s.autodue_hours[String(p)] ?? 0);
    const preview = el("span", { class: "sla-preview" });
    const refresh = () => {
      preview.textContent = slaPhrase(+valInp.value || 0, unitSel.value);
    };
    const valInp = el("input", {
      class: "field sla-val", type: "number", min: "0", step: "1",
      value: String(start.value), "aria-label": label + " duration",
      oninput: refresh,
    });
    const unitSel = el("select", { class: "field sla-unit-sel", "aria-label": label + " unit",
      onchange: refresh },
      ...SLA_UNITS.map(([u]) => el("option", { value: u, text: u })));
    unitSel.value = start.unit;
    slaRows[p] = { valInp, unitSel };
    refresh();
    grid.append(el("div", { class: "sla-row" },
      el("label", { class: "sla-label", text: label }),
      el("div", { class: "sla-in" }, valInp, unitSel),
      preview));
  }
  const saveBtn = el("button", { class: "btn btn-accent", text: "Save settings",
    onclick: async () => {
      const hours = {};
      for (const [p] of PRIO) {
        const { valInp, unitSel } = slaRows[p];
        hours[p] = humanToHours(+valInp.value || 0, unitSel.value);
      }
      try {
        await mut("PATCH", "settings", { autodue_enabled: enable.checked, autodue_hours: hours });
        saveBtn.textContent = "Saved ✓"; setTimeout(() => { saveBtn.textContent = "Save settings"; }, 1400);
      } catch (e) { toast(e); }
    } });

  root.append(el("div", { class: "card" },
    el("div", { class: "section-label", text: "Automatic due dates" }),
    el("p", { class: "page-sub",
      text: "Give each priority a target turnaround. New tickets you don't set a date on get one automatically from their priority. Applies to new tickets only; set a duration to 0 for a priority that shouldn't get an automatic due date." }),
    el("label", { class: "toggle-row" }, enable,
      document.createTextNode("Auto-set due dates from priority")),
    grid,
    el("div", { style: "margin-top:14px;display:flex;justify-content:flex-end" }, saveBtn)));
  mount(root);
}

/* ── routing ──────────────────────────────────────────── */
function route() {
  document.onpaste = null;                 // clear detail-view paste handler
  const hash = location.hash.slice(1);
  const m = hash.match(/^\/t\/(\d+)/);
  if (m) renderDetail(+m[1]);
  else if (hash === "/people") renderPeople();
  else if (hash === "/settings") renderSettings();
  else renderList();
}
window.addEventListener("hashchange", route);

/* ── theme (light / dark / system) ────────────────────── */
const THEME_KEY = "ticketsplease.theme";
const themeMq = window.matchMedia("(prefers-color-scheme: dark)");
const themeOpts = document.querySelectorAll(".theme-seg .theme-opt");

function themePref() {                       // stored choice, else "system"
  const v = localStorage.getItem(THEME_KEY);
  return v === "light" || v === "dark" ? v : "system";
}
function applyTheme(pref) {
  const dark = pref === "dark" || (pref === "system" && themeMq.matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  themeOpts.forEach((b) =>
    b.setAttribute("aria-checked", String(b.dataset.themeChoice === pref)));
}
function setTheme(pref) {
  if (pref === "system") localStorage.removeItem(THEME_KEY);
  else localStorage.setItem(THEME_KEY, pref);
  applyTheme(pref);
}
themeOpts.forEach((b) =>
  b.addEventListener("click", () => setTheme(b.dataset.themeChoice)));
themeMq.addEventListener("change", () => {   // live-track OS when on "system"
  if (themePref() === "system") applyTheme("system");
});
applyTheme(themePref());

/* ── backup / restore (masthead) ──────────────────────── */
document.getElementById("restoreBtn").addEventListener("click", () =>
  document.getElementById("restoreFile").click());
document.getElementById("restoreFile").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  e.target.value = "";                     // allow re-picking the same file
  if (!f) return;
  let data;
  try { data = JSON.parse(await f.text()); }
  catch { return toast(new Error("That file isn't valid JSON — pick a TicketsPlease backup.")); }
  if (!data || !Array.isArray(data.tickets)) {
    return toast(new Error("That file isn't a TicketsPlease backup — no tickets in it."));
  }
  if (!confirm('Restore "' + f.name + '" (' + data.tickets.length + " tickets)?\n\n" +
      "This replaces ALL current data. A safety snapshot of the current data " +
      "is saved to data\\backups\\ first, so this is undoable.")) return;
  try {
    const st = await mut("POST", "import", data);
    alert("Restored " + st.total + " tickets.");
    if (location.hash && location.hash !== "#/") location.hash = "#/";
    else renderList();
  } catch (err) { toast(err); }
});

document.getElementById("searchForm").addEventListener("submit", (e) => {
  e.preventDefault();
  F.q = document.getElementById("searchInput").value.trim();
  if (location.hash.startsWith("#/t/")) location.hash = "#/"; else renderList();
});

route();
