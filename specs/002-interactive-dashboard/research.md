# Research — Interactive KB Dashboard

This document resolves the unknowns surfaced in the spec and plan. Each decision lists its rationale and what was considered and rejected.

## R1 — How does the browser run the skills, given the user wants no client-side server?

**Decision**: A minimal Python 3 stdlib HTTP bridge on `127.0.0.1`. It does two things only: serve the static dashboard, and exec `claude -p "<prompt>" --output-format json` on behalf of the page, returning the JSON verbatim.

**Rationale**: Browsers cannot spawn subprocesses. *Something* local must shell out to `claude`. "No client-side server" is interpreted as the *spirit* of the directive: no framework, no separate backend service, no business logic outside the skills. A ~100-line Python stdlib script that's exclusively a "claude-CLI proxy" honours that — every action still happens via `claude -p` and the bridge has no KB knowledge of its own. The user's exact line — *"calling 'claude -p "<invoke your skill>" --output-format json" and displaying the results"* — is implementable as written; only the *transport* (subprocess vs HTTP-to-bridge) differs because the page itself can't do `subprocess.exec`.

**Alternatives considered**:
- **Pure static `file://` page** — impossible: no subprocess access, no fetch to local CLI.
- **TUI/Terminal app** instead of browser — sidesteps the bridge, but the spec explicitly calls for a browser-opened web page (FR-014, FR-016).
- **Electron / Tauri** — `node`/`rust` build chains, packaging, signing; vastly more complexity than the directive permits.
- **Flask/FastAPI backend** — would tempt re-implementing parts of the KB logic server-side, which is exactly what the user wants to avoid. Also requires `pip install`.
- **Node `http` server** — fine technically, but Python is universally present on macOS and matches the project's "no extra runtimes" posture.

## R2 — How should Markdown be rendered in the page?

**Decision**: Vendor `marked.min.js` (single file, ~30 KB) inside `dashboard/lib/`. No CDN, no build step.

**Rationale**: The page must render skill output as formatted Markdown (FR-002, SC-005). `marked` is the smallest mature option and is one file. Vendoring keeps the dashboard fully offline-clean and removes any network dependency on launch.

**Alternatives considered**:
- **Server-side rendering in the bridge** — bridge would need to grow markdown logic, against the spirit of the directive.
- **CDN-loaded marked** — adds a network dependency at page load.
- **Hand-rolled minimal Markdown** — risk of poor fidelity on tables, code blocks, and `[[wikilinks]]`.

## R3 — How does the page learn the path to the saved `outputs/...` file (for FR-004)?

**Decision**: After running a skill that writes to `outputs/`, the bridge lists `outputs/` and returns the newest matching file (`*query*.md` for query, `*lint*.md` for lint) as `output_file` on the response. The skills are not modified.

**Rationale**: Existing skills name output files with a date stamp and a slug (`YYYY-MM-DD_query-<slug>.md`, `YYYY-MM-DD_lint.md`). Snapshot the dir listing before the call, snapshot after, take the new entry. Zero changes to skills, zero parsing of model output.

**Alternatives considered**:
- **Parse the model's final message for a path** — fragile; the model could say "Saved to outputs/..." or not say it at all.
- **Patch each skill to emit a structured "saved_at" field** — requires editing all six skills and re-testing, just to support this UI.

## R4 — How are PDF uploads handled given the browser file picker?

**Decision**: The page POSTs the file as `multipart/form-data` to `/upload-pdf`. The bridge writes it to a tempfile inside the vault (e.g. `dashboard/.uploads/<uuid>.pdf`), then runs `claude -p "/second-brain-pdf-import <abs-path>" --output-format json --permission-mode bypassPermissions`. The skill itself copies the PDF into `raw/pdf/`. The bridge deletes the tempfile after the call returns.

**Rationale**: Honours FR-006 (standard OS file picker, file from anywhere on disk). The skill remains the only thing that touches `raw/pdf/`.

**Alternatives considered**:
- **`<input type="file" webkitdirectory>`** — irrelevant; PDF import is per-file.
- **Native file dialog via OS bridge (AppleScript / `osascript`)** — adds complexity; the browser's `<input type="file">` already shows the OS picker.
- **Pass the original disk path to the skill** — the browser deliberately hides the real path of an uploaded file. Tempfile is the standard workaround.

## R5 — How are concurrent long operations serialized (FR-013)?

**Decision**: The bridge holds a single in-process `threading.Lock` over long operations. While the lock is held, additional `POST /run` and `POST /upload-pdf` requests respond with HTTP 409 `{error: "busy", in_flight: {kind, started_at}}`. The page disables non-status controls while a request is in flight and shows what's running. `GET /status` is never locked.

**Rationale**: Matches spec exactly. Trivial to implement. Status remains instant (SC-007).

**Alternatives considered**:
- **Per-skill queues** — over-engineered for one user with one tab.
- **Allowing parallel ops** — risks two ingests racing on `wiki/` and the manifest.

