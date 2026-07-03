/*
 * Second Brain dashboard — front-end controller
 * Spec: specs/002-interactive-dashboard/spec.md
 * Contract: specs/002-interactive-dashboard/contracts/bridge-http.md
 *
 * No framework. ES module loaded after the vendored `marked` global.
 */

// ---------------------------------------------------------------------------
// Shared utilities
// ---------------------------------------------------------------------------

const $ = (sel, root = document) => root.querySelector(sel);

// Bridge CSRF token — injected into the index.html <meta> server-side and
// readable only by same-origin scripts. Sent on every bridge request so the
// bridge can distinguish the real dashboard from a cross-origin forgery.
const BRIDGE_TOKEN =
  document.querySelector('meta[name="bridge-token"]')?.content || "";

// fetch() wrapper that attaches the bridge token. Use for ALL same-origin
// bridge calls (GET and POST); the bridge rejects unauthenticated requests
// to every endpoint except the static shell.
function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (BRIDGE_TOKEN) headers.set("X-Bridge-Token", BRIDGE_TOKEN);
  return fetch(path, { ...options, headers });
}

async function postJSON(path, body) {
  const res = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let data;
  try {
    data = await res.json();
  } catch {
    data = { error: "bad_response", detail: `HTTP ${res.status} (non-JSON)` };
  }
  return { status: res.status, data };
}

async function postMultipart(path, formData) {
  const res = await apiFetch(path, { method: "POST", body: formData });
  let data;
  try {
    data = await res.json();
  } catch {
    data = { error: "bad_response", detail: `HTTP ${res.status} (non-JSON)` };
  }
  return { status: res.status, data };
}

// --- Markdown rendering ---------------------------------------------------

// Escape HTML special chars so untrusted text can't break out of an
// attribute or inject markup.
function escapeHtml(s) {
  return String(s).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]),
  );
}

// Sanitise rendered HTML before it ever reaches innerHTML. marked does NOT
// sanitise — wiki/query/output content is partly derived from imported web
// pages, so a poisoned article could otherwise smuggle <script>/onerror into
// the dashboard (same origin as the bridge). DOMPurify strips all of that;
// the CSP header is the second line of defence.
function sanitizeHtml(html) {
  if (typeof window.DOMPurify !== "undefined") {
    return window.DOMPurify.sanitize(html);
  }
  // DOMPurify failed to load — fail closed by neutralising markup entirely
  // rather than rendering unsanitised HTML.
  return escapeHtml(html);
}

// marked + DOMPurify are loaded globally via <script src="/static/lib/...">.
function renderMarkdown(md) {
  if (!md) return "";
  if (typeof window.marked === "undefined") {
    // Fallback: plain text in a <pre>.
    const pre = document.createElement("pre");
    pre.textContent = md;
    return pre.outerHTML;
  }
  // Turn [[wikilink]] tokens into clickable <a> tags everywhere they appear
  // (query answers, output/lint viewers, wiki articles). A single delegated
  // handler routes the click by slug prefix: `raw/…` opens the raw file
  // modal, `wiki/…`/bare open the wiki article. The captured name is
  // HTML-escaped so a crafted [[...]] can't inject markup.
  const withWikilinks = md.replace(
    /\[\[([^\]\n]+)\]\]/g,
    (_, name) => `<a class="wikilink" href="#" data-wiki-slug="${escapeHtml(name)}">${escapeHtml(name)}</a>`,
  );
  const html = window.marked.parse(withWikilinks, { gfm: true, breaks: false });
  return sanitizeHtml(html);
}

// Render a "could not load" message without using innerHTML on a template
// string (which would interpolate err.message into markup). Colour is set via
// the CSSOM, which CSP does not restrict.
function showLoadError(bodyEl, message) {
  if (!bodyEl) return;
  bodyEl.textContent = "";
  const p = document.createElement("p");
  p.textContent = `Could not load: ${message}`;
  p.style.color = "var(--error-ink)";
  bodyEl.appendChild(p);
}

// --- Op-status (per-form running / done / error) -------------------------
//
// Replaces the old global busy and error banners. Each form owns one
// [data-op-status] node; we drive its className + textContent. Three terminal
// states: running (pulsing dot + verb + elapsed), done (filled dot + "Thought
// for 47s"), error (clay dot + message). Hidden until the form is used.

const OP_VERBS = {
  query: [
    "Pondering", "Cogitating", "Ruminating", "Mulling", "Rummaging",
    "Distilling", "Lucubrating", "Synthesising", "Excogitating", "Marinating",
  ],
  "md-add": ["Filing", "Cataloguing", "Indexing", "Shelving", "Pinning"],
  "craft-import": ["Fetching", "Plucking", "Harvesting", "Retrieving", "Extracting"],
  "pdf-import": ["Decanting", "Liberating", "Transcribing", "Extracting", "Unfurling"],
  "file-import": ["Scanning", "Reading", "Importing", "Processing", "Absorbing"],
  "web-import": ["Fetching", "Reading", "Scraping", "Distilling", "Threshing"],
  ingest: [
    "Folding", "Weaving", "Knitting", "Stitching", "Crystallising", "Composting",
  ],
  lint: ["Scrutinising", "Auditing", "Surveying", "Inventorying", "Combing"],
  "wiki-edit": ["Editing", "Revising", "Updating", "Correcting", "Refining"],
};

const DONE_VERB = {
  query: "Thought",
  "md-add": "Filed",
  "craft-import": "Imported",
  "pdf-import": "Decanted",
  "file-import": "Imported",
  "web-import": "Fetched",
  ingest: "Ingested",
  lint: "Linted",
  "wiki-edit": "Revised",
};

const _opTimers = new WeakMap(); // node -> {intervalId, startedAt}

function pickVerb(kind) {
  const pool = OP_VERBS[kind] || ["Working"];
  return pool[Math.floor(Math.random() * pool.length)];
}

