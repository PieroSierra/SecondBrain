#!/usr/bin/env python3
"""docx_extract — convert a .docx to markdown using ONLY the Python standard library.

A .docx is a ZIP of OOXML (WordprocessingML), so its content is mechanically
extractable with no model call. This walks the document body in order and emits:
  * headings (`##`–`######`), resolved from styles.xml style names (Heading 1–9,
    Title) with an inline `outlineLvl` fallback; Subtitle renders as a bold block,
  * list paragraphs as markdown bullets, nested by `ilvl`, with numbered-vs-bullet
    resolved through numbering.xml,
  * run-level bold/italic as `**`/`*` — from direct `w:b`/`w:i` formatting AND
    from character styles that carry them (`Strong`/`Emphasis`, common in text
    pasted from the web); adjacent same-format runs are merged first,
  * tables as markdown pipe tables; cell paragraphs keep emphasis and lists,
    collapsed to one line per cell (`<br>`-separated, bullets/numbers inline);
    nested tables flagged, not recursed,
  * external hyperlinks as `[text](url)`,
  * text-box contents inline, and `[image]` flags for pictures/drawings.

Not extracted (lossy by design): headers/footers, footnotes/endnotes, comments,
rendered tables of contents, and deleted tracked-change text (insertions ARE
kept).

Two entry points:
  * `docx_to_markdown(path) -> dict` — used in-process by the dashboard bridge.
  * CLI: `python3 docx_extract.py <doc.docx>` — prints the full markdown, handy
    for testing/iterating on the extractor independently of the bridge.

SECURITY: the input is untrusted. Parsing is guarded against non-OOXML files,
zip bombs, and XML entity-expansion / XXE (a stdlib substitute for defusedxml);
all failures raise `DocxError`.
"""
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET


class DocxError(Exception):
    """Raised when a .docx can't be safely or validly parsed."""


# --- Safety limits (input is an untrusted upload) -------------------------
_MAX_ENTRIES = 5000
_MAX_PART_UNCOMPRESSED = 50 * 1024 * 1024     # 50 MB per internal part
_MAX_TOTAL_UNCOMPRESSED = 300 * 1024 * 1024   # 300 MB total (zip-bomb guard)
_MAX_BLOCKS = 20000                           # body paragraphs + tables processed
_MAX_MARKDOWN = 8 * 1024 * 1024               # cap assembled output


def _open_docx(path):
    """Open and validate a .docx, returning an open ZipFile. Raises DocxError.

    Rejects non-Zip/OOXML files (incl. password-protected documents, which are
    OLE containers, not Zips) and obvious zip bombs before any part is read.
    """
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
    except OSError as exc:
        raise DocxError("cannot read file: %s" % exc)
    if magic != b"PK\x03\x04":
        raise DocxError(
            "not a Word (.docx) file — it may be password-protected, an "
            "older .doc, or corrupt."
        )
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise DocxError("corrupt or unreadable .docx (bad Zip container).")
    try:
        names = set(zf.namelist())
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise DocxError("not a Word document (missing OOXML parts).")
        infos = zf.infolist()
        if len(infos) > _MAX_ENTRIES:
            raise DocxError("too many entries in the .docx (possible zip bomb).")
        total = 0
        for zi in infos:
            if zi.file_size > _MAX_PART_UNCOMPRESSED:
                raise DocxError("an internal part is too large (possible zip bomb).")
            total += zi.file_size
        if total > _MAX_TOTAL_UNCOMPRESSED:
            raise DocxError("uncompressed contents too large (possible zip bomb).")
    except DocxError:
        zf.close()
        raise
    return zf


def _safe_read_xml(zf, name):
    """Read a part and parse it as XML, guarding against entity-expansion / XXE.

    Valid OOXML parts never contain a DOCTYPE or entity definition, so scanning
    the raw bytes for them BEFORE `ET.fromstring` is a zero-false-positive guard
    that neutralises billion-laughs and external-entity attacks (the danger is at
    parse time, so the check must precede the parse). Raises DocxError.
    """
    try:
        data = zf.read(name)
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise DocxError("cannot read %s: %s" % (name, exc))
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise DocxError("unsafe XML (DOCTYPE/ENTITY) in %s" % name)
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise DocxError("malformed XML in %s: %s" % (name, exc))


# --- OOXML helpers --------------------------------------------------------

def local(tag):
    """Strip the XML namespace: '{...}t' -> 't' (so we can ignore namespaces)."""
    return tag.rsplit("}", 1)[-1]


