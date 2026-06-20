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
2. Read PDF content using Claude's native Read tool (paginated in batches of ≤20 pages for large PDFs)
3. Concatenate all page extracts into a single markdown document
4. Determine output filename: `raw/pdf/YYYY-MM-DD_<title-slug>.md`
5. Check whether a file with that name already exists:
   - If yes and content differs: overwrite
   - If yes and content is identical: skip with "already imported" message
   - If no: create new file
6. Write markdown output with a metadata header
7. Report: source PDF, output file, pages extracted, any warnings

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
- No partial files: either the full extraction is written or nothing

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| File not found | Report "file not found" with path; write nothing |
| File is not a PDF | Report "unsupported format"; write nothing |
| PDF is password-protected | Report "PDF is password-protected, cannot extract"; write nothing |
| PDF is empty (0 pages or no extractable text) | Report warning; write nothing |
| Partial extraction (some pages fail) | Write successfully extracted pages with a header warning noting which pages were skipped |