function fmtElapsed(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

function opRunning(node, kind) {
  if (!node) return;
  let verb = pickVerb(kind);
  let lastVerb = verb;
  let ticks = 0;
  const startedAt = Date.now();
  node.className = "op-status op-status-running";
  node.hidden = false;

  // Build static structure once: a verb span (which carries the colour
  // sweep, including the trailing ellipsis) and a calm elapsed span.
  // Each tick mutates textContent only.
  node.textContent = "";
  const verbEl = document.createElement("span");
  verbEl.className = "op-status-verb";
  const elapsedEl = document.createElement("span");
  elapsedEl.className = "op-status-elapsed";
  node.append(verbEl, document.createTextNode(" "), elapsedEl);

  const render = () => {
    if (verb !== lastVerb) {
      // Restart the gradient sweep on word change so it doesn't half-paint
      // a fresh word mid-cycle.
      verbEl.classList.remove("op-status-verb-anim");
      // Force a reflow before re-adding so the keyframes restart.
      void verbEl.offsetWidth;
      verbEl.classList.add("op-status-verb-anim");
      lastVerb = verb;
    }
    verbEl.textContent = `${verb}…`;
    elapsedEl.textContent = fmtElapsed(Date.now() - startedAt);
  };
  verbEl.classList.add("op-status-verb-anim");
  render();

  const intervalId = window.setInterval(() => {
    ticks += 1;
    // Rotate the verb every 15s. Avoid picking the same word twice in a row.
    if (ticks % 15 === 0) {
      let next = pickVerb(kind);
      let guard = 0;
      while (next === verb && guard < 5) {
        next = pickVerb(kind);
        guard += 1;
      }
      verb = next;
    }
    render();
  }, 1000);

  // Clear any previous timer for this node, then store the new one.
  const prev = _opTimers.get(node);
  if (prev) window.clearInterval(prev.intervalId);
  _opTimers.set(node, { intervalId, startedAt });
}

function opDone(node, kind, durationMs) {
  if (!node) return;
  const prev = _opTimers.get(node);
  if (prev) {
    window.clearInterval(prev.intervalId);
    _opTimers.delete(node);
  }
  // Prefer the bridge's reported duration; fall back to wall-clock.
  const elapsedMs =
    typeof durationMs === "number"
      ? durationMs
      : prev
        ? Date.now() - prev.startedAt
        : 0;
  node.className = "op-status op-status-done";
  node.hidden = false;
  node.textContent = `${DONE_VERB[kind] || "Done"} for ${fmtElapsed(elapsedMs)}.`;
}

function opError(node, message) {
  if (!node) return;
  const prev = _opTimers.get(node);
  if (prev) {
    window.clearInterval(prev.intervalId);
    _opTimers.delete(node);
  }
  node.className = "op-status op-status-error";
  node.hidden = false;
  node.textContent = message ?? "Something went wrong.";
}

function opClear(node) {
  if (!node) return;
  const prev = _opTimers.get(node);
  if (prev) {
    window.clearInterval(prev.intervalId);
    _opTimers.delete(node);
  }
  node.className = "op-status";
  node.hidden = true;
  node.textContent = "";
}

// --- Busy controller (global lock — only one op at a time) ---------------

let busyKind = null;

function setBusy(kind) {
  busyKind = kind;
  document
    .querySelectorAll("[data-long-op]")
    .forEach((el) => el.setAttribute("data-busy", "true"));
  document
    .querySelectorAll("[data-long-op] button, [data-long-op] input, [data-long-op] textarea")
    .forEach((el) => {
      el.disabled = true;
    });
}

function clearBusy() {
  busyKind = null;
  document
    .querySelectorAll("[data-long-op]")
    .forEach((el) => el.removeAttribute("data-busy"));
  document
    .querySelectorAll("[data-long-op] button, [data-long-op] input, [data-long-op] textarea")
    .forEach((el) => {
      el.disabled = false;
    });
}

// --- Output-file link helper ---------------------------------------------

let VAULT_ROOT = ""; // set lazily by ensureConfig()
let _configLoaded = false;

async function ensureConfig() {
  if (_configLoaded) return;
  try {
    const res = await apiFetch("/config");
    if (!res.ok) return;
    const data = await res.json();
    VAULT_ROOT = data.vault_root || "";
    if (!data.craft_enabled) {
      const craftEl = document.getElementById("craft-form");
      if (craftEl) craftEl.hidden = true;
    }
    // Show which agent CLI backs the skills (claude | codex). Config-derived,
    // so set once here — refreshStatus only updates the metric tiles.
    const engineLabel = { claude: "Claude Code", codex: "Codex" }[data.engine] || data.engine;
    if (engineLabel) setTile("engine", engineLabel, null);
    _configLoaded = true;
  } catch {
    /* best-effort */
  }
}

function outputFileLink(relPath) {
  const a = document.createElement("a");
  a.className = "output-file";
  // file:// links require an absolute path; if config hasn't loaded, fall
  // back to showing just the relative path with no href.
  if (VAULT_ROOT) {
    a.href = `file://${VAULT_ROOT}/${relPath}`;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  } else {
    a.href = "#";
    a.title = "Open the file from your editor or finder";
  }
  a.textContent = `📄 ${relPath}`;
  return a;
}

// --- Status refresh -------------------------------------------------------

async function refreshStatus() {
  // Best-effort with a 2 s budget — never blocks the page.
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), 2000);
  try {
    const res = await apiFetch("/status", { signal: ctrl.signal });
    if (!res.ok) return;
    const data = await res.json();
    applyStatus(data);
    // Reload the sidebar outputs list so newly saved queries appear.
    if (typeof loadOutputsList === "function") loadOutputsList(true);
  } catch {
    /* swallow — null tiles already show "—" */
  } finally {
    window.clearTimeout(timer);
  }
}

function applyStatus(data) {
  setTile("raw_total_count", data.raw_total_count, null);
  setTile("raw_pending_count", data.raw_pending_count, null);
  setTile("wiki_article_count", data.wiki_article_count, null);
  setTile("outputs_query_count", data.outputs_query_count, null);

  const ingest = formatIngestTime(data.last_ingest_iso, data.last_ingest_source);
  setTile("last_ingest_iso", ingest.label, ingest.sublabel, ingest.title);

  // The "| N ready to ingest" pipe + segment appear only when work is pending.
  const showReady = Number(data.raw_pending_count) > 0;
  document
    .querySelectorAll('#status-strip [data-seg="ready"]')
    .forEach((el) => { el.hidden = !showReady; });
}

function setTile(metric, value, sublabel, title) {
  const tile = document.querySelector(`#status-strip [data-metric="${metric}"]`);
  if (!tile) return;
  const numEl = tile.querySelector(".status-num");
  const labelEl = tile.querySelector(".status-label");
  const isMissing = value === null || value === undefined || value === "—";
  numEl.textContent = isMissing ? "—" : String(value);
  tile.classList.toggle("status-tile-empty", isMissing);
  if (sublabel && !isMissing) {
    labelEl.dataset.original = labelEl.dataset.original || labelEl.textContent;
    labelEl.textContent = sublabel;
  } else if (labelEl.dataset.original) {
    labelEl.textContent = labelEl.dataset.original;
  }
  if (title) tile.title = title;
}

function formatIngestTime(iso, source) {
  if (!iso) {
    return { label: "—", sublabel: "last ingest", title: "no ingest yet" };
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return { label: "—", sublabel: "last ingest", title: `unparseable: ${iso}` };
  }
  const rel = relativeTime(date);
  const local = date.toLocaleString(undefined, {
    dateStyle: "short",
    timeStyle: "short",
  });
  const note =
    source === "mtime"
      ? "INDEX.md mtime (manifest missing)"
      : source === "manifest"
        ? "from ingest manifest"
        : "";
  return {
    label: rel,
    sublabel: "last ingest",
    title: `${local}${note ? ` · ${note}` : ""}`,
  };
}

function relativeTime(date) {
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 0) return "just now";
  const s = Math.floor(diffMs / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  return date.toLocaleDateString();
}

// ---------------------------------------------------------------------------
// Copy buttons (result-card upper right)
// ---------------------------------------------------------------------------
//
// Two modes per result card:
//   data-copy="rich"  → write Markdown-rendered HTML to the clipboard so it
//                        pastes formatted into Mail/Notion/Slack/Docs. Falls
//                        back to plain text if rich-clipboard isn't available.
//   data-copy="md"    → write the raw Markdown source.
// The raw Markdown lives in the result-card's data-markdown attribute,
// stashed by the success branch of the query handler.

async function writeRichClipboard(markdown) {
  const html = renderMarkdown(markdown);
  const plain = markdown;
  if (navigator.clipboard && window.ClipboardItem) {
    try {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/html": new Blob([html], { type: "text/html" }),
          "text/plain": new Blob([plain], { type: "text/plain" }),
        }),
      ]);
      return true;
    } catch {
      /* fall through to text-only */
    }
  }
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(plain);
    return false;
  }
  // Legacy fallback for very old browsers / non-secure contexts.
  const ta = document.createElement("textarea");
  ta.value = plain;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    document.execCommand("copy");
  } finally {
    ta.remove();
  }
  return false;
}

async function writePlainClipboard(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    document.execCommand("copy");
  } finally {
    ta.remove();
  }
}

function flashCopyButton(btn, label) {
  const original = btn.dataset.label || btn.textContent;
  btn.dataset.label = original;
  btn.textContent = label;
  btn.classList.add("copy-btn-flash");
  window.setTimeout(() => {
    btn.textContent = original;
    btn.classList.remove("copy-btn-flash");
  }, 1500);
}

// One delegated listener handles every [data-copy] button on the page.
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".copy-btn");
  if (!btn) return;
  const card = btn.closest(".result-card, .maint-result, .viewer-content, #panel-wiki-viewer");
  const markdown = card?.dataset?.markdown;
  if (!markdown) {
    flashCopyButton(btn, "Nothing to copy");
    return;
  }
  try {
    if (btn.dataset.copy === "md") {
      await writePlainClipboard(markdown);
      flashCopyButton(btn, "Copied!");
    } else {
      const rich = await writeRichClipboard(markdown);
      flashCopyButton(btn, rich ? "Copied!" : "Copied (plain)");
    }
  } catch (err) {
    flashCopyButton(btn, "Copy failed");
    console.error("clipboard error:", err);
  }
});

