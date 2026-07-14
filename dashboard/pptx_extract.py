#!/usr/bin/env python3
"""pptx_extract — convert a .pptx to markdown using ONLY the Python standard library.

A .pptx is a ZIP of OOXML, so its content is mechanically extractable with no
model call. This walks slides in presentation order and, per shape, emits:
  * the slide title (`###`),
  * body text as markdown bullets, nested by outline level (explicit `lvl`, with
    a left-margin `marL` fallback),
  * tables as markdown tables,
  * native chart data (cached in `ppt/charts/chartN.xml`) as a table, values
    rounded to 2 decimals,
  * speaker notes,
  * inline `[image]` / `[diagram]` flags for non-text content.

Two entry points:
  * `pptx_to_markdown(path) -> dict` — used in-process by the dashboard bridge.
  * CLI: `python3 pptx_extract.py <deck.pptx>` — prints the full markdown, handy
    for testing/iterating on the extractor independently of the bridge.

SECURITY: the input is untrusted. Parsing is guarded against non-OOXML files,
zip bombs, and XML entity-expansion / XXE (a stdlib substitute for defusedxml);
all failures raise `PptxError`.
"""
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET


class PptxError(Exception):
    """Raised when a .pptx can't be safely or validly parsed."""


# --- Safety limits (input is an untrusted upload) -------------------------
_MAX_ENTRIES = 5000
_MAX_PART_UNCOMPRESSED = 50 * 1024 * 1024     # 50 MB per internal part
_MAX_TOTAL_UNCOMPRESSED = 300 * 1024 * 1024   # 300 MB total (zip-bomb guard)
_MAX_SLIDES = 500
_MAX_MARKDOWN = 8 * 1024 * 1024               # cap assembled output


def _open_pptx(path):
    """Open and validate a .pptx, returning an open ZipFile. Raises PptxError.

    Rejects non-Zip/OOXML files (incl. password-protected decks, which are OLE
    containers, not Zips) and obvious zip bombs before any part is read.
    """
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
    except OSError as exc:
        raise PptxError("cannot read file: %s" % exc)
    if magic != b"PK\x03\x04":
        raise PptxError(
            "not a PowerPoint (.pptx) file — it may be password-protected, an "
            "older .ppt, or corrupt."
        )
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise PptxError("corrupt or unreadable .pptx (bad Zip container).")
    try:
        names = set(zf.namelist())
        if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
            raise PptxError("not a PowerPoint presentation (missing OOXML parts).")
        infos = zf.infolist()
        if len(infos) > _MAX_ENTRIES:
            raise PptxError("too many entries in the .pptx (possible zip bomb).")
        total = 0
        for zi in infos:
            if zi.file_size > _MAX_PART_UNCOMPRESSED:
                raise PptxError("an internal part is too large (possible zip bomb).")
            total += zi.file_size
        if total > _MAX_TOTAL_UNCOMPRESSED:
            raise PptxError("uncompressed contents too large (possible zip bomb).")
    except PptxError:
        zf.close()
        raise
    return zf


def _safe_read_xml(zf, name):
    """Read a part and parse it as XML, guarding against entity-expansion / XXE.

    Valid OOXML parts never contain a DOCTYPE or entity definition, so scanning
    the raw bytes for them BEFORE `ET.fromstring` is a zero-false-positive guard
    that neutralises billion-laughs and external-entity attacks (the danger is at
    parse time, so the check must precede the parse). Raises PptxError.
    """
    try:
        data = zf.read(name)
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise PptxError("cannot read %s: %s" % (name, exc))
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise PptxError("unsafe XML (DOCTYPE/ENTITY) in %s" % name)
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise PptxError("malformed XML in %s: %s" % (name, exc))


# --- OOXML helpers --------------------------------------------------------

def local(tag):
    """Strip the XML namespace: '{...}t' -> 't' (so we can ignore namespaces)."""
    return tag.rsplit("}", 1)[-1]


def child(el, name):
    return next((c for c in el if local(c.tag) == name), None)


def para(p):
    """A paragraph -> (text, level, bullet_none, numbered, marL).

    `level` is the explicit outline level (`<a:pPr lvl=…>`). `marL` is the left
    margin in EMUs, used to *infer* levels when a deck indents by margin without
    setting lvl (see handle_sp).
    """
    runs = [t.text for t in p.iter() if local(t.tag) == "t" and t.text]
    text = "".join(runs).strip()
    lvl, none, numbered, marL = 0, False, False, None
    ppr = child(p, "pPr")
    if ppr is not None:
        lvl = int(ppr.get("lvl", "0") or 0)
        if ppr.get("marL"):
            try:
                marL = int(ppr.get("marL"))
            except ValueError:
                marL = None
        for c in ppr:
            lt = local(c.tag)
            if lt == "buNone":
                none = True
            elif lt == "buAutoNum":
                numbered = True
    return text, lvl, none, numbered, marL


