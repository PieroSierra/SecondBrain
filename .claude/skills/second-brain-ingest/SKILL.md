---
name: "second-brain-ingest"
description: "Read new or changed files in raw/, synthesise content into wiki/ topic articles, rebuild INDEX.md, and update the ingest manifest."
argument-hint: ""
user-invocable: true
---

# Second Brain — Ingest Raw Content

Read new or changed files in `raw/`, synthesise their content into `wiki/` topic articles, rebuild `wiki/INDEX.md`, and update the ingest manifest. This is the core pipeline of the knowledge base.

**Contract**: `specs/001-personal-knowledge-base/contracts/second-brain-ingest.md`

## Preconditions

- `CLAUDE.md` must exist at the vault root (run `/second-brain-setup` first if it doesn't)
- `raw/` directory must exist

## Manifest Format

The ingest state is tracked in `raw/.ingest-manifest.json`:

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

- Keys are vault-relative file paths (e.g., `raw/article.md`)
- `last_modified`: ISO 8601 timestamp of the file's last modification
- `ingested_at`: ISO 8601 timestamp of when it was last successfully ingested
- A file is **new** if it has no manifest entry
- A file is **stale** if `last_modified` is more recent than `ingested_at`
- A file is **current** if `last_modified` ≤ `ingested_at` — skip it

## Wiki Article Format

Every wiki article in `wiki/` must follow this exact structure:

```markdown
# Topic Name

[Summary paragraph — 2–4 sentences. ALWAYS the first paragraph. Required.]

## [Section headings as needed]

...synthesised content with [[related-topic]] wikilinks to other wiki articles...

---
*Sources: [[raw/source-1.md]] (2026-05-01), [[raw/source-2.md]] (2026-01-15)*
```

**Rules**:
- Filename: `wiki/<topic-name>.md` where topic-name is kebab-case (e.g., `engineering-leadership.md`)
- The summary paragraph is always present and always the first paragraph
- `[[wikilinks]]` use the exact filename of the target article WITHOUT the `.md` extension
- The sources footer lists EVERY raw file that contributed to this article, each followed by its `content_date` in parentheses if known (e.g., `(2026-05-01)`), or `(date unknown)` if not
- Never delete existing wiki articles — only create or update

## Execution Steps

### Step 1 — Read context and manifest

1. Read `CLAUDE.md` and extract the user's declared interests from the `### [INTERESTS]` block. These guide topic prioritisation during synthesis.
2. Read `raw/.ingest-manifest.json`. If the file does not exist, treat all `raw/` files as new (manifest is empty).

### Step 2 — Scan raw/ for files to process

Recursively list all files in `raw/` (including subdirectories `raw/craft/`, `raw/pdf/`, etc.).

**Supported file types**:
- `.md`, `.txt` — primary text content, always processed
- `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` — images, read visually and associated with their sibling markdown document (see Step 3)
- `.pdf` — auto-extracted to `raw/pdf/` before processing (see PDF Auto-Extraction below)
- All other extensions (`.docx`, `.zip`, etc.) — skip with warning: `[skip] <path> — unsupported format`
- `raw/.ingest-manifest.json` — always skip

**Grouping**: Before building the processing queue, group files by their parent directory. An image is associated with the nearest sibling `.md` file in the same directory. If no sibling `.md` exists, the image is skipped with: `[skip] <path> — no sibling markdown found`.

**PDF Auto-Extraction**: When a `.pdf` file is found anywhere in `raw/` (including subdirectories), treat it as an auto-extract source:
1. Check the manifest: if the PDF is current (not new or stale), skip silently — it was already extracted on a prior run
2. If new or stale: invoke the `/second-brain-import-pdf` extraction logic inline — read the PDF using the Read tool (paginating in ≤20-page batches), write the result to `raw/pdf/YYYY-MM-DD_<slug>.md` with the standard front-matter header
3. Add the resulting `.md` path to the processing queue for this run
4. Track the PDF path itself in the manifest (so it is not re-extracted unless modified)
5. If extraction fails (password-protected, unreadable, empty): log `[skip] <path> — PDF extraction failed: <reason>` and do not add to the manifest

**Queue logic**: For each `.md` or `.txt` file, check its last-modified timestamp against the manifest:
- New or stale → add to the **processing queue** (bring along any associated images from the same directory)
- Current → skip silently (also skip associated images)

If the processing queue is empty: output "Nothing to ingest — all files are up to date." and stop.

### Step 3 — Process each file in the queue

For each markdown file in the processing queue:

1. Read the markdown file content using the Read tool.
2. **Extract source date**: Read the front-matter of the raw file. If a `content_date` field is present and non-empty, record it as the **source date** for this file. If absent, note "date unknown". This date is used to stamp claims in wiki articles.
3. **Read associated images**: For each image file grouped with this markdown (same directory, supported image extension), read it using the Read tool. Claude's Read tool renders image content visually — extract the meaning, data, diagrams, and key information visible in each image. Treat this visual content as supplementary context that enriches the markdown text.
4. Synthesise a combined understanding of the document from: (a) the markdown text, and (b) the visual content extracted from any associated images.
5. Identify which topic(s) the combined content covers. Use the user's declared interests from `CLAUDE.md` to prioritise. If the content spans multiple topics, it may contribute to multiple wiki articles.
6. For each identified topic:
   a. Determine the wiki filename: kebab-case version of the topic name (e.g., "Engineering Leadership" → `engineering-leadership.md`)
   b. If `wiki/<topic>.md` exists: read it, then synthesise updated content that incorporates the new raw source while preserving all existing content. Do not discard previously synthesised content.
   c. If `wiki/<topic>.md` does not exist: synthesise a new article from scratch.
   d. **Date-stamp claims when synthesising**: When writing or updating wiki content, prefix sections or specific claims that come from a dated source with an "As of" marker — e.g., *"As of May 2026:"* or *"(Jan 2026)"*. Apply this especially to:
      - Metrics, statistics, and figures
      - Status assessments ("we are at parity", "Q1 was positive")
      - Decisions made, actions agreed, and open questions
      - Named personnel and role assignments
      When multiple sources cover the same topic at different dates, present the most recent information first and note older context below it, clearly dated.
   e. When images contributed meaningful content (diagrams, charts, screenshots with data), note this in the wiki article body — e.g., *"[Source includes architecture diagram showing X]"* — so future readers know visual evidence exists in the raw source.
   f. Ensure the article follows the required format: summary paragraph first, wikilinks to related topics where appropriate, sources footer listing all contributing raw files with their content dates.
   g. Write the updated or new wiki article to `wiki/<topic>.md`.
7. Report: `[processed] <raw-path> (content date: <date or unknown>) → <wiki-topic(s)>` (include image count if any were read)

### Step 4 — Rebuild INDEX.md

After all files in the queue are processed, read all files in `wiki/` (excluding `INDEX.md` itself). For each article, extract:
- The topic name from the `# Heading`
- The summary (first paragraph after the heading)
- Today's date as the "Last Updated" value

Write `wiki/INDEX.md`:

```markdown
# Knowledge Base Index

*Last updated: YYYY-MM-DD*

| Topic | Summary | Last Updated |
|-------|---------|--------------|
| [[topic-name]] | One-line summary (first sentence of summary paragraph) | YYYY-MM-DD |
```

Sort rows alphabetically by topic name. Every file in `wiki/` except `INDEX.md` must have an entry.

### Step 5 — Update the manifest

After ALL wiki writes complete successfully, write an updated `raw/.ingest-manifest.json`:
- Keep all existing manifest entries (tombstone pattern — don't remove deleted files)
- Add new entries for newly ingested files
- Update `ingested_at` for re-ingested stale files
- Use the current timestamp as `ingested_at`
- Write the manifest as a full overwrite (atomic)

### Step 6 — Report

Output a summary:

```
Ingest complete

Files processed: N (including I images read, P PDFs auto-extracted)
  Articles created: X
  Articles updated: Y

Files skipped (up to date): M
Files skipped (unsupported format): K

wiki/INDEX.md rebuilt with N topics
raw/.ingest-manifest.json updated
```

## Invariants

- NEVER modify, rename, or delete any file in `raw/`
- NEVER delete wiki articles — only create or update
- Write the manifest ONLY after all wiki updates complete successfully
- If any wiki write fails, do not update the manifest for that file (so it will be retried on next run)

## Error Handling

| Condition | Behaviour |
|-----------|-----------|
| Unsupported file type in `raw/` (e.g. `.docx`, `.zip`) | Skip with `[skip]` warning; do not add to manifest |
| PDF extraction fails (password-protected, unreadable, empty) | Log `[skip]` warning; do not add PDF to manifest; continue with other files |
| Image file with no sibling markdown | Skip with `[skip]` warning; do not add to manifest |
| Image file unreadable | Log warning and continue without it; process sibling markdown alone |
| File unreadable | Skip with error message; do not add to manifest |
| Wiki write fails mid-batch | Report partial progress; manifest reflects only successfully processed files |
| No new or changed files | Report "Nothing to ingest" and exit cleanly |
| `CLAUDE.md` missing | Report "Run /second-brain-setup first" and stop |