// ---------------------------------------------------------------------------
// User Story 1 — Query
// ---------------------------------------------------------------------------

const queryForm = $("#query-form");
const queryInput = $("#query-input");
const queryField = queryInput.closest(".query-field");
const queryOp = queryForm.querySelector("[data-op-status]");
const queryResult = $("#query-result");
const queryResultBody = queryResult.querySelector(".markdown-body");
const queryResultFooter = queryResult.querySelector(".result-footer");
const queryResultMeta = queryResultFooter.querySelector(".result-meta");

queryInput.addEventListener("keydown", (e) => {
  // Enter submits; Shift+Enter inserts a newline.
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    queryForm.requestSubmit();
  }
});

// Auto-grow the query textarea as the question gets longer.
function autoGrowQuery() {
  // Hide the custom placeholder once there's any content (mirrors native placeholder).
  queryField.classList.toggle("has-value", queryInput.value.length > 0);
  // Reset to natural height so shrinking works when the user deletes.
  queryInput.style.height = "auto";
  const target = queryInput.scrollHeight;
  queryInput.style.height = target + "px";
  // If we hit the max-height ceiling, switch overflow back on so the user can scroll.
  const maxH = parseFloat(getComputedStyle(queryInput).maxHeight) || Infinity;
  queryInput.classList.toggle("query-input-overflow", target > maxH);
}
queryInput.addEventListener("input", autoGrowQuery);
// Run once after layout settles in case the field starts with text (e.g. after reload).
window.requestAnimationFrame(autoGrowQuery);

queryForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = queryInput.value.trim();
  if (!question) return;
  if (busyKind) return; // belt-and-braces; controls should already be disabled

  // Clear previous result while running.
  queryResult.hidden = true;
  queryResultBody.innerHTML = "";
  queryResultFooter
    .querySelectorAll(".output-file")
    .forEach((el) => el.remove());
  queryResultMeta.hidden = true;
  queryResultMeta.textContent = "";

  setBusy("query");
  opRunning(queryOp, "query");
  try {
    const { status, data } = await postJSON("/run", {
      kind: "query",
      args: { question },
    });

    const errMsg = envelopeError("query", status, data);
    if (errMsg !== null) {
      opError(queryOp, errMsg);
      return;
    }

    const rawMd = data.result || "(no answer)";
    queryResultBody.innerHTML = renderMarkdown(rawMd);
    queryResult.dataset.markdown = rawMd;  // for the Copy buttons
    queryResult.hidden = false;

    if (data.output_file) {
      queryResultFooter.prepend(outputFileLink(data.output_file));
    }

    const bits = [];
    if (typeof data.cost_usd === "number" && data.cost_usd > 0) {
      bits.push(`$${data.cost_usd.toFixed(4)}`);
    }
    if (typeof data.num_turns === "number") {
      bits.push(`${data.num_turns} turns`);
    }
    if (bits.length > 0) {
      queryResultMeta.textContent = bits.join(" · ");
      queryResultMeta.hidden = false;
    }

    opDone(queryOp, "query", data.duration_ms);
    refreshStatus();
  } catch (err) {
    opError(queryOp, `Network error: ${err?.message ?? err}`);
  } finally {
    clearBusy();
  }
});

// ---------------------------------------------------------------------------
// User Story 2 — Imports (paste Markdown, PDF, Craft)
// ---------------------------------------------------------------------------

/** Format an envelope into a short user-facing error string. */
function envelopeError(kind, status, data) {
  if (status === 409) {
    return `Busy: ${data.in_flight?.kind ?? "another operation"} is already running.`;
  }
  if (status === 504) {
    return `${kind} timed out after ${data.after_seconds ?? "?"} seconds.`;
  }
  if (status === 502) {
    return `Could not start the Claude CLI (${data.detail ?? "spawn_failed"}). Is 'claude' on your PATH?`;
  }
  if (status === 413) {
    return data.detail || "Upload too large.";
  }
  if (status !== 200) {
    return data.detail || data.error || `HTTP ${status}`;
  }
  // 200 but is_error: surface the skill's own message.
  if (data?.is_error) {
    return data.result || data.detail || "Operation failed.";
  }
  return null;
}

/** Render a per-form status panel. type ∈ {"success","error"}. */
function showImportStatus(form, type, message, files) {
  const slot = form.querySelector("[data-import-status]");
  slot.className = `import-status import-status-${type}`;
  slot.innerHTML = "";

  const head = document.createElement("div");
  head.className = "import-status-head";
  head.textContent = message;
  slot.appendChild(head);

  if (files && files.length > 0) {
    const list = document.createElement("ul");
    list.className = "import-status-files";
    for (const f of files) {
      const li = document.createElement("li");
      li.textContent = f;
      list.appendChild(li);
    }
    slot.appendChild(list);
  }

  if (type === "success") {
    const reminder = document.createElement("p");
    reminder.className = "import-status-reminder";
    reminder.textContent =
      "Saved to raw/. This content is not searchable until the next ingest.";
    slot.appendChild(reminder);
  }

  slot.hidden = false;
}

function clearImportStatus(form) {
  const slot = form.querySelector("[data-import-status]");
  if (slot) {
    slot.hidden = true;
    slot.innerHTML = "";
    slot.className = "import-status";
  }
}

async function runImport(form, kind, opts) {
  if (busyKind) return;
  const opNode = form.querySelector("[data-op-status]");
  clearImportStatus(form);
  setBusy(kind);
  opRunning(opNode, kind);
  try {
    const { status, data } = opts.upload
      ? await postMultipart(opts.url, opts.formData)
      : await postJSON(opts.url, opts.body);

    // Optional opt-in callback that gets first look at any 200 envelope
    // (including is_error:true). Used by web-import to detect
    // ✗ FETCH_FAILED and flip the card to paste mode instead of rendering
    // a red banner. Returning true suppresses the default error/success
    // handling for this envelope. Other forms pass no hook and behave
    // identically to before.
    if (status === 200 && opts.onResult) {
      const handled = opts.onResult(data, opNode);
      if (handled) return;
    }

    const err = envelopeError(kind, status, data);
    if (err !== null) {
      opError(opNode, err);
      return;
    }
    const created = Array.isArray(data.created_files) ? data.created_files : [];
    const resultText = (data.result || "").trim();
    // The skills mark genuine success with "✓" near the top of the result
    // (often inside a ```fenced``` block). Use that as the discriminator
    // instead of "did a file get created" — the latter misclassifies
    // "Already imported" runs (no new file, but successful).
    const skillSucceeded = /✓/.test(resultText.slice(0, 200));
    if (created.length === 0 && !skillSucceeded) {
      // Real failure — surface the skill's own explanation.
      opError(opNode, resultText || "No file was created.");
      return;
    }
    // Headline reflects what actually happened.
    let headline;
    if (created.length === 0) {
      headline = "Already imported.";
    } else if (created.length === 1) {
      headline = "Added 1 file.";
    } else {
      headline = `Added ${created.length} files.`;
    }
    showImportStatus(form, "success", headline, created);
    opDone(opNode, kind, data.duration_ms);
    if (opts.onSuccess) opts.onSuccess(data);
    refreshStatus();
  } catch (e) {
    opError(opNode, `Network error: ${e?.message ?? e}`);
  } finally {
    clearBusy();
  }
}

// --- Paste Markdown -------------------------------------------------------

const pasteForm = $("#paste-form");
const pasteTitle = $("#paste-title");
const pasteBody = $("#paste-body");

pasteForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const markdown = pasteBody.value;
  if (!markdown.trim()) {
    opError(pasteForm.querySelector("[data-op-status]"), "Paste some Markdown first.");
    return;
  }
  const args = { markdown };
  const title = pasteTitle.value.trim();
  if (title) args.title_hint = title;
  runImport(pasteForm, "md-add", {
    url: "/run",
    body: { kind: "md-add", args },
    onSuccess: () => {
      pasteBody.value = "";
      pasteTitle.value = "";
    },
  });
});

// --- File upload (PDF / image / text) -------------------------------------

