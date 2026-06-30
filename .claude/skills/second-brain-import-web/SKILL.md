---
name: "second-brain-import-web"
description: "Import a webpage by URL into raw/web/. Fetches with WebFetch, converts to clean Markdown, stores with provenance. Falls back to a paste-Markdown mode for paywalled or unfetchable pages."
argument-hint: "<url> [--pasted-markdown <body>]"
user-invocable: true
---

# Second Brain — Web Import

Import a webpage from a URL into `raw/web/` as a clean Markdown file. The skill fetches the page with the built-in `WebFetch` tool and extracts the readable article body, preserving the original URL as provenance. When a page is paywalled or otherwise unfetchable, the skill writes nothing and reports a structured "fetch failed" message — the dashboard uses this signal to flip its import card into a paste-Markdown mode so the owner can paste the article body manually.

## Invocation

```
/second-brain-web-import "https://example.com/article"
/second-brain-web-import "https://example.com/article" --pasted-markdown "# Article Title\n\nFull markdown body here…"
```

| Argument | Required | Description |
|----------|----------|-------------|
| `<url>` | Yes | The HTTP(S) URL of the page to import. Always the canonical `source:` value. |
| `--pasted-markdown <body>` | No | If supplied, skip the fetch and use this body verbatim. Used by the dashboard's paste-fallback flow. |

If invoked with no URL: ask "Which URL do you want to import?" and stop. Write nothing.

## Execution

### Step 1 — Parse arguments

Parse the first quoted argument as the URL. Verify it begins with `http://` or `https://`. If not, report:
```
Error: Not a URL — <input>
```
and stop. Write nothing.

Detect the `--pasted-markdown` flag. If present, capture the remaining content as the pasted body. If the pasted body is empty or whitespace-only, report:
```
Error: Pasted markdown was empty — nothing written.
```
and stop.

The URL is mandatory in both modes — it always becomes the `source:` value in the front-matter, even when the body came from a paste.

### Step 2 — Fetch (skipped in paste-fallback mode)

If a `--pasted-markdown` body was provided, jump to Step 4 and use it verbatim.

Otherwise, call the **`WebFetch`** tool with the URL and this exact prompt:

```
Extract the full readable article from this page as clean Markdown.

Rules:
1. Return ONLY the article body. Strip site navigation, login banners, cookie notices,
   newsletter sign-up boxes, comment sections, "related articles", footers, ads,
   and breadcrumbs. Keep figure captions and pull quotes.
2. Preserve structure: headings, paragraphs, lists, blockquotes, code blocks, tables.
   Use ATX-style headings (# / ## / ###). Keep links as [text](url).
3. At the very top, include three labelled lines exactly in this order, each on its
   own line:
   TITLE: <the article's title, plain text, no markup>
   PUBLISHED: <publication date in ISO YYYY-MM-DD if visible on the page; otherwise UNKNOWN>
   AUTHOR: <author name(s) if visible; otherwise UNKNOWN>
   Then a blank line, then the article body as Markdown.
4. If the page is a paywall, login wall, "subscribe to continue" interstitial,
   error page, or otherwise has no readable article, return exactly the single
   word: PAYWALL on its own line, with no other content.
5. If the page is reachable but has effectively no article content (e.g. a directory
   index, a 404 page, a redirect landing), return exactly the single word: NOCONTENT
6. Do not invent content. Do not summarise. Do not add commentary.
```

The `TITLE: / PUBLISHED: / AUTHOR:` triplet at the top is the structured-extraction protocol. Step 4 parses these three lines off the body.

### Step 3 — Detect paywall or unfetchable

Apply these rules in order on the WebFetch reply text:

1. **PAYWALL sentinel** — reply trimmed equals `PAYWALL` or starts with `PAYWALL\n` → state = `paywall`.
2. **NOCONTENT sentinel** — reply trimmed equals `NOCONTENT` or starts with `NOCONTENT\n` → state = `no_content`.
3. **WebFetch error** — WebFetch surfaced an error (DNS failure, 4xx, 5xx, redirect loop) → state = `fetch_error`. Capture the error text for the reason.
4. **Length floor** — after stripping the `TITLE: / PUBLISHED: / AUTHOR:` header lines, if the body is shorter than 400 characters → state = `paywall`. Preview/teaser pages typically fall in 100–300 chars.
5. **Keyword check on a short reply** — if the body is shorter than 1500 characters AND contains any of the case-insensitive phrases `subscribe to continue`, `subscribe to read`, `sign in to read`, `sign in to continue`, `create a free account`, `you've reached your`, `become a subscriber`, `unlock this article`, `paywall` → state = `paywall`.
6. Otherwise → state = `ok`.

The keyword check fires only when the body is already suspiciously short, so a normal 4 000-word essay that happens to contain "subscribe to our newsletter" near the bottom is **not** flagged.

In paste-fallback mode (Step 2 was skipped), state is forced to `ok` regardless — trust the human paste.

#### Step 3b — Branch on state

- **`ok`** → continue to Step 4.
- **`paywall`, `no_content`, `fetch_error`** → write **nothing**, emit the fetch-failed message (Step 9b) and stop.

