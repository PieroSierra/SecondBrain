# Research: Personal Knowledge Base

**Date**: 2026-06-16  
**Branch**: `001-personal-knowledge-base`

---

## Decision 1: Claude Code Skill Architecture

**Decision**: Each capability (setup, ingest, craft-import, pdf-import, query, lint) is implemented as a separate Claude Code skill — a directory under `.claude/skills/` containing a single `prompt.md` instruction file.

**Rationale**: This is the native Claude Code extensibility primitive. Skills are invoked as `/skill-name`, require no installation steps or runtime dependencies, and benefit from full Claude Code context (file access, MCP tools, conversation memory). The spec's "no external dependencies" constraint is satisfied by definition.

**Alternatives considered**:
- Python CLI scripts (`ingest.py` as described in Leo's blog): Requires Python runtime, pip dependencies, and process management. Rejected — adds friction and violates the no-dependencies constraint.
- Single monolithic skill: Would combine all operations into one prompt, making it harder to invoke selectively and difficult to maintain.

---

## Decision 2: Ingest Manifest Format

**Decision**: `raw/.ingest-manifest.json` — a flat JSON object keyed by file path (relative to vault root), with each value containing `last_modified` (ISO 8601 timestamp string) and `ingested_at` (ISO 8601 timestamp string).

```json
{
  "raw/article-on-leadership.md": {
    "last_modified": "2026-06-15T10:22:00Z",
    "ingested_at": "2026-06-16T09:00:00Z"
  }
}
```

**Rationale**: Flat JSON is the simplest structure the ingest skill can read and write without a database. Keying by relative path is stable across machines. Storing both `last_modified` and `ingested_at` lets the skill detect changed files (where `last_modified` > `ingested_at`) and skip unchanged ones.

**Alternatives considered**:
- Git-based change detection: Requires the vault to be a git repository and complicates the skill logic. Rejected — vault may not always have git.
- Full rebuild on every run: Simple but O(n) cost for every invocation; impractical as corpus grows to hundreds of files.

---

## Decision 3: Craft MCP Integration Pattern

**Decision**: The `second-brain-craft-import` skill uses the Craft MCP tool to list and retrieve notes. The default scope (space/folder) is stored in `CLAUDE.md` under a `[CRAFT]` configuration block. A note name passed at invocation overrides the default scope for single-note import.

**Rationale**: The Craft MCP is already configured in the user's environment (spec assumption). Reading scope from `CLAUDE.md` keeps configuration co-located with other vault settings and avoids separate config files. The two-mode design (bulk space import vs. single note) directly addresses the clarified requirement.

**Craft MCP capabilities (relevant)**:
- List spaces/documents
- Read document content (returns markdown)
- No write access required — import is read-only from Craft's perspective

**Alternatives considered**:
- Hard-coding the space name in the skill: Non-portable, breaks for other users or when the user renames their space.
- Separate `craft-config.json`: Redundant when `CLAUDE.md` already serves as vault config.

---

## Decision 4: PDF Extraction Approach

**Decision**: The `second-brain-pdf-import` skill uses Claude's native PDF reading capability (Read tool on `.pdf` files) to extract content, then writes the result as markdown to `raw/pdf/`.

**Rationale**: Claude Code's Read tool supports PDF files natively — it reads up to 20 pages per request and returns the content as text. This requires zero additional dependencies. For PDFs longer than 20 pages, the skill reads in page-range batches and concatenates the results.

**Alternatives considered**:
- `pdftotext` CLI or Python `pdfminer`: Require external installation. Rejected — violates no-dependencies constraint.
- Asking the user to convert manually: Defeats the purpose of the import skill.

**Known limitations**:
- Scanned PDFs (image-only, no text layer): Claude's PDF reader can still extract text from these via OCR-like processing, but quality varies. The skill will proceed and note in the output file header if OCR was likely used.
- Password-protected PDFs: The Read tool will fail; the skill surfaces a clear error (FR-020).

---

## Decision 5: Wiki Synthesis Strategy

**Decision**: The ingest skill reads each new/changed raw file, then for each significant topic it identifies, either creates a new `wiki/<topic-name>.md` or reads the existing file and appends/refines it. After all files are processed, `wiki/INDEX.md` is updated.

**Rationale**: Incremental, additive wiki maintenance — consistent with the spec's "wiki accumulates, nothing overwritten wholesale" requirement. Claude reads each raw source in the context of the existing wiki article for that topic, which ensures continuity.

**Wiki article structure** (standardised):
```markdown
# Topic Name

[One-paragraph summary — always the first paragraph]

## [Section headings as appropriate]

...body content with [[wikilinks]] to related topics...

---
*Sources: [[raw/source-1.md]], [[raw/source-2.md]]*
```

**Alternatives considered**:
- Full wiki rebuild from scratch on every ingest: Simpler but loses accumulated nuance and is slow.
- Vector search / embeddings: Requires external service. Rejected.

---

## Decision 6: CLAUDE.md Schema

**Decision**: `CLAUDE.md` serves dual purpose — Claude Code project instructions AND vault configuration. It contains:
1. A prose description of the vault system and folder rules (consumed as project context by Claude Code)
2. A `[CRAFT]` configuration block with `space` or `folder` path for the default Craft import scope
3. A `[INTERESTS]` block listing the user's declared topics, written by the setup skill

**Rationale**: Single file for all vault-level configuration minimises the number of files the user needs to be aware of. Claude Code automatically loads `CLAUDE.md` as context, so configuration declared here is available to every skill invocation without extra reading steps.

**CLAUDE.md structure**:
```markdown
# Second Brain — Vault Schema

[Prose description of the vault, folder rules, what the AI should and shouldn't do]

## Configuration

### Craft Import
- Space: [space name or path]

### Interests
- [Interest 1]
- [Interest 2]
...
```

---

## Decision 7: Output File Naming

**Decision**: Already resolved in clarification — `YYYY-MM-DD_query-<slug>.md` for queries and `YYYY-MM-DD_lint.md` for lint reports. Slug is generated by the query skill as a kebab-case summary of the first 5–6 words of the question.

**Slug generation rule**: lowercase, strip punctuation, replace spaces with hyphens, truncate at 40 chars. Example: "What are the main arguments for agent ownership?" → `what-are-the-main-arguments-for-agent`.

---

## Resolved Unknowns

| Unknown | Resolution |
|---------|------------|
| Runtime implementation approach | Claude Code skills (markdown instruction files only) |
| Ingest change detection | Manifest JSON at `raw/.ingest-manifest.json` |
| PDF extraction method | Claude's native Read tool PDF support |
| Craft MCP scoping | Space/folder from `CLAUDE.md [CRAFT]` block, or single note at invocation |
| Wiki article structure | Standardised format with summary paragraph + sources footer |
| CLAUDE.md dual-purpose | Project instructions + vault config in one file |
| Output naming | `YYYY-MM-DD_query-<slug>.md` / `YYYY-MM-DD_lint.md` |
