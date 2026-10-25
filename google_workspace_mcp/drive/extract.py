"""Text from PDF and Office files, so an agent can read them without a local copy.

Each extractor takes the file's bytes and returns plain text with a marker
before every page, slide or sheet (``[page 3]``, ``[slide 3]``,
``[sheet Budget]``), so answers can cite where a passage came from.
``pages`` limits PDF pages and pptx slides to a range such as ``"3-7"`` or
``"1,4,9-10"``.

Office files are zip archives of XML. They are parsed with the standard
library; any XML part that declares a DOCTYPE is refused, since OOXML never
needs one and a DTD is how entity-expansion attacks arrive.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Optional
from xml.etree import ElementTree

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXTRACTABLE = {PDF_MIME, DOCX_MIME, PPTX_MIME, XLSX_MIME}

# Zip-bomb guard: total uncompressed XML read from one Office file.
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_SHEET_ROWS = 20_000

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def parse_pages(spec: Optional[str], total: int) -> list[int]:
    """1-based page numbers from ``"3-7"`` / ``"1,4,9-10"``; all pages when spec is empty."""
    if not spec or not spec.strip():
        return list(range(1, total + 1))
    chosen: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", part)
        if not m:
            raise ValueError(f"pages must look like '3-7' or '1,4,9-10'; got {spec!r}")
        lo = int(m.group(1))
        hi = int(m.group(2) or lo)
        if lo < 1 or hi < lo:
            raise ValueError(f"bad page range {part!r}")
        chosen.update(range(lo, min(hi, total) + 1))
    if not chosen:
        raise ValueError(f"pages {spec!r} are outside 1-{total}")
    return sorted(chosen)


def extract(data: bytes, mime: str, pages: Optional[str] = None) -> dict:
    """Return ``{"text", "unit", "total", "selected"}`` for one file's bytes."""
    if mime == PDF_MIME:
        return _pdf(data, pages)
    if mime == PPTX_MIME:
        return _pptx(data, pages)
    if pages:
        raise ValueError("pages applies only to PDF files and presentations")
    if mime == DOCX_MIME:
        return _docx(data)
    if mime == XLSX_MIME:
        return _xlsx(data)
    raise ValueError(f"cannot extract text from {mime!r}")


# --- PDF ---------------------------------------------------------------------

def _pdf(data: bytes, pages: Optional[str]) -> dict:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # Many PDFs are encrypted with an empty user password (print/copy limits only).
            try:
                opened = reader.decrypt("")
            except Exception as e:  # noqa: BLE001 - pypdf raises several types
                raise ValueError("PDF is password-protected") from e
            if not opened:
                raise ValueError("PDF is password-protected")
        total = len(reader.pages)
        chosen = parse_pages(pages, total)
        out = []
        found = False
        for n in chosen:
            text = (reader.pages[n - 1].extract_text() or "").strip()
            found = found or bool(text)
            out.append(f"[page {n}]\n{text}")
    except PdfReadError as e:
        raise ValueError(f"unreadable PDF: {e}") from e
    text = "\n\n".join(out)
    if chosen and not found:
        text += "\n\n(No text layer: these pages are probably scanned images.)"
    return {"text": text, "unit": "page", "total": total, "selected": chosen}


# --- Office (zip of XML) -------------------------------------------------------

class _Package:
    def __init__(self, data: bytes):
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise ValueError("not a valid Office file (bad zip)") from e
        self.read_bytes = 0

    def names(self) -> list[str]:
        return self.zip.namelist()

    def xml(self, name: str) -> Optional[ElementTree.Element]:
        try:
            info = self.zip.getinfo(name)
        except KeyError:
            return None
        self.read_bytes += info.file_size
        if self.read_bytes > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Office file expands past the size limit")
        raw = self.zip.read(name)
        # Entities can only be declared inside a DOCTYPE, so refusing it is enough.
        if b"<!DOCTYPE" in raw:
            raise ValueError(f"refusing {name}: XML declares a DOCTYPE")
        return ElementTree.fromstring(raw)

    def rels(self, part: str) -> dict[str, str]:
        """Relationship id -> target path for ``part``, resolved against its folder."""
        folder, _, base = part.rpartition("/")
        root = self.xml(f"{folder}/_rels/{base}.rels")
        out = {}
        if root is None:
            return out
        for rel in root.iter(f"{_PKG_REL}Relationship"):
            target = rel.get("Target", "")
            if target.startswith("/"):
                path = target.lstrip("/")
            else:
                parts = folder.split("/") if folder else []
                for piece in target.split("/"):
                    if piece == "..":
                        parts = parts[:-1]
                    elif piece and piece != ".":
                        parts.append(piece)
                path = "/".join(parts)
            out[rel.get("Id", "")] = path
        return out


