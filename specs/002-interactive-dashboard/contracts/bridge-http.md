# Bridge HTTP Contract — Interactive KB Dashboard

This is the **only** interface introduced by this feature. The dashboard page talks to the bridge over plain HTTP on `127.0.0.1`. The bridge has no KB logic of its own — it is a thin proxy that shells out to `claude -p` and serves static files.

## Common

- **Base URL**: `http://127.0.0.1:<port>` (default `4173`).
- **Binding**: `127.0.0.1` only. Never listens on `0.0.0.0`.
- **Auth**: every endpoint except `/`, `/static/*`, `/healthz` and `/busy` requires the per-start `X-Bridge-Token` header (see `dashboard/README.md`, Security model). The bridge refuses to start if any external network interface is requested.
- **Content type**: JSON for everything except static asset GETs, the file uploads and `/run-multipart`.
- **CORS**: not needed; the page is served by the bridge itself.
- **Process model**: foreground; Ctrl-C stops cleanly.

## Endpoints

### `GET /`
Serves `index.html`.

### `GET /static/<path>`
Serves `styles.css`, `app.js`, `attachments.js`, `lib/marked.min.js`. Path is constrained to the `dashboard/` directory; traversal attempts and any path segment starting with `.` (such as the `.uploads/` staging folder) return 404.

### `GET /attachments/<name>` → `200 image/*` | `404`

Serves one image attached to a thread question from `outputs/attachments/`. `<name>` must be a plain filename (`[A-Za-z0-9_.-]`, no `/`, not starting with `.`) ending in `.png`, `.jpg`, `.jpeg`, `.gif` or `.webp`. SVG is never served. Responses carry `X-Content-Type-Options: nosniff`. Requires the bridge token, so the page fetches the bytes and displays them through a `blob:` URL (the CSP allows `img-src 'self' data: blob:`).

### `GET /status` → `200 application/json`

Returns a `VaultStatus` object (see [`data-model.md`](../data-model.md)). Never invokes a skill. Must complete in under 1 second on a typical vault.

```json
{
  "wiki_article_count": 42,
  "raw_pending_count": 3,
  "raw_breakdown": {"paste": 1, "pdf": 0, "craft": 2},
  "outputs_query_count": 11,
  "outputs_lint_count": 4,
  "last_ingest_iso": "2026-06-15T18:22:01Z",
  "last_ingest_source": "manifest"
}
```

A missing or malformed `raw/.ingest-manifest.json` still returns 200 with `last_ingest_source: "mtime"` or `"none"`.

### `POST /run` → `200 application/json` | `409` | `504`

Body:
```json
{
  "kind": "query|md-add|craft-import|ingest|lint",
  "args": { /* kind-specific */ }
}
```

Per-kind `args` shape:

| kind          | args                                                  |
|---------------|-------------------------------------------------------|
| query         | `{"question": "..."}` *(non-empty)*                    |
| md-add        | `{"markdown": "...", "title_hint": "..."}` *(title_hint optional)* |
| craft-import  | `{"folder": "...", "document": "..."}`                |
| ingest        | `{}`                                                  |
| lint          | `{}`                                                  |

Server actions:

1. Acquire the long-operations mutex. If already held, return `409 {"error": "busy", "in_flight": {"kind": "...", "started_at": "..."}}`.
2. Snapshot `outputs/` and `raw/` listings (used to compute `output_file` / `created_files`).
3. Build the prompt from the static template table (see `research.md` §R9), with `args` substituted as separate argv entries — never interpolated into a shell string.
4. Run `claude -p "<prompt>" --output-format json --permission-mode bypassPermissions --add-dir <vault>` with `cwd = <vault>` and the per-kind timeout from `research.md` §R8. `subprocess.run(..., shell=False)`.
5. Re-snapshot `outputs/` and `raw/`; compute `output_file` (newest matching) and `created_files` (set difference).
6. Return the JSON from the CLI augmented with `kind`, `output_file`, `created_files`.

Timeout → `504 {"error": "timeout", "kind": "...", "after_seconds": N}`.
Spawn failure → `502 {"error": "spawn_failed", "detail": "..."}`.
Cancelled by the user (see `POST /cancel`) → `200 {"stopped": true, "kind": "..."}`. This is a benign outcome, not an error: the child was killed before completing, so no `output_file` is produced. For `ingest`, the manifest is deliberately left un-advanced so the next ingest re-synthesises the pending sources.

### `POST /cancel` → `200 application/json`

Cancels the single in-flight `/run` (there is only ever one, enforced by the long-operations mutex). Body is empty (`{}`).

Server actions:

