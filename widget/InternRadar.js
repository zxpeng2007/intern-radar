// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: teal; icon-glyph: briefcase;
//
// InternRadar.js - iPhone home-screen widget for intern-radar
// (https://github.com/zxpeng2007/intern-radar).
//
// Reads {BASE_URL}/widget.json, which refresh.py writes on your computer, and
// shows how many internships are open to you for your class year, how many
// were posted this week, and the newest postings. Tap a posting to open its
// apply page; tap anywhere else to open the full list.
//
// Your phone and your computer must both be on your Tailscale network (the
// server only answers there). There is no token: the list is public job
// postings, and nobody outside your tailnet can reach the server anyway.
//
// Requires: Scriptable (free, App Store). Single file, no imports.

// ===========================================================================
// EDIT THIS ONE
// ===========================================================================

/** Address of the computer running serve.py, as shown by `tailscale ip -4`
 *  on that machine, plus the port from config.json. */
const BASE_URL = "http://100.x.y.z:8765";

// ===========================================================================
// Everything below is optional tuning.
// ===========================================================================

/** Class year shown when the widget has no parameter. "" means the first
 *  year listed in config.json on the computer. Override per widget from the
 *  home screen: long-press -> Edit Widget -> Parameter, e.g. "2030" or
 *  "any". A category can follow a comma: "2029,quant" or "2030,swe". */
const DEFAULT_VIEW = "";

/** Hint to iOS for the next refresh. A floor, not a promise. */
const REFRESH_MINUTES = 60;

/** Fail fast and fall back to the cache rather than letting iOS kill a hung widget. */
const REQUEST_TIMEOUT_SECONDS = 12;

/** Folder under Scriptable's local documents holding the last good payload. */
const CACHE_DIR_NAME = "intern-radar-widget";

/** Size offered first when you run the script inside the Scriptable app. */
const PREVIEW_FAMILY = "medium";

/** Postings older than this many days no longer get the green dot. */
const NEW_DAYS = 7;

// ---------------------------------------------------------------------------
// Palette - mirrors template.html so the widget and the page read as one product.
// ---------------------------------------------------------------------------

function dyn(light, dark) {
  return Color.dynamic(new Color(light), new Color(dark));
}

const C = {
  surface: dyn("#F4F5F7", "#1B1F26"),
  ink: dyn("#1A1D24", "#E7E9ED"),
  muted: dyn("#5C6370", "#9AA3AF"),
  line: dyn("#D9DDE3", "#2C323B"),
  accent: dyn("#1D5C8C", "#6CA6DA"),
  ok: dyn("#2E7D4F", "#7CC69A"),
  warn: dyn("#B23A2A", "#E8887A"),
  chipBg: dyn("#E3EEF7", "#1E2E3D"),
  cat: {
    "Software": dyn("#1D5C8C", "#8FBDE6"),
    "AI/ML/Data": dyn("#5A3E9E", "#B9A3EA"),
    "Quant": dyn("#9A5B00", "#E5B45B"),
    "Hardware": dyn("#1F6E5C", "#7CCDB7"),
    "Product": dyn("#8A3B6E", "#E39BC9"),
  },
};

const CAT_SHORT = { "Software": "SWE", "AI/ML/Data": "AI/ML", "Quant": "Quant", "Hardware": "HW", "Product": "PM" };
const CAT_ALIASES = {
  quant: "Quant", q: "Quant",
  swe: "Software", software: "Software", sw: "Software",
  ai: "AI/ML/Data", ml: "AI/ML/Data", data: "AI/ML/Data",
  hw: "Hardware", hardware: "Hardware",
  pm: "Product", product: "Product",
};

function font(name, size) {
  try {
    const f = Font[name];
    if (typeof f === "function") return f(size);
  } catch (e) {
    // fall through
  }
  return Font.systemFont(size);
}

const F = {
  hero: (s) => font("boldRoundedSystemFont", s),
  strong: (s) => font("semiboldSystemFont", s),
  body: (s) => font("regularSystemFont", s),
  mono: (s) => font("regularMonospacedSystemFont", s),
};

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

function shortTime(date) {
  try {
    const df = new DateFormatter();
    df.useNoDateStyle();
    df.useShortTimeStyle();
    return df.string(date);
  } catch (e) {
    try {
      return date.toLocaleTimeString();
    } catch (e2) {
      return "";
    }
  }
}

