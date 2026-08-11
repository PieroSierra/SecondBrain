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
5. Generate a descriptive thread title: 4–8 words, maximum 60 characters, with request framing omitted
6. Generate output filename: `YYYY-MM-DD_thread-<slug>.md`
   - Slug: generated title, lowercased, kebab-case, max 60 chars
   - If the path exists, add a numeric suffix rather than overwriting it
7. Write the thread output file to `outputs/`
8. Display the answer to the user and note the output file path

## Output File Format

```markdown
<!-- sb:thread id="YYYY-MM-DD_thread-<slug>" created="YYYY-MM-DD" -->

# [Generated thread title]

<!-- sb:turn role="user" ts="YYYY-MM-DD" -->
## You

[Original question]

<!-- sb:turn role="assistant" ts="YYYY-MM-DD" -->
## Second Brain

[Synthesised answer]

*Sources: [[wiki/topic-1]], [[wiki/topic-2]]*
```

## Outputs

| Output | Description |
|--------|-------------|
| `outputs/YYYY-MM-DD_thread-<slug>.md` | Initial question and full answer in appendable thread format |

## Invariants

- Never modifies `raw/` or `wiki/`
- Always cites the wiki articles that informed the answer
- Never fabricates answers — acknowledges gaps explicitly

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| No wiki content exists | Report that the knowledge base is empty and prompt user to run ingest |
| No relevant wiki content for question | Answer that the topic is not yet in the knowledge base; suggest relevant raw content to add |
