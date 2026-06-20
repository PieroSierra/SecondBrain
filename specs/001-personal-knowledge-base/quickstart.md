# Quickstart: Personal Knowledge Base

## Prerequisites

- Claude Code CLI installed and running in this directory
- Craft MCP integration configured in your Claude Code environment (for Craft import)

## First-Time Setup

Run the setup skill once to initialise the vault:

```
/second-brain-setup
```

This will:
1. Ask you to declare your interests (topics you want the wiki to focus on)
2. Ask for your Craft space or folder name (for the Craft import skill)
3. Create `raw/`, `wiki/`, and `outputs/` directories
4. Generate `CLAUDE.md` with your configuration

## Your First Import

**Option A — Drop files manually**: Copy any markdown or text files into `raw/` directly.

**Option B — Import from Craft**:
```
/second-brain-craft-import               # imports all notes from your configured space
/second-brain-craft-import "Note title"  # imports a single specific note
```

**Option C — Import a PDF**:
```
/second-brain-pdf-import /path/to/document.pdf
```

## Run Ingest

After adding content to `raw/`, run ingest to build or update the wiki:

```
/second-brain-ingest
```

Check `wiki/INDEX.md` to see what topics have been synthesised.

## Query the Knowledge Base

Ask any question in natural language:

```
/second-brain-query "What are the main themes in my leadership notes?"
/second-brain-query "What do I know about [any topic]?"
```

Your answer is displayed immediately and also saved to `outputs/`.

## Lint (Monthly Maintenance)

Periodically check the knowledge base for quality issues:

```
/second-brain-lint
```

Review the report in `outputs/` for contradictions, unsupported claims, and suggested topics to add.

## Day-to-Day Workflow

```
[Add content] → /second-brain-ingest → /second-brain-query "your question"
```

That's it. The wiki accumulates automatically. The more you add, the more useful it becomes.

## Folder Reference

| Folder | Purpose | Who writes |
|--------|---------|------------|
| `raw/` | All source content — never organised by hand | You + import skills |
| `raw/craft/` | Notes imported from Craft | `second-brain-craft-import` skill |
| `raw/pdf/` | Text extracted from PDFs | `second-brain-pdf-import` skill |
| `wiki/` | AI-organised knowledge topics | `second-brain-ingest` skill only |
| `outputs/` | Query answers and lint reports | `second-brain-query` and `second-brain-lint` skills |