function stamp(date) {
  const now = new Date();
  const sameDay = date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
  if (sameDay) return shortTime(date);
  try {
    const df = new DateFormatter();
    df.useMediumDateStyle();
    df.useShortTimeStyle();
    return df.string(date);
  } catch (e) {
    return shortTime(date);
  }
}

function ageDays(posted) {
  const d = new Date(String(posted) + "T12:00:00");
  if (isNaN(d.getTime())) return 999;
  return Math.max(0, Math.round((Date.now() - d.getTime()) / 86400000));
}

function ageLabel(posted) {
  const days = ageDays(posted);
  if (days >= 999) return "";
  if (days === 0) return "today";
  return `${days}d`;
}

/** Titles repeat "Intern" and the season; trim them so the role fits one line. */
function shortTitle(title) {
  return String(title || "")
    .replace(/\s*[-–]\s*(Summer|Fall|Spring|Winter)\s*20\d\d\b/i, "")
    .replace(/\bInternship\b/i, "Intern")
    .replace(/\s{2,}/g, " ")
    .trim();
}

// ---------------------------------------------------------------------------
// Parameter: "<view>[,<category>]"
// ---------------------------------------------------------------------------

function resolveParam(payload) {
  const years = Array.isArray(payload && payload.class_years) ? payload.class_years.map(String) : [];
  const out = { view: DEFAULT_VIEW || (payload && payload.default_view) || years[0] || "any", category: null };
  let raw = args.widgetParameter;
  if (raw === null || raw === undefined) return out;
  raw = String(raw).trim().toLowerCase();
  if (!raw) return out;
  for (const p of raw.split(/[,\s]+/).filter(Boolean)) {
    if (p === "any" || /^20\d\d$/.test(p)) out.view = p;
    else if (CAT_ALIASES[p]) out.category = CAT_ALIASES[p];
  }
  return out;
}

// ---------------------------------------------------------------------------
// Network
// ---------------------------------------------------------------------------

function widgetError(kind, title, detail) {
  return { isWidgetError: true, kind: kind, title: title, detail: detail };
}

async function fetchPayload() {
  const base = String(BASE_URL || "").replace(/\/+$/, "");
  if (!base || /100\.x\.y\.z/.test(base)) {
    throw widgetError("config", "Set BASE_URL", "Open this script in Scriptable and set BASE_URL to your computer's Tailscale address, e.g. http://100.101.102.103:8765");
  }
  const req = new Request(`${base}/widget.json`);
  req.method = "GET";
  req.timeoutInterval = REQUEST_TIMEOUT_SECONDS;
  req.headers = { Accept: "application/json" };

  let body;
  try {
    body = await req.loadString();
  } catch (e) {
    throw widgetError("network", "Can't reach the computer", "Tailscale may be off on the phone or the computer, or the computer is asleep or not running serve.py.");
  }
  const status = (req.response && req.response.statusCode) || 0;
  if (status === 404) throw widgetError("other", "No widget.json yet", "Run  python refresh.py  on the computer to build it.");
  if (status < 200 || status >= 300) throw widgetError("other", "Server error", `The computer answered ${status}.`);
  try {
    return JSON.parse(body);
  } catch (e) {
    throw widgetError("other", "Bad response", "The reply was not valid JSON.");
  }
}

// ---------------------------------------------------------------------------
// Cache
// ---------------------------------------------------------------------------

function cachePath() {
  const fm = FileManager.local();
  const dir = fm.joinPath(fm.documentsDirectory(), CACHE_DIR_NAME);
  if (!fm.fileExists(dir)) fm.createDirectory(dir, true);
  return { fm: fm, path: fm.joinPath(dir, "widget.json") };
}

function writeCache(payload) {
  try {
    const { fm, path } = cachePath();
    fm.writeString(path, JSON.stringify({ saved_at: new Date().toISOString(), payload: payload }));
  } catch (e) {
    // never worth failing the render over
  }
}

