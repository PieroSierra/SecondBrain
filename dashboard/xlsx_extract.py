#!/usr/bin/env python3
"""xlsx_extract — convert an Excel workbook (.xlsx/.xlsm) or a .csv to markdown
using ONLY the Python standard library.

A workbook is a ZIP of OOXML (SpreadsheetML), so its content is mechanically
extractable with no model call. This walks visible sheets in workbook order and
emits one markdown pipe table per sheet (`## Sheet: <name>` headings), with:
  * shared strings and inline strings resolved (rich-text runs concatenated;
    phonetic/furigana blocks excluded),
  * formula cells rendered as their cached VALUE, never as formula text,
  * date/time serials converted to ISO dates via the cell's number format
    (styles.xml, incl. the date1904 workbook flag), percent formats ×100,
  * float noise cleaned (0.30000000000000004 → 0.3), booleans as TRUE/FALSE,
    error cells (#DIV/0! …) verbatim,
  * scale caps with exact counts: first 200 non-empty rows and 40 columns per
    sheet, 100 sheets, each truncation flagged (`_[truncated: N more rows…]_`).

Not extracted (lossy by design): charts, drawings, cell formatting, comments,
hidden sheets (skipped with a note), macros (vbaProject.bin is never read), and
merged-cell geometry — the value sits in the range's top-left cell, covered
cells render empty.

`.csv` is handled by the same module (`csv_to_markdown`): delimiter sniffed,
utf-8/latin-1 decoded, same caps and table rendering.

Two entry points:
  * `xlsx_to_markdown(path)` / `csv_to_markdown(path)` -> dict — used
    in-process by the dashboard bridge.
  * CLI: `python3 xlsx_extract.py <book.xlsx|data.csv>` — prints the full
    markdown, handy for testing the extractor independently of the bridge.

SECURITY: the input is untrusted. Parsing is guarded against non-OOXML files,
zip bombs, and XML entity-expansion / XXE (a stdlib substitute for defusedxml);
all failures raise `XlsxError`.
"""
import csv
import os
import posixpath
import re
import sys
import zipfile
from datetime import date, timedelta
from xml.etree import ElementTree as ET


class XlsxError(Exception):
    """Raised when a workbook/CSV can't be safely or validly parsed."""


# --- Safety limits (input is an untrusted upload) -------------------------
_MAX_ENTRIES = 5000
_MAX_PART_UNCOMPRESSED = 50 * 1024 * 1024     # per DOM-parsed part (workbook/styles/…)
_MAX_STREAM_PART = 512 * 1024 * 1024          # per streamed part (worksheets, sharedStrings)
_MAX_ROWS = 200                               # non-empty data rows per sheet
_MAX_COLS = 40                                # columns per sheet
_MAX_SHEETS = 100                             # visible sheets emitted
_MAX_CSV_BYTES = 500 * 1024 * 1024            # whole-file cap for .csv
_MAX_MARKDOWN = 8 * 1024 * 1024               # cap assembled output


def _open_xlsx(path):
    """Open and validate a workbook, returning an open ZipFile. Raises XlsxError.

    Rejects non-Zip/OOXML files (incl. password-protected workbooks, which are
    OLE containers, not Zips). Unlike the pptx/docx openers there is no
    whole-archive size cap: real workbooks legitimately carry 100MB+ worksheet
    and pivot-cache parts, and only the parts actually read are ever
    decompressed — each guarded at read time (_safe_read_xml) or stream time
    (_GuardedStream), so unread parts cost nothing.
    """
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
    except OSError as exc:
        raise XlsxError("cannot read file: %s" % exc)
    if magic != b"PK\x03\x04":
        raise XlsxError(
            "not an Excel (.xlsx) file — it may be password-protected, an "
            "older .xls, or corrupt."
        )
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise XlsxError("corrupt or unreadable workbook (bad Zip container).")
    try:
        names = set(zf.namelist())
        if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
            raise XlsxError("not an Excel workbook (missing OOXML parts).")
        if len(zf.infolist()) > _MAX_ENTRIES:
            raise XlsxError("too many entries in the workbook (possible zip bomb).")
    except XlsxError:
        zf.close()
        raise
    return zf


