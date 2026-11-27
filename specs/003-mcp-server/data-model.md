# Data Model: Local MCP Server

No new datastore. The feature reads `wiki/`, writes new files under `raw/mcp/`, and stores one setting in `.env`.

## MCP token

| Field | Value |
|---|---|
| Storage | `MCP_TOKEN=<value>` in the vault-root `.env` |
| Format | `secrets.token_urlsafe(32)` |
| Created | On bridge start when absent |
| Rotated | `POST /mcp/rotate-token` from the dashboard; the old value stops working at once |

## Topic (read)

Parsed from `wiki/INDEX.md` table rows of the form `| [[slug]] | summary | YYYY-MM-DD |`.

| Field | Source |
|---|---|
| `slug` | wikilink target |
| `title` | first `# ` heading of `wiki/<slug>.md`, else the slug title-cased |
| `summary` | second table column |
| `updated` | third table column |

## Article (read)

| Field | Source |
|---|---|
| `slug` | file stem; must match `^[a-z0-9][a-z0-9-]*$` |
| `path` | `wiki/<slug>.md` |
| `title` | first `# ` heading |
| `text` | full file content |
| `sources` | trailing lines that start with `*Sources`, joined by newlines; empty if none |
| `uri` | `sb://wiki/<slug>` |

## Search hit (read)

From the bridge's `_search_vault`, filtered to `source == "wiki"`: `slug`, `title`, `snippet`, `hits`.

## Capture (write)

Path: `raw/mcp/YYYY-MM-DD_<slug>.md`, with `-2`, `-3`, and so on when taken. `<slug>` is `_slugify(title)[:60]`, or `capture` when empty.

```markdown
---
source: mcp:<client name>
imported: YYYY-MM-DD
title: <title>
conversation_url: <url>        # only when provided
---

<body, unchanged>
```

Validation: title 1 to 200 characters after trimming, single line; body non-empty and at most 256 KB UTF-8; `conversation_url` an `http` or `https` URL when present.
