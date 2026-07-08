"use strict";

// Bridge port — must match background.js. The extension always talks to the
// local dashboard bridge on this port.
const BRIDGE_PORT = 4173;

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

const urlEl        = document.getElementById("page-url");
const btn          = document.getElementById("import-btn");
const opStatus     = document.getElementById("op-status");
const opVerb       = document.getElementById("op-verb");
const bgHint       = document.getElementById("bg-hint");
const statusEl     = document.getElementById("status");
const noteEl       = document.getElementById("note");
const contextField = document.getElementById("context-field");

// ---------------------------------------------------------------------------
// State — three inputs drive a single render(); never poked independently.
// ---------------------------------------------------------------------------
//
//   activeTab    — the tab the popup opened over.
//   importState  — the (session-scoped) import record from the service worker:
//                  {status:"running"|"success"|"error", url, filename?, verb?,
//                   result?, startedAt?}. A single GLOBAL slot — imports are
//                  one-at-a-time across all tabs (bridge holds a process lock).
//   dupeMatch    — /dedupe-check hit for this URL, or null ("already in raw/").
//   localBusy    — transient verb for the in-popup Extract/convert phase, before
//                  the import is handed to the service worker. Cleared as soon as
//                  a real importState arrives.

let activeTab   = null;
let importState = null;
let dupeMatch   = null;
let localBusy   = null;

// Bridge timeout is 240s; allow 300s before declaring a running state stale.
const STALE_TIMEOUT_MS = 300_000;

// ---------------------------------------------------------------------------
// Compute exactly one view from the current state. Precedence is the whole
// design: whichever branch matches first wins, so no two panels ever coexist.
// ---------------------------------------------------------------------------

function computeView() {
  const url = activeTab?.url;
  if (!url || url.startsWith("chrome://") || url.startsWith("chrome-extension://")) {
    return { kind: "internal" };
  }

  const s = importState;
  const same  = s?.url && s.url === activeTab.url;
  const fresh = s?.startedAt ? Date.now() - s.startedAt < STALE_TIMEOUT_MS : true;

  if (localBusy)                                 return { kind: "importing", verb: localBusy };
  if (s?.status === "running" && same  && fresh) return { kind: "importing", verb: s.verb || "Importing…", bg: true };
  if (s?.status === "running" && !same && fresh) return { kind: "busy-other" };
  if (s?.status === "success" && same)           return { kind: "success", filename: s.filename };
  if (s?.status === "error"   && same)           return { kind: "error", msg: s.result };
  if (dupeMatch)                                 return { kind: "dupe", match: dupeMatch };
  return { kind: "idle" };
}

// ---------------------------------------------------------------------------
// Apply a view to the DOM. The ONLY place that writes UI. Idempotent — called
// by both the storage listener and the 2s poll, so it must not restart the
// verb-sweep animation or thrash the DOM on repeat calls with the same view.
// ---------------------------------------------------------------------------

function applyView(v) {
  // Button base label: a duplicate or a just-completed import both re-import
  // "anyway"; otherwise a plain first import. States below may hide/disable it.
  const anyway = !!dupeMatch || v.kind === "success";
  setButton(anyway ? "Import page anyway" : "Import this page", anyway);

  // Reset every region; each branch re-enables only what it needs.
  btn.style.display = "";
  btn.disabled = false;
  hideStatus();
  setOp(null);
  setNote(null);

  switch (v.kind) {
    case "internal":
      btn.disabled = true;
      setNote("Cannot import browser-internal pages.");
      break;
    case "importing":
      btn.style.display = "none"; // no import button while this tab imports
      setOp(v.verb, v.bg);
      break;
    case "busy-other":
      btn.disabled = true;
      setNote("Another import is running — try again shortly.");
      break;
    case "success":
      buildSuccess(v.filename);
      break;
    case "error":
      showStatusText("error", v.msg || "Import failed.");
      break;
    case "dupe":
      buildDupe(v.match);
      break;
    case "idle":
    default:
      break;
  }
}

function render() {
  applyView(computeView());
  ensurePolling();
}

// ---------------------------------------------------------------------------
// DOM writers (all idempotent)
// ---------------------------------------------------------------------------

