# Skill Contract: second-brain-query

**Skill**: `/second-brain-query`  
**Purpose**: Answer a natural-language question by synthesising content from `wiki/`, and save the response to `outputs/`.

## Invocation

```
/second-brain-query "What are the main arguments for agent ownership vs. strike teams?"
```

| Argument | Required | Description |
|----------|----------|-------------|
| `<question>` | Yes | The natural-language question to answer |

## Preconditions

- `wiki/INDEX.md` must exist (run `/second-brain-ingest` at least once)

## Behaviour

1. Read `wiki/INDEX.md` to identify relevant topics
2. Read the full content of relevant wiki articles
3. Synthesise a response grounded in wiki content
4. If no relevant wiki content exists: acknowledge the gap and suggest what raw content would fill it — do not fabricate an answer
5. Generate output filename: `YYYY-MM-DD_query-<slug>.md`
   - Slug: first 5–6 words of question, lowercased, kebab-case, max 40 chars
6. Write the query output file to `outputs/`
7. Display the answer to the user and note the output file path

## Output File Format

```markdown
# Query: [Original question]

*Date: YYYY-MM-DD*

[Synthesised answer]

---
*Sources: [[wiki/topic-1]], [[wiki/topic-2]]*
```

## Outputs

| Output | Description |
|--------|-------------|
| `outputs/YYYY-MM-DD_query-<slug>.md` | Query and full answer with source citations |

## Invariants

- Never modifies `raw/` or `wiki/`
- Always cites the wiki articles that informed the answer
- Never fabricates answers — acknowledges gaps explicitly

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| No wiki content exists | Report that the knowledge base is empty and prompt user to run ingest |
| No relevant wiki content for question | Answer that the topic is not yet in the knowledge base; suggest relevant raw content to add |