def child(el, name):
    return next((c for c in el if local(c.tag) == name), None)


def attr(el, name):
    """Namespaced attribute lookup (WordprocessingML attrs carry the w: prefix,
    unlike DrawingML's bare attrs, so `el.get(...)` alone can't find them)."""
    for k, v in el.attrib.items():
        if local(k) == name:
            return v
    return None


# --- Style / numbering / link tables (each degrades to empty on failure) ---

_HEADING_RE = re.compile(r"^heading\s*(\d)$")
_TOC_RE = re.compile(r"^(toc\s*\d+|toc heading|tocheading)$")


def _style_level(s):
    """Heading depth for a style name or id: 1–9 for Heading N, 0 for Title,
    -1 for Subtitle (rendered bold), None if not a heading style."""
    s = (s or "").strip().lower()
    m = _HEADING_RE.match(s)
    if m:
        return int(m.group(1))
    if s == "title":
        return 0
    if s == "subtitle":
        return -1
    return None


def heading_levels(zf):
    """Style tables from word/styles.xml: (heading levels, TOC skips, char emphasis).

    Heading levels map paragraph styleId -> depth, matching the style *name*
    first — styleIds are localized (a German document has `berschrift1`) but
    Word keeps the internal English name — then falling back to the styleId.
    TOC styles are dropped entirely: a rendered table of contents is pure noise
    for the vault. Char emphasis maps character styleId -> (bold, italic) for
    styles whose own rPr sets them (`Strong`/`Emphasis` — how bold arrives in
    text pasted from the web, where runs carry no direct w:b). Missing or
    unparseable styles.xml degrades to empties; `outlineLvl` still catches
    direct-formatted headings.
    """
    if "word/styles.xml" not in zf.namelist():
        return {}, set(), {}
    try:
        root = _safe_read_xml(zf, "word/styles.xml")
    except DocxError:
        return {}, set(), {}
    levels, skips, chars = {}, set(), {}
    for st in root:
        if local(st.tag) != "style":
            continue
        sid = attr(st, "styleId")
        if not sid:
            continue
        stype = attr(st, "type")
        if stype == "character":
            rpr = child(st, "rPr")
            b = i = False
            if rpr is not None:
                for c in rpr:
                    lt = local(c.tag)
                    if lt in ("b", "i"):
                        on = (attr(c, "val") or "").lower() not in ("0", "false", "none")
                        if lt == "b":
                            b = on
                        else:
                            i = on
            if b or i:
                chars[sid] = (b, i)
            continue
        if stype != "paragraph":
            continue
        nm = child(st, "name")
        name = (attr(nm, "val") or "") if nm is not None else ""
        lvl = _style_level(name)
        if lvl is None:
            lvl = _style_level(sid)
        if lvl is not None:
            levels[sid] = lvl
        if _TOC_RE.match(name.strip().lower()) or _TOC_RE.match(sid.strip().lower()):
            skips.add(sid)
    return levels, skips, chars


_ORDERED_FMTS = frozenset({
    "decimal", "decimalZero", "lowerLetter", "upperLetter",
    "lowerRoman", "upperRoman", "ordinal",
})


def _ind_left(el):
    """Left indent in twips from a w:ind (w:left, or its newer alias w:start)."""
    ind = child(el, "ind") if el is not None else None
    if ind is None:
        return None
    try:
        return int(attr(ind, "left") or attr(ind, "start"))
    except (TypeError, ValueError):
        return None


def num_formats(zf):
    """Map (numId, ilvl) -> (numFmt, left-indent twips) from word/numbering.xml.

    Two passes: abstractNum defines per-level formats, num instances point at an
    abstractNum. Unresolved lookups fall back to bullets at render time. The
    indent matters because Word freely starts a NEW list (fresh numId, ilvl 0)
    for visually deep bullets — the nesting then lives only in the level's
    indent, at Word's convention of 720 twips per level.
    """
    if "word/numbering.xml" not in zf.namelist():
        return {}
    try:
        root = _safe_read_xml(zf, "word/numbering.xml")
    except DocxError:
        return {}
    abstract = {}
    for an in root:
        if local(an.tag) != "abstractNum":
            continue
        aid = attr(an, "abstractNumId")
        lvls = {}
        for lv in an:
            if local(lv.tag) != "lvl":
                continue
            nf = child(lv, "numFmt")
            fmt = (attr(nf, "val") or "") if nf is not None else ""
            lvls[attr(lv, "ilvl") or "0"] = (fmt, _ind_left(child(lv, "pPr")))
        if aid is not None:
            abstract[aid] = lvls
    out = {}
    for num in root:
        if local(num.tag) != "num":
            continue
        nid = attr(num, "numId")
        ai = child(num, "abstractNumId")
        aid = attr(ai, "val") if ai is not None else None
        for il, fmt_ind in abstract.get(aid, {}).items():
            out[(nid, il)] = fmt_ind
    return out


