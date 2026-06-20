# Data Model: Personal Knowledge Base

**Date**: 2026-06-16  
**Branch**: `001-personal-knowledge-base`

---

## Entities

### Raw Source

A file in `raw/` representing a single unit of captured content. The source of truth for all wiki content.

| Attribute | Type | Notes |
|-----------|------|-------|
| `path` | string | Relative path from vault root (e.g., `raw/craft/2026-06-16_leadership.md`) |
| `source_type` | enum | `manual` \| `craft-import` \| `pdf-import` |
| `created_at` | ISO 8601 | File creation date (from filesystem or import metadata) |
| `last_modified` | ISO 8601 | Filesystem last-modified timestamp |
| `ingested_at` | ISO 8601 \| null | When last successfully ingested; null if never ingested |

**Invariants**:
- Files in `raw/` are never modified by the ingest or query skills — only by import skills or the user directly
- The manifest is the only authoritative record of `ingested_at`; do not infer it from wiki content

**State transitions**:
```
[file created in raw/] → uningested
[ingest runs, file is new or changed] → ingested (manifest updated)
[file modified after ingestion] → stale (last_modified > ingested_at)
```

---

### Ingest Manifest

Machine-managed state file at `raw/.ingest-manifest.json`. Never edited by the user.

```json
{
  "raw/article.md": {
    "last_modified": "2026-06-15T10:22:00Z",
    "ingested_at": "2026-06-16T09:00:00Z"
  },
  "raw/craft/2026-06-16_note.md": {
    "last_modified": "2026-06-16T08:00:00Z",
    "ingested_at": "2026-06-16T09:00:00Z"
  }
}
```

**Rules**:
- Written atomically (full overwrite) after every successful ingest run
- If a file is deleted from `raw/`, its entry remains in the manifest (tombstone pattern — no action required, ingest simply won't find the file)
- The manifest does not store wiki article content or topics; it only tracks ingestion state of raw files

---

### Wiki Article

A synthesised, AI-maintained markdown file in `wiki/<topic-name>.md`.

| Attribute | Type | Notes |
|-----------|------|-------|
| `topic_name` | string | Title of the article, used as filename (kebab-case) |
| `summary` | string | Always the first paragraph — a 2–4 sentence overview |
| `body` | markdown | Free-form synthesised content with `[[wikilink]]` cross-references |
| `sources` | list of paths | Raw source files that contributed to this article (in footer) |

**File format**:
```markdown
# Topic Name

[Summary paragraph — always present, always first]

## [Section headings as needed]

...content with [[related-topic]] wikilinks...

---
*Sources: [[raw/source-1.md]], [[raw/source-2.md]]*
```

**Invariants**:
- Only the ingest skill writes to `wiki/` — never import skills, query, or the user
- Each file covers exactly one topic
- `[[wikilinks]]` use the exact filename of the target article (without `.md` extension)
- The sources footer lists every raw file that contributed to the article

---

### Index

`wiki/INDEX.md` — a continuously maintained list of all wiki topics.

**Format**:
```markdown
# Knowledge Base Index

*Last updated: YYYY-MM-DD*

| Topic | Summary | Last Updated |
|-------|---------|--------------|
| [[topic-name]] | One-line summary | YYYY-MM-DD |
```

**Rules**:
- Rebuilt on every ingest run — not incrementally updated
- Sorted alphabetically by topic name
- Every file in `wiki/` (except `INDEX.md` itself) must have an entry

---

### Query Output

A file in `outputs/` produced by the query skill.

| Attribute | Type | Notes |
|-----------|------|-------|
| `filename` | string | `YYYY-MM-DD_query-<slug>.md` |
| `question` | string | The exact question asked by the user |
| `answer` | markdown | Synthesised response with `[[wikilink]]` source citations |
| `sources` | list | Wiki articles cited in the answer |
| `created_at` | ISO 8601 | Date/time of query |

**File format**:
```markdown
# Query: [Original question]

*Date: YYYY-MM-DD*

[Synthesised answer with [[wiki-article]] citations]

---
*Sources: [[wiki/topic-1]], [[wiki/topic-2]]*
```

---

### Lint Report

A file in `outputs/` produced by the lint skill.

| Attribute | Type | Notes |
|-----------|------|-------|
| `filename` | string | `YYYY-MM-DD_lint.md` |
| `contradictions` | list | Articles with conflicting claims |
| `unsupported_claims` | list | Statements in wiki not traceable to any raw source |
| `gaps` | list | Suggested topics to add based on raw content patterns |
| `created_at` | date | Date of lint run |

**File format**:
```markdown
# Lint Report

*Date: YYYY-MM-DD | Articles scanned: N | Raw sources: M*

## Contradictions

- [[article-a]] and [[article-b]]: [description of conflict]

## Unsupported Claims

- [[article-name]]: "[quoted claim]" — no raw source found

## Suggested Content Gaps

- [Topic suggestion]: [rationale based on raw content]

## Summary

[N contradictions, M unsupported claims, P suggested gaps]
```

---

## Relationships

```
Raw Source (1) ──────────────────── (N) Wiki Article
  (a raw source can inform many articles)

Wiki Article (N) ──[[wikilinks]]── (N) Wiki Article
  (articles cross-link to related articles)

Raw Source (N) ─── manifest entry ─── Ingest Manifest
  (manifest tracks one entry per raw file)

Wiki Article (N) ─── cited in ─── (1) Query Output
Wiki Article (N) ─── scanned by ─── (1) Lint Report
```