def _docx(data: bytes) -> dict:
    pkg = _Package(data)
    root = pkg.xml("word/document.xml")
    if root is None:
        raise ValueError("not a Word document (no word/document.xml)")
    paragraphs = []
    for p in root.iter(f"{_W}p"):
        pieces = []
        for node in p.iter():
            if node.tag == f"{_W}t" and node.text:
                pieces.append(node.text)
            elif node.tag == f"{_W}tab":
                pieces.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                pieces.append("\n")
        line = "".join(pieces).strip()
        if line:
            paragraphs.append(line)
    return {"text": "\n".join(paragraphs), "unit": "document", "total": 1, "selected": [1]}


def _pptx(data: bytes, pages: Optional[str]) -> dict:
    pkg = _Package(data)
    pres = pkg.xml("ppt/presentation.xml")
    if pres is None:
        raise ValueError("not a presentation (no ppt/presentation.xml)")
    rels = pkg.rels("ppt/presentation.xml")
    order = [
        rels.get(s.get(f"{_R}id", ""), "")
        for s in pres.iter("{http://schemas.openxmlformats.org/presentationml/2006/main}sldId")
    ]
    order = [p for p in order if p]
    chosen = parse_pages(pages, len(order))
    out = []
    for n in chosen:
        slide = pkg.xml(order[n - 1])
        lines = []
        if slide is not None:
            for para in slide.iter(f"{_A}p"):
                line = "".join(t.text or "" for t in para.iter(f"{_A}t")).strip()
                if line:
                    lines.append(line)
        notes = _slide_notes(pkg, order[n - 1])
        block = f"[slide {n}]\n" + "\n".join(lines)
        if notes:
            block += "\nNotes: " + notes
        out.append(block)
    return {"text": "\n\n".join(out), "unit": "slide", "total": len(order), "selected": chosen}


def _slide_notes(pkg: _Package, slide_path: str) -> str:
    for target in pkg.rels(slide_path).values():
        if "notesSlides/" in target:
            notes = pkg.xml(target)
            if notes is None:
                return ""
            texts = [
                "".join(t.text or "" for t in p.iter(f"{_A}t")).strip()
                for p in notes.iter(f"{_A}p")
            ]
            # The notes page also holds the slide number placeholder; keep real text.
            return " ".join(t for t in texts if t and not t.isdigit())
    return ""


def _xlsx(data: bytes) -> dict:
    pkg = _Package(data)
    book = pkg.xml("xl/workbook.xml")
    if book is None:
        raise ValueError("not a spreadsheet (no xl/workbook.xml)")
    shared = []
    sst = pkg.xml("xl/sharedStrings.xml")
    if sst is not None:
        for si in sst.iter(f"{_S}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{_S}t")))
    rels = pkg.rels("xl/workbook.xml")
    out = []
    sheets = list(book.iter(f"{_S}sheet"))
    for sheet in sheets:
        name = sheet.get("name", "?")
        root = pkg.xml(rels.get(sheet.get(f"{_R}id", ""), ""))
        rows = []
        if root is not None:
            for count, row in enumerate(root.iter(f"{_S}row")):
                if count >= MAX_SHEET_ROWS:
                    rows.append(f"(cut at {MAX_SHEET_ROWS} rows)")
                    break
                cells = [_cell(c, shared) for c in row.iter(f"{_S}c")]
                while cells and cells[-1] == "":
                    cells.pop()
                if cells:
                    rows.append("\t".join(cells))
        out.append(f"[sheet {name}]\n" + "\n".join(rows))
    return {"text": "\n\n".join(out), "unit": "sheet", "total": len(sheets),
            "selected": list(range(1, len(sheets) + 1))}


def _cell(c: ElementTree.Element, shared: list[str]) -> str:
    kind = c.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in c.iter(f"{_S}t"))
    v = c.find(f"{_S}v")
    value = v.text if v is not None and v.text else ""
    if kind == "s":
        try:
            return shared[int(value)]
        except (ValueError, IndexError):
            return ""
    if kind == "b":
        return "TRUE" if value == "1" else "FALSE"
    return value