def _ext_links(zf):
    """Map r:id -> external URL from word/_rels/document.xml.rels.

    The mirror image of the pptx extractor's rels_for: hyperlink targets live in
    TargetMode="External" entries, and internal parts are never followed.
    """
    rp = "word/_rels/document.xml.rels"
    if rp not in zf.namelist():
        return {}
    try:
        root = _safe_read_xml(zf, rp)
    except DocxError:
        return {}
    out = {}
    for r in root:
        if (r.get("TargetMode") or "") != "External":
            continue
        rid, tgt = r.get("Id"), r.get("Target") or ""
        if rid and tgt:
            out[rid] = tgt
    return out


def core_props(zf):
    """(dc:title, dcterms:created as YYYY-MM-DD) from docProps/core.xml.

    Best-effort; degrades to (None, None). `dcterms:modified` is deliberately
    ignored — it drifts toward "today", which the import date already records.
    """
    if "docProps/core.xml" not in zf.namelist():
        return None, None
    try:
        root = _safe_read_xml(zf, "docProps/core.xml")
    except DocxError:
        return None, None
    title = created = None
    for el in root:
        lt = local(el.tag)
        if lt == "title" and el.text and el.text.strip():
            title = el.text.strip()
        elif lt == "created" and el.text:
            head = el.text.strip()[:10]     # W3CDTF: 2024-03-05T10:00:00Z
            if _ISO_RE.match(head):
                created = head
    return title, created


# --- Paragraph extraction ---------------------------------------------------

def p_info(p):
    """(styleId, outlineLvl, (numId, ilvl) or None, direct left indent)
    from a paragraph's pPr. The direct indent overrides the numbering
    definition's when both exist (Word semantics)."""
    ppr = child(p, "pPr")
    style = outline = num = None
    if ppr is not None:
        for c in ppr:
            lt = local(c.tag)
            if lt == "pStyle":
                style = attr(c, "val")
            elif lt == "outlineLvl":
                try:
                    outline = int(attr(c, "val"))
                except (TypeError, ValueError):
                    outline = None
            elif lt == "numPr":
                il_el, nid_el = child(c, "ilvl"), child(c, "numId")
                try:
                    ilvl = int(attr(il_el, "val")) if il_el is not None else 0
                except (TypeError, ValueError):
                    ilvl = 0
                nid = attr(nid_el, "val") if nid_el is not None else None
                if nid and nid != "0":      # numId 0 = "numbering removed"
                    num = (nid, max(0, ilvl))
    return style, outline, num, _ind_left(ppr)


def _run_flags(r, chars):
    """(bold, italic) from a run: its character style (w:rStyle → `chars`, so
    `Strong`/`Emphasis` count), overridden by direct w:b / w:i formatting.
    Full basedOn-chain resolution is deliberately out of scope.
    """
    rpr = child(r, "rPr")
    b = i = False
    if rpr is not None:
        rs = child(rpr, "rStyle")
        if rs is not None:
            b, i = chars.get(attr(rs, "val"), (False, False))
        for c in rpr:
            lt = local(c.tag)
            if lt in ("b", "i"):
                on = (attr(c, "val") or "").lower() not in ("0", "false", "none")
                if lt == "b":
                    b = on
                else:
                    i = on
    return b, i


def _merge_segs(segs):
    """Coalesce adjacent same-format segments. Word splits runs arbitrarily
    (spellcheck/rsid boundaries), so without merging, emphasis markers would
    land mid-word."""
    out = []
    for t, b, i in segs:
        if not t:
            continue
        if out and out[-1][1] == b and out[-1][2] == i:
            out[-1][0] += t
        else:
            out.append([t, b, i])
    return out


