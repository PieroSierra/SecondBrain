# Skill Contract: second-brain-setup

**Skill**: `/second-brain-setup`  
**Purpose**: Interactive vault initialisation — creates folder structure, generates `CLAUDE.md` with user's declared interests, and configures Craft import scope.

## Invocation

```
/second-brain-setup
```

No arguments. Idempotent — safe to re-run to update an existing vault.

## Preconditions

- Must be run from the vault root directory
- No other preconditions (this skill bootstraps everything else)

## Interaction Flow

1. Check whether `CLAUDE.md` already exists
   - If yes: load existing interests and Craft config as defaults; prompt user to confirm or update
   - If no: start fresh
2. Ask the user to declare their primary interests (one per line, until blank line)
3. Ask for the Craft space or folder name to use as the default import scope
4. Create `raw/`, `wiki/`, `outputs/` directories if they do not exist
5. Write `CLAUDE.md` with the vault schema, folder rules, declared interests, and Craft config
6. Confirm completion and suggest next step: `/second-brain-ingest` or `/second-brain-craft-import`

## Outputs

| Output | Description |
|--------|-------------|
| `CLAUDE.md` | Created or updated with vault schema, interests, and Craft config |
| `raw/` | Created if absent |
| `wiki/` | Created if absent |
| `outputs/` | Created if absent |

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| User provides no interests | Warn and allow empty interests block; vault still created |
| Craft space name not provided | Leave Craft config blank; user can re-run or edit manually |

## CLAUDE.md Output Format

The setup skill generates a `CLAUDE.md` file with exactly this structure:

```markdown
<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at specs/001-personal-knowledge-base/plan.md
<!-- SPECKIT END -->

# Second Brain — Vault Schema

This vault is a personal knowledge base managed by Claude Code skills.

## Folder Rules

- `raw/` — All source content. NEVER modified by AI skills. Append-only.
  - `raw/craft/` — Notes imported from Craft via the second-brain-craft-import skill
  - `raw/pdf/` — Text extracted from PDFs via the second-brain-pdf-import skill
  - `raw/.ingest-manifest.json` — Machine-managed ingestion state. Do not edit.
- `wiki/` — AI-organised knowledge. Written ONLY by the second-brain-ingest skill.
  - One markdown file per topic, cross-linked with [[wikilinks]]
  - `wiki/INDEX.md` — Master topic index, rebuilt on every ingest
- `outputs/` — Query answers and lint reports. Written by query and lint skills.
  - `YYYY-MM-DD_query-<slug>.md` — Query output files
  - `YYYY-MM-DD_lint.md` — Lint report files

## AI Behaviour Rules

- Never modify files in `raw/`
- Never delete files in `wiki/`
- Every wiki article must start with a summary paragraph
- Every wiki article must end with a Sources footer listing contributing raw files
- Use [[topic-name]] wikilink syntax (without .md extension) for cross-references
- When synthesising wiki content, prioritise topics listed under [INTERESTS] below

## Configuration

### [CRAFT]
Space: <craft-space-or-folder-name>

### [INTERESTS]
- <interest-1>
- <interest-2>
- <interest-3>
```

**Notes**:
- The `<!-- SPECKIT START -->` block is preserved verbatim if it already exists in CLAUDE.md
- The `[CRAFT]` block uses `Space: ` as the key; value is the Craft space or folder name entered by the user
- The `[INTERESTS]` block lists one interest per line with `- ` prefix
- If re-running on an existing vault, the skill reads the current `[INTERESTS]` and `[CRAFT]` values as defaults and asks the user to confirm or update them
- The skill never removes or modifies the `<!-- SPECKIT START -->` block