const ACCEPTED_FILE_EXTS = new Set([
  ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt", ".md",
]);
const ACCEPTED_MIME_TYPES = new Set([
  "application/pdf",
  "image/png", "image/jpeg", "image/gif", "image/webp",
  "text/plain", "text/markdown",
]);

const fileForm     = $("#file-form");
const fileInput    = $("#file-input");
const fileFilename = fileForm.querySelector("[data-file-filename]");
const fileClear    = fileForm.querySelector("[data-file-clear]");
const fileContext  = fileForm.querySelector("[data-file-context]");
const fileDropzone = fileForm.querySelector("[data-dropzone]");
const FILE_FILENAME_EMPTY = "No file selected";

function syncFileFilename() {
  const file = fileInput.files?.[0];
  if (file) {
    fileFilename.textContent = file.name;
    fileFilename.classList.remove("file-picker-name-empty");
    fileClear.hidden = false;
  } else {
    fileFilename.textContent = FILE_FILENAME_EMPTY;
    fileFilename.classList.add("file-picker-name-empty");
    fileClear.hidden = true;
  }
}
fileInput.addEventListener("change", syncFileFilename);

// Clear the selected file without having to pick another one. Leaves any
// typed context in place — clearing the file shouldn't discard the note.
fileClear.addEventListener("click", () => {
  fileInput.value = "";
  syncFileFilename();
});

// --- Drag-and-drop ---
// Prevent the browser from opening files dropped *outside* the dropzone.
// Without these handlers the page navigates to the file:// URL and the
// dashboard disappears.
["dragover", "drop"].forEach((evt) => {
  document.addEventListener(evt, (e) => e.preventDefault());
});

function isAcceptedFile(item) {
  if (ACCEPTED_MIME_TYPES.has(item.type)) return true;
  const name = item.name || "";
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  return ACCEPTED_FILE_EXTS.has(ext);
}

function dropzoneAcceptsFileDrag(dataTransfer) {
  // During dragenter/over item.type is available but item.name typically isn't.
  // Accept if any item looks like a supported type, or if the type is generic.
  if (!dataTransfer || !dataTransfer.items?.length) return true;
  for (const it of dataTransfer.items) {
    if (it.kind !== "file") continue;
    if (it.type === "" || ACCEPTED_MIME_TYPES.has(it.type)) return true;
  }
  return false;
}

let dragDepth = 0;          // track nested dragenter/leave so the highlight is stable

fileDropzone.addEventListener("dragenter", (e) => {
  e.preventDefault();
  dragDepth += 1;
  const ok = dropzoneAcceptsFileDrag(e.dataTransfer);
  fileDropzone.classList.toggle("dropzone-active", ok);
  fileDropzone.classList.toggle("dropzone-reject", !ok);
});
fileDropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
});
fileDropzone.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) {
    fileDropzone.classList.remove("dropzone-active", "dropzone-reject");
  }
});
fileDropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  fileDropzone.classList.remove("dropzone-active", "dropzone-reject");
  const files = e.dataTransfer?.files;
  if (!files || files.length === 0) return;
  const file = files[0];
  if (!isAcceptedFile(file)) {
    opError(
      fileForm.querySelector("[data-op-status]"),
      `${file.name || "That file"} is not a supported type (PDF, image, or plain text).`,
    );
    return;
  }
  const dt = new DataTransfer();
  dt.items.add(file);
  fileInput.files = dt.files;
  syncFileFilename();
});

fileForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const file = fileInput.files?.[0];
  if (!file) {
    opError(fileForm.querySelector("[data-op-status]"), "Select a file first.");
    return;
  }
  const fd = new FormData();
  fd.append("file", file);
  const ctx = fileContext.value.trim();
  if (ctx) fd.append("context", ctx);
  runImport(fileForm, "file-import", {
    url: "/upload-file",
    upload: true,
    formData: fd,
    onSuccess: () => {
      fileForm.reset();
      syncFileFilename();
    },
  });
});

// --- Craft ----------------------------------------------------------------

const craftForm = $("#craft-form");
const craftFolder = $("#craft-folder");
const craftDocument = $("#craft-document");

craftForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const folder = craftFolder.value.trim();
  const document_ = craftDocument.value.trim();
  if (!folder || !document_) {
    opError(
      craftForm.querySelector("[data-op-status]"),
      "Provide both a folder and a document name.",
    );
    return;
  }
  runImport(craftForm, "craft-import", {
    url: "/run",
    body: {
      kind: "craft-import",
      args: { folder, document: document_ },
    },
    onSuccess: () => {
      craftDocument.value = "";
      // Keep folder field as-is — likely useful for next import.
    },
  });
});

// --- Web URL --------------------------------------------------------------
//
// Two-state card: URL mode (default) → user types a URL, clicks Import,
// skill fetches via WebFetch and writes raw/web/<...>.md. If the skill
// can't fetch (paywall, dead URL, network failure), it returns a result
// containing "✗ FETCH_FAILED:" — the front-end detects that token and
// flips the same card into paste mode (textarea + Cancel button). The
// URL stays as the canonical `source:` either way.

const webForm = $("#web-form");
const webUrl = $("#web-url");
const webPasteBody = $("#web-paste-body");
const webModeUrl = webForm.querySelector("[data-web-mode-url]");
const webModePaste = webForm.querySelector("[data-web-mode-paste]");
const webPasteContext = webForm.querySelector("[data-web-paste-context]");
const webCancelPaste = webForm.querySelector("[data-web-cancel-paste]");
const webContext = $("#web-context");

function setWebMode(mode, opts = {}) {
  webForm.dataset.mode = mode;
  if (mode === "url") {
    webModeUrl.hidden = false;
    webModePaste.hidden = true;
    webPasteBody.required = false;
    webUrl.required = true;
    webPasteBody.value = "";
    webPasteContext.textContent = "";
  } else {
    // Hide URL field but keep its value — needed as `source:` on second submit.
    webModeUrl.hidden = true;
    webModePaste.hidden = false;
    webPasteBody.required = true;
    webUrl.required = false;
    webPasteContext.textContent =
      opts.contextMsg ||
      "Couldn't fetch the page. Paste the article body as Markdown below.";
    window.requestAnimationFrame(() => webPasteBody.focus());
  }
}

// The skill's contract for "I couldn't fetch — ask the user to paste"
// is the literal "✗ FETCH_FAILED:" token in the first 200 chars of the
// result (mirrors the existing ✓-success discriminator pattern).
function isWebFetchFailed(resultText) {
  if (!resultText) return false;
  return /✗\s*FETCH_FAILED:/.test(resultText.slice(0, 200));
}

webCancelPaste.addEventListener("click", () => {
  setWebMode("url");
  clearImportStatus(webForm);
  opClear(webForm.querySelector("[data-op-status]"));
});

webForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const url = webUrl.value.trim();
  if (!url) {
    opError(
      webForm.querySelector("[data-op-status]"),
      "Provide a URL to import.",
    );
    return;
  }
  const mode = webForm.dataset.mode;
  const args = { url };
  const ctx = webContext.value.trim();
  if (ctx) args.context = ctx;
  if (mode === "paste") {
    const md = webPasteBody.value;
    if (!md.trim()) {
      opError(
        webForm.querySelector("[data-op-status]"),
        "Paste the article body before importing.",
      );
      return;
    }
    args.pasted_markdown = md;
  }
  runImport(webForm, "web-import", {
    url: "/run",
    body: { kind: "web-import", args },
    // Intercept FETCH_FAILED before the default error rendering kicks in.
    onResult: (data, opNode) => {
      const text = data.result || "";
      if (!isWebFetchFailed(text)) return false;
      const reasonMatch = text.match(/Reason:\s*(.+)/);
      const reason = (reasonMatch ? reasonMatch[1] : "fetch_error").trim();
      let host;
      try { host = new URL(url).host; } catch { host = "this page"; }
      setWebMode("paste", {
        contextMsg: `Couldn't fetch ${host} (${reason}). Paste the article body below to import it manually.`,
      });
      // Drop the running spinner cleanly — the user is now editing.
      opClear(opNode);
      return true;
    },
    onSuccess: () => {
      // Real success — return to URL mode and clear the fields.
      setWebMode("url");
      webUrl.value = "";
      webContext.value = "";
    },
  });
});