def render_segs(segs, emphasis=True):
    """Join run segments into paragraph text.

    With emphasis, bold/italic segments are wrapped in `**`/`*`/`***`, with
    leading/trailing whitespace moved OUTSIDE the markers (a space inside them
    breaks markdown's flanking rules). Plain mode is used where markers would
    be wrong: headings, subtitles, and structural checks.
    """
    parts = []
    for t, b, i in _merge_segs(segs):
        core = t.strip()
        if emphasis and (b or i) and core:
            m = "***" if (b and i) else ("**" if b else "*")
            lead = t[: len(t) - len(t.lstrip())]
            trail = t[len(t.rstrip()):]
            parts.append(lead + m + core + m + trail)
        else:
            parts.append(t)
    return "".join(parts).strip()


def para_text(p, links, chars):
    """Assemble a paragraph's visible content in document order.

    Returns (segments, extra_lines, images) — segments are [text, bold, italic]
    for render_segs; extra_lines are text-box contents (emitted as their own
    blocks). Walks children recursively rather than flat `.iter()` (a deliberate
    divergence from the pptx extractor) because Word runs carry structure:
    hyperlinks wrap runs, fields cache their result as runs, and tracked
    changes nest runs inside w:ins / w:del. Deleted text is excluded for free —
    it lives in w:delText, which is never read.
    """
    images = 0
    extra = []      # text-box lines, emitted as blocks after the paragraph

    def drawing(el):
        nonlocal images
        tb = next((d for d in el.iter() if local(d.tag) == "txbxContent"), None)
        if tb is None:
            images += 1
            return
        # Text boxes carry body-visible content (pull quotes, sidebars).
        for tp in tb.iter():
            if local(tp.tag) == "p":
                txt = "".join(
                    t.text for t in tp.iter() if local(t.tag) == "t" and t.text
                ).strip()
                if txt:
                    extra.append(txt)

    def walk(el, out):
        tag = local(el.tag)
        if tag == "pPr":
            return
        if tag == "r":
            b, i = _run_flags(el, chars)
            for c in el:
                ct = local(c.tag)
                if ct == "t":
                    out.append([c.text or "", b, i])
                elif ct == "tab":
                    out.append([" ", False, False])
                elif ct in ("br", "cr"):
                    # Plain, so emphasis never spans a line break.
                    out.append(["\n", False, False])
                elif ct == "noBreakHyphen":
                    out.append(["-", b, i])
                elif ct in ("drawing", "pict", "object"):
                    drawing(c)
            return
        if tag == "hyperlink":
            inner = []
            for c in el:
                walk(c, inner)
            text = "".join(s[0] for s in inner).strip()
            url = links.get(attr(el, "id"))
            if text and url:
                # Emphasis inside link text is dropped — the link is the signal.
                out.append(["[%s](%s)" % (text, url), False, False])
            else:   # internal bookmark anchor, or unresolved rid — keep the text
                out.extend(inner)
            return
        if tag == "del":
            return
        if tag == "sdt":
            content = child(el, "sdtContent")
            if content is not None:
                for c in content:
                    walk(c, out)
            return
        if tag in ("drawing", "pict", "object"):
            drawing(el)
            return
        # ins / fldSimple / smartTag / bdo … — containers of ordinary runs.
        # (fldSimple needs no special code: Word caches the field RESULT as runs
        # inside it, and instruction text is w:instrText, which is never read.)
        for c in el:
            walk(c, out)

    buf = []
    for c in p:
        walk(c, buf)
    return buf, extra, images