def _safe_read_xml(zf, name):
    """Read a small part and parse it as XML, guarding against XXE.

    Valid OOXML parts never contain a DOCTYPE or entity definition, so scanning
    the raw bytes for them BEFORE `ET.fromstring` is a zero-false-positive guard
    that neutralises billion-laughs and external-entity attacks (the danger is at
    parse time, so the check must precede the parse). The claimed size is capped
    first — big parts (worksheets, sharedStrings) go through _GuardedStream
    instead. Raises XlsxError.
    """
    try:
        if zf.getinfo(name).file_size > _MAX_PART_UNCOMPRESSED:
            raise XlsxError("part %s is too large to parse." % name)
        data = zf.read(name)
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise XlsxError("cannot read %s: %s" % (name, exc))
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise XlsxError("unsafe XML (DOCTYPE/ENTITY) in %s" % name)
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise XlsxError("malformed XML in %s: %s" % (name, exc))


class _GuardedStream:
    """File-like wrapper for streamed parts (fed to ET.iterparse).

    Enforces the streaming byte cap against the DECOMPRESSED flow (so a zip
    header that lies about its size still can't bomb us) and scans every chunk
    for DOCTYPE/ENTITY — the same XXE / billion-laughs guard as _safe_read_xml,
    carrying a small tail so a pattern can't hide across a chunk boundary.
    """
    _BAD = (b"<!DOCTYPE", b"<!ENTITY")

    def __init__(self, fh, name):
        self._fh, self._name = fh, name
        self._left = _MAX_STREAM_PART
        self._tail = b""

    def read(self, n=-1):
        if n is None or n < 0:
            n = 1 << 20
        data = self._fh.read(n)
        self._left -= len(data)
        if self._left < 0:
            raise XlsxError("part %s exceeds the streaming size cap." % self._name)
        probe = self._tail + data
        for bad in self._BAD:
            if bad in probe:
                raise XlsxError("unsafe XML (DOCTYPE/ENTITY) in %s" % self._name)
        self._tail = probe[-16:]
        return data

    def close(self):
        try:
            self._fh.close()
        except OSError:
            pass


def _stream_part(zf, name):
    try:
        return _GuardedStream(zf.open(name), name)
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise XlsxError("cannot read %s: %s" % (name, exc))


# --- OOXML helpers --------------------------------------------------------

def local(tag):
    """Strip the XML namespace: '{...}t' -> 't' (so we can ignore namespaces)."""
    return tag.rsplit("}", 1)[-1]


def child(el, name):
    return next((c for c in el if local(c.tag) == name), None)


def attr(el, name):
    """Namespaced attribute lookup — SpreadsheetML attrs are mostly bare
    (`el.get` works), but a sheet's `r:id` carries the relationship prefix."""
    for k, v in el.attrib.items():
        if local(k) == name:
            return v
    return None