// ---------------------------------------------------------------------------
// User Story 4 — Maintenance (Ingest + Lint)
// ---------------------------------------------------------------------------

async function runMaintenance(form, kind) {
  if (busyKind) return;
  const opNode = form.querySelector("[data-op-status]");
  const resultBox = form.querySelector("[data-maint-result]");
  const body = resultBox.querySelector(".markdown-body");
  const footer = resultBox.querySelector(".result-footer");
  const meta = footer.querySelector(".result-meta");
  // Reset previous render.
  resultBox.hidden = true;
  body.innerHTML = "";
  footer.querySelectorAll(".output-file").forEach((el) => el.remove());
  meta.hidden = true;
  meta.textContent = "";

  setBusy(kind);
  opRunning(opNode, kind);
  try {
    const { status, data } = await postJSON("/run", { kind, args: {} });

    const err = envelopeError(kind, status, data);
    if (err !== null) {
      opError(opNode, err);
      return;
    }

    const rawMd = data.result || "(no output)";
    body.innerHTML = renderMarkdown(rawMd);
    resultBox.dataset.markdown = rawMd;  // for any future Copy buttons
    if (data.output_file) {
      footer.prepend(outputFileLink(data.output_file));
    }
    const bits = [];
    if (typeof data.cost_usd === "number" && data.cost_usd > 0) {
      bits.push(`$${data.cost_usd.toFixed(4)}`);
    }
    if (bits.length > 0) {
      meta.textContent = bits.join(" · ");
      meta.hidden = false;
    }
    resultBox.hidden = false;

    opDone(opNode, kind, data.duration_ms);
    refreshStatus();
  } catch (e) {
    opError(opNode, `Network error: ${e?.message ?? e}`);
  } finally {
    clearBusy();
  }
}

const ingestForm = $("#ingest-form");
ingestForm.addEventListener("submit", (e) => {
  e.preventDefault();
  runMaintenance(ingestForm, "ingest");
});

const lintForm = $("#lint-form");
lintForm.addEventListener("submit", (e) => {
  e.preventDefault();
  // Hide the edit bar and reset input when re-running lint.
  const lintEditBar = document.getElementById("lint-edit-bar");
  if (lintEditBar) lintEditBar.hidden = true;
  const lintEditInput = document.getElementById("lint-edit-input");
  if (lintEditInput) lintEditInput.value = "";
  runMaintenance(lintForm, "lint");
});

// ---------------------------------------------------------------------------
// Wiki edit — shared helper for all three suggestion boxes
// ---------------------------------------------------------------------------

async function runWikiEdit(prompt, slug, opNode, statusNode, onSuccess) {
  if (busyKind) {
    opError(opNode, "Another operation is running. Please wait.");
    return;
  }
  setBusy("wiki-edit");
  opRunning(opNode, "wiki-edit");
  if (statusNode) { statusNode.hidden = true; statusNode.className = "import-status"; }
  try {
    const args = slug ? { prompt, slug } : { prompt };
    const { status, data } = await postJSON("/run", { kind: "wiki-edit", args });
    const err = envelopeError("wiki-edit", status, data);
    if (err !== null) {
      opError(opNode, err);
      return;
    }
    opDone(opNode, "wiki-edit", data.duration_ms);
    if (statusNode) {
      statusNode.className = "import-status import-status-success visible";
      statusNode.textContent = data.result?.split("\n")[0] || "Done.";
      statusNode.hidden = false;
    }
    if (onSuccess) onSuccess(data);
  } catch (e) {
    opError(opNode, `Network error: ${e?.message ?? e}`);
  } finally {
    clearBusy();
  }
}

// --- Lint homepage edit bar ---
const lintEditBar    = document.getElementById("lint-edit-bar");
const lintEditInput  = document.getElementById("lint-edit-input");
const lintEditBtn    = document.getElementById("lint-edit-btn");
const lintEditOpSt   = document.getElementById("lint-edit-op-status");
const lintEditImpSt  = document.getElementById("lint-edit-import-status");

// Patch runMaintenance success path: reveal edit bar after lint completes.
// We wrap the lint form's submit to inject a post-success hook.
const _origLintSubmit = lintForm.onsubmit;
(function patchLintForEditBar() {
  const _origRunMaint = runMaintenance;
  // Intercept by observing resultBox visibility changes via MutationObserver.
  const lintResultBox = lintForm.querySelector("[data-maint-result]");
  if (lintResultBox && lintEditBar) {
    const obs = new MutationObserver(() => {
      if (!lintResultBox.hidden) lintEditBar.hidden = false;
    });
    obs.observe(lintResultBox, { attributes: true, attributeFilter: ["hidden"] });
  }
})();

lintEditBtn?.addEventListener("click", () => {
  const prompt = lintEditInput?.value.trim();
  if (!prompt) { opError(lintEditOpSt, "Enter an edit instruction first."); return; }
  runWikiEdit(prompt, null, lintEditOpSt, lintEditImpSt, () => {});
});

// --- Output viewer edit bar (shown only for lint files) ---
const outputEditBar   = document.getElementById("output-edit-bar");
const outputEditInput = document.getElementById("output-edit-input");
const outputEditBtn   = document.getElementById("output-edit-btn");
const outputEditOpSt  = document.getElementById("output-edit-op-status");
const outputEditImpSt = document.getElementById("output-edit-import-status");

outputEditBtn?.addEventListener("click", () => {
  const prompt = outputEditInput?.value.trim();
  if (!prompt) { opError(outputEditOpSt, "Enter an edit instruction first."); return; }
  runWikiEdit(prompt, null, outputEditOpSt, outputEditImpSt, () => {});
});

// --- Wiki article viewer edit bar ---
let currentWikiSlug = null;

const wikiEditInput = document.getElementById("wiki-edit-input");
const wikiEditBtn   = document.getElementById("wiki-edit-btn");
const wikiEditOpSt  = document.getElementById("wiki-edit-op-status");
const wikiEditImpSt = document.getElementById("wiki-edit-import-status");

wikiEditBtn?.addEventListener("click", () => {
  const prompt = wikiEditInput?.value.trim();
  if (!prompt) { opError(wikiEditOpSt, "Enter an edit instruction first."); return; }
  runWikiEdit(prompt, currentWikiSlug, wikiEditOpSt, wikiEditImpSt, () => {
    if (wikiEditInput) wikiEditInput.value = "";
    if (currentWikiSlug) openWikiArticle(currentWikiSlug);
  });
});

// ---------------------------------------------------------------------------
// Navigation — sidebar tabs, output history, wiki browser
// ---------------------------------------------------------------------------

let _currentSection = "home";       // "home" | "wiki"
let _lastHomePanel  = "panel-home-new"; // last visible panel within Home tab
let _activeNavItem  = null;         // currently highlighted sidebar button
let _outputsLoaded  = false;        // guard against duplicate fetches
let _wikiLoaded     = false;

function setActiveNavItem(el) {
  if (_activeNavItem) _activeNavItem.classList.remove("nav-item-active");
  _activeNavItem = el;
  if (el) el.classList.add("nav-item-active");
}

function showPanel(id) {
  ["panel-home-new", "panel-output-viewer", "panel-wiki-viewer"].forEach((pid) => {
    const p = document.getElementById(pid);
    if (p) p.hidden = pid !== id;
  });
  document.querySelector(".main-panel")?.scrollTo(0, 0);
}

