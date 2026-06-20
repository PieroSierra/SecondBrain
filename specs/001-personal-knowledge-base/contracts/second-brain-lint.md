# Skill Contract: second-brain-lint

**Skill**: `/second-brain-lint`  
**Purpose**: Scan `wiki/` for quality issues — contradictions, unsupported claims, and content gaps — and save a report to `outputs/`.

## Invocation

```
/second-brain-lint
```

No arguments.

## Preconditions

- `wiki/INDEX.md` must exist with at least a few articles
- `raw/` must contain source files (needed to verify claim support)

## Behaviour

1. Read all articles in `wiki/`
2. For each article:
   a. Check for claims that contradict claims in other articles (same topic, different conclusion)
   b. Check whether key claims are traceable to a source listed in the article's `Sources:` footer
3. Scan raw source filenames and topics mentioned in raw/ that do not yet have wiki articles — flag as potential gaps
4. Generate lint report
5. Write report to `outputs/YYYY-MM-DD_lint.md`
6. Display summary to user

## Output File Format

```markdown
# Lint Report

*Date: YYYY-MM-DD | Articles scanned: N | Raw sources: M*

## Contradictions

- [[article-a]] and [[article-b]]: [description of conflict]

## Unsupported Claims

- [[article-name]]: "[quoted claim]" — no raw source found

## Suggested Content Gaps

- [Topic suggestion]: [rationale]

## Summary

N contradictions, M unsupported claims, P suggested gaps
```

## Outputs

| Output | Description |
|--------|-------------|
| `outputs/YYYY-MM-DD_lint.md` | Lint report with all findings |

## Invariants

- Never modifies `raw/` or `wiki/`
- Reports a clean state explicitly when no issues found (does not silently produce an empty report)

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| No wiki articles exist | Report "knowledge base is empty — run ingest first" |
| Single article in wiki | Run lint on that article only; note limited contradiction detection with one article |
