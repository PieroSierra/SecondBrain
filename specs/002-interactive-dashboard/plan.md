# Implementation Plan: Interactive KB Dashboard

**Branch**: `002-interactive-dashboard` | **Date**: 2026-06-16 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/002-interactive-dashboard/spec.md`

## Summary

Add an attractive local web dashboard over the existing Second Brain skills. The home view leads with a natural-language query box; secondary controls cover paste-Markdown / PDF / Craft import, plus ingest and lint. A read-only status strip surfaces filesystem-derived counts and the last-ingest time.

Per the owner's directive, the dashboard does **no KB logic of its own** — every action is performed by shelling out to `claude -p "<invoke-skill>" --output-format json` and rendering the JSON result. The skills (`second-brain-query`, `-md-add`, `-pdf-import`, `-craft-import`, `-ingest`, `-lint`) remain the sole system of record.

Because a browser cannot spawn `claude` directly, a **minimal bridge** is required. The bridge is a single Python stdlib script with two responsibilities and zero KB logic:

1. Serve the static dashboard files on `127.0.0.1`.
2. On POST, exec `claude -p ...`, capture stdout, return it as JSON. On GET `/status`, read the filesystem (no skill invocation).

There is no framework, no `pip install`, no `node_modules`, no database, no auth, no remote exposure.

## Technical Context

**Language/Version**: Python 3 (macOS system Python, stdlib only) for the bridge; vanilla HTML/CSS/JS (ES modules) for the page
**Primary Dependencies**: Claude Code CLI (`claude` on PATH), Python stdlib (`http.server`, `subprocess`, `json`, `pathlib`), one vendored markdown renderer (`marked.min.js`, single file)
**Storage**: Reuses the existing vault (`raw/`, `wiki/`, `outputs/`, `raw/.ingest-manifest.json`). No new datastore.
**Testing**: Manual acceptance against the spec's user stories; no automated test framework (consistent with feature 001).
**Target Platform**: macOS, single-user, localhost only; modern desktop browser (Safari/Chrome/Firefox current)
**Project Type**: Local desktop-web app (static page + thin bridge process); no hosted service
**Performance Goals**: Status strip renders in <1 s (SC-007); query end-to-end <60 s (SC-006); batch ingest of ≤20 files <2 min (SC-006)
**Constraints**: Bridge binds to `127.0.0.1` only; no auth (single-user local); subprocess invocations use list-argv (never `shell=True`); only `claude` is exec'd; bridge has zero KB knowledge
**Scale/Scope**: One user, one vault, one browser tab; six skill-backed operations plus a filesystem-derived status read

## Constitution Check

No active constitution is defined for this project (the constitution file contains only the template placeholders). No gates to evaluate. Re-check if a constitution is ratified before tasks are generated.

## Architecture Sketch

```
┌────────────────────┐    HTTP (127.0.0.1)    ┌──────────────────────┐    subprocess    ┌───────────────────┐
│  Browser           │ ─────────────────────► │  bridge.py (stdlib)  │ ───────────────► │  claude -p "..."  │
│  (static HTML/JS)  │ ◄───────────────────── │  • serves /          │ ◄─────────────── │  --output-format  │
│  • query box       │     JSON               │  • POST /run-skill   │    stdout JSON   │      json         │
│  • import forms    │                        │  • GET  /status      │                  │  (runs the skill) │
│  • status strip    │                        │  • POST /upload      │                  └────────┬──────────┘
└────────────────────┘                        └──────────┬───────────┘                           │ reads/writes
                                                         │ reads only (status)                    ▼
                                                         └────────────────────────────► ./raw, ./wiki, ./outputs
```

**Key contract**: the bridge never interprets a skill response. It captures `claude`'s stdout JSON verbatim and forwards it. The page parses `.result` (the final assistant message) and renders it as Markdown. Generated files (e.g. `outputs/YYYY-MM-DD_query-*.md`) are located by listing the relevant output directory after the call, not by parsing the model's text.

## Project Structure

### Documentation (this feature)

```text
specs/002-interactive-dashboard/
├── plan.md                 # This file
├── research.md             # Phase 0 — decisions: bridge language, markdown rendering, file-link discovery, locking
├── data-model.md           # Phase 1 — entities the dashboard reads/derives
├── quickstart.md           # Phase 1 — how to start and use the dashboard
├── contracts/              # Phase 1 — HTTP contract the bridge exposes (the only API in this feature)
│   └── bridge-http.md
└── tasks.md                # Phase 2 output (generated by /speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
/Users/piero.sierra/Development/SecondBrain/
├── dashboard/                          # NEW — everything for this feature lives here
│   ├── bridge.py                       # ~100-line stdlib HTTP server; only KB-aware code is "run this skill string"
│   ├── index.html                      # single-page dashboard
│   ├── styles.css
│   ├── app.js                          # ES module — query, imports, status, ingest, lint
│   └── lib/
│       └── marked.min.js               # vendored markdown renderer (single file, no CDN at runtime)
│
├── raw/, wiki/, outputs/               # existing vault — UNCHANGED in structure
├── .claude/skills/                     # existing skills — UNCHANGED
└── CLAUDE.md                           # plan reference updated to this plan
```

**Structure Decision**: A single `dashboard/` directory at the vault root holds the entire feature. There is no `src/`, no `tests/`, no build step. The page is opened by running `python3 dashboard/bridge.py` (or a tiny wrapper script) — the bridge prints a `http://127.0.0.1:<port>/` URL and optionally `open`s it. The vault layout and the skills are unchanged.