## R6 — How is the status strip computed without invoking a skill?

**Decision**: `GET /status` reads:

- **Wiki article count** — `len(list(wiki/*.md))` minus `INDEX.md`.
- **Raw items awaiting ingest** — preferred path: read `raw/.ingest-manifest.json` and count files in `raw/**` whose path is not in the processed set. Fallback: count all `raw/**/*.md` and `raw/**/*.pdf`.
- **Last ingest time** — `raw/.ingest-manifest.json` `last_ingest` field. Fallback: `mtime` of `wiki/INDEX.md`. Final fallback: `null` (UI shows "—").
- **Optional** — breakdown counts for `raw/craft/`, `raw/pdf/`, `raw/` top-level paste files; counts of `outputs/*query*.md` and `outputs/*lint*.md`.

All values are read in a single pass; the endpoint should return in well under 100 ms.

**Rationale**: Honours FR-018/019/020/021. Filesystem only, no model call, no new datastore.

**Alternatives considered**:
- **Persist a cached status JSON** — violates FR-020 (status must be derived, not stored).
- **Watch the filesystem with `fsnotify`** — adds a dependency; the status reloads on demand, which is fast enough.

## R7 — What `claude -p` invocation flags are required?

**Decision**: `claude -p "<prompt>" --output-format json --permission-mode bypassPermissions --add-dir <abs-vault-path>`. The CWD is set to the vault root.

**Rationale**:
- `--output-format json` produces a single JSON object with `result`, `is_error`, `cost_usd`, etc., which the bridge can return verbatim.
- `--permission-mode bypassPermissions` is needed for fully non-interactive skill execution that writes to `raw/`, `wiki/`, and `outputs/`. The bridge is local-only (`127.0.0.1`) and the user is the operator, so this is the appropriate trust mode.
- `--add-dir <vault>` ensures the skill has tool access to the entire vault even when CWD-resolution doesn't cover it.

**Alternatives considered**:
- **`--output-format stream-json`** — useful for surfacing progress (FR-012); reserved for a follow-up enhancement to avoid scope creep in v1. The page can show a spinner with an elapsed-time counter from the single-shot call.
- **No `--permission-mode`** — the prompt would block awaiting permission acks, which there's no human to give over HTTP.

## R8 — Subprocess construction (security)

**Decision**: Always invoke `subprocess.run([...], shell=False, check=False, capture_output=True, text=True, cwd=VAULT_ROOT, timeout=...)` with list-argv. The skill prompt is a single argv entry; any paths are separate entries. The bridge only ever execs `claude` — no other binary, no shell.

**Rationale**: Eliminates command injection. Even though the dashboard is single-user/local, paste-Markdown content and Craft folder names are still user input; passing them as separate argv entries means they cannot break out into shell syntax.

**Per-kind timeouts** (used both as `subprocess.run(timeout=…)` and to size the UI's spinner):

| kind          | timeout |
|---------------|--------:|
| query         |   90 s  |
| md-add        |   30 s  |
| craft-import  |   60 s  |
| pdf-import    |  120 s  |
| ingest        |  180 s  |
| lint          |  120 s  |

A timeout surfaces as HTTP 504 in the JSON envelope `{error: "timeout", kind, after_seconds}`.

## R9 — Skill-prompt template table (the only KB-aware lookup in the bridge)

The bridge maps `kind` → a one-line prompt string. This is the only KB-aware code in the bridge.

| kind          | prompt template                                            | notes                                  |
|---------------|------------------------------------------------------------|----------------------------------------|
| query         | `/second-brain-query "{question}"`                         | `question` from body                   |
| md-add        | `/second-brain-md-add` then page sends body as stdin… *or* `/second-brain-md-add` with the body inlined into the prompt | TBD in implementation; whichever the skill supports today |
| craft-import  | `/second-brain-craft-import "{folder}/{document}"`         | folder + document from body            |
| pdf-import    | `/second-brain-pdf-import "{abs_tempfile_path}"`           | path injected by bridge                |
| ingest        | `/second-brain-ingest`                                     | no args                                |
| lint          | `/second-brain-lint`                                       | no args                                |

The exact md-add invocation will be confirmed against the skill's current invocation contract during implementation. Anything that doesn't match will be addressed by an inline prompt body, not by changing the skill.

## R10 — Port, browser open, and lifecycle

**Decision**: Bridge defaults to `127.0.0.1:4173` with `--port` override. On start it prints the URL; on macOS it shells `open <url>` so the page launches automatically. Ctrl-C stops the bridge cleanly. No daemon, no LaunchAgent — owner starts/stops it like any other foreground command.

**Rationale**: Matches the existing project's "skills + foreground commands" posture. No system-level install.

---

All NEEDS CLARIFICATION items from the spec are resolved here. Ready for Phase 1.
