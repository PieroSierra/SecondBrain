
![[dashboard/chrome-extension/icon-128.png]]
# Second Brain
A personal knowledge base that lives in this folder. Drop content in, have it organized automatically, ask questions, and get sourced answers — either through Claude Code **slash commands** or a **local web dashboard**.

---

## What it does

The system has three tiers:

| Folder     | Purpose                                                                         |
| ---------- | ------------------------------------------------------------------------------- |
| `raw/`     | Everything you capture. Append-only. Never modified by AI.                      |
| `wiki/`    | AI-organised topic articles with cross-links. Written only by the ingest skill. |
| `outputs/` | Query answers and lint reports, dated and saved automatically.                  |

Content flows in one direction: `raw/` → ingest → `wiki/` → query → `outputs/`.

---

## Prerequisites

- **Claude Code CLI** (`claude` on your PATH). Install from [claude.ai/code](https://claude.ai/code).
- **Python 3** (macOS system Python is fine — no `pip install` needed).
- (optional) The **Craft MCP** integration configured in Claude Code if you want Craft import.

---

## First-time setup

```bash
/second-brain-setup
```

This walks you through declaring your interests and writes the `CLAUDE.md` configuration file. Run it once, or again any time you want to update your interests.

---

## Usage — Claude Code slash commands

All knowledge-base operations are Claude Code skills. Run them by typing the slash command in a Claude Code session open to this folder.

### Capture

You can also drop files directly into `raw/` — a `.md` note, a PDF, even an image — and they will be picked up on the next `/second-brain-ingest`. The skills below are convenience wrappers that handle conversion (e.g. PDF text extraction) and Craft/web fetching before writing to `raw/`.

| Command                                          | What it does                                                            |
| ------------------------------------------------ | ----------------------------------------------------------------------- |
| `/second-brain-import-md`                           | Save a pasted Markdown note into `raw/`                                   |
| `/second-brain-import-file "<path>"`                | Import any file — PDF → `raw/pdf/`, image → `raw/images/`, text → `raw/` |
| `/second-brain-import-pdf <path>`                   | Extract and save a PDF into `raw/pdf/` (also used by ingest internally)   |
| `/second-brain-import-craft Folder/DocumentName`    | Pull a named note from Craft into `raw/craft/`                            |
| `/second-brain-import-web <url>`                    | Fetch a webpage and save it into `raw/web/`                               |

### Organise

| Command                                              | What it does                                                                                |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `/second-brain-ingest`                               | Read new or changed files in `raw/`, update `wiki/` articles, rebuild `INDEX.md`            |
| `/second-brain-lint`                                 | Scan the wiki for contradictions, unsupported claims, and gaps; save a report to `outputs/` |
| `/second-brain-edit-wiki "<prompt>" [<slug>]`        | Apply a natural-language edit to one or more wiki articles; preserves sources footer and wikilinks |

### Retrieve

| Command | What it does |
|---|---|
| `/second-brain-query "your question here"` | Ask the knowledge base a natural-language question; answer saved to `outputs/` |

### Notes

- Ingest is **explicit** — new content in `raw/` does not appear in the wiki until you run `/second-brain-ingest`.
- The wiki is **AI-managed** — never edit files in `wiki/` directly.
- `raw/` is **append-only** — never delete or modify files there.

---

## Usage — Interactive dashboard

The dashboard is a local web UI that surfaces the same six operations without opening a terminal.

![[dashboard/dashboard_sample.png]]

### Start

```bash
./run.sh
```

The bridge prints `http://127.0.0.1:4173/` and opens it in your browser. Stop with `Ctrl-C`.

`run.sh` is idempotent — if the port is already occupied by a previous bridge it kills it before starting a fresh one.

### Custom port

```bash
PORT=4180 ./run.sh
# or directly:
python3 dashboard/bridge.py --port 4180 --no-open
```

### What the dashboard provides

- **Hero query box** — ask a question and read the rendered answer directly in the page.
- **Navigation bar** — browse past answers and wiki articles.
- **Import controls** — paste Markdown, drop/select any file (PDF, image, plain text), import from a URL, or specify a Craft folder and document name.
- **Ingest / Lint buttons** — trigger wiki maintenance and view the results inline.
- **Wiki edit boxes** — after a lint report runs, or while viewing any wiki article, a suggestion box lets you describe an edit in plain English and apply it directly without touching files manually.
- **Status strip** — wiki article count, pending raw items, and last ingest time, derived from the filesystem with no model call.

### How it works

The dashboard is a static HTML page. Every long operation fires a `POST /run` request to a tiny Python bridge (`dashboard/bridge.py`) which execs `claude -p "/second-brain-..." --output-format json` and streams the result back. The bridge has no knowledge-base logic of its own — the skills are the only system of record.

### Chrome extension

A companion browser extension lets you import any page directly from Chrome without opening the dashboard first. It connects to the same local bridge, so the bridge must be running.

**Install (one-time):**

1. Open Chrome and navigate to `chrome://extensions`.
2. Enable **Developer mode** (toggle in the top-right corner).
3. Click **Load unpacked**.
4. Select the `dashboard/chrome-extension/` folder inside this repo.

The "Second Brain Importer" extension will appear in your toolbar (pin it for easy access).

**Usage:**

1. Start the bridge with `./run.sh` (or keep it running in the background).
2. Browse to any page you want to capture.
3. Click the Second Brain icon in your toolbar and press **Import this page**.

The extension extracts the page's main content, converts it to Markdown, and saves it to `raw/web/` — identical to `/second-brain-web-import` but triggered from the browser. For paywalled pages where the extension can't extract content, use `/second-brain-web-import` with paste mode instead.

### Local settings (.env)

Create a `.env` file at the vault root to override defaults without editing any code:

```
# Use a different claude binary (e.g. a Max subscription account):
CLAUDE_BIN=claude-personal

# Show the Craft import card in the dashboard (requires Craft MCP in Claude Code):
CRAFT_ENABLED=1
```

The `.env` file is gitignored — it never leaves your machine.

### Permissions

Skills are invoked with `--permission-mode bypassPermissions`. On managed Claude Code installs you may also need `Write`, `Edit`, and `Bash` entries under `permissions.allow` in `.claude/settings.local.json`.

### Troubleshooting

| Symptom | Fix |
|---|---|
| "Connection refused" in the browser | The bridge isn't running — start it with `./run.sh`. |
| `claude: command not found` in the bridge log | Ensure `claude` is on the PATH of the shell that launches the bridge, or set `CLAUDE_BIN` in `.env`. |
| Long operation returns 504 | Skill timed out. Run the same prompt directly in terminal to debug: `claude -p "/second-brain-query \"...\"" --output-format json --permission-mode bypassPermissions`. |
| 409 Busy | Another operation is in flight — wait for it to finish. |
| Status strip shows `—` | `raw/.ingest-manifest.json` is missing; run `/second-brain-ingest` once to create it. |

---

## Project layout

```
SecondBrain/
├── raw/                        Source content (append-only)
│   ├── craft/                  Notes imported from Craft
│   ├── pdf/                    Text extracted from PDFs
│   ├── images/                 Visual descriptions of imported images
│   ├── web/                    Pages fetched by web-import
│   └── .ingest-manifest.json   Machine-managed ingestion state
├── wiki/                       AI-organised topic articles
│   └── INDEX.md                Master topic index (rebuilt on every ingest)
├── outputs/                    Query answers and lint reports
├── dashboard/                  Local web UI
│   ├── bridge.py               Python stdlib HTTP server + claude proxy
│   ├── index.html              Single-page dashboard
│   ├── styles.css              Visual design
│   ├── app.js                  Front-end controller
│   ├── lib/marked.min.js       Vendored Markdown renderer
│   └── chrome-extension/       Browser extension (load unpacked in Chrome)
├── run.sh                      Start the dashboard (idempotent port cleanup)
├── CLAUDE.md                   Vault schema + your declared interests (gitignored — personal)
├── CLAUDE.md.example           Template to copy when setting up a new vault
├── .env                        Local overrides: CLAUDE_BIN, CRAFT_ENABLED (gitignored)
└── specs/                      Feature specs and implementation plans
```

---

## Further reading

- `CLAUDE.md` — vault schema, folder rules, and your declared interests.
- `specs/001-personal-knowledge-base/spec.md` — PKB feature specification.
- `specs/002-interactive-dashboard/spec.md` — Dashboard feature specification.
- `specs/002-interactive-dashboard/plan.md` — Implementation plan and architecture.
- `specs/002-interactive-dashboard/contracts/bridge-http.md` — Bridge HTTP API.
