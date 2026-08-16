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

from date_extract import select_content_date

# ---------------------------------------------------------------------------
# Safety limits
# ---------------------------------------------------------------------------
_MAX_BYTES = 50 * 1024 * 1024   # 50 MB — same order of magnitude as other extractors
_MAX_MARKDOWN = 8 * 1024 * 1024  # 8 MB cap on assembled output


class TextError(Exception):
    """Raised when a text file can't be safely read."""


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
    content_date = select_content_date(context=context, title=title, content=head)

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
    `context` is the operator-supplied Document Context note and has first
    priority when it contains a date.
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
    `context` is the operator note from the Document Context field and has first
    priority when it contains a date.
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
