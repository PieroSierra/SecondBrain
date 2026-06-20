# Bridge HTTP Contract — Interactive KB Dashboard

This is the **only** interface introduced by this feature. The dashboard page talks to the bridge over plain HTTP on `127.0.0.1`. The bridge has no KB logic of its own — it is a thin proxy that shells out to `claude -p` and serves static files.

## Common

- **Base URL**: `http://127.0.0.1:<port>` (default `4173`).
- **Binding**: `127.0.0.1` only. Never listens on `0.0.0.0`.
- **Auth**: none. Single-user local. The bridge refuses to start if any external network interface is requested.
- **Content type**: JSON for everything except static asset GETs and the PDF upload.
- **CORS**: not needed; the page is served by the bridge itself.
- **Process model**: foreground; Ctrl-C stops cleanly.

## Endpoints

### `GET /`
Serves `index.html`.

### `GET /static/<path>`
Serves `styles.css`, `app.js`, `lib/marked.min.js`. Path is constrained to the `dashboard/` directory; traversal attempts return 404.

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

## Error envelope

All error responses use the same shape:
```json
{ "error": "<short_code>", "detail": "<human-readable string>" }
```

Codes used in this feature: `busy`, `timeout`, `spawn_failed`, `not_a_pdf`, `bad_request`, `not_found`.

## What the bridge is *not* allowed to do

- Parse the model's `result` text to make decisions. The only structured signals it reads from `claude` are `is_error`, `result` (passed through), and JSON shape.
- Touch `raw/`, `wiki/`, or `outputs/` other than as documented (status reads + before/after listings).
- Run any binary other than `claude`.
- Listen on any interface other than `127.0.0.1`.
- Persist any new state (no DB, no cache file, no log file that lives beyond a single process).
