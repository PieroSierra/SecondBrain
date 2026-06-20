# Quickstart — Interactive KB Dashboard

## Prerequisites

- `claude` CLI on PATH (`which claude` returns a path).
- Python 3 (macOS system Python is fine; no `pip install` required).
- A populated vault — i.e. you've run `/second-brain-setup` and at least one `/second-brain-ingest` already.

## Start the dashboard

From the vault root:

```bash
python3 dashboard/bridge.py
```

On start the bridge prints:

```
Second Brain dashboard listening on http://127.0.0.1:4173/
Opening browser…
```

…and `open`s the URL on macOS. Stop with Ctrl-C.

## Smoke-test each user story

### US1 — Ask a question (P1)
1. In the hero query box, type *"What do I know about AI partnerships?"* and press Enter.
2. **Expect**: a spinner appears, then a rendered Markdown answer with `[[wikilinks]]` highlighted, plus a footer link to the saved `outputs/YYYY-MM-DD_query-*.md` file.
3. If the wiki is empty: the answer politely says so rather than erroring.

### US2 — Add content (P2)
Run each of these once:
1. **Paste Markdown**: paste any short note into the paste-import textarea, click "Add". Expect a confirmation naming the new `raw/*.md` file.
2. **PDF import**: click the PDF upload, pick any PDF on disk. Expect a confirmation naming the new `raw/pdf/<file>.md`.
3. **Craft import**: enter `AI Partnerships` and the name of any document you know exists in that folder. Expect a confirmation naming the new `raw/craft/<file>.md`.
4. **Negative**: enter a Craft document that doesn't exist. Expect a specific error message, not a generic spinner-forever.

### US3 — Status strip (P2)
1. Reload the page after the imports above.
2. **Expect**: the "raw pending" counter has increased by 3, the wiki count is unchanged, the last-ingest time is unchanged.
3. Strip renders in well under a second.

### US4 — Refresh / lint (P3)
1. Click "Ingest". Expect a progress indicator, then a Markdown summary of what changed.
2. Reload — `last_ingest_iso` is now recent; `raw_pending_count` is 0.
3. Click "Lint". Expect the lint report rendered in the page.

### Serialization check
1. Click "Ingest" — while it's running, click any other long-op control.
2. **Expect**: that control is disabled (or, if clicked anyway, the bridge returns 409 and the UI shows "busy — ingest is running").

## Troubleshooting

- **`claude: command not found`** in the bridge log → install Claude Code CLI and ensure it's on the PATH of the shell that launches `bridge.py`.
- **Browser shows "Connection refused"** → bridge isn't running; start it from the vault root.
- **All operations time out** → run the same skill manually from the terminal (`claude -p "/second-brain-query \"...\"" --output-format json --permission-mode bypassPermissions`) and check the JSON. If that hangs, the issue is in the skill / vault state, not the bridge.
- **Status strip shows `—` for last ingest** → `raw/.ingest-manifest.json` is missing or unreadable; this is degraded-but-not-broken behaviour. Running `/second-brain-ingest` once should fix it.
