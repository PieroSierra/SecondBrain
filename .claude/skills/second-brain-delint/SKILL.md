---
name: "second-brain-delint"
description: "Work through open findings in the latest lint report one at a time — propose a fix, apply on approval, skip on rejection."
argument-hint: ""
user-invocable: true
---

# Second Brain — Delint

Read the latest lint report, walk through each open finding, and apply fixes with your approval.

## Invocation

```
/second-brain-delint
```

No arguments. Always operates on the most recent `outputs/*lint*.md` file.

## Execution Steps

### Step 1 — Find the latest lint report

List all files in `outputs/` matching `*lint*.md`. Sort by filename (date-prefixed, so lexicographic order gives recency). Take the last one.

If no lint report exists:
```
No lint report found — run /second-brain-lint first.
```
Stop.

### Step 2 — Parse open findings

Read the lint report. Extract all `<!-- sb:finding ... -->` blocks where `status="open"`. Also read their corresponding `<!-- sb:proposal ... -->` blocks (matched by `id`).

If no open findings:
```
No open findings — all issues resolved. ✓
```
Stop.

Report the count upfront:
```
Found N open findings in outputs/YYYY-MM-DD_lint.md
```

### Step 3 — Process each finding

For each open finding in document order:

---

**Print a header:**
```
─── Finding fN of M ───────────────────────────────
```

**Print the finding prose** — the bullet text immediately preceding the `sb:finding` tag (the human-readable description of the issue).

**Then branch by type:**

#### type = `acknowledge`

```
[No action needed — housekeeping note]
```

Mark `status="applied"` in the report file immediately. Continue to next finding.

#### type = `run-ingest`

Print the proposal:
```
Proposed: run /second-brain-ingest to fold this raw file into the wiki
File: <instruction value>

Run ingest now? [y/n]
```

- `y` → invoke `/second-brain-ingest`. On success, mark `status="applied"`.
- `n` → mark `status="skipped"`.

#### type = `edit-wiki`

Print the proposal. If `confidence="low"`, prefix with a caution line:
```
⚠  I'm not certain about this fix — please verify before applying.
```

Then:
```
Proposed fix:
  <instruction value>

Apply this fix? [y / n / e to edit the instruction]
```

- `y` → invoke `/second-brain-edit-wiki "<instruction>"`. On success, mark `status="applied"`.
- `n` → mark `status="skipped"`.
- `e` → prompt: `Enter revised instruction:`. Read the user's reply. Invoke `/second-brain-edit-wiki "<revised instruction>"`. On success, mark `status="applied"`.

---

### Step 4 — Update the report file after each action

After each finding is resolved (applied or skipped), update `outputs/YYYY-MM-DD_lint.md` in place:

1. For the relevant `sb:finding` tag, replace `status="open"` with `status="applied"` or `status="skipped"`.
2. Recalculate and update the `sb:delint` summary tag at the top:
   - Decrement `open` by 1
   - Increment `applied` or `skipped` by 1

Write the updated file with the Write tool.

### Step 5 — Final summary

After all findings are processed:

```
─── Done ──────────────────────────────────────────
Applied: A  |  Skipped: S  |  Remaining open: R

Lint report updated: outputs/YYYY-MM-DD_lint.md
```

If any edits were applied, remind the user to run `/second-brain-ingest` if they want the wiki INDEX rebuilt (edit-wiki edits articles in place; INDEX is not rebuilt automatically).

## Invariants

- Never modifies `raw/` or `wiki/` directly — all wiki changes go through `/second-brain-edit-wiki`
- Never re-runs lint — reads the existing report only
- Never marks a finding `applied` unless the underlying skill call succeeded
- Updates the report file after **each** finding, not in bulk at the end — so a partial run leaves correct state

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| Lint report has no `sb:finding` tags | Inform user the report predates the delint format; ask them to re-run `/second-brain-lint` |
| edit-wiki skill call fails | Report the error, leave finding `status="open"`, continue to next |
| ingest skill call fails | Report the error, leave finding `status="open"`, continue to next |
| Report file not writable | Report the error and stop — do not continue without being able to persist state |