function switchSection(section) {
  if (_currentSection === section) {
    // Re-tapping the active Home tab returns to New Question.
    if (section === "home") {
      _lastHomePanel = "panel-home-new";
      showPanel("panel-home-new");
      setActiveNavItem(document.getElementById("nav-new-question"));
      document.getElementById("query-input")?.focus();
    }
    return;
  }
  _currentSection = section;

  document.querySelectorAll(".nav-tab").forEach((btn) => {
    const active = btn.dataset.navTab === section;
    btn.classList.toggle("nav-tab-active", active);
    btn.setAttribute("aria-selected", String(active));
  });

  const homeList = document.getElementById("nav-list-home");
  const wikiList = document.getElementById("nav-list-wiki");
  if (homeList) homeList.hidden = section !== "home";
  if (wikiList) wikiList.hidden = section !== "wiki";

  if (section === "wiki") {
    showPanel("panel-wiki-viewer");
    loadWikiList().then(() => {
      if (currentWikiSlug) {
        // Article already loaded — just sync the nav highlight.
        const navBtn = document.querySelector(
          `.nav-item-wiki[data-wiki-slug="${CSS.escape(currentWikiSlug)}"]`,
        );
        if (navBtn) setActiveNavItem(navBtn);
      } else {
        // First visit — open INDEX and highlight it.
        const indexBtn = document.querySelector('.nav-item-wiki[data-wiki-slug="INDEX"]');
        if (indexBtn) setActiveNavItem(indexBtn);
        openWikiArticle("INDEX", "Index");
      }
    });
  } else {
    showPanel(_lastHomePanel);
    setActiveNavItem(document.getElementById("nav-new-question"));
  }
}

// ── Nav icon helper ──────────────────────────────────────────────────────

function makeNavIcon(name, size = 14) {
  const img = document.createElement("img");
  img.src = `/static/icons/${name}.png`;
  img.width = size;
  img.height = size;
  img.alt = "";
  img.setAttribute("aria-hidden", "true");
  img.className = "nav-item-icon";
  return img;
}

// ── Output list ──────────────────────────────────────────────────────────

async function loadOutputsList(force = false) {
  if (_outputsLoaded && !force) return;
  const container = document.getElementById("nav-outputs-list");
  if (!container) return;
  try {
    const res = await apiFetch("/outputs");
    if (!res.ok) return;
    const items = await res.json();
    if (!Array.isArray(items)) return;

    // Track which filename is currently active so we can restore it after re-render.
    const activeFn = _activeNavItem?.dataset?.outputFilename ?? null;

    container.innerHTML = "";
    for (const item of items) {
      const btn = document.createElement("button");
      btn.className = "nav-item nav-item-output";
      btn.dataset.outputFilename = item.filename;
      // Lint reports get their own icon; query answers use the answer icon.
      btn.appendChild(makeNavIcon(item.kind === "lint" ? "lint" : "answer"));

      // Wrap the title so the trash control can sit in a fixed right-hand
      // column: the label flexes, the trash slot is always reserved (even
      // when hidden) so revealing it on hover never re-wraps the text.
      const label = document.createElement("span");
      label.className = "nav-item-label";
      label.textContent = item.title;
      btn.appendChild(label);

      const trash = document.createElement("span");
      trash.className = "nav-item-trash";
      trash.setAttribute("role", "button");
      trash.setAttribute("aria-label", `Delete ${item.title}`);
      trash.title = "Delete";
      const trashIcon = document.createElement("img");
      trashIcon.src = "/static/icons/trash.png";
      trashIcon.width = 20;
      trashIcon.height = 20;
      trashIcon.alt = "";
      trashIcon.setAttribute("aria-hidden", "true");
      trash.appendChild(trashIcon);
      trash.addEventListener("click", (e) => {
        // Don't let the click bubble to the row (which would open the output).
        e.stopPropagation();
        openDeleteModal(item.filename, item.title, btn);
      });
      btn.appendChild(trash);

      btn.title = `${item.date_iso} · ${item.kind}`;
      btn.addEventListener("click", () => {
        setActiveNavItem(btn);
        _lastHomePanel = "panel-output-viewer";
        openOutput(item.filename, item.title, item.date_iso, item.kind);
      });
      if (item.filename === activeFn) btn.classList.add("nav-item-active");
      container.appendChild(btn);
    }
    // If an output was active and we just re-rendered, restore _activeNavItem reference.
    if (activeFn) {
      const restored = container.querySelector(`[data-output-filename="${CSS.escape(activeFn)}"]`);
      if (restored) _activeNavItem = restored;
    }
    _outputsLoaded = true;
  } catch {
    /* best-effort */
  }
}

