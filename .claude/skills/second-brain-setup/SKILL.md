---
name: "second-brain-setup"
description: "Initialize or update the Second Brain vault — creates folder structure, declares interests, configures Craft import scope, and generates CLAUDE.md."
argument-hint: ""
user-invocable: true
---

# Second Brain — Vault Setup

Initialize or update this personal knowledge base vault. This skill creates the folder structure, declares your interests, and configures your Craft import scope. It is idempotent — safe to run again to update an existing vault.

> **Note on first run:** on a fresh clone this skill often runs before the agent
> has registered its skills (skills load at startup, based on the folder the agent
> opened in). That's fine — follow the steps below anyway. Because of this, the
> completion message in Step 6 **must** tell the user to restart their agent
> inside the vault folder; keep that instruction prominent and first.

**Contract**: `specs/001-personal-knowledge-base/contracts/second-brain-setup.md`

## What this skill does

1. Checks whether `CLAUDE.md` / `AGENTS.md` already exist and reads existing configuration as defaults
2. Asks which agent engine you use — Claude Code or OpenAI Codex — and records it in `.env`
3. Asks you to declare your primary interests (topics you want the wiki to focus on)
4. Asks for your Craft space or folder name
5. Creates `raw/`, `wiki/`, and `outputs/` directories if they do not exist
6. Writes or updates `CLAUDE.md` and `AGENTS.md` (identical vault schema, one per engine) with your interests and Craft config
7. Confirms completion and suggests next steps

## Execution

### Step 1 — Check existing configuration

Read `CLAUDE.md` (or `AGENTS.md`) if it exists. Look for the `[INTERESTS]` block and `[CRAFT]` block. If found, extract the current values to use as defaults in the questions below. Also read `.env` if present and note any existing `AGENT_ENGINE` value.

### Step 1b — Choose your agent engine

Present this prompt:

```
Which agent runs your Second Brain skills?
  1) Claude Code   (the `claude` CLI)   [default]
  2) OpenAI Codex  (the `codex` CLI)
Press Enter for Claude Code, or type 1 / 2:
```

If an `AGENT_ENGINE` value already exists in `.env`, show it as the default and let the user press Enter to keep it.

Write the choice to `.env` at the vault root as `AGENT_ENGINE=claude` or `AGENT_ENGINE=codex`:
- If `.env` does not exist, create it with that single line.
- If it exists and already has an `AGENT_ENGINE=` line, replace that line in place.
- If it exists without one, append the line.
- Never write more than one `AGENT_ENGINE` line.

The dashboard reads this to decide which CLI to invoke. Both engines run the same skills; the choice changes nothing else.

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
(This is the default scope for /second-brain-import-craft)
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

### Step 5 — Write CLAUDE.md and AGENTS.md

Write the vault schema to **both** `CLAUDE.md` (read by Claude Code) and `AGENTS.md` (read by Codex) at the vault root, with **identical bodies**, using exactly the structure below. Writing both means the skills work whichever engine is active and you can switch engines later without re-running setup. Preserve the `<!-- SPECKIT START -->` block verbatim if it already exists — insert it at the top, then write the vault schema below it.

```markdown
<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at specs/001-personal-knowledge-base/plan.md
<!-- SPECKIT END -->

# Second Brain — Vault Schema

This vault is a personal knowledge base managed by AI agent skills (Claude Code or OpenAI Codex).

## Folder Rules

- `raw/` — All source content. NEVER modified by AI skills. Append-only.
  - `raw/craft/` — Notes imported from Craft via the second-brain-import-craft skill
  - `raw/pdf/` — Text extracted from PDFs via the second-brain-import-pdf skill
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

Write this identical content to `AGENTS.md` as well (nothing engine-specific is embedded — both files just describe the vault).

**Codex + Craft only:** if the chosen engine is Codex *and* a Craft space was configured, Codex reads MCP servers from `~/.codex/config.toml`, not from the agent file. Show the user the block to add there:

```toml
[mcp_servers.craft]
command = "npx"
args = ["-y", "mcp-remote", "<MCP URL from the [CRAFT] section>"]
```

(`mcp-remote` bridges a remote/HTTP MCP into Codex's stdio MCP client — the common pattern; adjust if your Codex version supports remote MCP servers natively. Claude Code users configure the same Craft MCP through Claude Code's own MCP settings and need no `config.toml`.)

The dashboard auto-creates the `.agents/skills` link that lets Codex find these skills, so there is nothing to link by hand.

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
AGENTS.md: [created | updated]
Engine:    [Claude Code | Codex]   (saved to .env as AGENT_ENGINE)

Declared interests:
  - [interest-1]
  - [interest-2]
  ...

Craft import scope: [space-name | not configured]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  ONE MORE STEP — restart to finish
   Quit your agent and reopen it INSIDE this folder. Skills and config load
   at startup, so the setup you just ran only takes full effect after a
   restart. Until you do this, the /second-brain-* commands may not be loaded.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Then start using it — pick a path:

  Easiest (no terminal):
    • Download SecondBrain.app and point it at this folder:
      https://github.com/PieroSierra/SecondBrain/releases/latest
      The dashboard re-reads your config on every launch — no restarts to manage.
    • Or run ./run.sh from this folder to open the same dashboard in a browser.

  In your agent (after the restart above):
    • /second-brain-import-md   → paste a note into raw/
    • /second-brain-ingest      → build the wiki from raw/
    • /second-brain-query "…"   → ask a sourced question
    (Codex: swap the leading / for $.)
```

If the chosen engine is Codex, show the `$`-prefixed skill names in the
"In your agent" list instead of the `/` forms.
