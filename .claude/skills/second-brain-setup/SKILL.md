---
name: "second-brain-setup"
description: "Initialize or update the Second Brain vault — creates folder structure, declares interests, configures Craft import scope, and generates CLAUDE.md."
argument-hint: ""
user-invocable: true
---

# Second Brain — Vault Setup

Initialize or update this personal knowledge base vault. This skill creates the folder structure, declares your interests, and configures your Craft import scope. It is idempotent — safe to run again to update an existing vault.

**Contract**: `specs/001-personal-knowledge-base/contracts/second-brain-setup.md`

## What this skill does

1. Checks whether `CLAUDE.md` already exists and reads existing configuration as defaults
2. Asks you to declare your primary interests (topics you want the wiki to focus on)
3. Asks for your Craft space or folder name (used by `/second-brain-craft-import`)
4. Creates `raw/`, `wiki/`, and `outputs/` directories if they do not exist
5. Writes or updates `CLAUDE.md` with the vault schema, your interests, and Craft config
6. Confirms completion and suggests next steps

## Execution

### Step 1 — Check existing configuration

Read `CLAUDE.md` if it exists. Look for the `[INTERESTS]` block and `[CRAFT]` block. If found, extract the current values to use as defaults in the questions below.

### Step 2 — Declare interests

Present this prompt to the user:

```
What topics should your knowledge base focus on?
Enter one interest per line. Press Enter on a blank line when done.
(These guide how the wiki synthesis prioritises and organises content.)
```

If existing interests were found, show them first:

```
Current interests:
- [existing-interest-1]
- [existing-interest-2]

Press Enter to keep these, or type new interests (one per line, blank line to finish):
```

Collect the user's response. If the user provides nothing and existing interests exist, keep the existing values. If the user provides nothing and no existing interests exist, warn: "No interests declared — wiki synthesis will not be topic-guided. You can re-run /second-brain-setup at any time to add interests."

### Step 3 — Configure Craft import scope

Present this prompt:

```
What is the name of your Craft space or folder to import from?
(This is the default scope for /second-brain-craft-import)
```

If an existing value was found, show it as the default:

```
Current Craft space: [existing-value]
Press Enter to keep this, or type a new value:
```

If the user provides nothing and no existing value exists, proceed with a blank value and note: "Craft space not configured. You can set it later by re-running /second-brain-setup."

### Step 4 — Create vault directories

Create the following directories if they do not already exist:
- `raw/`
- `raw/craft/`
- `raw/pdf/`
- `wiki/`
- `outputs/`

Report which directories were created vs already existed.

### Step 5 — Write CLAUDE.md

Write `CLAUDE.md` at the vault root using exactly this structure. Preserve the `<!-- SPECKIT START -->` block verbatim if it already exists — insert it at the top, then write the vault schema below it.

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
Space: <craft-space-value-from-step-3>

### [INTERESTS]
<interests-from-step-2-one-per-line-with-dash-prefix>
```

### Step 6 — Confirm completion

Output a summary:

```
✓ Vault setup complete

Directories:
  raw/        [created | already existed]
  raw/craft/  [created | already existed]
  raw/pdf/    [created | already existed]
  wiki/       [created | already existed]
  outputs/    [created | already existed]

CLAUDE.md: [created | updated]

Declared interests:
  - [interest-1]
  - [interest-2]
  ...

Craft import scope: [space-name | not configured]

Next steps:
  • Drop markdown files into raw/ and run /second-brain-ingest
  • Import Craft notes with /second-brain-craft-import
  • Import a PDF with /second-brain-pdf-import /path/to/file.pdf
```
