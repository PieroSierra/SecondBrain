# Second Brain — Add Markdown

Accepts pasted markdown content directly and writes it as a raw source file into `raw/`, ready for ingestion. Use this when you have content from any source (a web article, a copied document, notes, a transcript, an email) and want to get it into the knowledge base without exporting a file first.

## Invocation

```
/second-brain-md-add [markdown content]
/second-brain-md-add "Optional Title" [markdown content]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `[markdown content]` | Yes | The markdown text to store. Can be any length. |
| `"Optional Title"` | No | A quoted title at the start overrides the auto-derived slug. |

If invoked with no content at all, ask: "Please paste the markdown content you'd like to add."

## Execution

### Step 1 — Parse arguments

Check whether the first token of the argument is a quoted string (starts and ends with `"`). If so, treat it as a **title override** and the remainder as the content body.

Otherwise, treat the entire argument as the content body and derive the title automatically (Step 3).

### Step 2 — Validate content

If the content body is empty or only whitespace, report:
```
Error: No content provided. Please paste the markdown you'd like to add.
```
and stop. Write nothing.

### Step 3 — Derive title and slug

**If a title override was provided**: use it directly.

**Otherwise**, derive the title from the content:
1. Look for the first `# Heading` in the content — use that as the title if found.
2. If no heading, use the first non-empty sentence or line (truncated to 60 chars).
3. If content is very short or unstructured, use "Untitled Note".

Generate the slug from the title:
- Lowercase all words
- Replace spaces and special characters with hyphens
- Remove characters that are not alphanumeric or hyphens
- Truncate to 50 characters

### Step 3b — Detect content date

Scan the content body for signals about when the content was originally created or published. Look for:
- Explicit date stamps: `PUBLISHED JAN 15 2026`, `Date: 2026-01-15`, `Updated: May 2026`, `March 2026`, etc.
- Datelines in headings or sub-headings: `# Q1 2026 Review`, `## June 2026 MBR`
- Inline temporal markers near the top of the document: "Published", "Written", "Last updated"
- Document title containing a year/month/quarter

If a date is found, convert it to `YYYY-MM-DD` format (use the 1st of the month when only month/year is available, e.g. `May 2026` → `2026-05-01`). Set `content_date` to this value.

If no date is found, leave `content_date` blank — do not guess.

### Step 4 — Determine output path

```
raw/YYYY-MM-DD_<slug>.md
```

Where `YYYY-MM-DD` is today's date.

If a file at that exact path already exists, append a counter: `raw/YYYY-MM-DD_<slug>-2.md`, etc.

### Step 5 — Write the file

Write the file with a minimal front-matter header followed by the content as-is:

```markdown
---
source: pasted
imported: YYYY-MM-DD
title: <Derived or provided title>
content_date: YYYY-MM-DD        # omit this line entirely if no date was detected
---

<content body>
```

- `imported` is always today's date (when the file was added to the vault)
- `content_date` is the detected original creation/publication date of the content — omit the field if not found
- Do not modify or reformat the content body. Preserve all original markdown — headings, lists, tables, code blocks, links.

### Step 6 — Confirm

Report:
```
✓ Markdown added

Title:         <title>
Output:        raw/<filename>
Size:          ~N words
Content date:  <YYYY-MM-DD if detected, otherwise "not detected">

Next step: run /second-brain-ingest to incorporate into the wiki
```

## Invariants

- Only writes to `raw/` (never `raw/craft/`, `raw/pdf/`, `wiki/`, or `outputs/`)
- Never modifies existing files — always creates a new file
- Never reformats or summarises the content — stores it verbatim

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| No content provided | Ask user to paste content; write nothing |
| Content is only whitespace | Report error; write nothing |
| File collision (same slug same day) | Append `-2`, `-3`, etc. to filename |
