---
name: "second-brain-lint"
description: "Scan wiki/ for quality issues — contradictions, unsupported claims, and content gaps — and save a structured report to outputs/."
argument-hint: ""
user-invocable: true
---

# Second Brain — Lint

Scan `wiki/` for quality issues — contradictions, unsupported claims, and content gaps — and save a structured report to `outputs/`.

**Contract**: `specs/001-personal-knowledge-base/contracts/second-brain-lint.md`

## Invocation

```
/second-brain-lint
```

No arguments. Takes no input — operates on the current state of `wiki/` and `raw/`.

## Execution

### Step 1 — Check preconditions

Check that `wiki/INDEX.md` exists. If it does not:
```
Knowledge base is empty — wiki/INDEX.md does not exist.
Run /second-brain-ingest first to populate the wiki.
```
Stop. Write nothing.

### Step 2 — Read all wiki articles

Read `wiki/INDEX.md` to get the full list of articles.

Read the full content of every wiki article listed (all `wiki/<topic>.md` files except `INDEX.md`). For each article, extract:
- All factual claims (sentences asserting something is true, numbered/bulleted facts, quoted figures and statistics)
- The sources footer (lines under `*Sources: ...*`) — the list of raw files credited as contributing to this article
- Any `[[wikilinks]]` to other articles (cross-references)

### Step 3 — Check for contradictions

Compare claims across articles. Look for cases where:
- Two articles make incompatible assertions about the same subject (e.g. one says a metric is X, another says it is Y)
- An article's claim directly conflicts with a claim in a cross-referenced article

For each contradiction found, record:
- The two articles involved
- The conflicting claims (quoted briefly)

If no contradictions are found, record: *No contradictions detected.*

### Step 4 — Check for unsupported claims

For each article, check whether key claims are traceable to the sources listed in the article's `Sources:` footer:

- A claim is **supported** if: the article's sources footer lists at least one raw file, AND the claim is the kind of assertion that would plausibly come from those sources
- A claim is **potentially unsupported** if: the sources footer is empty, missing, or the claim asserts a specific fact (figure, decision, date, name) that cannot be traced to any listed source

Flag claims that appear to be specific assertions (statistics, named decisions, quoted figures) where the sources footer is empty or suspiciously thin.

Do NOT flag general synthesis or summary sentences — only flag specific factual assertions that require a source.

If no unsupported claims are found, record: *No unsupported claims detected.*

### Step 5 — Identify content gaps

Scan `raw/` for content that has not yet generated a wiki article:

1. List all files in `raw/` (all subdirectories)
2. For each raw file, check whether its content appears to be represented in any existing wiki article (by checking the article sources footers)
3. Additionally, scan the topics and subject matter of all raw files — identify subjects mentioned in raw content that do not have a dedicated wiki article and are relevant to the user's declared interests in `CLAUDE.md`

For each gap identified, suggest a topic name and rationale.

If no gaps are found, record: *No content gaps detected.*

### Step 6 — Generate output filename

Output path: `outputs/YYYY-MM-DD_lint.md`

Where `YYYY-MM-DD` is today's date.

If a lint report already exists for today (same filename), append a counter: `outputs/YYYY-MM-DD_lint-2.md`, etc.

### Step 7 — Write the lint report

Check that `outputs/` directory exists. If not, create it.

Write the report with this exact format:

```markdown
# Lint Report

<!-- sb:delint total="T" open="T" applied="0" skipped="0" -->

*Date: YYYY-MM-DD | Articles scanned: N | Raw sources: M*

## Contradictions

- [[article-a]] and [[article-b]]: [description of conflict]
<!-- sb:finding id="f1" type="edit-wiki" articles="article-a,article-b" status="open" -->
<!-- sb:proposal id="f1" confidence="high"
instruction="[Exact natural-language instruction to pass to /second-brain-edit-wiki to resolve this contradiction — e.g. 'Date-stamp the 26% figure in skyscanner-company as internal/undated and the 23% figure in ai-storytelling as Apple Barbican fireside Jun 2026']"
-->

*or: No contradictions detected.*

## Unsupported Claims

- [[article-name]]: "[quoted claim]" — no raw source found
<!-- sb:finding id="f2" type="edit-wiki" articles="article-name" status="open" -->
<!-- sb:proposal id="f2" confidence="high"
instruction="[Exact instruction to fix the sourcing issue — e.g. 'Add raw/images/2026-06-19_software-pulse-delivery-steering.md to the Sources footer of ai-caio-decisions']"
-->

*or: No unsupported claims detected.*

## Suggested Content Gaps

- **[Topic name]**: [rationale — what raw content exists and why a wiki article would be valuable]
<!-- sb:finding id="f3" type="run-ingest" articles="" status="open" -->
<!-- sb:proposal id="f3" confidence="high"
instruction="[The raw file path(s) to ingest — e.g. 'raw/web/2026-07-02_thin-layer-op-model.md']"
-->

*or: No content gaps detected.*

## Summary

N contradictions, M unsupported claims, P suggested gaps.
[One sentence overall assessment — e.g. "Knowledge base is in good shape." or "Several gaps worth addressing."]
```

**Tag generation rules:**

- Assign sequential ids `f1`, `f2`, … across all findings in document order
- `type` values:
  - `edit-wiki` — any finding that requires changing wiki article text (contradictions, unsupported claims, labelling fixes)
  - `run-ingest` — a raw file exists but is not yet in the wiki (content gaps where a raw file can be cited)
  - `acknowledge` — housekeeping notes with no required action (duplicate files, already-reconciled items)
- `confidence` values:
  - `high` — the fix is unambiguous and the LLM is confident (e.g. add a missing source footer entry, label a figure with its source)
  - `low` — the fix requires human judgement or knowledge the LLM cannot verify (e.g. reconciling two figures that may reflect genuinely different data)
- The `instruction` field must be a single line of plain text (no newlines inside the value) that can be passed verbatim to `/second-brain-edit-wiki` or used to identify which file to ingest
- For `acknowledge` findings, omit the `sb:proposal` block entirely — they auto-resolve
- The `sb:delint` summary comment at the top: `total` = total findings count, `open` = same count at write time, `applied` = 0, `skipped` = 0
- Tags are HTML comments — invisible in rendered markdown, parseable by the delint skill and dashboard

### Step 8 — Display and confirm

Display the full report to the user in the conversation.

Then report:
```
Lint report saved to: outputs/YYYY-MM-DD_lint.md
Articles scanned: N | Raw sources checked: M
Findings: X contradictions, Y unsupported claims, Z gaps
```

## Invariants

- Never modifies any file in `raw/` or `wiki/`
- Always writes a report, even when the knowledge base is clean — clean state is reported explicitly, never silently
- Output files use date-based names — do not overwrite prior lint reports

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| `wiki/INDEX.md` missing | Report "knowledge base is empty — run ingest first"; stop |
| Only one article in wiki | Run lint on it; note that contradiction detection requires at least two articles |
| `raw/` directory missing or empty | Skip gap analysis; note in report |
| `outputs/` directory missing | Create it, then write the report |
| Wiki article listed in INDEX but file missing | Note the missing file in the report under Unsupported Claims; continue |