function readCache() {
  try {
    const { fm, path } = cachePath();
    if (!fm.fileExists(path)) return null;
    const wrapper = JSON.parse(fm.readString(path));
    if (!wrapper || !wrapper.payload) return null;
    const savedAt = new Date(wrapper.saved_at);
    return { payload: wrapper.payload, savedAt: isNaN(savedAt.getTime()) ? new Date(fm.modificationDate(path)) : savedAt };
  } catch (e) {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Payload -> model for one view
// ---------------------------------------------------------------------------

function num(v, fallback) {
  if (v === null || v === undefined || v === "") return fallback;
  const n = Number(v);
  return isFinite(n) ? n : fallback;
}

function normalize(raw, param) {
  const data = raw && typeof raw === "object" ? raw : {};
  const views = data.views && typeof data.views === "object" ? data.views : {};
  const view = views[param.view] || views[data.default_view] || views["any"] || {};
  const byCat = view.by_category && typeof view.by_category === "object" ? view.by_category : {};
  const categories = Array.isArray(data.categories) ? data.categories : Object.keys(byCat);
  const catOpen = (c) => { const v = byCat[c]; return v && typeof v === "object" ? num(v.open, 0) : num(v, 0); };
  const catNew7 = (c) => { const v = byCat[c]; return v && typeof v === "object" ? num(v.new7, 0) : 0; };

  let newest = (Array.isArray(data.newest) ? data.newest : []).filter((p) => p && typeof p === "object");
  if (param.view !== "any") newest = newest.filter((p) => !(p.ok && p.ok[param.view] === false) && p["ok" + param.view] !== false);
  if (param.category) newest = newest.filter((p) => p.category === param.category);

  let generated = null;
  if (data.generated_at) {
    const d = new Date(data.generated_at);
    if (!isNaN(d.getTime())) generated = d;
  }

  return {
    season: String(data.season || "Internships"),
    viewLabel: param.view === "any" ? "any class year" : `class of ${param.view}`,
    categoryLabel: param.category ? CAT_SHORT[param.category] || param.category : null,
    open: param.category ? catOpen(param.category) : num(view.open, 0),
    new7: param.category ? catNew7(param.category) : num(view.new7, 0),
    excluded: num(view.excluded, 0),
    catLine: categories.map((c) => `${catOpen(c)} ${(CAT_SHORT[c] || c).toLowerCase()}`).join(" · "),
    newest: newest,
    siteUrl: String(data.site_url || BASE_URL).replace(/\/+$/, ""),
    generated: generated,
    snapshot: data.snapshot || null,
  };
}

// ---------------------------------------------------------------------------
// Building blocks
// ---------------------------------------------------------------------------

function addLabel(stack, text, size, color, opts) {
  const t = stack.addText(String(text));
  t.font = (opts && opts.font) || F.body(size);
  t.textColor = color;
  t.lineLimit = (opts && opts.lineLimit) || 1;
  if (opts && opts.minimumScaleFactor) t.minimumScaleFactor = opts.minimumScaleFactor;
  return t;
}

function addChip(stack, text, color) {
  const chip = stack.addStack();
  chip.backgroundColor = C.chipBg;
  chip.cornerRadius = 4;
  chip.setPadding(1, 5, 1, 5);
  const t = chip.addText(text);
  t.font = F.mono(9);
  t.textColor = color;
  t.lineLimit = 1;
  return chip;
}

function addHeadline(stack, m, size) {
  const n = stack.addText(String(m.open));
  n.font = F.hero(size);
  n.textColor = C.ink;
  n.lineLimit = 1;
  n.minimumScaleFactor = 0.6;
  const what = m.categoryLabel ? `${m.categoryLabel} roles open` : "US roles open";
  addLabel(stack, `${what} · ${m.viewLabel}`, 10, C.muted, { minimumScaleFactor: 0.7 });
}

function addSubline(stack, m, size) {
  const row = stack.addStack();
  row.layoutHorizontally();
  row.centerAlignContent();
  const a = row.addText(`${m.new7} new this week`);
  a.font = F.strong(size);
  a.textColor = m.new7 > 0 ? C.ok : C.muted;
  a.lineLimit = 1;
  if (m.excluded > 0) {
    row.addSpacer(6);
    const b = row.addText(`${m.excluded} exclude you`);
    b.font = F.body(size);
    b.textColor = C.warn;
    b.lineLimit = 1;
    b.minimumScaleFactor = 0.7;
  }
}

/** One posting row. Tap opens the apply page. Right-aligned text is a
 *  horizontal stack with a leading spacer, which is all Scriptable offers. */
function addPostingRow(stack, p, compact) {
  const row = stack.addStack();
  row.layoutHorizontally();
  row.centerAlignContent();
  if (p.url) row.url = String(p.url);

  const dot = row.addText("●");
  dot.font = F.body(7);
  dot.textColor = ageDays(p.posted) <= NEW_DAYS ? C.ok : C.line;
  row.addSpacer(5);

  const text = row.addStack();
  text.layoutVertically();
  const co = text.addText(String(p.company || ""));
  co.font = F.strong(compact ? 11 : 12);
  co.textColor = C.ink;
  co.lineLimit = 1;
  co.minimumScaleFactor = 0.8;
  const role = text.addText(shortTitle(p.title));
  role.font = F.body(compact ? 10 : 11);
  role.textColor = C.muted;
  role.lineLimit = 1;
  role.minimumScaleFactor = 0.8;

  row.addSpacer();

  const right = row.addStack();
  right.layoutVertically();
  if (!compact) {
    const chips = right.addStack();
    chips.layoutHorizontally();
    chips.spacing = 3;
    chips.addSpacer();
    addChip(chips, CAT_SHORT[p.category] || String(p.category || ""), C.cat[p.category] || C.accent);
    if (p.grad) addChip(chips, String(p.grad), C.ok);
    right.addSpacer(2);
  }
  const ageRow = right.addStack();
  ageRow.layoutHorizontally();
  ageRow.addSpacer();
  const age = ageRow.addText(ageLabel(p.posted));
  age.font = F.mono(9);
  age.textColor = C.muted;
  age.lineLimit = 1;
}

function addStaleFooter(widget, cached, err) {
  const reason = err && err.kind === "network" ? "offline" : "stale";
  const t = widget.addText(`⚠ ${reason} · as of ${stamp(cached.savedAt)}`);
  t.font = F.body(9);
  t.textColor = C.warn;
  t.lineLimit = 1;
  t.minimumScaleFactor = 0.7;
}

function addSnapshotFooter(widget, m) {
  const row = widget.addStack();
  row.layoutHorizontally();
  row.centerAlignContent();
  const when = m.generated ? stamp(m.generated) : m.snapshot || "";
  addLabel(row, `list refreshed ${when}`, 9, C.muted);
  row.addSpacer();
  addLabel(row, m.catLine, 9, C.muted, { font: F.mono(9), minimumScaleFactor: 0.7 });
}

// ---------------------------------------------------------------------------
// Layouts
// ---------------------------------------------------------------------------

function newWidget(m) {
  const w = new ListWidget();
  w.backgroundColor = C.surface;
  w.setPadding(12, 13, 12, 13);
  w.refreshAfterDate = new Date(Date.now() + REFRESH_MINUTES * 60 * 1000);
  w.url = (m && m.siteUrl) || String(BASE_URL || "").replace(/\/+$/, "");
  return w;
}

function buildSmall(m, cached, err) {
  const w = newWidget(m);
  addLabel(w, m.season.toUpperCase() + " INTERNS", 9, C.muted, { minimumScaleFactor: 0.7 });
  w.addSpacer(2);
  addHeadline(w, m, 30);
  w.addSpacer(6);
  addSubline(w, m, 11);
  w.addSpacer();
  for (const p of m.newest.slice(0, 2)) {
    addPostingRow(w, p, true);
    w.addSpacer(3);
  }
  if (cached) addStaleFooter(w, cached, err);
  return w;
}

function buildMedium(m, cached, err) {
  const w = newWidget(m);
  const body = w.addStack();
  body.layoutHorizontally();
  body.spacing = 12;

  const left = body.addStack();
  left.layoutVertically();
  left.size = new Size(120, 0);
  addLabel(left, m.season.toUpperCase(), 9, C.muted, { minimumScaleFactor: 0.7 });
  left.addSpacer(2);
  addHeadline(left, m, 30);
  left.addSpacer(6);
  addSubline(left, m, 10);
  left.addSpacer();
  addLabel(left, m.catLine, 9, C.muted, { font: F.mono(9), minimumScaleFactor: 0.7 });

  const right = body.addStack();
  right.layoutVertically();
  addLabel(right, "NEWEST", 9, C.muted);
  right.addSpacer(4);
  const top = m.newest.slice(0, 3);
  if (top.length === 0) addLabel(right, "nothing new for this view", 11, C.muted);
  for (let i = 0; i < top.length; i++) {
    if (i > 0) right.addSpacer(4);
    addPostingRow(right, top[i], true);
  }
  right.addSpacer();

  if (cached) {
    w.addSpacer(4);
    addStaleFooter(w, cached, err);
  }
  return w;
}

function buildLarge(m, cached, err) {
  const w = newWidget(m);
  const head = w.addStack();
  head.layoutHorizontally();
  head.centerAlignContent();
  addLabel(head, m.season.toUpperCase() + " INTERNS", 10, C.muted, { minimumScaleFactor: 0.7 });
  head.addSpacer();
  addLabel(head, m.viewLabel, 10, C.accent, { font: F.strong(10) });

  w.addSpacer(4);
  const hero = w.addStack();
  hero.layoutHorizontally();
  hero.bottomAlignContent();
  const n = hero.addText(String(m.open));
  n.font = F.hero(38);
  n.textColor = C.ink;
  n.lineLimit = 1;
  hero.addSpacer(8);
  const side = hero.addStack();
  side.layoutVertically();
  addLabel(side, m.categoryLabel ? `${m.categoryLabel} roles open to you` : "US roles open to you", 11, C.muted);
  addSubline(side, m, 11);
  side.addSpacer(4);
  hero.addSpacer();

  w.addSpacer(8);
  addLabel(w, "NEWEST POSTINGS", 9, C.muted);
  w.addSpacer(5);
  const top = m.newest.slice(0, 7);
  if (top.length === 0) addLabel(w, "nothing new for this view", 12, C.muted);
  for (let i = 0; i < top.length; i++) {
    if (i > 0) w.addSpacer(5);
    addPostingRow(w, top[i], false);
  }
  w.addSpacer();
  if (cached) {
    addStaleFooter(w, cached, err);
    w.addSpacer(3);
  }
  addSnapshotFooter(w, m);
  return w;
}

function buildFor(family, m, cached, err) {
  if (family === "small") return buildSmall(m, cached, err);
  if (family === "large" || family === "extraLarge") return buildLarge(m, cached, err);
  return buildMedium(m, cached, err);
}

// ---------------------------------------------------------------------------
// Failure states
// ---------------------------------------------------------------------------

function buildErrorWidget(family, err, cached) {
  const w = newWidget(null);
  const compact = family === "small";
  const head = w.addStack();
  head.layoutHorizontally();
  head.centerAlignContent();
  const icon = head.addText(err.kind === "config" ? "⛔" : "⚠");
  icon.font = F.body(compact ? 13 : 15);
  icon.textColor = C.warn;
  head.addSpacer(5);
  const title = head.addText(err.title);
  title.font = F.strong(compact ? 13 : 15);
  title.textColor = C.ink;
  title.lineLimit = 2;
  title.minimumScaleFactor = 0.7;

  w.addSpacer(6);
  const detail = w.addText(err.detail);
  detail.font = F.body(compact ? 10 : 12);
  detail.textColor = C.muted;
  detail.lineLimit = compact ? 4 : 5;
  detail.minimumScaleFactor = 0.7;

  w.addSpacer();
  const foot = w.addText(cached ? `Last good data ${stamp(cached.savedAt)} · checked ${shortTime(new Date())}` : `Intern Radar · checked ${shortTime(new Date())}`);
  foot.font = F.body(9);
  foot.textColor = C.muted;
  foot.lineLimit = 2;
  foot.minimumScaleFactor = 0.7;
  return w;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

async function buildWidget(family) {
  let payload = null;
  let err = null;
  try {
    payload = await fetchPayload();
    writeCache(payload);
  } catch (e) {
    err = e && e.isWidgetError ? e : widgetError("other", "Unexpected error", String((e && e.message) || e));
  }
  if (payload) return buildFor(family, normalize(payload, resolveParam(payload)), null, null);

  const cached = readCache();
  if (cached && err.kind !== "config") {
    try {
      return buildFor(family, normalize(cached.payload, resolveParam(cached.payload)), cached, err);
    } catch (e) {
      // fall through to the plain error tile
    }
  }
  return buildErrorWidget(family, err, cached);
}

async function chooseFamily() {
  const families = ["small", "medium", "large"];
  try {
    const alert = new Alert();
    alert.title = "Preview size";
    for (const f of families) alert.addAction(f);
    alert.addCancelAction("Cancel");
    const idx = await alert.presentSheet();
    if (idx >= 0 && idx < families.length) return families[idx];
  } catch (e) {
    // no UI available
  }
  return PREVIEW_FAMILY;
}

async function main() {
  const family = config.widgetFamily || (config.runsInApp ? await chooseFamily() : PREVIEW_FAMILY);
  let widget;
  try {
    widget = await buildWidget(family);
  } catch (e) {
    widget = buildErrorWidget(family, widgetError("other", "Widget error", String((e && e.message) || e)));
  }
  if (config.runsInWidget) {
    Script.setWidget(widget);
  } else if (family === "small") {
    await widget.presentSmall();
  } else if (family === "large" || family === "extraLarge") {
    await widget.presentLarge();
  } else {
    await widget.presentMedium();
  }
  Script.complete();
}

await main();
