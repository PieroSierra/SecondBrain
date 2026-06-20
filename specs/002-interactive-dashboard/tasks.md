---

description: "Task list for the Interactive KB Dashboard"
---

# Tasks: Interactive KB Dashboard

**Input**: Design documents from `/specs/002-interactive-dashboard/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/bridge-http.md, quickstart.md

**Tests**: Not requested. No automated test tasks are generated. Manual acceptance via [`quickstart.md`](quickstart.md) at the end of each phase.

**Organization**: Tasks are grouped by user story so each story is independently demoable. The bridge (`dashboard/bridge.py`), the page shell (`index.html`, `styles.css`, `app.js`), and the vendored library (`lib/marked.min.js`) all live under `dashboard/` at the vault root. The bridge file is touched by multiple stories — tasks that share `bridge.py` (or `index.html`, `app.js`, `styles.css`) are NOT marked `[P]`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel — different files, no dependency on incomplete tasks
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- File paths are absolute-style under the vault root (`dashboard/...`)

## Path Conventions

- Vault root: `/Users/piero.sierra/Development/SecondBrain/`
- All new code lives under `dashboard/` (see [`plan.md`](plan.md) §Project Structure)
- No `src/`, no `tests/`, no build step

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the empty skeleton of the `dashboard/` directory and vendor the one external asset (`marked.min.js`). No behaviour yet.

- [X] T001 Create `dashboard/`, `dashboard/lib/`, and `dashboard/.uploads/` directories at the vault root; add `dashboard/.uploads/.gitignore` containing `*` so transient PDFs are never committed
- [X] T002 Vendor the Markdown renderer at `dashboard/lib/marked.min.js` (single minified file from the `marked` distribution; pinned by SHA in a one-line `dashboard/lib/PROVENANCE.md`)
- [X] T003 [P] Create empty placeholder files `dashboard/index.html`, `dashboard/styles.css`, `dashboard/app.js` with a one-line comment header naming the feature and the spec path
- [X] T004 [P] Create empty placeholder `dashboard/bridge.py` with a shebang `#!/usr/bin/env python3`, module docstring referencing `specs/002-interactive-dashboard/plan.md`, and `if __name__ == "__main__":` guard

**Checkpoint**: `dashboard/` directory exists with stubs; nothing runs yet.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build the parts of the bridge and page shell that every user story needs — HTTP server, static serving, the subprocess helper, the single long-operations mutex, the `POST /run` dispatcher with an empty kind→prompt table, and the front-end skeleton (page layout, base styles, shared JS helpers).

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T005 Implement Python stdlib HTTP server skeleton in `dashboard/bridge.py` — bind to `127.0.0.1` only; refuse `0.0.0.0`/other interfaces; `--port` arg (default 4173); print launch URL and `open` it on macOS; clean Ctrl-C shutdown
- [X] T006 Implement static-file serving in `dashboard/bridge.py` — `GET /` returns `dashboard/index.html`; `GET /static/<path>` returns files only inside `dashboard/` (reject `..` / absolute paths with 404); correct MIME types for `.html/.css/.js`
- [X] T007 Implement subprocess helper `run_claude(argv_tail: list[str], timeout: int) -> dict | tuple[int, dict]` in `dashboard/bridge.py` — builds full argv as `["claude", "-p", <prompt>, "--output-format", "json", "--permission-mode", "bypassPermissions", "--add-dir", VAULT, *argv_tail]`; uses `subprocess.run(..., shell=False, capture_output=True, text=True, cwd=VAULT, timeout=timeout)`; returns parsed JSON on success, `(504, {"error":"timeout",...})` on `TimeoutExpired`, `(502, {"error":"spawn_failed", "detail":...})` if `claude` is missing
- [X] T008 Implement single global long-operations mutex in `dashboard/bridge.py` — `threading.Lock` plus an `in_flight` dict (`{kind, started_at}`); a context manager that acquires non-blocking and raises a typed "Busy" exception if held
- [X] T009 Implement `POST /run` dispatcher in `dashboard/bridge.py` — read JSON body, look up `kind` in a `PROMPT_TEMPLATES` dict initially containing **no** entries (each story registers its own); reject unknown kind with `400 {"error":"bad_request","detail":"unknown kind"}`; acquire mutex (return `409` with `in_flight` if held); snapshot `outputs/` and `raw/` file listings; call `run_claude`; on success augment the parsed JSON with `kind`, `output_file` (newest matching path under `outputs/`, story-specific glob set by each kind), and `created_files` (set diff of `raw/` listings); on timeout/spawn-failure return the envelope from `run_claude`
- [X] T010 [P] Implement page layout shell in `dashboard/index.html` — single column with empty named regions (header/branding, status-strip, hero-query, imports, maintenance, result, error-banner); `<script type="module" src="/static/app.js">`; `<link rel="stylesheet" href="/static/styles.css">`; load `lib/marked.min.js` synchronously before `app.js`
- [X] T011 [P] Implement base styles in `dashboard/styles.css` — CSS custom properties for the palette and type scale; page grid; `.busy[disabled]` visuals for controls; `.error-banner` styling; `.markdown-body` typography for rendered answers; focus states; system-ui font stack
- [X] T012 [P] Implement shared JS utilities in `dashboard/app.js` — `postJSON(path, body)`, `postMultipart(path, formData)`, busy-state controller (single in-flight flag that disables all `[data-long-op]` controls and shows the in-flight kind + elapsed timer), `renderMarkdown(md)` wrapping `marked.parse`, `showError(detail)`, `outputFileLink(path)` returning an anchor element that points at `file://` for the saved file, and `refreshStatus()` stub (no-op until US3)