async function openOutput(filename, title, date, kind, highlightTerm = "") {
  showPanel("panel-output-viewer");
  const panel       = document.getElementById("panel-output-viewer");
  const titleEl     = panel.querySelector(".viewer-title");
  const metaEl      = panel.querySelector(".viewer-meta");
  const bodyEl      = panel.querySelector(".viewer-body");
  const contentEl   = panel.querySelector(".viewer-content");

  if (titleEl)    titleEl.textContent    = title;
  if (metaEl)     metaEl.textContent     = `${date} · ${kind}`;
  if (bodyEl)     bodyEl.innerHTML       = '<p class="viewer-placeholder">Loading…</p>';
  if (contentEl)  contentEl.dataset.markdown = ""; // clear while loading

  // Show the edit bar only for lint reports; reset its state when switching outputs.
  const isLint = /[_-]lint/i.test(filename);
  if (outputEditBar) {
    outputEditBar.hidden = !isLint;
    if (outputEditInput) outputEditInput.value = "";
    if (outputEditImpSt) { outputEditImpSt.hidden = true; outputEditImpSt.className = "import-status"; }
  }

  try {
    const res = await apiFetch(`/outputs/${encodeURIComponent(filename)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const md = await res.text();
    if (bodyEl)    bodyEl.innerHTML           = renderMarkdown(md);
    if (contentEl) contentEl.dataset.markdown = md;
    if (bodyEl && highlightTerm) highlightMatches(bodyEl, highlightTerm);
  } catch (err) {
    showLoadError(bodyEl, err.message);
  }
}

// ── Wiki list & viewer ───────────────────────────────────────────────────

async function loadWikiList() {
  if (_wikiLoaded) return;
  const container = document.getElementById("nav-wiki-list");
  if (!container) return;
  try {
    const res = await apiFetch("/wiki");
    if (!res.ok) return;
    const items = await res.json();
    if (!Array.isArray(items)) return;

    container.innerHTML = "";

    // INDEX entry first
    const indexBtn = document.createElement("button");
    indexBtn.className = "nav-item nav-item-wiki nav-item-wiki-index";
    indexBtn.dataset.wikiSlug = "INDEX";
    indexBtn.appendChild(makeNavIcon("index"));
    indexBtn.appendChild(document.createTextNode("Index"));
    indexBtn.addEventListener("click", () => {
      setActiveNavItem(indexBtn);
      openWikiArticle("INDEX", "Index");
    });
    container.appendChild(indexBtn);

    for (const item of items) {
      const btn = document.createElement("button");
      btn.className = "nav-item nav-item-wiki";
      btn.dataset.wikiSlug = item.slug;
      btn.appendChild(makeNavIcon("page"));
      btn.appendChild(document.createTextNode(item.title));
      btn.addEventListener("click", () => {
        setActiveNavItem(btn);
        openWikiArticle(item.slug, item.title);
      });
      container.appendChild(btn);
    }
    _wikiLoaded = true;
  } catch {
    /* best-effort */
  }
}

async function openWikiArticle(slug, displayTitle, highlightTerm = "") {
  currentWikiSlug = slug;
  showPanel("panel-wiki-viewer");
  const panel  = document.getElementById("panel-wiki-viewer");
  const crumb  = panel.querySelector(".wiki-breadcrumb-article");
  const bodyEl = panel.querySelector(".viewer-body");

  if (crumb)  crumb.textContent   = displayTitle ?? slug;
  if (bodyEl) bodyEl.innerHTML    = '<p class="viewer-placeholder">Loading…</p>';
  panel.dataset.markdown = ""; // clear while loading
  // Reset edit bar state when navigating to a new article.
  if (wikiEditImpSt) { wikiEditImpSt.hidden = true; wikiEditImpSt.className = "import-status"; }

  try {
    const res = await apiFetch(`/wiki/${encodeURIComponent(slug)}`);
    if (!res.ok) throw new Error(`Article not found: ${slug}`);
    const md = await res.text();
    if (bodyEl) {
      bodyEl.innerHTML = renderMarkdown(md);
      // Use the rendered H1 as the breadcrumb title if available
      const h1 = bodyEl.querySelector("h1");
      if (h1 && crumb) crumb.textContent = h1.textContent;
      if (highlightTerm) highlightMatches(bodyEl, highlightTerm);
    }
    panel.dataset.markdown = md;
  } catch (err) {
    showLoadError(bodyEl, err.message);
  }
}

// Open a wiki article from anywhere (query answer, output viewer, another
// article), flipping the sidebar into the Wiki section. We set the section
// chrome directly rather than calling switchSection() to avoid its "first
// visit opens INDEX" async branch racing the target article load.
function goToWikiArticle(slug, highlightTerm = "") {
  _currentSection = "wiki";
  document.querySelectorAll(".nav-tab").forEach((btn) => {
    const active = btn.dataset.navTab === "wiki";
    btn.classList.toggle("nav-tab-active", active);
    btn.setAttribute("aria-selected", String(active));
  });
  const homeList = document.getElementById("nav-list-home");
  const wikiList = document.getElementById("nav-list-wiki");
  if (homeList) homeList.hidden = true;
  if (wikiList) wikiList.hidden = false;
  openWikiArticle(slug, undefined, highlightTerm);
  // Highlight the matching nav item once the list is present.
  loadWikiList().then(() => {
    const navBtn = document.querySelector(
      `.nav-item-wiki[data-wiki-slug="${CSS.escape(slug)}"]`,
    );
    if (navBtn) setActiveNavItem(navBtn);
  });
}

// Delegated click handler for every [[wikilink]]-derived <a>, wherever it is
// rendered. Routes by slug prefix: `raw/…` opens the raw-file modal, `wiki/…`
// (query-answer sources) and bare slugs open the wiki article.
document.addEventListener("click", (e) => {
  const link = e.target.closest("[data-wiki-slug]");
  if (!link || !link.dataset.wikiSlug) return;
  if (link.closest(".sidebar")) return; // nav buttons have their own handlers
  e.preventDefault();
  const slug = link.dataset.wikiSlug;
  if (slug.startsWith("raw/")) {
    openRawFile(slug);
    return;
  }
  const wikiSlug = slug.startsWith("wiki/") ? slug.slice(5) : slug;
  goToWikiArticle(wikiSlug);
});

// ── Raw file modal ───────────────────────────────────────────────────────
// A lightweight, dismissable viewer for a single raw source file. It has no
// nav/tab state and no history: open, read, close back to where you were.

const rawModal = document.getElementById("raw-modal");

function closeRawModal() {
  if (rawModal) rawModal.hidden = true;
}

// slug is the full `raw/…​.md` path from the link's data-wiki-slug.
async function openRawFile(slug) {
  if (!rawModal) return;
  const pathEl = rawModal.querySelector(".raw-modal-path");
  const bodyEl = rawModal.querySelector(".raw-modal-body");
  if (pathEl) pathEl.textContent = slug;
  if (bodyEl) bodyEl.innerHTML = '<p class="viewer-placeholder">Loading…</p>';
  rawModal.hidden = false;
  rawModal.querySelector(".raw-modal-sheet")?.scrollTo(0, 0);

  const subpath = slug.replace(/^raw\//, "");
  const encoded = subpath.split("/").map(encodeURIComponent).join("/");
  try {
    const res = await apiFetch(`/raw/${encoded}`);
    if (!res.ok) throw new Error(`File not found: ${slug}`);
    const md = await res.text();
    if (bodyEl) bodyEl.innerHTML = renderMarkdown(md);
  } catch (err) {
    if (bodyEl) showLoadError(bodyEl, err.message);
  }
}

// Dismiss on scrim/× click and on Escape while open.
rawModal?.addEventListener("click", (e) => {
  if (e.target.closest("[data-raw-close]")) closeRawModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && rawModal && !rawModal.hidden) closeRawModal();
});

// ── Search overlay ─────────────────────────────────────────────────────────
// Keyword search across saved answers (outputs/) and wiki articles. Opened by
// the sidebar magnifier or ⌘K/Ctrl+K. Search-as-you-type (debounced); the
// bridge returns plain-text snippets that we highlight safely in the DOM.

const searchModal = document.getElementById("search-modal");
const searchInput = document.getElementById("search-input");
const searchResults = document.getElementById("search-results");
const searchEmpty = searchModal?.querySelector(".search-empty");
let _searchCtrl = null; // AbortController for the in-flight /search request

function debounce(fn, ms) {
  let t;
  return (...args) => {
    window.clearTimeout(t);
    t = window.setTimeout(() => fn(...args), ms);
  };
}

function openSearchModal() {
  if (!searchModal) return;
  searchModal.hidden = false;
  // Keep the prior query but select it so typing replaces immediately.
  searchInput?.focus();
  searchInput?.select();
}

function closeSearchModal() {
  if (searchModal) searchModal.hidden = true;
  if (_searchCtrl) { _searchCtrl.abort(); _searchCtrl = null; }
}

// Append `text` to `container`, wrapping each case-insensitive occurrence of
// `q` in <mark>. Built from text nodes so snippet content can never inject.
function appendHighlighted(container, text, q) {
  const needle = q.toLowerCase();
  if (!needle) { container.appendChild(document.createTextNode(text)); return; }
  const hay = text.toLowerCase();
  let i = 0;
  for (;;) {
    const idx = hay.indexOf(needle, i);
    if (idx < 0) { container.appendChild(document.createTextNode(text.slice(i))); break; }
    if (idx > i) container.appendChild(document.createTextNode(text.slice(i, idx)));
    const mark = document.createElement("mark");
    mark.textContent = text.slice(idx, idx + needle.length);
    container.appendChild(mark);
    i = idx + needle.length;
  }
}

function renderSearchResults(items, q) {
  if (!searchResults) return;
  searchResults.textContent = "";
  for (const item of items) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "search-result";
    const iconName = item.source === "wiki" ? "page" : (item.kind === "lint" ? "lint" : "answer");
    btn.appendChild(makeNavIcon(iconName, 16));

    const textWrap = document.createElement("div");
    textWrap.className = "search-result-text";

    const titleEl = document.createElement("div");
    titleEl.className = "search-result-title";
    titleEl.textContent = item.title;
    const meta = document.createElement("span");
    meta.className = "search-result-meta";
    const label = item.source === "wiki" ? "wiki" : (item.kind === "lint" ? "lint" : "answer");
    meta.textContent = `  ·  ${label}${item.hits > 1 ? ` · ${item.hits} hits` : ""}`;
    titleEl.appendChild(meta);
    textWrap.appendChild(titleEl);

    const snip = document.createElement("div");
    snip.className = "search-snippet";
    appendHighlighted(snip, item.snippet || "", q);
    textWrap.appendChild(snip);

    btn.appendChild(textWrap);
    btn.addEventListener("click", () => selectSearchResult(item));
    searchResults.appendChild(btn);
  }
  if (searchEmpty) searchEmpty.hidden = !(q.trim().length >= 2 && items.length === 0);
}

async function runSearch(q) {
  q = q.trim();
  if (q.length < 2) {
    if (searchResults) searchResults.textContent = "";
    if (searchEmpty) searchEmpty.hidden = true;
    return;
  }
  if (_searchCtrl) _searchCtrl.abort();
  _searchCtrl = new AbortController();
  try {
    const res = await apiFetch("/search?q=" + encodeURIComponent(q), { signal: _searchCtrl.signal });
    if (!res.ok) return;
    const data = await res.json();
    renderSearchResults(data.results || [], q);
  } catch (err) {
    if (err?.name !== "AbortError") { /* best-effort — leave prior results */ }
  }
}

// Navigate to a saved answer, flipping the sidebar into the Home section. Mirror
// of goToWikiArticle() for outputs (set chrome directly to avoid switchSection's
// async INDEX branch).
function goToOutput(filename, title, date, kind, highlightTerm = "") {
  _currentSection = "home";
  document.querySelectorAll(".nav-tab").forEach((btn) => {
    const active = btn.dataset.navTab === "home";
    btn.classList.toggle("nav-tab-active", active);
    btn.setAttribute("aria-selected", String(active));
  });
  const homeList = document.getElementById("nav-list-home");
  const wikiList = document.getElementById("nav-list-wiki");
  if (homeList) homeList.hidden = false;
  if (wikiList) wikiList.hidden = true;
  _lastHomePanel = "panel-output-viewer";
  openOutput(filename, title, date, kind, highlightTerm);
  loadOutputsList(true).then(() => {
    const navBtn = document.querySelector(
      `.nav-item-output[data-output-filename="${CSS.escape(filename)}"]`,
    );
    if (navBtn) setActiveNavItem(navBtn);
  });
}

// Highlight every occurrence of `term` in an already-rendered doc body and
// scroll the first into view. Matches are wrapped in <mark class="search-hit">
// built from text nodes (safe). A match is only found when it sits within a
// single text node — good enough for keyword hits; one straddling inline markup
// (e.g. **bold**) is simply left un-highlighted.
function highlightMatches(root, term) {
  const needle = (term || "").toLowerCase();
  if (!root || needle.length < 2) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(n) {
      if (!n.nodeValue || !n.nodeValue.toLowerCase().includes(needle)) return NodeFilter.FILTER_REJECT;
      const tag = n.parentNode?.nodeName;
      if (tag === "SCRIPT" || tag === "STYLE" || tag === "MARK") return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const text = node.nodeValue;
    const hay = text.toLowerCase();
    const frag = document.createDocumentFragment();
    let i = 0;
    for (;;) {
      const idx = hay.indexOf(needle, i);
      if (idx < 0) { frag.appendChild(document.createTextNode(text.slice(i))); break; }
      if (idx > i) frag.appendChild(document.createTextNode(text.slice(i, idx)));
      const mark = document.createElement("mark");
      mark.className = "search-hit";
      mark.textContent = text.slice(idx, idx + needle.length);
      frag.appendChild(mark);
      i = idx + needle.length;
    }
    node.parentNode.replaceChild(frag, node);
  }
  const first = root.querySelector("mark.search-hit");
  // Defer so it wins over showPanel()'s scroll-to-top after the panel switch.
  if (first) window.requestAnimationFrame(() => first.scrollIntoView({ block: "center", behavior: "smooth" }));
}

function selectSearchResult(item) {
  const term = (searchInput?.value || "").trim();
  closeSearchModal();
  if (item.source === "wiki") {
    goToWikiArticle(item.id, term);
  } else {
    goToOutput(item.id, item.title, item.date_iso, item.kind, term);
  }
}

function searchResultButtons() {
  return searchResults ? [...searchResults.querySelectorAll(".search-result")] : [];
}

document.getElementById("nav-search-btn")?.addEventListener("click", openSearchModal);
searchModal?.addEventListener("click", (e) => {
  if (e.target.closest("[data-search-close]")) closeSearchModal();
});
searchInput?.addEventListener("input", debounce(() => runSearch(searchInput.value), 180));

// From the input: Enter opens the first result (or forces a search); ↓ steps
// focus into the results list.
searchInput?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    const first = searchResultButtons()[0];
    if (first) first.click();
    else runSearch(searchInput.value);
  } else if (e.key === "ArrowDown") {
    e.preventDefault();
    searchResultButtons()[0]?.focus();
  }
});

// Within the results list: ↑/↓ move focus between results (↑ off the top returns
// to the input); Enter/Space open the focused result natively.
searchResults?.addEventListener("keydown", (e) => {
  const cur = e.target.closest(".search-result");
  if (!cur) return;
  const items = searchResultButtons();
  const idx = items.indexOf(cur);
  if (e.key === "ArrowDown") {
    e.preventDefault();
    (items[idx + 1] || items[0]).focus();
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    if (idx <= 0) searchInput?.focus();
    else items[idx - 1].focus();
  }
});

// Trap Tab inside the dialog: it toggles between the search field and the
// results list rather than silently leaking focus to the occluded page.
searchModal?.addEventListener("keydown", (e) => {
  if (e.key !== "Tab") return;
  e.preventDefault();
  if (document.activeElement === searchInput) {
    searchResultButtons()[0]?.focus(); // into results (no-op if none)
  } else {
    searchInput?.focus(); // back to the search field
  }
});

document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
    e.preventDefault();
    openSearchModal();
  } else if (e.key === "Escape" && searchModal && !searchModal.hidden) {
    closeSearchModal();
  }
});

// ── Delete-answer modal ──────────────────────────────────────────────────
// Confirms before removing a saved output. Reuses the raw-modal scrim for the
// darkened backdrop. Holds the pending target between opening and confirming.

const deleteModal = document.getElementById("delete-modal");
let _pendingDelete = null; // { filename, title, btn }

function openDeleteModal(filename, title, btn) {
  if (!deleteModal) return;
  _pendingDelete = { filename, title, btn };
  const nameEl = deleteModal.querySelector(".delete-modal-name");
  if (nameEl) nameEl.textContent = title;
  const confirmBtn = deleteModal.querySelector("[data-delete-confirm]");
  if (confirmBtn) {
    confirmBtn.disabled = false;
    confirmBtn.textContent = "Delete";
  }
  deleteModal.hidden = false;
  confirmBtn?.focus();
}

function closeDeleteModal() {
  if (deleteModal) deleteModal.hidden = true;
  _pendingDelete = null;
}

async function confirmDelete() {
  if (!_pendingDelete) return;
  const { filename, btn } = _pendingDelete;
  const confirmBtn = deleteModal.querySelector("[data-delete-confirm]");
  if (confirmBtn) confirmBtn.disabled = true;
  try {
    const res = await apiFetch(`/outputs/${encodeURIComponent(filename)}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    // If the deleted answer was open in the viewer, fall back to New Question.
    const wasActive = btn.classList.contains("nav-item-active");
    btn.remove();
    if (wasActive) {
      _lastHomePanel = "panel-home-new";
      setActiveNavItem(document.getElementById("nav-new-question"));
      showPanel("panel-home-new");
    }
    closeDeleteModal();
    // Refresh the status strip so the answers count reflects the deletion
    // (mirrors the query/import/ingest paths, which all refresh after mutating).
    refreshStatus();
  } catch (err) {
    // Surface the failure inline on the confirm button and let the user retry.
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = "Retry delete";
    }
    console.error("Delete failed:", err);
  }
}