### Step 4 — Detect title, published date, author

In fetch mode, parse the top three lines of the WebFetch reply:
- `TITLE: <text>` → title
- `PUBLISHED: <YYYY-MM-DD or UNKNOWN>` → content_date (omit if `UNKNOWN`)
- `AUTHOR: <text or UNKNOWN>` → author (omit if `UNKNOWN`)

Then strip these three lines (and the blank line that follows) from the body before writing.

In paste-fallback mode, derive the title from the pasted body the same way `second-brain-import-md` does:
1. First `# Heading` if present.
2. First non-empty line truncated to 80 chars.
3. "Untitled webpage" as a fallback.

For content_date in paste-fallback mode, scan the top of the body for the same date markers `md-add` looks for: `Published: …`, `Date: …`, ISO dates, month-year strings. Convert to `YYYY-MM-DD` (use the 1st of the month when only month/year is available). Omit the field if no date found — do not guess.

### Step 5 — Generate slug and output path

Generate the slug from the title:
- Lowercase
- Replace spaces and non-alphanumeric characters with `-`
- Collapse repeated `-` and trim leading/trailing `-`
- Truncate at **50 characters**

Output path:
```
raw/web/YYYY-MM-DD_<slug>.md
```

Where `YYYY-MM-DD` is **today's date** (the import date). The detected publication date goes into front-matter only — never into the filename. This matches `craft-import` and `pdf-import`.

### Step 6 — Check for existing file

Look for a file at the exact output path:

- If the file exists and the body content is **identical** (ignoring the `imported:` line, which always differs on re-import): report "Already imported"; do not write.
- If the file exists and the content **differs**: append `-2`, `-3`, … to the slug until the path is free, and write a fresh file. URLs are not content-addressable, so appending is safer than overwriting (matches `md-add` behaviour, not `pdf-import`'s overwrite).
- If no file exists: create.

### Step 7 — Write the file

Write the file with this exact format:

```markdown
---
source: <url>
imported: YYYY-MM-DD
title: <Detected title>
content_date: YYYY-MM-DD        # omit this line entirely if no date was detected
author: <Detected author>       # omit this line entirely if AUTHOR was UNKNOWN or absent
fetch_mode: webfetch            # or "pasted" in paste-fallback mode
---

# <Title>

<body markdown from Step 4>
```

Notes:
- `source:` is always the URL the user supplied. In paste-fallback mode, the URL is the one they typed in the URL field on first try — the dashboard passes it through on the second call.
- `fetch_mode:` distinguishes auto-fetched from human-pasted bodies, useful for future audits or debugging.
- The `# Title` heading inside the body mirrors `pdf-import`.

### Step 8 — Confirm (success)

Report:
```
✓ Web import complete

Source:        <url>
Output:        raw/web/<filename>
Mode:          [WebFetch | Pasted]
Status:        [Created | Already imported]
Title:         <title>
Content date:  <YYYY-MM-DD if detected, otherwise "not detected">

Next step: run /second-brain-ingest to incorporate into the wiki
```

The leading `✓` is the success discriminator the dashboard already uses. Keep it.

### Step 9b — Fetch-failed message (only when state ≠ `ok`)

When the skill cannot fetch (paywall, no_content, or fetch_error), end the reply with this exact text and nothing useful before it:

```
✗ FETCH_FAILED: Could not extract a readable article from this URL.
URL:           <url>
DetectedTitle: <best-guess from URL slug, or empty>
Reason:        <"paywall" | "no_content" | "fetch_error: <detail>">

To import this page, paste the rendered Markdown into the same card and click Import again.
```

The leading `✗ FETCH_FAILED:` token is the dashboard's discriminator for the paste-fallback flow. The `Reason:` field gives a human message; the `URL:` line lets the front-end pre-fill on flip; the `DetectedTitle:` line is a hint for the user.

The `DetectedTitle` is a best-effort guess from the URL's last path segment (e.g. `https://nyt.com/2026/06/17/hello-world` → `hello-world` → `Hello World`). Leave empty if no useful slug is in the URL.

## Invariants

- Never writes outside `raw/web/`.
- On any non-`ok` state, writes nothing.
- The `source:` front-matter value is always the original URL — never `pasted` or anything else.
- Single-shot: one URL per call; no batch mode.

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| No URL argument | Ask user to provide a URL; write nothing |
| Argument is not http(s) URL | `Error: Not a URL — <input>`; write nothing |
| WebFetch unreachable (DNS/network) | Emit `✗ FETCH_FAILED` with `Reason: fetch_error: <detail>` |
| WebFetch returns `PAYWALL` sentinel | Emit `✗ FETCH_FAILED` with `Reason: paywall` |
| WebFetch returns `NOCONTENT` sentinel | Emit `✗ FETCH_FAILED` with `Reason: no_content` |
| Body too short / paywall keywords present | Emit `✗ FETCH_FAILED` with `Reason: paywall` |
| `--pasted-markdown` empty/whitespace | `Error: Pasted markdown was empty — nothing written.`; write nothing |
| Identical content already on disk | Report "Already imported"; write nothing |
| Slug collision, different content | Append `-2`, `-3`, … and write |
