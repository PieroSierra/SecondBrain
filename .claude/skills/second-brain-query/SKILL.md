# Second Brain — Query

Answer a natural-language question by synthesising content from `wiki/`, and save the response to `outputs/`.

**Contract**: `specs/001-personal-knowledge-base/contracts/second-brain-query.md`

## Invocation

```
/second-brain-query "What are the main arguments for agent ownership vs. strike teams?"
/second-brain-query "What do I know about AI partnerships?"
```

| Argument | Required | Description |
|----------|----------|-------------|
| `<question>` | Yes | The natural-language question to answer |

If invoked without a question argument, ask: "What would you like to know? Please provide a question."

## Execution

### Step 1 — Parse the question

Extract the question from the argument string. If no argument is provided, ask the user for their question.

### Step 2 — Check preconditions

Check that `wiki/INDEX.md` exists. If it does not:
```
The knowledge base is empty — wiki/INDEX.md does not exist.
Run /second-brain-ingest first to populate the wiki from your raw/ content.
```
Stop. Write nothing.

### Step 3 — Read the index and identify relevant topics

Read `wiki/INDEX.md`. Scan the topic list and summaries to identify which wiki articles are likely to contain relevant information for the question.

Select the most relevant topics (typically 1–5 articles depending on question scope). If no topics appear relevant, note that the question may not be covered.

### Step 4 — Read relevant wiki articles

Read the full content of each selected wiki article from `wiki/<topic>.md`.

If a referenced article does not exist as a file (only in INDEX), skip it and note the gap.

### Step 5 — Synthesise the answer

Using only the content read from wiki articles, synthesise a clear, grounded answer to the question:

- Answer directly — lead with the key point, then support with detail
- Use `[[topic-name]]` wikilink citations inline to attribute claims to their source article
- Structure with headings or bullet points if the answer spans multiple sub-topics
- Do NOT fabricate information not present in the wiki articles

**Freshness rules — apply these when synthesising:**
- When claims are date-stamped (e.g., "As of Jan 2026:" or "(May 2026)"), **prioritise the most recent information**. If a newer source contradicts an older one on the same claim, lead with the newer fact and note the older one only as historical context.
- When surfacing a status, metric, or decision, always include the date it was recorded — e.g., *"As of May 2026, AI-assisted PRs are at 25%"* — so the reader can judge freshness.
- If a claim has no date, note it as *"(date unknown)"* rather than presenting it as current.
- When the question is explicitly about current state (e.g., "what is our status", "how are we doing"), flag any response built primarily from sources older than 3 months with a warning like: *"Note: the most recent source on this topic is from [date] — newer context may exist."*

**If no relevant wiki content exists for the question**:
```
This topic is not yet in your knowledge base.

To add it, you could:
- Import relevant Craft notes with /second-brain-craft-import
- Import a PDF with /second-brain-pdf-import
- Add a markdown file to raw/ and run /second-brain-ingest
```
Do not attempt to answer from general knowledge. Write an output file noting the gap (see Step 6) and stop.

**If only partial content exists** (question is partly covered): answer the parts that are covered, then explicitly note which aspects are not in the wiki.

### Step 6 — Generate output filename

Generate the output slug from the first 5–6 words of the question:
- Lowercase all words
- Replace spaces with hyphens
- Remove characters that are not alphanumeric or hyphens
- Truncate to 40 characters maximum

Output path: `outputs/YYYY-MM-DD_query-<slug>.md`

Example: "What do I know about AI partnerships?" → `outputs/2026-06-16_query-what-do-i-know-about-ai.md`

### Step 7 — Write the output file

Check that `outputs/` directory exists. If not, create it.

Write the output file with this exact format:

```markdown
# Query: [Original question]

*Date: YYYY-MM-DD*

[Synthesised answer]

---
*Sources: [[wiki/topic-1]], [[wiki/topic-2]]*
```

Where:
- The original question is reproduced verbatim in the heading
- The sources footer lists every wiki article that contributed to the answer (using `[[wiki/topic-name]]` syntax)
- For gap responses, the answer section contains the gap acknowledgement and suggestions

### Step 8 — Display and confirm

Display the full answer to the user in the conversation.

Then report:
```
Answer saved to: outputs/YYYY-MM-DD_query-<slug>.md
Sources: [[wiki/topic-1]], [[wiki/topic-2]]
```

## Invariants

- Never modifies any file in `raw/` or `wiki/`
- Always cites the wiki articles that informed the answer — never fabricates
- Always writes an output file, even for gap responses
- Output files are append-only — never overwrite a prior query result

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| No argument provided | Ask user for their question |
| `wiki/INDEX.md` missing | Report empty knowledge base; prompt to run ingest; stop |
| No relevant wiki content | Acknowledge gap; suggest how to fill it; write gap-noting output file |
| Wiki article referenced in INDEX but file missing | Skip missing file; note in answer; continue with available articles |
| `outputs/` directory missing | Create it, then write the output file |