deleteModal?.addEventListener("click", (e) => {
  if (e.target.closest("[data-delete-cancel]")) return closeDeleteModal();
  if (e.target.closest("[data-delete-confirm]")) return confirmDelete();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && deleteModal && !deleteModal.hidden) closeDeleteModal();
});

// Tab button clicks
document.querySelectorAll(".nav-tab").forEach((btn) => {
  btn.addEventListener("click", () => switchSection(btn.dataset.navTab));
});

// "New question" button
document.getElementById("nav-new-question")?.addEventListener("click", () => {
  _lastHomePanel = "panel-home-new";
  setActiveNavItem(document.getElementById("nav-new-question"));
  showPanel("panel-home-new");
});

// Init: set initial nav state and pre-load the outputs list
setActiveNavItem(document.getElementById("nav-new-question"));
loadOutputsList();

// ---------------------------------------------------------------------------
// First-load: config + status
// ---------------------------------------------------------------------------

ensureConfig();
refreshStatus();

// ---------------------------------------------------------------------------
// Live activity: keep the status strip current when work is triggered outside
// this page — e.g. an import fired from the browser extension, or another tab.
// This page only calls refreshStatus() after its OWN operations, so without
// this poll the "N ready to ingest" count would go stale until a manual nav /
// reload. We poll the cheap public /busy endpoint and refresh when an op we
// were watching finishes, or when the pending count moves under us.
// ---------------------------------------------------------------------------

let _lastActivity = { running: false, pending: null };

async function pollActivity() {
  try {
    const res = await apiFetch("/busy");
    if (!res.ok) return;
    const { running, pending } = await res.json();
    const finished = _lastActivity.running && !running;
    const pendingMoved =
      _lastActivity.pending !== null && pending !== _lastActivity.pending;
    _lastActivity = { running, pending };
    if (finished || pendingMoved) refreshStatus();
  } catch {
    /* best-effort — the strip just keeps its last values */
  }
}

window.setInterval(pollActivity, 3000);