def ph_type(sp):
    """Placeholder type of a shape ('title'/'ctrTitle'/'body'/…), or None."""
    for el in sp.iter():
        if local(el.tag) == "ph":
            return el.get("type", "body")
    return None


def render_table(tbl):
    rows = []
    for tr in tbl:
        if local(tr.tag) != "tr":
            continue
        cells = []
        for tc in tr:
            if local(tc.tag) != "tc":
                continue
            paras = [
                "".join(t.text for t in p.iter() if local(t.tag) == "t" and t.text).strip()
                for p in tc.iter()
                if local(p.tag) == "p"
            ]
            cell = " ".join(x for x in paras if x).replace("|", "\\|")
            cells.append(cell or " ")
        rows.append(cells)
    if not rows:
        return ["`[empty table]`"]
    ncol = max(len(r) for r in rows)
    rows = [r + [" "] * (ncol - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |",
           "| " + " | ".join(["---"] * ncol) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return out


def _esc(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def _num2(v):
    """Round a numeric chart value to 2 decimals (trailing zeros stripped).

    Excel-backed charts cache values as long doubles (e.g. 60.864322536754891).
    Non-numeric cells (`#N/A`, blanks) are returned unchanged.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return ("%.2f" % f).rstrip("0").rstrip(".")


def rels_for(zf, part):
    """Map relationship Id -> resolved part path for `part`'s .rels file."""
    d = os.path.dirname(part)
    rp = d + "/_rels/" + os.path.basename(part) + ".rels"
    if rp not in zf.namelist():
        return {}
    try:
        root = _safe_read_xml(zf, rp)
    except PptxError:
        return {}
    out = {}
    for r in root:
        if (r.get("TargetMode") or "") == "External":
            continue
        tgt = r.get("Target") or ""
        resolved = (
            tgt.lstrip("/")
            if tgt.startswith("/")
            else os.path.normpath(os.path.join(d, tgt)).replace("\\", "/")
        )
        out[r.get("Id")] = resolved
    return out


def _pts(el):
    """Extract [(idx, value)] from a chart <c:cat>/<c:val> number/string cache."""
    out = []
    for pt in el.iter():
        if local(pt.tag) == "pt":
            v = next((c.text for c in pt if local(c.tag) == "v"), None)
            out.append((int(pt.get("idx", "0") or 0), (v or "").strip()))
    out.sort()
    return out


def read_chart(zf, part):
    """Return (title, categories, series) cached inside a chartN.xml, or None.

    A native chart stores the data it plots, so the numbers behind the picture
    are recoverable: category (x-axis) labels + one value series per line/bar.
    """
    if not part or part not in zf.namelist():
        return None
    try:
        root = _safe_read_xml(zf, part)
    except PptxError:
        return None
    title = ""
    for el in root.iter():
        if local(el.tag) == "title":
            txt = [t.text for t in el.iter() if local(t.tag) in ("t", "v") and t.text]
            title = " ".join(txt).strip()
            break
    cats, series = [], []
    for ser in (e for e in root.iter() if local(e.tag) == "ser"):
        tx = next((c for c in ser if local(c.tag) == "tx"), None)
        name = ""
        if tx is not None:
            vs = [v.text for v in tx.iter() if local(v.tag) == "v" and v.text]
            name = (vs[0] if vs else "").strip()
        cat_el = next((c for c in ser if local(c.tag) == "cat"), None)
        if cat_el is not None and not cats:
            cats = [lbl for _i, lbl in _pts(cat_el)]
        val_el = next((c for c in ser if local(c.tag) == "val"), None)
        vals = {i: _num2(v) for i, v in _pts(val_el)} if val_el is not None else {}
        series.append((name, vals))
    if not series:
        return None
    return title, cats, series


def render_chart(title, cats, series):
    lines = ["_[chart: %s]_" % title if title else "_[chart]_"]
    if cats:
        lines.append("")  # blank line so the table parses cleanly under the caption
        header = ["Series"] + list(cats)
        lines.append("| " + " | ".join(_esc(h) for h in header) + " |")
        lines.append("| " + " | ".join(["---"] * len(header)) + " |")
        for name, vmap in series:
            row = [name or "series"] + [vmap.get(i, "") for i in range(len(cats))]
            lines.append("| " + " | ".join(_esc(x) for x in row) + " |")
    else:  # no categories (e.g. scatter/pie edge cases) — list series values
        for name, vmap in series:
            vals = ", ".join(_esc(vmap[i]) for i in sorted(vmap))
            lines.append("- %s: %s" % (_esc(name or "series"), vals))
    return lines


def chart_block(gf, zf, rels):
    """Resolve a chart graphicFrame's data via its r:id, or fall back to a flag."""
    cel = next(
        (e for e in gf.iter()
         if local(e.tag) == "chart" and any(local(k) == "id" for k in e.attrib)),
        None,
    )
    rid = (
        next((v for k, v in cel.attrib.items() if local(k) == "id"), None)
        if cel is not None
        else None
    )
    data = read_chart(zf, rels.get(rid)) if rid else None
    return render_chart(*data) if data else ["_[chart]_"]


def extract(root, zf, rels):
    """Walk a slide tree. Return (markdown_blocks, counts).

    Each block is a self-contained markdown unit (a heading, one tight bullet
    list, a table, or an `[image]`/`[chart]` marker). The caller joins blocks
    with blank lines, so text, sub-headings and lists never run together.
    """
    blocks = []
    counts = {"image": 0, "chart": 0, "diagram": 0, "table": 0}

    def handle_sp(sp):
        t = ph_type(sp)
        if t in ("sldNum", "ftr", "dt"):  # slide-number / footer / date chrome — skip
            return
        tb = child(sp, "txBody")
        if tb is None:
            return
        paras = [para(p) for p in tb if local(p.tag) == "p"]
        paras = [pp for pp in paras if pp[0] and not pp[0].isdigit()]
        if not paras:
            return
        if t in ("title", "ctrTitle"):
            blocks.append("### " + " ".join(p[0] for p in paras))
            return
        # Bullet nesting: prefer explicit lvl; else rank left margins — but ONLY
        # among bulleted paragraphs, so a non-bulleted header at margin 0 doesn't
        # push the bullets beneath it down an extra level.
        bulleted = [pp for pp in paras if not pp[2]]
        use_marL = bool(bulleted) and not any(pp[1] for pp in bulleted)
        rank = {}
        if use_marL:
            rank = {m: i for i, m in enumerate(sorted({(pp[4] or 0) for pp in bulleted}))}
        cur = []  # a run of consecutive bullets = one tight list block

        def flush():
            if cur:
                blocks.append("\n".join(cur))
                cur.clear()

        for text, lvl, none, numbered, marL in paras:
            if none:  # non-bulleted paragraph → a sub-heading, its own block
                flush()
                blocks.append("**" + text + "**")
                continue
            level = rank[(marL or 0)] if use_marL else lvl
            cur.append("  " * max(0, level) + ("1. " if numbered else "- ") + text)
        flush()

    def walk(el):
        tag = local(el.tag)
        if tag == "sp":
            handle_sp(el)
            return
        if tag == "pic":
            counts["image"] += 1
            blocks.append("_[image]_")
            return
        if tag == "graphicFrame":
            tbl = next((e for e in el.iter() if local(e.tag) == "tbl"), None)
            if tbl is not None:
                counts["table"] += 1
                blocks.append("\n".join(render_table(tbl)))
                return
            uri = " ".join(g.get("uri", "") for g in el.iter() if local(g.tag) == "graphicData")
            if "chart" in uri:
                counts["chart"] += 1
                blocks.append("\n".join(chart_block(el, zf, rels)))
            elif "diagram" in uri or "smartArt" in uri:
                counts["diagram"] += 1
                blocks.append("_[diagram]_")
            return
        if tag == "tbl":
            counts["table"] += 1
            blocks.append("\n".join(render_table(el)))
            return
        for c in el:
            walk(c)

    walk(root)
    return blocks, counts


def plain_text(root):
    """Flat paragraph text (used for speaker notes), dropping bare page numbers."""
    out = []
    for p in root.iter():
        if local(p.tag) == "p":
            txt = "".join(t.text for t in p.iter() if local(t.tag) == "t" and t.text).strip()
            if txt and not txt.isdigit():
                out.append(txt)
    return out


def slide_order(zf):
    """Slide part names in presentation order; fall back to numeric filename sort."""
    try:
        pres = _safe_read_xml(zf, "ppt/presentation.xml")
        rels = _safe_read_xml(zf, "ppt/_rels/presentation.xml.rels")
        rid = {r.get("Id"): r.get("Target") for r in rels}
        order = []
        for el in pres.iter():
            if local(el.tag) == "sldId":
                ref = next((v for k, v in el.attrib.items() if local(k) == "id"), None)
                tgt = rid.get(ref)
                if tgt:
                    name = os.path.normpath("ppt/" + tgt.lstrip("/")).replace("\\", "/")
                    if name in zf.namelist():
                        order.append(name)
        if order:
            return order
    except PptxError:
        pass
    names = [n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
    return sorted(names, key=lambda n: int(re.search(r"(\d+)", n).group(1)))


def notes_part(zf, slide):
    rp = "ppt/slides/_rels/%s.rels" % slide.split("/")[-1]
    if rp not in zf.namelist():
        return None
    try:
        rels = _safe_read_xml(zf, rp)
    except PptxError:
        return None
    for r in rels:
        if "notesSlide" in (r.get("Type") or ""):
            name = os.path.normpath("ppt/slides/" + r.get("Target")).replace("\\", "/")
            if name in zf.namelist():
                return name
    return None


# --- Content-date detection (best-effort) --------------------------------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_DAY_YEAR_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})[,\s]+(\d{4})\b",
    re.IGNORECASE,
)
_MONTH_YEAR_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{4})\b",
    re.IGNORECASE,
)


def _detect_date(text):
    """Return YYYY-MM-DD from a date signal in `text`, or None. Best-effort."""
    m = _ISO_RE.search(text)
    if m:
        return m.group(0)
    m = _MONTH_DAY_YEAR_RE.search(text)
    if m:
        mon = _MONTHS.get(m.group(1).lower()[:3])
        if mon:
            return "%s-%02d-%02d" % (m.group(3), mon, int(m.group(2)))
    m = _MONTH_YEAR_RE.search(text)
    if m:
        mon = _MONTHS.get(m.group(1).lower()[:3])
        if mon:
            return "%s-%02d-01" % (m.group(2), mon)
    return None


# --- Public API -----------------------------------------------------------

def pptx_to_markdown(path):
    """Extract a .pptx to markdown. Returns a dict; raises PptxError on failure.

    Keys: markdown (slide sections joined by blank lines), slides (int),
    title (first-slide title or None), content_date (YYYY-MM-DD or None),
    counts ({image, chart, diagram, table}).
    """
    zf = _open_pptx(path)
    try:
        slides = slide_order(zf)
        if not slides:
            raise PptxError("no slides found — nothing to import.")
        slides = slides[:_MAX_SLIDES]
        sections = []
        tot = {"image": 0, "chart": 0, "diagram": 0, "table": 0}
        title = None
        content_date = None
        for i, name in enumerate(slides, 1):
            root = _safe_read_xml(zf, name)
            rels = rels_for(zf, name)
            blocks, counts = extract(root, zf, rels)
            section = ["## Slide %d" % i]
            section.extend(blocks or ["_(no text)_"])
            nf = notes_part(zf, name)
            if nf:
                try:
                    notes = plain_text(_safe_read_xml(zf, nf))
                except PptxError:
                    notes = []
                if notes:
                    section.append("> **Notes:** " + " ".join(notes))
            sections.append("\n\n".join(section))
            for k in tot:
                tot[k] += counts[k]
            if i == 1:
                if blocks and blocks[0].startswith("### "):
                    title = blocks[0][4:].strip()
                content_date = _detect_date("\n".join(blocks))
        markdown = "\n\n".join(sections)
        if len(markdown) > _MAX_MARKDOWN:
            markdown = markdown[:_MAX_MARKDOWN] + "\n\n_[truncated: exceeded size cap]_"
        return {
            "markdown": markdown,
            "slides": len(slides),
            "title": title,
            "content_date": content_date,
            "counts": tot,
        }
    finally:
        zf.close()


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python3 pptx_extract.py <deck.pptx>")
    try:
        res = pptx_to_markdown(sys.argv[1])
    except PptxError as exc:
        sys.exit("error: %s" % exc)
    c = res["counts"]
    print("# %s\n" % os.path.basename(sys.argv[1]))
    print(res["markdown"])
    print("\n---\n")
    print(
        "**Summary:** %d slides · %d images · %d charts · %d diagrams · %d tables"
        % (res["slides"], c["image"], c["chart"], c["diagram"], c["table"])
    )
    if res["content_date"]:
        print("_Detected content date: %s_" % res["content_date"])


if __name__ == "__main__":
    main()