def _pipe_table(rows):
    """Assemble rows of one-line cell strings into a markdown pipe table.

    `:---` (not `---`) — markdown makes the first row a <th> header, which
    browsers center by default; explicit left alignment keeps Word tables
    looking like Word tables.
    """
    ncol = max(len(r) for r in rows)
    rows = [r + [" "] * (ncol - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |",
           "| " + " | ".join([":---"] * ncol) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return out


def _inline_list(blk):
    """Flatten one tight-list block into per-item cell lines.

    Real markdown lists can't live inside a pipe-table cell, so items are
    rendered inline: `•`/`◦` bullets, sequential `1.`/`a.` numbering (our list
    blocks always say `1.` and let markdown renumber — inside a cell nothing
    renumbers, so the counter has to be explicit), `&nbsp;` indentation.
    """
    counters = {}
    out = []
    for ln in blk.split("\n"):
        m = re.match(r"^(\s*)(- |1\. )(.*)$", ln)
        if not m:
            out.append(ln)
            continue
        indent, marker, text = m.groups()
        lvl = len(indent) // 2
        for k in [k for k in counters if k > lvl]:   # left a nested run — reset it
            del counters[k]
        if marker == "1. ":
            counters[lvl] = counters.get(lvl, 0) + 1
            n = counters[lvl]
            pref = "%d. " % n if lvl == 0 else "%s. " % chr(ord("a") + (n - 1) % 26)
        else:
            counters.pop(lvl, None)
            pref = "• " if lvl == 0 else "◦ "
        out.append("&nbsp;&nbsp;" * lvl + pref + text)
    return out


def _cell_text(blocks):
    """Collapse a cell's blocks to the one line a pipe-table cell can hold:
    headings → bold, lists inlined, line breaks and block joins → `<br>`."""
    lines = []
    for blk in blocks:
        m = re.match(r"^(#{2,6})\s+(.*)$", blk)
        if m:
            lines.append("**" + m.group(2) + "**")
        elif re.match(r"^\s*(?:- |1\. )", blk):
            lines.extend(_inline_list(blk))
        else:
            lines.append(blk.replace("\n", "<br>"))
    return "<br>".join(l for l in lines if l).replace("|", "\\|") or " "


def _flatten(children):
    """Yield body-level elements with w:sdt wrappers unwrapped (cover pages and
    TOCs arrive inside structured-document-tag blocks; without this they'd be
    silently dropped)."""
    for el in children:
        if local(el.tag) == "sdt":
            content = child(el, "sdtContent")
            if content is not None:
                for c in _flatten(content):
                    yield c
        else:
            yield el


def _one_line(s):
    return re.sub(r"\s*\n\s*", " ", s)


def extract(root, levels, skips, nums, links, chars):
    """Walk the document body. Return (markdown_blocks, counts).

    Same block discipline as the pptx extractor: each block is a self-contained
    markdown unit (a heading, one tight list, a paragraph, a table, or an
    `[image]` marker). The caller joins blocks with blank lines. Table cells run
    through the same paragraph pipeline, so lists and emphasis survive inside
    tables.
    """
    body = child(root, "body")
    counts = {"image": 0, "table": 0}
    if body is None:
        return [], counts
    budget = {"blocks": 0, "noted": False}

    def walk_children(children, depth, blocks):
        """Process a sequence of block-level elements into `blocks`.
        Returns True when the global block cap was hit."""
        cur = []    # a run of consecutive list items = one tight list block

        def flush():
            if cur:
                blocks.append("\n".join(cur))
                cur.clear()

        for el in _flatten(children):
            tag = local(el.tag)
            if tag not in ("p", "tbl"):     # sectPr, tcPr, bookmarks — skip
                continue
            budget["blocks"] += 1
            if budget["blocks"] > _MAX_BLOCKS:
                flush()
                if not budget["noted"]:
                    budget["noted"] = True
                    blocks.append("_[truncated: exceeded block cap]_")
                return True
            if tag == "tbl":
                flush()
                counts["table"] += 1
                if depth > 0:   # table inside a table cell — flag, don't recurse
                    blocks.append("`[nested table]`")
                else:
                    blocks.append(table_block(el))
                continue
            style, outline, num, p_left = p_info(el)
            if style in skips:
                continue
            segs, extra, images = para_text(el, links, chars)
            counts["image"] += images
            plain = render_segs(segs, emphasis=False)
            if plain:
                level = levels.get(style)
                if level is None and outline is not None:
                    level = outline + 1     # outlineLvl is 0-based: 0 == Heading-1 depth
                if level == -1:     # Subtitle → bold block (like pptx buNone sub-headings)
                    flush()
                    blocks.append("**" + _one_line(plain) + "**")
                elif level is not None:
                    # The bridge writes the document under a `# <title>` H1, so body
                    # headings start at H2: Title and Heading 1 → ##, deeper capped
                    # at 6. Headings render plain — they're already styled.
                    flush()
                    blocks.append("#" * min(max(level, 1) + 1, 6) + " " + _one_line(plain))
                elif num is not None:
                    nid, ilvl = num
                    fmt, left = nums.get((nid, str(ilvl)), ("", None))
                    if p_left is not None:      # direct indent beats the list's
                        left = p_left
                    # Effective depth: Word starts a NEW list (ilvl 0) for
                    # visually deep bullets, encoding the nesting only in the
                    # indent — 720 twips per level.
                    depth_lvl = ilvl if left is None else max(ilvl, left // 720 - 1)
                    marker = "1. " if fmt in _ORDERED_FMTS else "- "
                    cur.append("  " * depth_lvl + marker + _one_line(render_segs(segs)))
                elif cur and p_left is not None and p_left >= 720:
                    # List continuation: Word can split one visual bullet into a
                    # numPr'd paragraph plus indented plain follow-ons (no own
                    # marker) — fold those into the open item, don't detach them.
                    cur[-1] += " " + _one_line(render_segs(segs))
                else:
                    flush()
                    blocks.append(render_segs(segs))
            for line in extra:      # text-box content → its own blocks
                flush()
                blocks.append(line)
            if images:
                flush()
                blocks.extend(["_[image]_"] * images)
        flush()
        return False

    def table_block(tbl):
        """Render a w:tbl as one markdown pipe-table block.

        Cell paragraphs run through the full pipeline first, so emphasis,
        headings, and lists survive — then collapse to a single `<br>`-joined
        line per cell (see _cell_text). Tables stay tables: even Word's
        layout tables carry meaning in their grid, so the structure is kept
        and only the inside of each cell is flattened.
        """
        rows = []
        for tr in tbl:
            if local(tr.tag) != "tr":
                continue
            cells = []
            for tc in tr:
                if local(tc.tag) != "tc":
                    continue
                span = 1
                tcpr = child(tc, "tcPr")
                if tcpr is not None:
                    gs = child(tcpr, "gridSpan")
                    if gs is not None:
                        try:
                            span = max(1, int(attr(gs, "val")))
                        except (TypeError, ValueError):
                            span = 1
                cblocks = []
                walk_children(list(tc), 1, cblocks)
                cells.append(_cell_text(cblocks))
                # gridSpan: pad with empty cells so columns stay aligned. vMerge
                # continuation cells need no code — Word keeps them present-but-empty.
                cells.extend([" "] * (span - 1))
            rows.append(cells)
        if not rows:
            return "`[empty table]`"
        return "\n".join(_pipe_table(rows))

    blocks = []
    walk_children(body, 0, blocks)
    return blocks, counts


# --- Content-date detection (best-effort) --------------------------------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_YEAR_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{4})\b",
    re.IGNORECASE,
)


def _detect_date(text):
    """Return YYYY-MM-DD from a date signal in `text`, or None. Best-effort."""
    m = _ISO_RE.search(text)
    if m:
        return m.group(0)
    m = _MONTH_YEAR_RE.search(text)
    if m:
        mon = _MONTHS.get(m.group(1).lower()[:3])
        if mon:
            return "%s-%02d-01" % (m.group(2), mon)
    return None


# --- Public API -----------------------------------------------------------

def docx_to_markdown(path):
    """Extract a .docx to markdown. Returns a dict; raises DocxError on failure.

    Keys: markdown (body blocks joined by blank lines), words (int), title
    (dc:title or first heading, or None), content_date (YYYY-MM-DD or None),
    counts ({image, table}).
    """
    zf = _open_docx(path)
    try:
        levels, skips, chars = heading_levels(zf)
        nums = num_formats(zf)
        links = _ext_links(zf)
        root = _safe_read_xml(zf, "word/document.xml")
        blocks, counts = extract(root, levels, skips, nums, links, chars)
        if not blocks:
            raise DocxError("no readable text found — nothing to import.")
        markdown = "\n\n".join(blocks)
        if len(markdown) > _MAX_MARKDOWN:
            markdown = markdown[:_MAX_MARKDOWN] + "\n\n_[truncated: exceeded size cap]_"
        title, created = core_props(zf)
        if not title:
            first = next((b for b in blocks if b.startswith("#")), None)
            if first:
                title = first.lstrip("#").strip() or None
        # A date typed near the top of the document is the strongest content-date
        # signal (mirrors the pptx first-slide scan); file metadata is the fallback.
        content_date = _detect_date("\n".join(blocks[:15])) or created
        return {
            "markdown": markdown,
            "words": len(markdown.split()),
            "title": title,
            "content_date": content_date,
            "counts": counts,
        }
    finally:
        zf.close()


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python3 docx_extract.py <doc.docx>")
    try:
        res = docx_to_markdown(sys.argv[1])
    except DocxError as exc:
        sys.exit("error: %s" % exc)
    c = res["counts"]
    print("# %s\n" % os.path.basename(sys.argv[1]))
    print(res["markdown"])
    print("\n---\n")
    print(
        "**Summary:** %d words · %d images · %d tables"
        % (res["words"], c["image"], c["table"])
    )
    if res["content_date"]:
        print("_Detected content date: %s_" % res["content_date"])


if __name__ == "__main__":
    main()
