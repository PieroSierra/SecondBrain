"use strict";

// Bridge port — must match background.js. The extension always talks to the
// local dashboard bridge on this port.
const BRIDGE_PORT = 4173;

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

const urlEl      = document.getElementById("page-url");
const btn        = document.getElementById("import-btn");
const opStatus   = document.getElementById("op-status");
const opVerb     = document.getElementById("op-verb");
const bgHint     = document.getElementById("bg-hint");
const statusEl   = document.getElementById("import-status");
const dupeEl     = document.getElementById("dupe-warning");

// ---------------------------------------------------------------------------
// UI state helpers
// ---------------------------------------------------------------------------

function showIdle() {
  btn.disabled = false;
  opStatus.classList.remove("visible");
  bgHint.style.display = "none";
  statusEl.className = "import-status";
  stopPolling();
}

function showRunning(verb) {
  btn.disabled = true;
  opVerb.textContent = verb;
  opStatus.classList.add("visible");
  bgHint.style.display = "none";
  statusEl.className = "import-status";
}

// Called once we've handed off to the service worker — popup can close freely.
function showRunningBackground() {
  btn.disabled = true;
  opVerb.textContent = "Importing…";
  opStatus.classList.add("visible");
  bgHint.style.display = "block";
  statusEl.className = "import-status";
  startPolling();
}

function showSuccess(filename) {
  stopPolling();
  btn.disabled = false;
  opStatus.classList.remove("visible");
  bgHint.style.display = "none";
  statusEl.className = "import-status import-status-success visible";
  // Build with textContent, never innerHTML — `filename` originates from the
  // bridge response and must not be interpreted as markup.
  statusEl.textContent = "Imported successfully!";
  if (filename) {
    const fn = document.createElement("div");
    fn.className = "status-filename";
    fn.textContent = `raw/web/${filename}`;
    statusEl.appendChild(fn);
  }
}

function showError(msg) {
  stopPolling();
  btn.disabled = false;
  opStatus.classList.remove("visible");
  bgHint.style.display = "none";
  statusEl.className = "import-status import-status-error visible";
  statusEl.textContent = msg;
}

// ---------------------------------------------------------------------------
// Poll storage while running (defense-in-depth against onChanged missing)
// ---------------------------------------------------------------------------

// Bridge timeout is 240s; allow 300s before declaring stale.
const STALE_TIMEOUT_MS = 300_000;
let pollTimer = null;

function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => {
    const { importState } = await chrome.storage.local.get("importState");
    if (!importState || importState.status !== "running") {
      stopPolling();
      if (importState) applyState(importState);
      return;
    }
    const elapsed = Date.now() - (importState.startedAt ?? 0);
    if (elapsed > STALE_TIMEOUT_MS) {
      stopPolling();
      await chrome.storage.local.remove("importState");
      showError(
        "Import timed out in the background. The file may still have been saved — check raw/web/ and run ingest."
      );
    }
  }, 2000);
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

// ---------------------------------------------------------------------------
// Reflect stored state (written by background.js service worker)
// ---------------------------------------------------------------------------

function applyState(state) {
  if (!state) { showIdle(); return; }
  switch (state.status) {
    case "running":
      showRunningBackground();
      break;
    case "success":
      // A finished import belongs to the page it was fired from. If the popup
      // is now open on a different page, stay quiet rather than showing a green
      // banner about some other page (imports are slow, so this is common).
      if (state.url && activeTab?.url && state.url !== activeTab.url) {
        showIdle();
      } else {
        showSuccess(state.filename ?? null);
      }
      break;
    case "error":
      showError(state.result ?? "Import failed.");
      break;
    default:
      showIdle();
  }
}

// React to service worker storage writes in real time (popup stays open).
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" || !changes.importState) return;
  applyState(changes.importState.newValue ?? null);
});

// ---------------------------------------------------------------------------
// Duplicate detection — ask the bridge whether this page is already in raw/
// ---------------------------------------------------------------------------
//
// A sub-100ms, model-free filesystem scan (the same /dedupe-check the dashboard
// uses). If the current URL was already imported we show a red box above the
// button and relabel it "Import page anyway" — a genuine re-import is still one
// click. The link hands off to the dashboard's raw-file preview modal.

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
      showDuplicate(data.matches);
    }
  } catch {
    /* Bridge down or offline — skip silently; importing still works. */
  }
}

