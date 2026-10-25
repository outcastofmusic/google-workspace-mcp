"""Small PDF and Office files built in memory, for the Drive extraction tests."""

import io
import zipfile

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def make_pdf(pages, password=None):
    """A PDF whose page n shows pages[n]; an empty string makes a page with no text."""
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
        })
        stream = DecodedStreamObject()
        if text:
            stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(user_password=password, owner_password=password)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_zip(parts):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return buf.getvalue()


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
PKG = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'


def make_docx(paragraphs, prolog=""):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    return make_zip({"word/document.xml": f"{prolog}<w:document {W}><w:body>{body}</w:body></w:document>"})


def make_pptx(slides, notes=None):
    notes = notes or {}
    ids = "".join(f'<p:sldId id="{256 + i}" r:id="rId{i + 1}"/>' for i in range(len(slides)))
    parts = {
        "ppt/presentation.xml": f"<p:presentation {P} {R}><p:sldIdLst>{ids}</p:sldIdLst></p:presentation>",
        "ppt/_rels/presentation.xml.rels": f"<Relationships {PKG}>" + "".join(
            f'<Relationship Id="rId{i + 1}" Target="slides/slide{i + 1}.xml" Type="slide"/>'
            for i in range(len(slides))
        ) + "</Relationships>",
    }
    for i, lines in enumerate(slides, start=1):
        paras = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in lines)
        parts[f"ppt/slides/slide{i}.xml"] = f"<p:sld {P} {A}><p:cSld>{paras}</p:cSld></p:sld>"
        if i in notes:
            parts[f"ppt/slides/_rels/slide{i}.xml.rels"] = (
                f'<Relationships {PKG}><Relationship Id="rN" '
                f'Target="../notesSlides/notesSlide{i}.xml" Type="notesSlide"/></Relationships>'
            )
            parts[f"ppt/notesSlides/notesSlide{i}.xml"] = (
                f"<p:notes {P} {A}><a:p><a:r><a:t>{notes[i]}</a:t></a:r></a:p>"
                f"<a:p><a:r><a:t>{i}</a:t></a:r></a:p></p:notes>"
            )
    return make_zip(parts)


def make_xlsx(sheets):
    """sheets: {name: [[cell, ...], ...]}; strings go to shared strings, numbers inline."""
    shared, sheet_parts, rels, entries = [], {}, [], []
    for n, (name, rows) in enumerate(sheets.items(), start=1):
        xml_rows = []
        for r, row in enumerate(rows, start=1):
            cells = []
            for value in row:
                if isinstance(value, str):
                    shared.append(value)
                    cells.append(f'<c t="s"><v>{len(shared) - 1}</v></c>')
                elif isinstance(value, bool):
                    cells.append(f'<c t="b"><v>{int(value)}</v></c>')
                else:
                    cells.append(f"<c><v>{value}</v></c>")
            xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
        sheet_parts[f"xl/worksheets/sheet{n}.xml"] = (
            f"<worksheet {S}><sheetData>{''.join(xml_rows)}</sheetData></worksheet>"
        )
        rels.append(f'<Relationship Id="rId{n}" Target="worksheets/sheet{n}.xml" Type="ws"/>')
        entries.append(f'<sheet name="{name}" sheetId="{n}" r:id="rId{n}"/>')
    sst = "".join(f"<si><t>{s}</t></si>" for s in shared)
    return make_zip({
        "xl/workbook.xml": f"<workbook {S} {R}><sheets>{''.join(entries)}</sheets></workbook>",
        "xl/_rels/workbook.xml.rels": f"<Relationships {PKG}>{''.join(rels)}</Relationships>",
        "xl/sharedStrings.xml": f"<sst {S}>{sst}</sst>",
        **sheet_parts,
    })
