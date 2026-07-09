# Skill Contract: second-brain-pdf-import

**Skill**: `/second-brain-pdf-import`  
**Purpose**: Extract text from a PDF file, convert it to markdown, and write the result into `raw/pdf/`.

## Invocation

```
/second-brain-pdf-import /path/to/document.pdf
/second-brain-pdf-import /path/to/document.pdf "Optional custom title"
```

| Argument | Required | Description |
|----------|----------|-------------|
| `<path>` | Yes | Absolute or vault-relative path to the PDF file |
| `<title>` | No | Override for the output filename slug; defaults to PDF filename stem |

## Preconditions

- The PDF file must exist and be readable
- `raw/pdf/` directory must exist (created by `/second-brain-setup`)

## Behaviour

1. Verify the PDF file exists and is not empty
2. Determine output filename: `raw/pdf/YYYY-MM-DD_<title-slug>.md`
3. Extract **incrementally** to avoid stalling the model's response stream on large PDFs:
   - Write the output file scaffold (metadata header, title, and a trailing `<!-- sb:incomplete -->` marker), **overwriting** any existing file at that path. A re-import therefore restarts from page 1 and never appends to a stale file.
   - Read the PDF with Claude's native Read tool in **≤10-page batches** and append each batch to the file as it is transcribed (by replacing the marker), so partial progress persists to disk.
   - When all pages are done, remove the marker. The completed file never contains `<!-- sb:incomplete -->`.
4. If a file already existed at that path, the status is "updated"; otherwise "created". (The byte-exact "already imported" skip is not offered — incremental writing does not buffer the whole document to compare.)
5. Report: source PDF, output file, pages extracted, any warnings

## Output File Format

```markdown
---
source: /path/to/original.pdf
imported: YYYY-MM-DD
pages: N
---

# Document Title (or filename if no title detected)

[Extracted and cleaned markdown content]
```

## File Naming Convention

`raw/pdf/YYYY-MM-DD_<title-slug>.md`

Where `<title-slug>` is the custom title or PDF filename stem, lowercased, spaces replaced by hyphens, truncated at 60 chars.

## Outputs

| Output | Description |
|--------|-------------|
| `raw/pdf/<filename>.md` | Extracted markdown content with metadata header |

## Invariants

- Source PDF is never modified
- Only writes to `raw/pdf/`
- Extraction is incremental and progressive: the output is written batch-by-batch. An interrupted run leaves the pages extracted so far plus a `<!-- sb:incomplete -->` marker; `/second-brain-ingest` skips any file bearing that marker (partial content never reaches the wiki), and re-running the import overwrites the file from scratch (never appends). A successful run always removes the marker.

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| File not found | Report "file not found" with path; write nothing |
| File is not a PDF | Report "unsupported format"; write nothing |
| PDF is password-protected | Report "PDF is password-protected, cannot extract"; write nothing |
| PDF is empty (0 pages or no extractable text) | Report warning; write nothing |
| Partial extraction (some pages fail) | Write successfully extracted pages with a header warning noting which pages were skipped |