function showDuplicate(matches) {
  const m = matches[0];
  dupeEl.innerHTML = "";

  const head = document.createElement("div");
  head.className = "dupe-warning-head";
  head.textContent = "Already imported";
  dupeEl.appendChild(head);

  // Link to the matched raw file. The dashboard has no auth-free file route,
  // so we deep-link its own previewer: /?raw=<path> opens the raw modal.
  const link = document.createElement("a");
  link.className = "dupe-warning-link";
  link.href = "#";
  link.textContent = m.title || m.path;
  link.addEventListener("click", (e) => {
    e.preventDefault();
    const target = `http://localhost:${BRIDGE_PORT}/?raw=${encodeURIComponent(m.path)}`;
    chrome.tabs.create({ url: target });
  });
  dupeEl.appendChild(link);

  const meta = document.createElement("div");
  meta.className = "dupe-warning-meta";
  const bits = [];
  if (m.imported) bits.push(`imported ${m.imported}`);
  bits.push(m.ingested ? "in wiki" : "pending ingest");
  meta.textContent = bits.join(" · ");
  dupeEl.appendChild(meta);

  dupeEl.classList.add("visible");
  btn.textContent = "Import page anyway"; // CSS uppercases it
  btn.classList.add("import-btn-anyway"); // red/clay
}

// ---------------------------------------------------------------------------
// Initialise: show tab URL + any persisted import state
// ---------------------------------------------------------------------------

let activeTab = null;

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  activeTab = tab;
  urlEl.textContent = tab?.url ?? "(no URL)";
  urlEl.title       = tab?.url ?? "";

  if (!tab?.url || tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://")) {
    btn.disabled = true;
    showError("Cannot import browser-internal pages.");
    return;
  }

  // Warn early if this page is already in the vault (non-blocking).
  checkDuplicate(tab.url);

  // Reflect any in-flight or completed import from the service worker.
  const { importState } = await chrome.storage.local.get("importState");
  if (importState) {
    // If it's a stale running state from a previous session, clear it.
    if (importState.status === "running") {
      const elapsed = Date.now() - (importState.startedAt ?? 0);
      if (elapsed > STALE_TIMEOUT_MS) {
        await chrome.storage.local.remove("importState");
        return; // leave as idle
      }
    }
    applyState(importState);
  }
}

// ---------------------------------------------------------------------------
// Import button click
// ---------------------------------------------------------------------------

btn.addEventListener("click", async () => {
  if (!activeTab) return;

  // Clear any previous result so the user starts fresh.
  await chrome.storage.local.remove("importState");

  showRunning("Extracting…");

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
    showError(`Could not access page: ${err.message}`);
    return;
  }

  if (!pageData?.html) {
    showError("No page content returned.");
    return;
  }

  // Step 2 — Readability article extraction (runs in popup window).
  let article;
  try {
    const doc = new DOMParser().parseFromString(pageData.html, "text/html");
    article = new Readability(doc.cloneNode(true)).parse();
  } catch (err) {
    showError(`Article extraction failed: ${err.message}`);
    return;
  }

  if (!article) {
    showError("Could not extract article content. Try the paste import on the dashboard instead.");
    return;
  }

  // Step 3 — HTML → Markdown.
  let markdown;
  try {
    const td = new TurndownService({ headingStyle: "atx", bulletListMarker: "-" });
    markdown = `# ${article.title}\n\n${td.turndown(article.content)}`;
  } catch (err) {
    showError(`Markdown conversion failed: ${err.message}`);
    return;
  }

  // Step 4 — Hand off to service worker. The fetch runs independently of this
  // popup, so the user can close it freely. The result lands in storage.
  showRunningBackground();
  const context = (document.getElementById("context-field")?.value || "").trim();
  chrome.runtime.sendMessage({ type: "import", url: pageData.url, pasted_markdown: markdown, context });
});

init();