function setButton(text, anyway) {
  if (btn.textContent !== text) btn.textContent = text;
  btn.classList.toggle("import-btn-anyway", anyway);
}

let lastVerb = null;
function setOp(verb, bg) {
  if (!verb) {
    opStatus.classList.remove("visible");
    bgHint.classList.remove("visible");
    return;
  }
  // Only touch textContent when the verb changes — otherwise the gold
  // verb-sweep animation restarts on every poll tick.
  if (verb !== lastVerb) { opVerb.textContent = verb; lastVerb = verb; }
  opStatus.classList.add("visible");
  bgHint.classList.toggle("visible", !!bg);
}

function setNote(text) {
  if (!text) { noteEl.classList.remove("visible"); return; }
  if (noteEl.textContent !== text) noteEl.textContent = text;
  noteEl.classList.add("visible");
}

function hideStatus() {
  statusEl.classList.remove("visible", "status--success", "status--warn", "status--error");
  statusEl.replaceChildren();
}

function showStatusText(variant, text) {
  statusEl.classList.remove("status--success", "status--warn", "status--error");
  statusEl.classList.add(`status--${variant}`, "visible");
  statusEl.textContent = text; // replaces any children
}

function buildSuccess(filename) {
  showStatusText("success", "Imported successfully!");
  if (filename) {
    // textContent only — `filename` comes from the bridge response and must
    // never be interpreted as markup.
    const fn = document.createElement("div");
    fn.className = "status-filename";
    fn.textContent = `raw/web/${filename}`;
    statusEl.appendChild(fn);
  }
}

// "Already imported" warning — head + link (deep-links to the dashboard's
// raw-file preview) + meta. Rendered inside the single #status panel.
function buildDupe(m) {
  statusEl.classList.remove("status--success", "status--error");
  statusEl.classList.add("status--warn", "visible");
  statusEl.replaceChildren();

  const head = document.createElement("div");
  head.className = "dupe-warning-head";
  head.textContent = "Already imported";
  statusEl.appendChild(head);

  // The dashboard has no auth-free file route, so deep-link its own previewer:
  // /?raw=<path> opens the raw modal.
  const link = document.createElement("a");
  link.className = "dupe-warning-link";
  link.href = "#";
  link.textContent = m.title || m.path;
  link.addEventListener("click", (e) => {
    e.preventDefault();
    const target = `http://localhost:${BRIDGE_PORT}/?raw=${encodeURIComponent(m.path)}`;
    chrome.tabs.create({ url: target });
  });
  statusEl.appendChild(link);

  const meta = document.createElement("div");
  meta.className = "dupe-warning-meta";
  const bits = [];
  if (m.imported) bits.push(`imported ${m.imported}`);
  bits.push(m.ingested ? "in wiki" : "pending ingest");
  meta.textContent = bits.join(" · ");
  statusEl.appendChild(meta);
}

// ---------------------------------------------------------------------------
// Poll storage while an import is in flight (defense-in-depth vs a missed
// onChanged, plus stale-timeout detection). Started/stopped from render().
// ---------------------------------------------------------------------------

let pollTimer = null;

function ensurePolling() {
  const inFlight = !!localBusy || importState?.status === "running";
  if (inFlight && !pollTimer) startPolling();
  else if (!inFlight && pollTimer) stopPolling();
}

function startPolling() {
  pollTimer = setInterval(async () => {
    const { importState: s } = await chrome.storage.session.get("importState");

    if (s?.status === "running") {
      const elapsed = Date.now() - (s.startedAt ?? 0);
      if (elapsed > STALE_TIMEOUT_MS) {
        await chrome.storage.session.remove("importState");
        importState = {
          status: "error",
          url: s.url,
          result:
            "Import timed out in the background. The file may still have been saved — check raw/web/ and run ingest.",
        };
        localBusy = null;
        render();
        return;
      }
      importState = s;
      localBusy = null;
      render();
      return;
    }

    // No longer running — reflect the final state and let render() stop polling.
    importState = s ?? null;
    if (s) localBusy = null;
    render();
  }, 2000);
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

// ---------------------------------------------------------------------------
// Duplicate detection — ask the bridge whether this page is already in raw/.
// A sub-100ms, model-free filesystem scan (the same /dedupe-check the dashboard
// uses). Only sets state; render() decides whether/when to show it.
// ---------------------------------------------------------------------------

async function checkDuplicate(url) {
  try {
    const resp = await fetch(`http://localhost:${BRIDGE_PORT}/dedupe-check`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "web", url }),
    });
    if (!resp.ok) return;
    const data = await resp.json();
    if (data && Array.isArray(data.matches) && data.matches.length > 0) {
      dupeMatch = data.matches[0];
      render();
    }
  } catch {
    /* Bridge down or offline — skip silently; importing still works. */
  }
}

