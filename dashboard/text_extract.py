#!/usr/bin/env python3
"""text_extract — convert a plain-text or Markdown file (or string) to a raw/
vault file using ONLY the Python standard library.

Unlike PDF or image imports there is nothing to understand: the content is
already text and is stored verbatim. This module handles the mechanical parts
that the LLM-backed skill would otherwise handle at the cost of a model call:

  * reading the file (UTF-8 with latin-1 fallback),
  * stripping any existing YAML front-matter from .md files (to avoid
    double-wrapping when the file already carries its own front-matter),
  * extracting a title from the first `# Heading` or first non-empty line,
  * detecting a content date from a regex scan of the first 30 lines.

Two entry points:
  * `text_to_markdown(path) -> dict` — used in-process by the dashboard bridge
    for .txt / .md file uploads.
  * `text_from_string(content, title_hint=None) -> dict` — used for pasted text
    (the md-add bridge path).
  * CLI: `python3 text_extract.py <file.md>` — prints result, handy for testing.

SECURITY: the input is untrusted. The file size is capped before reading;
encoding errors fall back gracefully rather than crashing.
"""
import os
import re
import sys

# ---------------------------------------------------------------------------
# Safety limits
# ---------------------------------------------------------------------------
_MAX_BYTES = 50 * 1024 * 1024   # 50 MB — same order of magnitude as other extractors
_MAX_MARKDOWN = 8 * 1024 * 1024  # 8 MB cap on assembled output


class TextError(Exception):
    """Raised when a text file can't be safely read."""


# ---------------------------------------------------------------------------
# Date detection — identical regexes to docx_extract / pptx_extract
# ---------------------------------------------------------------------------
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# "Jul 14, 2026" / "July 14 2026" — groups: (month, day, year)
_MONTH_DAY_YEAR_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})[,\s]+(\d{4})\b",
    re.IGNORECASE,
)
# "July 2026" / "Jul 2026" — groups: (month, year)
_MONTH_YEAR_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{4})\b",
    re.IGNORECASE,
)
# Matches "date: 2026-07-14" or "date: July 2026" style frontmatter / prose
_DATE_LABEL_RE = re.compile(
    r"\bdate\s*[:\-]\s*(.*)", re.IGNORECASE
)


def _detect_date(text):
    """Return YYYY-MM-DD from a date signal in `text`, or None. Best-effort."""
    # Check labelled date fields first ("Date: 2026-07-14")
    m = _DATE_LABEL_RE.search(text)
    if m:
        candidate = m.group(1).strip()
        iso = _ISO_RE.search(candidate)
        if iso:
            return iso.group(0)
        mdy = _MONTH_DAY_YEAR_RE.search(candidate)
        if mdy:
            mon = _MONTHS.get(mdy.group(1).lower()[:3])
            if mon:
                return "%s-%02d-%02d" % (mdy.group(3), mon, int(mdy.group(2)))
        my = _MONTH_YEAR_RE.search(candidate)
        if my:
            mon = _MONTHS.get(my.group(1).lower()[:3])
            if mon:
                return "%s-%02d-01" % (my.group(2), mon)
    # Bare ISO date anywhere in the text
    m = _ISO_RE.search(text)
    if m:
        return m.group(0)
    # "Month Day Year" anywhere in text
    m = _MONTH_DAY_YEAR_RE.search(text)
    if m:
        mon = _MONTHS.get(m.group(1).lower()[:3])
        if mon:
            return "%s-%02d-%02d" % (m.group(3), mon, int(m.group(2)))
    # "Month Year" anywhere in text
    m = _MONTH_YEAR_RE.search(text)
    if m:
        mon = _MONTHS.get(m.group(1).lower()[:3])
        if mon:
            return "%s-%02d-01" % (m.group(2), mon)
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_YAML_FM_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)")


def _strip_frontmatter(text):
    """Remove a leading YAML front-matter block if present."""
    m = _YAML_FM_RE.match(text)
    return text[m.end():] if m else text


def _extract_title(text):
    """Return the first # heading text, or the first non-empty line (max 60 chars)."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _HEADING_RE.match(line)
        if m:
            return m.group(1).strip()[:60]
        return line[:60]
    return None


def _process(content, title_hint=None, context=None):
    """Shared logic for both entry points. Returns the standard dict."""
    if len(content) > _MAX_MARKDOWN:
        content = content[:_MAX_MARKDOWN] + "\n\n_[truncated: exceeded size cap]_"

    title = (title_hint or "").strip() or _extract_title(content) or None
    head = "\n".join(content.splitlines()[:30])
    content_date = _detect_date(head)
    # Fall back to scanning the operator-supplied context note when the content
    # itself has no date signal (e.g. "created Jun 12 2026" in the context field).
    if not content_date and context:
        content_date = _detect_date(context)

    return {
        "markdown": content,
        "words": len(content.split()),
        "title": title,
        "content_date": content_date,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def text_to_markdown(path, context=None):
    """Read a .txt or .md file from disk. Returns a dict; raises TextError.

    Keys: markdown (str, verbatim content), words (int), title (str or None),
    content_date (YYYY-MM-DD or None).
    `context` is the operator-supplied Document Context note; scanned for a date
    signal when the file itself carries none.
    """
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise TextError("cannot stat file: %s" % exc)
    if size > _MAX_BYTES:
        raise TextError(
            "file is %.1f MB — exceeds the 50 MB limit for text import."
            % (size / (1024 * 1024))
        )
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except UnicodeDecodeError:
        try:
            with open(path, encoding="latin-1") as fh:
                raw = fh.read()
        except OSError as exc:
            raise TextError("cannot read file: %s" % exc)
    except OSError as exc:
        raise TextError("cannot read file: %s" % exc)

    if not raw.strip():
        raise TextError("file is empty — nothing to import.")

    # Strip any existing YAML front-matter from .md files to avoid double-wrapping.
    if str(path).lower().endswith(".md"):
        raw = _strip_frontmatter(raw)

    return _process(raw, context=context)


def text_from_string(content, title_hint=None, context=None):
    """Process raw pasted text. Returns the same dict shape as text_to_markdown.

    `title_hint` is the optional title the user typed in the paste form.
    `context` is the operator note from the Document Context field; scanned for a
    date signal when the content itself carries none.
    Raises TextError if content is empty.
    """
    if not content or not content.strip():
        raise TextError("no content provided — nothing to import.")
    return _process(content, title_hint=title_hint, context=context)


# ---------------------------------------------------------------------------
# CLI entry point (for manual testing)
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python3 text_extract.py <file.txt|file.md>")
    try:
        res = text_to_markdown(sys.argv[1])
    except TextError as exc:
        sys.exit("error: %s" % exc)
    print("# %s\n" % os.path.basename(sys.argv[1]))
    print(res["markdown"])
    print("\n---\n")
    print("**Summary:** %d words" % res["words"])
    if res["title"]:
        print("_Detected title: %s_" % res["title"])
    if res["content_date"]:
        print("_Detected content date: %s_" % res["content_date"])


if __name__ == "__main__":
    main()
