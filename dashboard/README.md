# Second Brain — Dashboard

A local web front-end over the Second Brain skills.

```bash
python3 dashboard/bridge.py
```

The bridge prints `http://127.0.0.1:4173/` and opens it in your browser. Stop with `Ctrl-C`.

## What it does

Every action is performed by shelling out to the configured agent CLI — `claude -p "..." --output-format json` (default) or `codex exec "$..." --sandbox workspace-write` when `AGENT_ENGINE=codex`. The bridge is a single Python stdlib script (~700 lines) that:

- Serves the static page (`index.html`, `styles.css`, `app.js`, `lib/marked.min.js`).
- Forwards `POST /run` requests to the corresponding skill (`/second-brain-query`, `-md-add`, `-craft-import`, `-pdf-import`, `-ingest`, `-lint`).
- Accepts PDF uploads at `POST /upload-pdf`, stages them in `dashboard/.uploads/`, runs the import skill, and cleans up.
- Reads `raw/.ingest-manifest.json` plus the filesystem for `GET /status` — never spawns `claude` for that.

There is no framework, no `pip install`, no `node_modules`, no database, no remote exposure. Listens only on `127.0.0.1`.

## Security model

Binding to `127.0.0.1` is **not** a security boundary on its own — any web page you visit can issue a cross-origin request to `http://127.0.0.1:4173`. The bridge therefore gates every data/action endpoint:

- **CSRF token.** A fresh random token is generated each start and injected into `index.html`; the page echoes it as `X-Bridge-Token`. A cross-origin page cannot read another origin's DOM, so it cannot learn the token. Requests without it get `403`.
- **Origin + Host checks.** Requests from a non-allowlisted `Origin` are rejected, and a `Host`-header check defeats DNS-rebinding. The Chrome extension authenticates by its `chrome-extension://` origin (no token); pin it with `EXTENSION_ORIGIN` in `.env` if you want to allow only one extension id.
- **Scoped executor.** Skills run **without** `bypassPermissions`. Each skill gets only the tools it needs via `--allowedTools`; `Write`/`Edit` are granted **path-scoped to the vault** through a generated `--settings` lockdown file (`.lockdown-settings.json`), so a write can't land outside this folder; and `--disallowedTools` denies `Bash`, network egress, and subagent spawning outright (deny beats every allow). This bounds the blast radius of a prompt-injection carried in imported content.
- **Output sanitisation + CSP.** Rendered Markdown is sanitised with DOMPurify before it touches the DOM, and `index.html` ships a strict `Content-Security-Policy` (`script-src 'self'`).

> **Important:** the vault-confinement of `Write`/`Edit` only holds if `.claude/settings.local.json` does **not** grant a *bare* `Write`, `Edit`, or `Bash` (a bare allow unions back to "anywhere"). Keep `permissions.allow` entries narrow/path-scoped.

**Engine note — Codex (`AGENT_ENGINE=codex`).** The same operations run via `codex exec`. Codex has no per-tool allow/deny list and is inherently shell-capable, so confinement is by **sandbox + working root**, not tool denial: every op runs with `--sandbox workspace-write -C <vault>`, which limits file writes to the vault and disables network egress by default (web-import is the one op granted network). The trade-off vs. Claude Code is deliberate and worth understanding: writes are still vault-confined, but Codex **can** run shell commands inside the sandbox — there is no "deny `Bash`" equivalent. The `.claude/skills/` ↔ `.agents/skills` link means both engines run the *same* skill instructions; only the enforcement mechanism differs. Choose the engine with this in mind.

## Customising the port

```bash
python3 dashboard/bridge.py --port 4180 --no-open
```

`--no-open` skips the auto `open` on macOS — useful in headless contexts.

## Permissions

The skills are **not** invoked with `bypassPermissions`. Each skill is run with an explicit `--allowedTools` list (the minimum it needs, with `Write`/`Edit` path-scoped to the vault) plus a `--disallowedTools` deny list (`Bash`, `WebFetch` except web-import, `Agent`, `Workflow`). The dashboard surfaces any denials as part of the model's reply.

Do **not** add bare `Write`, `Edit`, or `Bash` to `permissions.allow` in `.claude/settings.local.json` to "fix" a denial — that re-opens the vault-escape hole the scoping closes. If a skill genuinely needs another capability, add the narrowest possible rule (path-scoped or command-scoped).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Browser shows "Connection refused" | Bridge isn't running — start it from the vault root. |
| `claude: command not found` in the bridge log | Install Claude Code and ensure `claude` is on `PATH` (or set `AGENT_ENGINE=codex`). |
| `codex: command not found` in the bridge log | With `AGENT_ENGINE=codex`, install the Codex CLI and ensure `codex` is on `PATH` (or set `CODEX_BIN`). |
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

The dashboard is a thin front-end; every action it exposes runs one of the
`second-brain-*` skills under `.claude/skills/`. Shown in vault context:

```
SecondBrain/                     vault root
├── dashboard/                   this web front-end
│   ├── bridge.py                HTTP server + claude proxy
│   ├── index.html               single-page UI
│   ├── styles.css               visual design (cream paper, serif display)
│   ├── app.js                   front-end controller (vanilla ES module)
│   ├── README.md                this file
│   ├── .uploads/                transient PDF staging (gitignored)
│   ├── chrome-extension/        browser extension (load unpacked in Chrome)
│   └── lib/
│       ├── marked.min.js        vendored Markdown renderer
│       ├── purify.min.js        vendored DOMPurify (HTML sanitiser)
│       └── PROVENANCE.md        SHA-pinned source of truth
├── .claude/skills/              the skills each dashboard action invokes
│   ├── second-brain-query/      ← Ask a question
│   ├── second-brain-ingest/     ← Run ingest
│   ├── second-brain-lint/       ← Run lint
│   ├── second-brain-edit-wiki/  ← Apply a wiki edit
│   └── second-brain-import-{md,web,pdf,file,craft}/  ← Add content
├── raw/                         captured source content (gitignored)
├── wiki/                        AI-organised knowledge (gitignored)
└── outputs/                     query answers & lint reports (gitignored)
```

## Spec

The full specification, plan, and design contract live in [`/specs/002-interactive-dashboard/`](../specs/002-interactive-dashboard/) — start with `spec.md`, then `plan.md`, then `contracts/bridge-http.md`.
