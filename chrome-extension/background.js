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
  }
}

// Include the url so the popup can scope the error to the tab it came from.
async function storeError(url, msg) {
  await chrome.storage.session.set({
    importState: { status: "error", url, result: msg },
  });
}
