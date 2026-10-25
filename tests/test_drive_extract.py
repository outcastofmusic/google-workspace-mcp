"""Text extraction from PDF and Office files (drive/extract.py)."""


import pytest
from doc_builders import W, make_docx, make_pdf, make_pptx, make_xlsx, make_zip

from google_workspace_mcp.drive import extract
from google_workspace_mcp.drive.extract import (
    DOCX_MIME,
    PDF_MIME,
    PPTX_MIME,
    XLSX_MIME,
    parse_pages,
)

# --- page ranges ---------------------------------------------------------------


@pytest.mark.parametrize("spec, total, want", [
    (None, 3, [1, 2, 3]),
    ("", 3, [1, 2, 3]),
    ("2", 5, [2]),
    ("2-4", 5, [2, 3, 4]),
    ("1,4,3-3", 5, [1, 3, 4]),
    ("4-9", 5, [4, 5]),  # pages past the end are ignored
])
def test_parse_pages(spec, total, want):
    assert parse_pages(spec, total) == want


@pytest.mark.parametrize("spec, match", [
    ("7-9", "outside"),
    ("abc", "look like"),
    ("3-1", "bad page range"),
    ("0", "bad page range"),
])
def test_parse_pages_errors(spec, match):
    with pytest.raises(ValueError, match=match):
        parse_pages(spec, 5)


# --- PDF -----------------------------------------------------------------------


def test_pdf_marks_every_page():
    out = extract.extract(make_pdf(["Alpha", "Beta", "Gamma"]), PDF_MIME)
    assert out["unit"] == "page" and out["total"] == 3 and out["selected"] == [1, 2, 3]
    text = out["text"]
    assert text.index("[page 1]") < text.index("Alpha") < text.index("[page 2]")
    assert "Beta" in text and "[page 3]\nGamma" in text


def test_pdf_page_range():
    out = extract.extract(make_pdf(["A1", "B2", "C3", "D4"]), PDF_MIME, pages="2-3")
    assert out["selected"] == [2, 3] and out["total"] == 4
    assert "[page 1]" not in out["text"] and "B2" in out["text"] and "C3" in out["text"]


def test_pdf_range_outside_document_fails():
    with pytest.raises(ValueError, match="outside"):
        extract.extract(make_pdf(["only"]), PDF_MIME, pages="5-6")


def test_pdf_blank_pages_get_scanned_note():
    out = extract.extract(make_pdf(["", ""]), PDF_MIME)
    assert "[page 1]" in out["text"] and "[page 2]" in out["text"]
    assert "scanned images" in out["text"]


def test_pdf_with_some_text_gets_no_scanned_note():
    out = extract.extract(make_pdf(["", "words"]), PDF_MIME)
    assert "scanned" not in out["text"]


def test_password_protected_pdf_fails():
    with pytest.raises(ValueError, match="password-protected"):
        extract.extract(make_pdf(["secret"], password="pw"), PDF_MIME)


def test_garbage_pdf_fails_cleanly():
    with pytest.raises(ValueError):
        extract.extract(b"%PDF-1.7 not really", PDF_MIME)


# --- Office ----------------------------------------------------------------------


def test_docx_paragraphs():
    out = extract.extract(make_docx(["Title", "Body text"]), DOCX_MIME)
    assert out["text"] == "Title\nBody text" and out["unit"] == "document"


def test_pages_on_docx_fails():
    with pytest.raises(ValueError, match="only to PDF"):
        extract.extract(make_docx(["x"]), DOCX_MIME, pages="1")


def test_pptx_slides_and_notes():
    data = make_pptx([["Intro", "Agenda"], ["Results"], ["Thanks"]], notes={2: "Say the numbers"})
    out = extract.extract(data, PPTX_MIME)
    assert out["unit"] == "slide" and out["total"] == 3
    assert "[slide 1]\nIntro\nAgenda" in out["text"]
    assert "[slide 2]\nResults\nNotes: Say the numbers" in out["text"]
    part = extract.extract(data, PPTX_MIME, pages="3")
    assert part["selected"] == [3] and "Intro" not in part["text"] and "Thanks" in part["text"]


def test_xlsx_every_sheet_tab_separated():
    data = make_xlsx({"Budget": [["Item", "Cost"], ["Rent", 900], ["Paid", True]],
                      "Notes": [["ok"]]})
    out = extract.extract(data, XLSX_MIME)
    assert out["unit"] == "sheet" and out["total"] == 2
    assert "[sheet Budget]\nItem\tCost\nRent\t900\nPaid\tTRUE" in out["text"]
    assert "[sheet Notes]\nok" in out["text"]


def test_bad_zip_fails_cleanly():
    with pytest.raises(ValueError, match="bad zip"):
        extract.extract(b"PK not a zip", DOCX_MIME)


def test_unknown_mime_fails():
    with pytest.raises(ValueError, match="cannot extract"):
        extract.extract(b"x", "image/png")


# --- guards ---------------------------------------------------------------------


def test_doctype_is_refused_and_not_expanded():
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
    )
    data = make_zip({
        "word/document.xml": f"{bomb}<w:document {W}><w:body><w:p><w:r><w:t>&lol2;</w:t>"
        "</w:r></w:p></w:body></w:document>",
    })
    with pytest.raises(ValueError, match="DOCTYPE"):
        extract.extract(data, DOCX_MIME)


def test_uncompressed_size_cap(monkeypatch):
    monkeypatch.setattr(extract, "MAX_UNCOMPRESSED_BYTES", 1000)
    data = make_docx(["x" * 5000])
    with pytest.raises(ValueError, match="size limit"):
        extract.extract(data, DOCX_MIME)