**Checkpoint**: Running `python3 dashboard/bridge.py` serves a page that loads cleanly and shows empty regions; `POST /run` works mechanically but every `kind` returns `400 unknown kind`. No user story is functional yet.

---

## Phase 3: User Story 1 — Ask the knowledge base a question (Priority: P1) 🎯 MVP

**Goal**: The owner types a natural-language question in the hero box, sees a spinner, then a rendered Markdown answer plus a link to the saved `outputs/YYYY-MM-DD_query-*.md` file.

**Independent Test**: Against a populated vault, start the bridge, open the page, submit *"What do I know about AI partnerships?"*, confirm the answer renders as formatted Markdown (not raw source) and the saved-file link works.

### Implementation for User Story 1

- [X] T013 [US1] Register `query` in `PROMPT_TEMPLATES` inside `dashboard/bridge.py` — template `/second-brain-query "{question}"` with `question` validated non-empty (else `400 bad_request`), timeout 90 s, `output_glob = "outputs/*query*.md"`; the bridge passes the rendered prompt as a single argv entry to `run_claude` (never via shell)
- [X] T014 [US1] Implement the hero query block in `dashboard/index.html` — a large `<input type="text">` (or `<textarea>` for multi-line), an "Ask" submit button, an in-progress region, and the result container (with `.markdown-body` class)
- [X] T015 [US1] Implement the query submit handler in `dashboard/app.js` — `Enter` and click both submit; calls `postJSON("/run", {kind:"query", args:{question}})`; while pending uses the foundational busy controller (showing kind="query" and elapsed time); on success calls `renderMarkdown(result)` into the result container and appends `outputFileLink(output_file)` underneath; on `is_error:true` shows the result text inside the error banner; on `409` shows "busy: <other-kind> is running"; on `504` shows "query timed out after 90 s"; on `502` shows "claude CLI not reachable"
- [X] T016 [US1] Style the hero query block prominently in `dashboard/styles.css` — large input, generous vertical space, central placement under header; result region in a card with subtle border; output-file link styled as a small footer link with monospace path

**Checkpoint**: User Story 1 is fully functional and demoable as MVP. The dashboard answers KB questions end-to-end with no terminal use.

---

## Phase 4: User Story 2 — Add new content (Priority: P2)

**Goal**: Three import controls (paste Markdown, PDF, Craft) each land a file in the correct `raw/` subfolder and confirm the created path on the page.

**Independent Test**: Exercise each of the three controls once; confirm a new file appears in `raw/`, `raw/pdf/`, and `raw/craft/` respectively; trigger a failing Craft import and confirm a specific error appears.

### Implementation for User Story 2

