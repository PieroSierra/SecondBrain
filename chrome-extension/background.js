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
// Busy icon — pulsing brand dot in the lower-left corner of the toolbar icon
// while an import is in progress. Mirrors the dashboard's `@keyframes pulse`
// (styles.css) and the macOS Dock dot (DockActivity.swift): the fill travels
// from ink to the hot-pink accent while the dot scales 0.7→1, over a 1.4s
// period. Opacity is constant — the colour carries the pulse.
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

// Brand pulse endpoints — keep in step with dashboard styles.css `:root`.
const _DOT_INK  = [0x19, 0x1B, 0x1F]; // --ink,  trough (small)
const _DOT_CLAY = [0xDB, 0x27, 0x77]; // --clay, peak (large)

function _drawFrame(base, phase) {
  const size = 16;
  const canvas = new OffscreenCanvas(size, size);
  const ctx = canvas.getContext("2d");
  ctx.drawImage(base, 0, 0, size, size);
  // Sine ease: t = 0 at the trough (phase 0/1), t = 1 at the peak (phase 0.5),
  // matching the CSS keyframe's scale/colour travel.
  const t = Math.sin(phase * Math.PI);
  // Radius peaks at its native largest state and shrinks to 0.7 at the trough.
  const radius = 3.5 * (0.7 + 0.3 * t);
  // Lerp ink → clay in sRGB — the colour, not opacity, carries the pulse.
  const r = Math.round(_DOT_INK[0] + (_DOT_CLAY[0] - _DOT_INK[0]) * t);
  const g = Math.round(_DOT_INK[1] + (_DOT_CLAY[1] - _DOT_INK[1]) * t);
  const b = Math.round(_DOT_INK[2] + (_DOT_CLAY[2] - _DOT_INK[2]) * t);
  const cx = 4, cy = size - 4; // bottom-left, inset 1px to clear the icon edge
  // Light outline: the dot is near-black at the trough, so a dark ring would
  // vanish into it. White separates it from the icon at both ends of the pulse.
  ctx.fillStyle = "rgba(255,255,255,0.55)";
  ctx.beginPath();
  ctx.arc(cx, cy, radius + 1.5, 0, 2 * Math.PI);
  ctx.fill();
  ctx.fillStyle = `rgb(${r},${g},${b})`;
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
    // The import was cancelled (e.g. via the dashboard's Stop control): the
    // bridge killed the run and returned {stopped:true} with HTTP 200. Nothing
    // was saved, so report a neutral "stopped" — not success, not an error.
    if (body?.stopped) {
      await chrome.storage.session.set({
        importState: { status: "stopped", url },
      });
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
