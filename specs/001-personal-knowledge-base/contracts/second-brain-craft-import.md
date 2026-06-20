# Skill Contract: second-brain-craft-import

**Skill**: `/second-brain-craft-import`  
**Purpose**: Retrieve notes from Craft (via MCP) and write them as markdown files into `raw/craft/`.

## Invocation

```
/second-brain-craft-import                          # bulk: uses configured space/folder
/second-brain-craft-import "Note title or ID"       # single: imports one specific note
```

## Preconditions

- `CLAUDE.md` must exist with a `[CRAFT]` configuration block (for bulk mode)
- Craft MCP integration must be available in the Claude Code environment

## Behaviour (bulk mode)

1. Read Craft space/folder name from `CLAUDE.md [CRAFT]` block
2. Use Craft MCP to list all notes in that space/folder
3. For each note:
   a. Retrieve full content via Craft MCP
   b. Check `raw/craft/` for an existing file matching this note
   c. If existing file content differs: overwrite with updated content
   d. If no existing file: write new file
   e. Skip if content is unchanged
4. Report: notes retrieved, files created, files updated, files skipped

## Behaviour (single-note mode)

1. Use Craft MCP to retrieve the note matching the provided title or ID
2. Apply same create/update/skip logic as bulk mode
3. Report result for that single note

## File Naming Convention

`raw/craft/YYYY-MM-DD_<note-title-slug>.md`

Where:
- `YYYY-MM-DD` is today's date (import date, not note creation date)
- `<note-title-slug>` is the note title lowercased with spaces replaced by hyphens, truncated at 60 chars
- If the note has no title: `YYYY-MM-DD_untitled-<first-40-chars-of-content>.md`

## Outputs

| Output | Description |
|--------|-------------|
| `raw/craft/<filename>.md` | One file per imported note |

## Invariants

- Craft is read-only from this skill's perspective — no writes back to Craft
- No partial files: either the full note is written or nothing

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| Craft MCP unavailable | Report clear error; write no files |
| Note not found (single mode) | Report "note not found" with the identifier used |
| Note content empty | Write file with a note in the header: `*Note: imported content was empty*` |
| Title collision (two notes with same slug) | Append `-2`, `-3`, etc. to the filename |
