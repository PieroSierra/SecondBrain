![Second Brain](dashboard/app_taskbar.png)
# Second Brain — macOS app

A native macOS wrapper around the Second Brain dashboard. It:

- launches the Python bridge (`dashboard/bridge.py`) as a child process,
- shows the dashboard in an embedded window (a `WKWebView`, not a Safari tab),
- appears in the Dock while running — click the Dock icon to bring the window back,
- shows docs to ingest and live status during operations,
- **kills the bridge when you quit** (Cmd-Q).

The app contains **no knowledge-base logic**. It only starts, displays, and stops the
bridge — the skills remain the sole system of record. It points at your on-disk repo,
so `git pull` updates the dashboard with no rebuild.

## Requirements

- macOS 12+ with the Xcode command line tools (`xcode-select --install`) — provides `swiftc`.
- The Second Brain repo checked out locally (this folder lives inside it).
- `python3` and `claude` (and/or `codex`) reachable from your **interactive shell**. The
  app launches the bridge via `zsh -ilc`, so whatever `command -v claude` finds in a normal
  terminal is what the app uses — including PATH entries you add in `~/.zshrc` (where most
  setups, e.g. `~/.local/bin` and Homebrew, actually live).

## Build

```sh
cd macos-app
./scripts/build.sh
```

This compiles the Swift sources, generates the app icon from `dashboard/logo.png`, and
assembles `build/SecondBrain.app`. No Xcode project required.

## Install

```sh
cp -R build/SecondBrain.app /Applications/
```

**First launch (Gatekeeper).** The app is unsigned, so double-clicking shows
“can’t be opened.” Do one of:

- Right-click the app → **Open** → **Open** (only needed once), or
- `xattr -dr com.apple.quarantine /Applications/SecondBrain.app`

## First run

On first launch the app asks for your **vault folder** — pick the SecondBrain repo root
(the folder containing `dashboard/bridge.py`). The choice is remembered. Change it later
via the app menu → **Choose Vault…**.

## Switching engine (Claude ↔ Codex)

The **Engine** menu picks which agent CLI backs the skills. Choosing an engine restarts the
bridge with `AGENT_ENGINE` set and reloads the dashboard (~1s); the active engine shows a
checkmark and the dashboard's engine tile updates. Until you pick from the menu, the app
defers to the vault's `.env` (`AGENT_ENGINE=…`), or Claude by default. Once you choose, your
selection is remembered and overrides `.env`. (Codex must be installed for *running* skills
under it — switching itself always succeeds; a missing `codex` only surfaces when you run an
operation.)

## How it works

| Concern | Behavior |
|---|---|
| **Finding `claude`/`python3`** | Bridge is spawned with `/bin/zsh -ilc` (interactive login), so it inherits the full PATH your `~/.zshrc` builds — not launchd's bare PATH. |
| **Port** | Fixed `127.0.0.1:4173` (same as `run.sh`). |
| **Readiness** | Polls `GET /healthz` until the dashboard responds, then loads the web view. |
| **Already running?** | If a bridge is already up (e.g. you ran `./run.sh`), the app **adopts** it and does **not** kill it on quit. |
| **Engine switch** | The **Engine** menu restarts the bridge with `AGENT_ENGINE` injected (reclaiming the port if a foreign bridge holds it) and reloads the page. |
| **Quit** | Sends `SIGTERM` to the bridge it started (frees the port in ~50 ms), escalating to `SIGKILL` if needed. |
| **Close window** | App stays running; click the Dock icon to reopen. |
| **Logs** | Bridge stdout/stderr tee to `~/Library/Application Support/SecondBrain/bridge.log`. |

## Sharing with others

The `.app` is a companion to the repo, not a standalone download: a recipient still needs
the repo cloned plus `claude`/`codex` on their PATH. To share the *source* path, have them
clone the repo and run `./scripts/build.sh`.

### Signed + notarized binary (`release.sh`)

For a downloadable `.app` that opens with a double-click (no right-click / no Gatekeeper
prompt), use `scripts/release.sh`. It builds, re-signs with your **Developer ID Application**
identity + hardened runtime, notarizes with Apple, staples the ticket, and zips the result.

One-time setup under your Apple Developer account (paid membership required):

1. Create a **Developer ID Application** certificate — Xcode → Settings → Accounts →
   your team → Manage Certificates → **+** → *Developer ID Application*.
2. Store notarization credentials once:
   ```sh
   xcrun notarytool store-credentials "SecondBrain-Notary" \
     --apple-id "you@example.com" --team-id "YOURTEAMID" \
     --password "app-specific-password"   # created at appleid.apple.com
   ```

Then:
```sh
DEV_ID="Developer ID Application: Your Name (TEAMID)" ./scripts/release.sh
# → build/SecondBrain.zip  (signed, notarized, stapled)
```

Note the binary still only *supervises* the bridge — the recipient needs the repo and
`claude`/`codex` for it to do anything.

## Files

```
macos-app/
├── Sources/
│   ├── main.swift            # NSApplication entry point (.regular = Dock app)
│   ├── AppDelegate.swift     # lifecycle, menu, quit → stop bridge
│   ├── BridgeController.swift # spawn / healthz-poll / SIGTERM stop
│   ├── WebWindow.swift       # NSWindow + WKWebView
│   └── Preferences.swift     # vault path, first-run picker, engine choice
├── Resources/Info.plist
└── scripts/{build,make-icon}.sh
```