def _pipe_table(rows):
    """Assemble rows of one-line cell strings into a markdown pipe table.

    `:---` (not `---`) — markdown makes the first row a <th> header, which
    browsers center by default; explicit left alignment keeps sheets looking
    like sheets.
    """
    ncol = max(len(r) for r in rows)
    rows = [r + [" "] * (ncol - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |",
           "| " + " | ".join([":---"] * ncol) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return out


def _sanitize(s):
    """One pipe-table-safe line: newlines → <br>, pipes escaped."""
    return s.replace("\r", "").replace("\n", "<br>").replace("|", "\\|").strip()


# --- Workbook tables (each degrades to empty on failure) -------------------

def _wb_rels(zf):
    """Map r:id -> internal worksheet part path from xl/_rels/workbook.xml.rels.

    The inverse twist on the docx extractor's `_ext_links`: here only INTERNAL
    targets matter (worksheet parts); external targets are skipped.
    """
    rp = "xl/_rels/workbook.xml.rels"
    if rp not in zf.namelist():
        return {}
    try:
        root = _safe_read_xml(zf, rp)
    except XlsxError:
        return {}
    out = {}
    for r in root:
        if (r.get("TargetMode") or "") == "External":
            continue
        rid, tgt = r.get("Id"), r.get("Target") or ""
        if not rid or not tgt:
            continue
        out[rid] = tgt.lstrip("/") if tgt.startswith("/") else posixpath.normpath("xl/" + tgt)
    return out


def _si_text(el):
    """Text of an <si>/<is>: direct <t> plus <r>-run <t>s, concatenated.
    <rPh>/<phoneticPr> (furigana) blocks are never entered."""
    if el is None:
        return ""
    parts = []
    for c in el:
        lt = local(c.tag)
        if lt == "t":
            parts.append(c.text or "")
        elif lt == "r":
            t = child(c, "t")
            if t is not None:
                parts.append(t.text or "")
    return "".join(parts)


def _shared_strings(zf):
    """The shared-string table as a list (cells reference it by index).

    Streamed with iterparse — real workbooks carry 80MB+ string tables, and a
    DOM parse would multiply that in memory. The root is cleared after each
    <si> so memory stays flat; only the extracted strings are kept.
    """
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    out = []
    src = _stream_part(zf, "xl/sharedStrings.xml")
    try:
        root = None
        for event, el in ET.iterparse(src, events=("start", "end")):
            if event == "start":
                if root is None:
                    root = el
                continue
            if local(el.tag) == "si":
                out.append(_si_text(el))
                if root is not None:
                    root.clear()    # drop processed <si> subtrees
    except ET.ParseError:
        pass    # damaged table — keep what was recovered; cells degrade to ""
    finally:
        src.close()
    return out


# --- Number formats: dates and percents ------------------------------------

# Builtin numFmtIds that render as dates/times (ECMA-376 §18.8.30).
_DATE_FMT_IDS = (
    frozenset(range(14, 23)) | frozenset(range(27, 37))
    | frozenset(range(45, 48)) | frozenset(range(50, 59))
)
_PERCENT_FMT_IDS = frozenset({9, 10})
# Strips quoted literals, [..] sections, and \-escapes from a format code so
# date-letter / percent tests only see live format tokens.
_FMT_STRIP_RE = re.compile(r'"[^"]*"|\[[^\]]*\]|\\.')


def _styles(zf):
    """Per-cellXf (is_date, is_percent), indexed by a cell's `s` attribute.

    A date in xlsx is just a number plus a date-looking format, so this table
    is what makes `45123` render as `2023-07-16`. Missing or unparseable
    styles.xml degrades to [] — serials then stay raw numbers, not an error.
    """
    if "xl/styles.xml" not in zf.namelist():
        return []
    try:
        root = _safe_read_xml(zf, "xl/styles.xml")
    except XlsxError:
        return []
    custom = {}
    numfmts = child(root, "numFmts")
    if numfmts is not None:
        for nf in numfmts:
            if local(nf.tag) == "numFmt" and nf.get("numFmtId"):
                custom[nf.get("numFmtId")] = nf.get("formatCode") or ""
    out = []
    cellxfs = child(root, "cellXfs")
    if cellxfs is not None:
        for xf in cellxfs:
            if local(xf.tag) != "xf":
                continue
            fid = xf.get("numFmtId") or "0"
            try:
                n = int(fid)
            except ValueError:
                n = 0
            is_date = n in _DATE_FMT_IDS
            is_pct = n in _PERCENT_FMT_IDS
            code = custom.get(fid)
            if code is not None:
                stripped = _FMT_STRIP_RE.sub("", code)
                if not is_date:
                    # y/m/d/h/s tokens with no 0/#/? number placeholders → date.
                    is_date = bool(re.search(r"[ymdhs]", stripped, re.I)) and not re.search(r"[0#?]", stripped)
                if not is_pct:
                    is_pct = "%" in stripped
            out.append((is_date, is_pct))
    return out


def _serial_to_text(raw, date1904):
    """Excel date serial -> 'YYYY-MM-DD' (or 'HH:MM' for pure time), else None.

    Epoch 1899-12-30 absorbs Excel's phantom 1900-02-29 for serials >= 61 (the
    accepted cost: a 1-day error for genuine Jan/Feb 1900 dates); date1904
    workbooks use 1904-01-01. Fractions (time of day) are dropped unless the
    fraction IS the value. Out-of-range serials fall back to plain numbers.
    """
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    if 0 < f < 1:   # pure time of day
        secs = int(round(f * 86400))
        return "%02d:%02d" % (secs // 3600 % 24, secs % 3600 // 60)
    days = int(f)
    if days < 0 or days > 2958465:      # 9999-12-31
        return None
    epoch = date(1904, 1, 1) if date1904 else date(1899, 12, 30)
    try:
        return (epoch + timedelta(days=days)).isoformat()
    except OverflowError:
        return None


def _fmt_num(raw, is_percent=False):
    """Clean cached numerics: 0.30000000000000004 → 0.3, integral → no decimals,
    percent formats ×100 with a % suffix. Unparsable text passes through."""
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return raw or ""
    if is_percent:
        f *= 100.0
    if f == int(f) and abs(f) < 1e15:
        s = "%d" % int(f)
    else:
        s = "%.10g" % f
    return s + "%" if is_percent else s


# --- Cell / sheet extraction ------------------------------------------------

_COL_REF_RE = re.compile(r"^([A-Za-z]+)\d*$")


def _col_index(ref):
    """'B3' -> 1, 'AA1' -> 26 (letters folded base-26); None if unparsable."""
    m = _COL_REF_RE.match(ref or "")
    if not m:
        return None
    idx = 0
    for ch in m.group(1).upper():
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def _cell_value(c, shared, styles, date1904):
    """A cell's display text: type dispatch on `t`, then date/percent/number
    shaping via the cell's style for plain numerics."""
    t = c.get("t") or ""
    if t == "s":        # shared string
        v = child(c, "v")
        try:
            return shared[int((v.text or "").strip())] if v is not None else ""
        except (ValueError, IndexError):
            return ""
    if t == "inlineStr":
        return _si_text(child(c, "is"))
    v = child(c, "v")
    raw = (v.text or "") if v is not None else ""
    if t == "str":      # cached formula string result
        return raw
    if t == "b":
        return "TRUE" if raw.strip() == "1" else "FALSE"
    if t == "e":        # #DIV/0!, #N/A, … — the error IS the value
        return raw
    if t == "d":        # rare ISO-date cell type
        return raw.strip()[:10]
    if not raw.strip():
        return ""
    is_date = is_pct = False
    s = c.get("s")
    if s is not None:
        try:
            is_date, is_pct = styles[int(s)]
        except (ValueError, IndexError):
            pass
    if is_date:
        d = _serial_to_text(raw, date1904)
        if d is not None:
            return d
    return _fmt_num(raw, is_pct)


def _row_has_data(row_el):
    """Cheap non-empty test for rows past the cap (counts real truncation)."""
    for c in row_el:
        if local(c.tag) != "c":
            continue
        for ch in c:
            lt = local(ch.tag)
            if lt == "v" and (ch.text or "").strip():
                return True
            if lt == "is":
                return True
    return False


def _sheet_grid(zf, part, shared, styles, date1904):
    """One worksheet -> (grid, more_rows, dropped_cols).

    grid is a list of equal-length cell-string rows: up to _MAX_ROWS non-empty
    rows, columns beyond _MAX_COLS dropped, width = max used column (ignores
    the `dimension` attr, which routinely lies about trailing ghost columns).
    Fully-empty rows are skipped — a pipe table has no vertical whitespace, so
    they'd render as noise and burn the row budget.

    Streamed with iterparse: worksheet parts run to 100MB+ in real workbooks,
    so the tree is never fully materialised — <sheetData> is cleared after each
    row, and past the row cap remaining rows are only counted, not built.
    """
    rows = []
    more = 0
    max_dropped = -1
    src = _stream_part(zf, part)
    try:
        sheetdata = None
        for event, el in ET.iterparse(src, events=("start", "end")):
            if event == "start":
                if sheetdata is None and local(el.tag) == "sheetData":
                    sheetdata = el
                continue
            if local(el.tag) != "row":
                continue
            if len(rows) >= _MAX_ROWS:
                if _row_has_data(el):
                    more += 1
            else:
                cells = {}
                nextcol = 0
                for c in el:
                    if local(c.tag) != "c":
                        continue
                    ci = _col_index(c.get("r") or "")
                    if ci is None:  # no ref — Excel omits it for consecutive cells
                        ci = nextcol
                    nextcol = ci + 1
                    txt = _cell_value(c, shared, styles, date1904)
                    if not txt.strip():
                        continue    # empties render via grid padding
                    if ci >= _MAX_COLS:
                        max_dropped = max(max_dropped, ci)
                        continue
                    cells[ci] = _sanitize(txt)
                if cells:
                    rows.append(cells)
            if sheetdata is not None:
                sheetdata.clear()   # drop processed rows; parser keeps appending
    except ET.ParseError as exc:
        raise XlsxError("malformed XML in %s: %s" % (part, exc))
    finally:
        src.close()
    if not rows:
        return [], more, 0
    ncols = max(max(r) for r in rows) + 1
    grid = [[r.get(i, " ") for i in range(ncols)] for r in rows]
    dropped = (max_dropped + 1 - _MAX_COLS) if max_dropped >= _MAX_COLS else 0
    return grid, more, dropped


# --- Metadata ---------------------------------------------------------------

def core_props(zf):
    """(dc:title, dcterms:created as YYYY-MM-DD) from docProps/core.xml.

    Best-effort; degrades to (None, None). `dcterms:modified` is deliberately
    ignored — it drifts toward "today", which the import date already records.
    """
    if "docProps/core.xml" not in zf.namelist():
        return None, None
    try:
        root = _safe_read_xml(zf, "docProps/core.xml")
    except XlsxError:
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

def xlsx_to_markdown(path):
    """Extract a workbook to markdown. Returns a dict; raises XlsxError on failure.

    Keys: markdown (one pipe table per visible sheet), sheets (int, visible),
    rows (int, table rows emitted incl. headers), title (dc:title or None),
    content_date (YYYY-MM-DD or None), counts ({hidden_sheets, truncated_sheets}).
    """
    zf = _open_xlsx(path)
    try:
        wb = _safe_read_xml(zf, "xl/workbook.xml")
        pr = child(wb, "workbookPr")
        date1904 = pr is not None and (pr.get("date1904") or "").lower() in ("1", "true")
        rels = _wb_rels(zf)
        shared = _shared_strings(zf)
        styles = _styles(zf)
        names = set(zf.namelist())

        visible, hidden = [], 0
        sheets_el = child(wb, "sheets")
        for sh in (sheets_el if sheets_el is not None else ()):
            if local(sh.tag) != "sheet":
                continue
            if (sh.get("state") or "") in ("hidden", "veryHidden"):
                hidden += 1
                continue
            name = (sh.get("name") or "Sheet").strip() or "Sheet"
            part = rels.get(attr(sh, "id"))
            if not part or part not in names:
                fallback = "xl/worksheets/sheet%s.xml" % (sh.get("sheetId") or "")
                part = fallback if fallback in names else None
            if part:
                visible.append((name, part))
        skipped_sheets = max(0, len(visible) - _MAX_SHEETS)
        visible = visible[:_MAX_SHEETS]

        # A lone default-named sheet ("Sheet1") adds nothing under the bridge's
        # `# <title>` H1; a meaningful lone name ("Q3 Budget") is kept.
        solo_default = len(visible) == 1 and re.match(r"^sheet\d*$", visible[0][0], re.I)

        blocks = []
        total_rows = 0
        truncated_sheets = 0
        for name, part in visible:
            grid, more, dropped = _sheet_grid(zf, part, shared, styles, date1904)
            if not solo_default:
                blocks.append("## Sheet: " + re.sub(r"[|\n]+", " ", name).strip())
            if not grid:
                blocks.append("_[empty sheet]_")
                continue
            total_rows += len(grid)
            blocks.append("\n".join(_pipe_table(grid)))
            if more:
                blocks.append("_[truncated: {:,} more rows not shown]_".format(more))
            if dropped:
                blocks.append("_[+%d more columns not shown]_" % dropped)
            if more or dropped:
                truncated_sheets += 1
        if hidden:
            blocks.append("_[%d hidden sheet%s skipped]_" % (hidden, "" if hidden == 1 else "s"))
        if skipped_sheets:
            blocks.append("_[+%d more sheets not shown]_" % skipped_sheets)
        if total_rows == 0:
            raise XlsxError("no readable cell data found — nothing to import.")

        markdown = "\n\n".join(blocks)
        if len(markdown) > _MAX_MARKDOWN:
            markdown = markdown[:_MAX_MARKDOWN] + "\n\n_[truncated: exceeded size cap]_"
        title, created = core_props(zf)
        # A date in the first sheet's top rows is the strongest content-date
        # signal (ISO-rendered date cells feed this for free); file metadata
        # is the fallback.
        head = "\n".join(markdown.splitlines()[:12])
        content_date = _detect_date(head) or created
        return {
            "markdown": markdown,
            "sheets": len(visible),
            "rows": total_rows,
            "title": title,
            "content_date": content_date,
            "counts": {"hidden_sheets": hidden, "truncated_sheets": truncated_sheets},
        }
    finally:
        zf.close()


def _csv_rows(path, encoding):
    """One streamed pass over a CSV: (rows ≤ caps, more_rows, dropped_cols).
    Raises UnicodeDecodeError (caller retries latin-1) or OSError."""
    with open(path, "r", encoding=encoding, newline="") as fh:
        try:
            dialect = csv.Sniffer().sniff(fh.read(4096), delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel     # comma
        fh.seek(0)
        rows, more, dropped = [], 0, 0
        for rec in csv.reader(fh, dialect):
            if not any(cell.strip() for cell in rec):
                continue
            if len(rows) >= _MAX_ROWS:
                more += 1
                continue
            if len(rec) > _MAX_COLS:
                dropped = max(dropped, len(rec) - _MAX_COLS)
                rec = rec[:_MAX_COLS]
            rows.append([_sanitize(cell) or " " for cell in rec])
    return rows, more, dropped


def csv_to_markdown(path):
    """Extract a .csv to markdown (one pipe table). Same return shape as
    xlsx_to_markdown (`sheets: 1`) so the bridge has a single code path.

    Streamed — rows past the cap are counted, not kept — so a 100MB export
    imports fine; the size guard only rejects the truly pathological."""
    try:
        if os.path.getsize(path) > _MAX_CSV_BYTES:
            raise XlsxError("CSV file too large (over 500 MB).")
        try:
            rows, more, dropped = _csv_rows(path, "utf-8-sig")
        except UnicodeDecodeError:
            rows, more, dropped = _csv_rows(path, "latin-1")    # never fails
    except OSError as exc:
        raise XlsxError("cannot read file: %s" % exc)
    if not rows:
        raise XlsxError("empty CSV — nothing to import.")
    blocks = ["\n".join(_pipe_table(rows))]
    if more:
        blocks.append("_[truncated: {:,} more rows not shown]_".format(more))
    if dropped:
        blocks.append("_[+%d more columns not shown]_" % dropped)
    markdown = "\n\n".join(blocks)
    if len(markdown) > _MAX_MARKDOWN:
        markdown = markdown[:_MAX_MARKDOWN] + "\n\n_[truncated: exceeded size cap]_"
    content_date = _detect_date("\n".join(" ".join(r) for r in rows[:10]))
    return {
        "markdown": markdown,
        "sheets": 1,
        "rows": len(rows),
        "title": None,
        "content_date": content_date,
        "counts": {"hidden_sheets": 0, "truncated_sheets": 1 if (more or dropped) else 0},
    }


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python3 xlsx_extract.py <book.xlsx|book.xlsm|data.csv>")
    path = sys.argv[1]
    fn = csv_to_markdown if path.lower().endswith(".csv") else xlsx_to_markdown
    try:
        res = fn(path)
    except XlsxError as exc:
        sys.exit("error: %s" % exc)
    c = res["counts"]
    print("# %s\n" % os.path.basename(path))
    print(res["markdown"])
    print("\n---\n")
    print(
        "**Summary:** %d sheet%s · %d rows · %d hidden sheet%s skipped"
        % (res["sheets"], "" if res["sheets"] == 1 else "s",
           res["rows"], c["hidden_sheets"], "" if c["hidden_sheets"] == 1 else "s")
    )
    if res["content_date"]:
        print("_Detected content date: %s_" % res["content_date"])


if __name__ == "__main__":
    main()
