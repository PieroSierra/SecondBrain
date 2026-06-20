# Second Brain — Dashboard

A local web front-end over the Second Brain skills.

```bash
python3 dashboard/bridge.py
```

The bridge prints `http://127.0.0.1:4173/` and opens it in your browser. Stop with `Ctrl-C`.

## What it does

Every action is performed by shelling out to `claude -p "..." --output-format json`. The bridge is a single Python stdlib script (~700 lines) that:

- Serves the static page (`index.html`, `styles.css`, `app.js`, `lib/marked.min.js`).
- Forwards `POST /run` requests to the corresponding skill (`/second-brain-query`, `-md-add`, `-craft-import`, `-pdf-import`, `-ingest`, `-lint`).
- Accepts PDF uploads at `POST /upload-pdf`, stages them in `dashboard/.uploads/`, runs the import skill, and cleans up.
- Reads `raw/.ingest-manifest.json` plus the filesystem for `GET /status` — never spawns `claude` for that.

There is no framework, no `pip install`, no `node_modules`, no database, no auth, no remote exposure. Listens only on `127.0.0.1`.

## Customising the port

```bash
python3 dashboard/bridge.py --port 4180 --no-open
```

`--no-open` skips the auto `open` on macOS — useful in headless contexts.

## Permissions

The skills are invoked with `--permission-mode bypassPermissions`. On corporate-managed Claude Code installs, that is not always sufficient — you may also need bare `Write`, `Edit`, and `Bash` entries under `permissions.allow` in `.claude/settings.local.json`. The dashboard surfaces any denials as part of the model's reply.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Browser shows "Connection refused" | Bridge isn't running — start it from the vault root. |
| `claude: command not found` in the bridge log | Install Claude Code and ensure `claude` is on `PATH`. |
| Long operation returns 504 | Skill exceeded its per-kind timeout. Try the same prompt directly: `claude -p "..." --output-format json` to debug. |
| `409 busy` when starting an op | Another long operation is in flight; wait or check the busy banner. |
| Status strip stuck on `—` | `/status` is failing — check the bridge log for stack traces. |
| PDF import 504 with a large file | Per-kind timeout is 360 s. Very large PDFs may need to be split. |

## Chrome extension

A companion browser extension lets you import any page from Chrome without opening the dashboard first.

**Install (one-time):**

1. Open Chrome and go to `chrome://extensions`.
2. Enable **Developer mode** (top-right toggle).
3. Click **Load unpacked** and select the `dashboard/chrome-extension/` folder.

The bridge must be running (`./run.sh`) for the extension to work. It sends the page's extracted content to the same `POST /run` endpoint as the dashboard's web import card.

## Layout

```
dashboard/
├── bridge.py            HTTP server + claude proxy
├── index.html           single-page UI
├── styles.css           visual design (cream paper, serif display)
├── app.js               front-end controller (vanilla ES module)
├── README.md            this file
├── .uploads/            transient PDF staging (gitignored)
├── chrome-extension/    browser extension (load unpacked in Chrome)
└── lib/
    ├── marked.min.js    vendored Markdown renderer
    └── PROVENANCE.md    SHA-pinned source of truth
```

## Spec

The full specification, plan, and design contract live in [`/specs/002-interactive-dashboard/`](../specs/002-interactive-dashboard/) — start with `spec.md`, then `plan.md`, then `contracts/bridge-http.md`.