// React to service worker storage writes in real time (popup stays open).
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "session" || !changes.importState) return;
  const nv = changes.importState.newValue ?? null;
  importState = nv;
  if (nv) localBusy = null; // a real record supersedes the transient extract phase
  render();
});

// ---------------------------------------------------------------------------
// Initialise: tab URL + any in-flight/completed import, then a dedupe check.
// ---------------------------------------------------------------------------

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  activeTab = tab;
  urlEl.textContent = tab?.url ?? "(no URL)";
  urlEl.title       = tab?.url ?? "";

  const { importState: s } = await chrome.storage.session.get("importState");
  if (s) {
    // Drop a running state left behind by a service worker that died mid-flight.
    if (s.status === "running" && Date.now() - (s.startedAt ?? 0) > STALE_TIMEOUT_MS) {
      await chrome.storage.session.remove("importState");
    } else {
      importState = s;
    }
  }

  render();

  // Warn if this page is already in the vault (non-blocking; skips internal pages).
  if (tab?.url && !tab.url.startsWith("chrome://") && !tab.url.startsWith("chrome-extension://")) {
    checkDuplicate(tab.url);
  }
}

// ---------------------------------------------------------------------------
// Import button click
// ---------------------------------------------------------------------------

function failImport(msg) {
  importState = { status: "error", result: msg, url: activeTab?.url };
  localBusy = null;
  render();
}

btn.addEventListener("click", async () => {
  if (!activeTab) return;

  // Enter the in-popup extract phase and clear any previous result. The remove
  // fires onChanged(null); localBusy survives it (null writes don't clear it).
  localBusy = "Extracting…";
  render();
  await chrome.storage.session.remove("importState");

  // Step 1 — grab raw HTML from the live page.
  let pageData;
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: activeTab.id },
      func: () => ({
        html:  document.documentElement.outerHTML,
        url:   location.href,
        title: document.title,
      }),
    });
    pageData = results?.[0]?.result;
  } catch (err) {
    failImport(`Could not access page: ${err.message}`);
    return;
  }

  if (!pageData?.html) {
    failImport("No page content returned.");
    return;
  }

  // Step 2 — Readability article extraction (runs in popup window).
  let article;
  try {
    const doc = new DOMParser().parseFromString(pageData.html, "text/html");
    article = new Readability(doc.cloneNode(true)).parse();
  } catch (err) {
    failImport(`Article extraction failed: ${err.message}`);
    return;
  }

  if (!article) {
    failImport("Could not extract article content. Try the paste import on the dashboard instead.");
    return;
  }

  // Step 3 — HTML → Markdown.
  let markdown;
  try {
    const td = new TurndownService({ headingStyle: "atx", bulletListMarker: "-" });
    markdown = `# ${article.title}\n\n${td.turndown(article.content)}`;
  } catch (err) {
    failImport(`Markdown conversion failed: ${err.message}`);
    return;
  }

  // Step 4 — Hand off to the service worker. Optimistically set the running
  // state so "Importing…" + the background hint show instantly; the worker's
  // identical write is idempotent. The fetch runs independently of this popup,
  // so the user can close it freely — the result lands in session storage.
  importState = { status: "running", url: pageData.url, verb: "Importing…", startedAt: Date.now() };
  localBusy = null;
  render();
  const context = (contextField?.value || "").trim();
  chrome.runtime.sendMessage({ type: "import", url: pageData.url, pasted_markdown: markdown, context });
});

init();
