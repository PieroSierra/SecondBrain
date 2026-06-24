---
name: "second-brain-import-craft"
description: "Import a specific document from your configured Craft space into raw/craft/. Always target a specific Folder/DocumentName — no bulk import."
argument-hint: "Folder/DocumentName (e.g. 'AI Partnerships/Meeting with Anthropic')"
user-invocable: true
---

# Second Brain — Craft Import

Import a specific document from your configured Craft space into `raw/craft/` as a markdown file. The space name and MCP URL are read from the `[CRAFT]` section of your vault config file (`CLAUDE.md` for Claude Code, `AGENTS.md` for Codex). Always requires a precise target — no bulk or space-wide import.

**Contract**: `specs/001-personal-knowledge-base/contracts/second-brain-craft-import.md`

## Invocation

```
/second-brain-craft-import "AI Partnerships/Meeting with Anthropic"
/second-brain-craft-import "AI Product/Thin Slices Q3"
/second-brain-craft-import "Board Meetings/June 2026 MBR"
```

The argument is always `Folder/DocumentName` — the folder name followed by the document name, separated by `/`.

If invoked without an argument, ask: "Which document do you want to import? Please specify as Folder/DocumentName (e.g. 'AI Partnerships/Meeting with Anthropic')."

## Craft Space Configuration

Read the following from the `[CRAFT]` section of your vault config file (`CLAUDE.md`, or `AGENTS.md` under Codex) before proceeding:
- **Space**: the `Space:` value (read-only)
- **MCP URL**: the `MCP URL:` value
- **Known folders**: the `Known folders:` value (used for error messages when a folder is not found)

## Craft MCP Tool Reference

The Craft MCP server exposes these tools. Use them in this order:

1. **`craft_get_spaces`** (or similar list tool) — list available spaces to confirm your configured space is accessible. If this tool is not available, skip and proceed directly to document retrieval.

2. **`craft_get_documents`** (or `craft_list_documents`) — list documents in a specific folder/space. Pass the folder name to narrow results. Use this when the document is not found, to show the user what's available.

3. **`craft_get_document`** (or `craft_read_document`) — retrieve the full content of a specific document by its title or ID. This returns markdown content.

**Important**: Craft MCP tool names may vary by server version. If the above names don't match, call the available tools with a descriptive intent (e.g., "list documents in AI Partnerships folder", "get document titled X"). The Craft MCP server at the configured URL is read-only — no write operations are available or needed.

**If Craft MCP is not connected**: The skill will surface: "Craft MCP is not available. Please ensure the Craft MCP server is connected in your agent environment (Claude Code MCP settings, or `~/.codex/config.toml` `[mcp_servers.craft]` under Codex). Check the `MCP URL` in your config file's `[CRAFT]` section."

## Execution

### Step 1 — Parse the target

Parse the argument as `<Folder>/<DocumentName>`. If no `/` separator is found, treat the entire argument as the document name and ask the user to confirm the folder.

### Step 2 — Connect to Craft via MCP

Use the Craft MCP tool at the URL configured in your config file's `[CRAFT]` → `MCP URL`. Navigate to the specified folder and locate the document by name.

If the document is not found:
- List available documents in that folder (if accessible)
- Report: "Document '[name]' not found in folder '[folder]'. Available documents: [list]"
- Write nothing

### Step 3 — Retrieve document content

Retrieve the full content of the document via Craft MCP. The content will be returned as markdown.

### Step 3b — Detect content date

After retrieving the document, look for signals about when the content was originally created or written. Check in order:
1. **Craft document metadata**: the MCP response may include `Created` or `Modified` timestamps — prefer `Created` as it reflects when the note was first written
2. **Document title**: dates embedded in the title (e.g. `Meeting with Anthropic [Thu 12 Jun 2026]`, `June 2026 MBR`, `Q1 2025 Review`)
3. **Document body**: explicit date markers near the top (`Date: 2026-06-12`, `PUBLISHED JAN 15 2026`, `Timestamp: May 2026`)

Convert any detected date to `YYYY-MM-DD` format (use the 1st of the month when only month/year is available). If no date is found, leave `content_date` blank — do not guess.

### Step 4 — Determine output filename

Generate the output filename:
```
raw/craft/YYYY-MM-DD_<folder-slug>_<document-slug>.md
```

Where:
- `YYYY-MM-DD` is today's date
- `<folder-slug>` is the folder name lowercased with spaces replaced by hyphens (e.g., `ai-partnerships`)
- `<document-slug>` is the document name lowercased with spaces replaced by hyphens, truncated at 50 chars

Example: `raw/craft/2026-06-16_ai-partnerships_meeting-with-anthropic.md`

### Step 5 — Check for existing file

Look for an existing file in `raw/craft/` that matches the same folder+document slug (ignoring the date prefix):

- If a matching file exists and content is **identical**: skip and report "Already up to date: [filename]"
- If a matching file exists and content **differs**: overwrite with updated content; report "Updated: [filename]"
- If no matching file exists: create new file; report "Created: [filename]"

### Step 6 — Write the file

Write the output file with a metadata header:

```markdown
---
source: Craft / <Space> / <Folder> / <DocumentName>
imported: YYYY-MM-DD
craft-folder: <Folder>
craft-document: <DocumentName>
content_date: YYYY-MM-DD        # omit this line entirely if no date was detected
---

<document content from Craft>
```

### Step 7 — Confirm

Report:
```
✓ Craft import complete

Source:        <Space> / <Folder> / <DocumentName>
Output:        raw/craft/<filename>
Status:        [Created | Updated | Already up to date]
Content date:  <YYYY-MM-DD if detected, otherwise "not detected">

Next step: run /second-brain-ingest to incorporate into the wiki
```

## Invariants

- Never writes to any location other than `raw/craft/`
- Never modifies, renames, or deletes existing files other than the target document's output file
- Never writes back to Craft — this is read-only

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| No argument provided | Ask user to specify Folder/DocumentName |
| Craft MCP unavailable | Report clear error; write nothing |
| Folder not found | Report error with list of known folders from the config file |
| Document not found | Report error with available documents in that folder |
| Content is empty | Report warning; write nothing |
