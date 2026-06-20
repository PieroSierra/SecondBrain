# Second Brain — Wiki Edit

Apply targeted edits to one or more wiki articles based on a natural-language instruction, while preserving all structural invariants.

## Input

```
/second-brain-edit-wiki "<edit_prompt>" [<slug>]
```

- `edit_prompt` — free-text description of the edit to apply (required)
- `slug` — kebab-case wiki article filename **without** `.md` (optional)
  - If provided, the edit is applied only to that article
  - If omitted, the skill reads `wiki/INDEX.md` and infers which article(s) to edit from the prompt

## Execution Steps

### Step 1 — Parse and validate

1. Extract `edit_prompt` from the first quoted argument.
2. Extract optional `slug` from the second quoted argument.
3. If `slug` is provided, verify `wiki/<slug>.md` exists. If not, stop with:
   `✗ Article not found: wiki/<slug>.md`

### Step 2 — Identify target article(s)

**If `slug` is provided:** target is `wiki/<slug>.md` only.

**If `slug` is omitted:**
1. Read `wiki/INDEX.md` to get the full list of topics and their one-line summaries.
2. Scan the `edit_prompt` for article names, topic references, or contradiction descriptions (e.g. "company-overview", "revenue figure", "headcount data").
3. Match against INDEX entries. Select all articles that are plausibly relevant to the edit.
4. If no match is found, stop with:
   `✗ Could not determine which article to edit from: "<edit_prompt>"`
   `  Hint: re-run with an explicit slug, e.g. /second-brain-edit-wiki "<prompt>" company-overview`

### Step 3 — Apply edits

For each target article:

1. **Read** the current content of `wiki/<slug>.md`.
2. **Apply** the edit described in `edit_prompt`. Use good judgement to make the minimal, targeted change that satisfies the instruction.
3. **Preserve all invariants:**
   - **Summary paragraph** — must remain the first paragraph after the `# Heading`. Never remove, replace with headings, or reorder.
   - **Sources footer** — the `---\n*Sources: ...*` block at the end must remain byte-for-byte unchanged. Never modify, remove, or add to source references.
   - **Wikilinks** — all existing `[[wikilinks]]` must be preserved. You may add new ones; never remove without an explicit instruction to do so.
   - **Section structure** — preserve all `## Headings` and their order unless the edit explicitly requires restructuring.
4. **Write** the updated content back to `wiki/<slug>.md`.

### Step 4 — Confirm

Output one status line per article edited:

```
✓ wiki/company-overview.md — Updated revenue figure in summary: "$120M" → "$134M (2024 actual)"
✓ wiki/product-strategy.md — Corrected launch date from Q3 to Q4 in Key Milestones section
```

For errors use `✗` prefix with a brief reason.

## Invariants

- **Never** delete or rename wiki articles
- **Never** modify any file in `raw/`
- **Never** alter the Sources footer
- **Never** write to `raw/edits/` or any other staging location — edits are in-place only
- If the edit would require creating a new wiki article, note it in output but do not create it (suggest running `/second-brain-ingest` instead)
