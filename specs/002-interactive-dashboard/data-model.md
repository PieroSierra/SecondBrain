# Data Model — Interactive KB Dashboard

The dashboard introduces **no new persisted data**. Everything below is either (a) computed on the fly from existing files, (b) ephemeral UI state, or (c) a pass-through of the skill's own output. The vault remains the single source of truth.

## Entities

### VaultStatus *(computed; never persisted)*

A read-only snapshot returned by `GET /status`. Derived entirely from the filesystem; no model call.

| Field                       | Type                          | Source                                                                                          |
|-----------------------------|-------------------------------|-------------------------------------------------------------------------------------------------|
| `wiki_article_count`        | integer                       | `len(glob "wiki/*.md") - 1` (exclude `INDEX.md`)                                                |
| `raw_pending_count`         | integer                       | Files in `raw/**` not in `raw/.ingest-manifest.json`'s processed set; fallback: all raw files   |
| `raw_breakdown`             | `{paste, pdf, craft}` integers| Files in `raw/*.md`, `raw/pdf/`, `raw/craft/` respectively                                      |
| `outputs_query_count`       | integer                       | `glob "outputs/*query*.md"`                                                                     |
| `outputs_lint_count`        | integer                       | `glob "outputs/*lint*.md"`                                                                      |
| `last_ingest_iso`           | ISO 8601 string \| null       | `raw/.ingest-manifest.json:last_ingest`; fallback: `mtime(wiki/INDEX.md)`; final fallback: null |
| `last_ingest_source`        | `"manifest"\|"mtime"\|"none"` | Indicates which of the three branches above was used                                            |

**Validation**: All counts ≥ 0. Missing/unreadable `raw/.ingest-manifest.json` does not error — the endpoint falls back as noted and returns a 200 with degraded fields.

### SkillCallResult *(pass-through + minimal bridge augmentation)*

The bridge's response to `POST /run` and `POST /upload-pdf`. It wraps the `claude -p --output-format json` output verbatim and adds a small number of bridge-known fields. The page parses this directly.

| Field            | Type                  | Origin              | Notes                                                                                       |
|------------------|-----------------------|---------------------|---------------------------------------------------------------------------------------------|
| `result`         | string (Markdown)     | `claude` JSON       | The final assistant message. The page renders this as Markdown.                             |
| `is_error`       | boolean               | `claude` JSON       | True if the CLI signalled an error                                                          |
| `session_id`     | string                | `claude` JSON       | Opaque to the dashboard; useful for debugging                                                |
| `cost_usd`       | number                | `claude` JSON       | Surfaced as a tiny "$0.012" footnote under the result if non-zero                            |
| `duration_ms`    | integer               | `claude` JSON       | Surfaced as elapsed time                                                                     |
| `num_turns`      | integer               | `claude` JSON       | Surfaced as a tiny footnote                                                                  |
| `kind`           | string                | bridge              | Echoes the `kind` sent in the request                                                        |
| `output_file`    | string \| null        | bridge (newest-file)| For `query` and `lint` only: path of the newest matching `outputs/...` file after the call  |
| `created_files`  | string[]              | bridge              | For `md-add`, `craft-import`, `pdf-import`: new file paths under `raw/` since call start    |

**Validation**: If `claude` exits non-zero, the bridge still returns 200 with `is_error: true` and a populated `result` containing the stderr text. Network/transport-level failures return HTTP 502 with `{error, detail}`.

### PendingOperation *(UI-only, not over the wire)*

Ephemeral client-side state held while a long operation is in flight.

| Field         | Type                                                        |
|---------------|-------------------------------------------------------------|
| `kind`        | `"query"\|"md-add"\|"craft-import"\|"pdf-import"\|"ingest"\|"lint"` |
| `started_at`  | ISO 8601                                                    |
| `elapsed_ms`  | integer (ticking)                                           |
| `timeout_ms`  | integer (from the per-kind table in research.md)            |

When `elapsed_ms >= timeout_ms` the UI shows a "still working… the bridge will return shortly" hint; cancellation is not in v1.

## Read/Write Map

| File or path                       | Read by   | Written by   |
|------------------------------------|-----------|--------------|
| `wiki/*.md`                        | bridge (status), skills | ingest skill                     |
| `wiki/INDEX.md`                    | bridge (status fallback), skills | ingest skill            |
| `raw/.ingest-manifest.json`        | bridge (status), ingest skill | ingest skill              |
| `raw/*.md`                         | skills    | md-add skill (paste import)                       |
| `raw/pdf/*`                        | skills    | pdf-import skill                                  |
| `raw/craft/*.md`                   | skills    | craft-import skill                                |
| `outputs/*query*.md`               | bridge (newest-file lookup) | query skill                     |
| `outputs/*lint*.md`                | bridge (newest-file lookup) | lint skill                      |
| `dashboard/.uploads/*`             | bridge    | bridge (temp PDF, deleted on completion)          |

**Invariant**: The bridge never writes inside `raw/`, `wiki/`, or `outputs/`. The only place it writes is its own `dashboard/.uploads/` tempdir for in-flight PDF uploads.