## Phase 0 — Research (decisions to resolve before design)

Captured in [`research.md`](research.md). Summary of decisions made there:

- **Bridge runtime**: Python 3 stdlib (no `pip`). Ships with macOS; zero install friction.
- **Markdown rendering**: vendor `marked.min.js` locally (~30 KB single file). No CDN at runtime → no network dependency.
- **Skill invocation**: `claude -p "<prompt>" --output-format json --permission-mode bypassPermissions --add-dir <vault>`. The bridge forwards the resulting JSON verbatim; the page reads `.result`.
- **Locating saved output files** (query, lint reports): after each call that writes to `outputs/`, the bridge lists `outputs/` and returns the newest matching file (`*query*.md`, `*lint*.md`). The skill is not modified.
- **PDF upload**: multipart POST → bridge writes to a tempfile inside the vault, then runs `/second-brain-pdf-import <temp-path>`; the skill copies into `raw/pdf/`; bridge deletes the temp on completion.
- **Concurrency**: bridge holds a single in-flight "long operation" mutex. A second long op returns HTTP 409 with a clear message. Status (`GET /status`) is never locked.
- **Status strip**: pure filesystem read — count files in `wiki/*.md` (excl. `INDEX.md`), count entries in `raw/.ingest-manifest.json`'s "unprocessed" set or fall back to file-mtime comparison against last ingest; read `last_ingest` from manifest, fall back to mtime of `wiki/INDEX.md`.
- **Binding & auth**: bind to `127.0.0.1` on a fixed default port (e.g. `4173`) with `--port` override. No auth (single-user local; spec FR-017).
- **Subprocess safety**: always exec `claude` with list-argv; the prompt and any path argument are passed as separate argv entries (not interpolated into a shell string).

## Phase 1 — Design & Contracts

### Data model

Captured in [`data-model.md`](data-model.md). No new persisted entities. The dashboard reads and presents:

- **VaultStatus** — derived snapshot (wiki count, raw counts by source, last ingest time, output counts).
- **SkillCallResult** — passthrough of `claude -p --output-format json` output (`result`, `is_error`, `cost_usd`, …) plus a bridge-added `output_file` field where applicable.
- **PendingOperation** — UI-only state for the in-flight long operation (kind, started_at).

### Contracts

The only API in this feature is the bridge's HTTP surface, captured in [`contracts/bridge-http.md`](contracts/bridge-http.md):

- `GET /` — serves `index.html`
- `GET /static/<path>` — serves `styles.css`, `app.js`, `lib/marked.min.js`
- `GET /status` — returns `VaultStatus` JSON (filesystem-derived; never invokes a skill)
- `POST /run` — body `{kind: "query"|"md-add"|"craft-import"|"ingest"|"lint", args: {...}}`; bridge maps `kind` to a skill prompt, execs `claude -p`, returns `SkillCallResult`
- `POST /upload-pdf` — multipart upload; bridge stores tempfile and runs the PDF-import skill; returns `SkillCallResult`

The mapping from `kind` → skill prompt is the only KB-aware code in the bridge, and it is a static dictionary of one-line templates.

### Quickstart

Captured in [`quickstart.md`](quickstart.md): how to start the bridge, what to expect on first open, and how to verify each user story end-to-end.

### Agent context update

`CLAUDE.md` currently points at `specs/001-personal-knowledge-base/plan.md` (the implementation note at the top). Updated to point at this plan for the duration of work on feature 002.

## Complexity Tracking

No constitution violations to justify.

| Decision | Why minimal | What we explicitly rejected |
|----------|-------------|------------------------------|
| Python stdlib bridge (~100 LOC) | Browser cannot spawn subprocesses; this is the smallest possible bridge | FastAPI/Flask backend (would tempt rewriting skill logic server-side, violating user directive); Electron/Tauri (build chain); pure file:// page (cannot exec) |
| Vendored `marked.min.js` | One file, no build, no CDN at runtime, offline-clean | npm + bundler; CDN-loaded (network dependency) |
| Newest-file-in-outputs heuristic for output_file | Zero skill changes | Modifying every skill to emit a structured "saved at X" line |
| Single-mutex serialization | Trivial; matches spec FR-013 | Per-skill queues; full job system |