1. Read the registered child-process handle for the in-flight run, if any.
2. If none is running, return `200 {"cancelled": false, "kind": null}` (idempotent — safe to call when idle).
3. Otherwise signal the child's **process group** (`SIGTERM`, escalating to `SIGKILL` after a short grace) so the agent CLI and any grandchildren are terminated, and return `200 {"cancelled": true, "kind": "<in-flight kind>"}`.

The killed `/run` request then returns its own `{"stopped": true}` response (above), which is what resets the dashboard UI. Auth is the same as `/run` (bridge token, or an allowlisted extension Origin).

### `POST /upload-pdf` → `200 application/json` | `409` | `504`

`multipart/form-data` with one part:

| field | required | description                       |
|-------|----------|-----------------------------------|
| `file`| yes      | The PDF the owner selected         |

Server actions:

1. Acquire the long-operations mutex (same as `/run`).
2. Validate `Content-Type: application/pdf` *or* filename ending `.pdf`. Otherwise return `400 {"error": "not_a_pdf"}`.
3. Write to `dashboard/.uploads/<uuid>.pdf` inside the vault.
4. Run `claude -p "/second-brain-pdf-import <abs-tempfile-path>" --output-format json --permission-mode bypassPermissions --add-dir <vault>`.
5. Delete the tempfile.
6. Return the same `SkillCallResult` envelope as `/run`, with `kind: "pdf-import"`.

If step 2 fails, no skill is run and no mutex is acquired.

### `POST /upload-file` → `200 application/json` | `400` | `409` | `413` | `504`

`multipart/form-data` with a `file` part and an optional `context` text part (up to 2000 characters). One file per request; the dashboard sends several files as consecutive requests. Office and CSV files are converted in-process. PDFs, images, `.txt` and `.md` go through their import paths. Images must pass a first-bytes check (PNG, JPEG, GIF, WebP). After a successful image import, the bridge copies the original next to the description note under the same stem (`raw/images/X.md` → `raw/images/X.png`), adds `original: X.png` to its front matter, and lists the copy in `created_files`.

### `POST /run-multipart` → `200 application/json` | `400` | `409` | `413` | `422` | `504`

A thread question or a pasted note with attached images. Token only; the Chrome extension cannot call it.

| field   | required | description |
|---------|----------|-------------|
| `kind`  | yes      | `thread-start`, `thread-reply` or `md-add` |
| `args`  | yes      | JSON object, same shape as `/run` for that kind. An empty `question` is replaced with a default question about the attached images. |
| `image` | 1-10     | Image files: PNG, JPEG, GIF or WebP by first bytes; 20 MB each, 64 MB in total |

Server actions:

1. Validate the kind, args and images. On failure return `400` without running anything.
2. Stage each image as `dashboard/.uploads/<uuid>.<ext>`.
3. `thread-start` / `thread-reply`: run the skill with repeatable `--image "<path>"` flags before the question (and `-i` / `-f` for Codex / OpenCode). After a successful, un-stopped run, copy the images to `outputs/attachments/<thread-stem>-<n>.<ext>` and add one `![image n](attachments/<file>)` line per image at the end of the latest user turn.
4. `md-add`: run `second-brain-describe-images`, which returns JSON describing each image. Write `raw/images/<date>_<slug>.md` with the pasted text verbatim followed by one section per image, and copy the originals next to it as `<stem>__<n>.<ext>`. A reply that is not valid JSON for every image returns `422 {"error": "bad_describe"}` and writes nothing.
5. Delete the staged files, whatever the outcome.

Copies into the vault are all-or-nothing: if any copy or the note/thread write fails, the copies already made are deleted. A stopped or failed run copies nothing. `DELETE /outputs/<thread>.md` also deletes that thread's attachments.

## Error envelope

All error responses use the same shape:
```json
{ "error": "<short_code>", "detail": "<human-readable string>" }
```

Codes used in this feature: `busy`, `timeout`, `spawn_failed`, `not_a_pdf`, `bad_request`, `bad_upload`, `bad_file`, `bad_describe`, `too_large`, `write_failed`, `not_found`.

## What the bridge is *not* allowed to do

- Parse the model's `result` text to make decisions. The only structured signals it reads from `claude` are `is_error`, `result` (passed through), and JSON shape. The exception is `second-brain-describe-images`, whose reply is a fenced JSON block the bridge validates and turns into a note.
- Touch `raw/`, `wiki/`, or `outputs/` other than as documented (status reads + before/after listings).
- Run any binary other than `claude`.
- Listen on any interface other than `127.0.0.1`.
- Persist any new state (no DB, no cache file, no log file that lives beyond a single process).