- [X] T017 [US2] Register `md-add` and `craft-import` in `PROMPT_TEMPLATES` inside `dashboard/bridge.py` — `md-add`: template `/second-brain-md-add "{title_hint?}"` with the pasted Markdown piped on stdin via a new `stdin=` parameter on `run_claude` (extend T007's helper minimally to accept `stdin: str | None`), timeout 30 s, `output_glob = None`, `created_glob = "raw/*.md"`; `craft-import`: template `/second-brain-craft-import "{folder}/{document}"`, timeout 60 s, `created_glob = "raw/craft/*"` (a follow-up confirms the exact md-add invocation against the existing skill — see research.md §R9; if the skill prefers an inline-prompt form rather than stdin, switch the template accordingly without changing the public API)
- [X] T018 [US2] Implement `POST /upload-pdf` in `dashboard/bridge.py` — parse `multipart/form-data` using stdlib `email.parser` / `cgi.FieldStorage` (whichever is supported on the target Python); reject non-PDF with `400 not_a_pdf`; write the body to `dashboard/.uploads/<uuid>.pdf`; acquire the long-op mutex (same instance as `/run`); call `run_claude(["/second-brain-pdf-import <abs-tempfile>"], timeout=120)`; in a `finally:` delete the tempfile; return the same SkillCallResult envelope with `kind:"pdf-import"`, `output_file:null`, and `created_files` set to new `raw/pdf/*` entries
- [X] T019 [US2] Add the three import forms to `dashboard/index.html` inside the imports region — (a) paste-Markdown: `<textarea>` plus optional title-hint input plus "Add" button; (b) PDF: `<input type="file" accept=".pdf,application/pdf">` plus "Import" button; (c) Craft: folder input + document input + "Import" button; each form has `data-long-op` so the foundational busy controller can disable it; each form has a dedicated confirmation slot underneath
- [X] T020 [US2] Implement the three import handlers in `dashboard/app.js` — paste posts to `/run` `kind:"md-add"`; Craft posts to `/run` `kind:"craft-import"`; PDF posts to `/upload-pdf` via `postMultipart`; each on success renders a confirmation panel naming the file(s) from `created_files` and an explicit reminder "this content lives in `raw/` and is not searchable until the next ingest" (FR-009); each renders specific errors from the bridge envelope (e.g. "Craft document not found", "not a PDF", `409 busy`)
- [X] T021 [P] [US2] Style the imports section in `dashboard/styles.css` — three equal cards in a row that collapse to a stack on narrow widths; subdued vs. the hero; confirmation panels in a muted success colour; the "not searchable until ingest" reminder visually tied to each confirmation

**Checkpoint**: All three capture paths work from the dashboard; failure cases show specific errors; nothing is searchable until ingest (US4) runs.

---

## Phase 5: User Story 3 — See vault status at a glance (Priority: P2)

**Goal**: The home view shows a compact, filesystem-derived status strip — wiki article count, raw items pending, last ingest time — that renders effectively instantly and never invokes a skill.

**Independent Test**: Open the page against the existing vault and confirm the strip shows accurate numbers with no model call (verify by watching the bridge log: `GET /status` returns in <100 ms and never spawns `claude`).

### Implementation for User Story 3

- [X] T022 [US3] Implement `GET /status` in `dashboard/bridge.py` — single pass over the filesystem: `wiki_article_count` from `glob(wiki/*.md)` minus `INDEX.md`; `raw_breakdown` from `raw/*.md`, `raw/pdf/**`, `raw/craft/**`; `raw_pending_count` preferred from `raw/.ingest-manifest.json`'s processed set, fallback to total raw files (set `last_ingest_source` accordingly); `outputs_query_count` and `outputs_lint_count` from `outputs/*query*.md` and `outputs/*lint*.md`; `last_ingest_iso` from manifest `last_ingest`, fallback to `mtime(wiki/INDEX.md)`, final fallback `null`; never acquires the mutex; never spawns a subprocess; gracefully handles missing files (returns 200 with degraded fields, no 5xx)
- [X] T023 [US3] Implement the status strip UI in `dashboard/index.html` — five compact tiles at the top under the header (wiki, raw pending, last ingest, query outputs, lint outputs); each tile has a `data-metric` attribute for the JS to populate; placeholder text `—` until the first fetch resolves
- [X] T024 [US3] Replace the foundational `refreshStatus()` stub in `dashboard/app.js` — fetch `/status` on page load and after every successful long-op call (so the pending count drops after an ingest and rises after an import); render `last_ingest_iso` in the user's local time with a relative hint ("2 hours ago"); render `—` for null fields; never block the page on a slow status call (timeout 2 s, then leave tiles as-is)
- [X] T025 [P] [US3] Style the status strip in `dashboard/styles.css` — five small tiles in a horizontal row at the very top, muted, monospace numbers, label small-caps; collapses to two rows on narrow widths; tile that's stale or null is visually de-emphasized

**Checkpoint**: Status strip renders in <1 s and updates after each successful import/ingest/lint.

---

## Phase 6: User Story 4 — Refresh and check the wiki (Priority: P3)

**Goal**: Two maintenance controls — "Ingest" and "Lint" — invoke the corresponding skills and render their results (with a link to the saved lint report).

**Independent Test**: With pending content in `raw/`, click "Ingest" — `wiki/` updates and a change summary appears in the page; click "Lint" — a rendered lint report appears with a footer link to `outputs/YYYY-MM-DD_lint.md`.

### Implementation for User Story 4

- [X] T026 [US4] Register `ingest` and `lint` in `PROMPT_TEMPLATES` inside `dashboard/bridge.py` — `ingest`: template `/second-brain-ingest`, timeout 180 s, `output_glob = None` (the summary lives in `result`); `lint`: template `/second-brain-lint`, timeout 120 s, `output_glob = "outputs/*lint*.md"`
- [X] T027 [US4] Add the Ingest and Lint controls to `dashboard/index.html` inside the maintenance region — two prominent buttons, each with a dedicated result container and a `data-long-op` marker so the foundational busy controller disables them while either is in flight; brief one-liner under each ("Fold pending raw/ items into the wiki", "Scan the wiki for quality issues")
- [X] T028 [US4] Implement Ingest and Lint handlers in `dashboard/app.js` — each calls `postJSON("/run", {kind})`; renders the `result` as Markdown into its own container; for lint also appends `outputFileLink(output_file)`; on success calls `refreshStatus()`; long timeouts and 409 busy surface through the same error/busy plumbing built in Phase 2
- [X] T029 [P] [US4] Style the maintenance section in `dashboard/styles.css` — two distinct, secondary-looking buttons (less prominent than the hero) below imports; result panels share `.markdown-body` styling

**Checkpoint**: All six skill-backed operations are invocable from the dashboard. SC-001, SC-002, and SC-003 are satisfied.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final visual polish, hygiene around the upload tempdir, README, and a real end-to-end walkthrough of [`quickstart.md`](quickstart.md).

- [X] T030 [P] Add `dashboard/README.md` — brief launch instructions (one paragraph), troubleshooting cribbed from `quickstart.md`, and a pointer to the spec
- [X] T031 [P] On bridge startup, remove any stale files under `dashboard/.uploads/` (best-effort) — code lives in `dashboard/bridge.py`; protects against an unclean previous shutdown leaving PDFs behind
- [X] T032 Final visual polish pass on `dashboard/styles.css` — verify hero is unambiguously the page's primary element, status strip is calm and unobtrusive, imports/maintenance are clearly secondary (FR-015/FR-016); check keyboard focus rings, light/dark mode if trivial via `prefers-color-scheme`
- [X] T033 Walk through every section of `specs/002-interactive-dashboard/quickstart.md` end-to-end against the real vault; capture and fix any rough edge inline (small bug fixes go to the relevant `dashboard/*.{py,js,html,css}` file); stop here — anything larger becomes a new task

**Checkpoint**: Feature is complete. All four user stories pass their independent tests per quickstart.md.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → no dependencies; can start immediately
- **Foundational (Phase 2)** → depends on Setup; BLOCKS all user stories
- **User Stories** → all depend on Foundational
  - **US1 (P1)** is MVP and should land first
  - **US2 / US3 / US4** can then be picked up in any order
  - Different stories can be worked on in parallel by different agents *if* they coordinate on `dashboard/bridge.py` (it's the only shared mutable file; see notes below)
- **Polish (Phase 7)** → depends on US1–US4 being complete

### User Story Dependencies

- **US1 (P1)**: depends only on Phase 2. Independent of US2/3/4.
- **US2 (P2)**: depends only on Phase 2. Adds three `kind`s and one new endpoint to the bridge.
- **US3 (P2)**: depends only on Phase 2. Adds `GET /status`; touches the status tile in `app.js` (`refreshStatus`). The US3 status-refresh-after-success hook reads slightly cleaner if US1 lands first, but US3 is functional on its own.
- **US4 (P3)**: depends only on Phase 2. Adds two `kind`s. The "refresh status after ingest" effect is cosmetic — works either way.

### Within Each User Story

- Bridge `kind`/endpoint additions land before the corresponding JS handler (the JS handler needs the route to exist to test against).
- HTML form/control additions can land before or after the JS handler — the foundational busy controller and shared utilities are already there.
- CSS work is independent of the JS/bridge work for that story (marked `[P]`).

### Parallel Opportunities

- **Phase 1 (Setup)**: T003 and T004 are different files and `[P]`. T001 and T002 are sequential by virtue of T002 writing inside the directory created by T001.
- **Phase 2 (Foundational)**: bridge work T005→T006→T007→T008→T009 is sequential (same file). Front-end shell T010/T011/T012 are three different files and run `[P]` with each other AND with the bridge sequence.
- **Phase 3 (US1)**: bridge (T013) → JS (T015) is a soft dependency. T016 CSS is `[P]`. T014 HTML is independent of all three and can land any time after T013.
- **Phase 4 (US2)**: T017+T018 both edit `bridge.py` — sequential. T019 HTML and T020 JS can land in either order. T021 CSS is `[P]`.
- **Phase 5 (US3)**: T022 (bridge) → T024 (JS using `/status`). T023 HTML independent of both. T025 CSS is `[P]`.
- **Phase 6 (US4)**: T026 (bridge) → T028 (JS). T027 HTML independent. T029 CSS is `[P]`.
- **Phase 7 (Polish)**: T030 and T031 are `[P]`. T032 and T033 are serialized last.

**Important nuance about parallelism**: This is a small feature whose code mostly lives in four files (`bridge.py`, `index.html`, `app.js`, `styles.css`). Two agents adding *different* keys to the same dictionary in `bridge.py` will conflict at merge time. If multiple agents work in parallel, each agent should own one file at a time within a story.

---

## Parallel Example: Phase 2 (Foundational)

```bash
# Start these three in parallel (different files, no inter-task deps):
Task: "Implement page layout shell in dashboard/index.html"       # T010
Task: "Implement base styles in dashboard/styles.css"              # T011
Task: "Implement shared JS utilities in dashboard/app.js"          # T012

# Meanwhile, build the bridge sequentially (one file):
Task: "T005 HTTP server skeleton in dashboard/bridge.py"
  → "T006 Static file serving in dashboard/bridge.py"
    → "T007 run_claude helper in dashboard/bridge.py"
      → "T008 Long-op mutex in dashboard/bridge.py"
        → "T009 POST /run dispatcher in dashboard/bridge.py"
```

## Parallel Example: User Story 1

```bash
# After T013 (bridge kind=query) lands, these can interleave freely:
Task: "Hero query block in dashboard/index.html"                   # T014
Task: "Query submit handler in dashboard/app.js"                   # T015
Task: "Style hero block in dashboard/styles.css"                   # T016  [P]
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 (Setup)
2. Phase 2 (Foundational) — **non-negotiable**; the bridge plumbing and shared front-end utilities live here
3. Phase 3 (US1) — query end-to-end
4. **Stop and validate** against the US1 section of `quickstart.md`
5. Demo: the dashboard answers KB questions without the terminal

### Incremental Delivery

After MVP, add stories in this order:

1. **US3 (status strip)** next — small, read-only, and instantly visible improvement. Makes the dashboard feel "alive" even without further imports.
2. **US2 (imports)** — closes the capture loop.
3. **US4 (ingest + lint)** — completes the round-trip and lets you forget the terminal entirely.

Each story is independently demoable; the foundational busy controller and status refresh tie them together visually.

### Single-Agent vs Multi-Agent

Single agent: walk through phases in numeric order. Total work is small enough that the whole feature fits one focused session.

Multi-agent (only if you want parallelism): split by file, not by story. One agent owns `bridge.py`, another owns the front-end (`html`/`css`/`js`). Stories then advance as a pipeline rather than as parallel branches.

---

## Notes

- `[P]` strictly means different files. Two tasks editing `dashboard/bridge.py` are never `[P]` with each other.
- Tests were not requested in the spec or by the user; no test tasks are generated. Acceptance is per-story manual validation against `quickstart.md`.
- The bridge has **no KB logic** beyond the static `PROMPT_TEMPLATES` table. If a story tempts logic into `bridge.py` beyond "look up kind → run claude → forward JSON", push it into the corresponding skill instead (or call it out in `research.md`).
- All paths are vault-root-relative for brevity but every `dashboard/...` reference resolves to `/Users/piero.sierra/Development/SecondBrain/dashboard/...`.
- Commit after each task (or after each phase). Stop at any checkpoint to validate independently.
