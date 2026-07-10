"use strict";

// Service worker — handles the long-running bridge fetch independently of
// the popup's lifetime. The popup sends an "import" message and can close
// immediately; this script writes the result to chrome.storage.session so the
// popup can read it whenever it is next opened. Session (not local) storage is
// deliberate: import status is ephemeral UI state that should survive popup
// close within a browser session but reset on a full restart.
//
// KEEPALIVE: Chrome MV3 terminates idle service workers after ~30s. The
// handler returns synchronously (doImport isn't awaited), so Chrome thinks
// the event is done. We ping chrome.runtime every 20s inside doImport to
// prevent termination while the bridge fetch is in flight.

const BRIDGE_PORT = 4173;

// ---------------------------------------------------------------------------
// Busy icon — pulsing orange dot in the bottom-right corner of the toolbar
// icon while an import is in progress.
// ---------------------------------------------------------------------------

let _baseIcon16   = null; // cached ImageBitmap, loaded once on first import
let _busyTimer    = null;
let _busyStart    = 0;
const _PULSE_MS   = 1400; // matches dashboard brand-pulse duration
const _FRAME_MS   = 80;   // ~12fps — smooth for a slow pulse, low overhead

async function _getBaseIcon() {
  if (_baseIcon16) return _baseIcon16;
  const resp = await fetch(chrome.runtime.getURL("icon-16.png"));
  const blob = await resp.blob();
  _baseIcon16 = await createImageBitmap(blob);
  return _baseIcon16;
}

function _drawFrame(base, phase) {
  const size = 16;
  const canvas = new OffscreenCanvas(size, size);
  const ctx = canvas.getContext("2d");
  ctx.drawImage(base, 0, 0, size, size);
  // sine-based ease matching brand-pulse: opacity 0.4→1→0.4, radius 2.5→3.5
  const t       = Math.sin(phase * Math.PI);
  const opacity = 0.4 + 0.6 * t;
  const radius  = 2.5 + 1.0 * t;
  const cx = 3, cy = size - 3; // bottom-left
  // white outline for contrast against the brain icon background
  ctx.globalAlpha = opacity;
  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.arc(cx, cy, radius + 1.5, 0, 2 * Math.PI);
  ctx.fill();
  ctx.fillStyle = "#E07B2A";
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, 2 * Math.PI);
  ctx.fill();
  return ctx.getImageData(0, 0, size, size);
}

async function _startBusyIcon() {
  if (_busyTimer) return;
  const base = await _getBaseIcon();
  _busyStart = Date.now();
  _busyTimer = setInterval(() => {
    const phase = ((Date.now() - _busyStart) % _PULSE_MS) / _PULSE_MS;
    chrome.action.setIcon({ imageData: { 16: _drawFrame(base, phase) } });
  }, _FRAME_MS);
}

function _stopBusyIcon() {
  if (_busyTimer) { clearInterval(_busyTimer); _busyTimer = null; }
  chrome.action.setIcon({ path: { 16: "icon-16.png", 48: "icon-48.png", 128: "icon-128.png" } });
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type !== "import") return;
  doImport(msg.url, msg.pasted_markdown, msg.context);
  // Return false — we communicate back via storage, not sendResponse.
});

async function doImport(url, markdown, context) {
  // Record start time so the popup can detect a stale running state if this
  // service worker is ever killed despite the keepalive.
  await chrome.storage.session.set({
    importState: { status: "running", url, verb: "Importing…", startedAt: Date.now() },
  });

  _startBusyIcon();

  // Keep the service worker alive during the long-running bridge fetch.
  // chrome.runtime.getPlatformInfo() is a lightweight no-op that Chrome
  // counts as "active work", preventing idle termination.
  const keepAlive = setInterval(
    () => chrome.runtime.getPlatformInfo(() => {}),
    20_000
  );

  try {
    const resp = await fetch(`http://localhost:${BRIDGE_PORT}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: "web-import",
        args: { url, pasted_markdown: markdown, context: context || "" },
      }),
    });

    const body = await resp.json().catch(() => ({}));

    if (resp.status === 409) {
      await storeError(url, "Bridge is busy — try again in a moment.");
      return;
    }
    if (!resp.ok || body?.is_error) {
      await storeError(url, body?.result ?? body?.error ?? `Bridge returned HTTP ${resp.status}`);
      return;
    }

    const filename = body?.created_files?.[0]?.split("/").pop() ?? null;
    await chrome.storage.session.set({
      importState: { status: "success", url, filename },
    });
  } catch (err) {
    const msg =
      err instanceof TypeError && err.message.toLowerCase().includes("fetch")
        ? `Bridge not reachable on port ${BRIDGE_PORT}. Is the dashboard running?`
        : err.message;
    await storeError(url, msg);
  } finally {
    clearInterval(keepAlive);
    _stopBusyIcon();
  }
}

// Include the url so the popup can scope the error to the tab it came from.
async function storeError(url, msg) {
  await chrome.storage.session.set({
    importState: { status: "error", url, result: msg },
  });
}
