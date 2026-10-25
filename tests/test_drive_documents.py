"""read_file_text routing by file type, read_files, and list_folder_tree."""

import json
from unittest.mock import MagicMock

import pytest
from doc_builders import make_docx, make_pdf, make_pptx, make_xlsx
from mcp.types import TextContent

from google_workspace_mcp.drive import server
from google_workspace_mcp.drive.drive_api import (
    DOCUMENT_READ_MAX_BYTES,
    FOLDER_MIME,
    PRESENTATION_MIME,
    TEXT_READ_MAX_BYTES,
    DriveAPI,
)
from google_workspace_mcp.drive.extract import DOCX_MIME, PDF_MIME, PPTX_MIME, XLSX_MIME

SHEET_MIME = "application/vnd.google-apps.spreadsheet"
DOC_MIME = "application/vnd.google-apps.document"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def envelope(res):
    if isinstance(res, tuple):
        res = res[0]
    if isinstance(res, list):
        res = res[0]
    if isinstance(res, TextContent):
        return json.loads(res.text)
    if hasattr(res, "content"):
        return json.loads(res.content[0].text)
    return res


@pytest.fixture
def drive(monkeypatch):
    """A DriveAPI over a mocked service; ``drive.serve(meta, data)`` sets one file."""
    svc = MagicMock()
    monkeypatch.setattr("google_auth_core.get_service", lambda *a, **k: svc)
    api = DriveAPI("x@x.com")
    downloads = []

    def fake_download(request, max_bytes):
        downloads.append((request, max_bytes))
        return api.data

    monkeypatch.setattr("google_workspace_mcp.drive.drive_api._download_to_bytes", fake_download)

    def serve(meta, data=b""):
        svc.files().get().execute.return_value = {"id": "F1", "name": "f", **meta}
        api.data = data

    api.serve, api.svc, api.downloads = serve, svc, downloads
    return api


def test_pdf_is_extracted_with_document_cap(drive):
    drive.serve({"mimeType": PDF_MIME, "size": "900"}, make_pdf(["One", "Two"]))
    out = drive.read_file_text("F1")
    assert out["unit"] == "page" and out["total"] == 2 and "[page 2]\nTwo" in out["text"]
    assert drive.downloads[0][1] == DOCUMENT_READ_MAX_BYTES
    drive.svc.files().get_media.assert_called()


def test_pdf_page_range_reaches_extractor(drive):
    drive.serve({"mimeType": PDF_MIME, "size": "900"}, make_pdf(["One", "Two", "Three"]))
    out = drive.read_file_text("F1", pages="3")
    assert out["selected"] == [3] and "One" not in out["text"]


def test_docx_and_xlsx_files(drive):
    drive.serve({"mimeType": DOCX_MIME, "size": "10"}, make_docx(["Hello"]))
    assert drive.read_file_text("F1")["text"] == "Hello"
    drive.serve({"mimeType": XLSX_MIME, "size": "10"}, make_xlsx({"S": [["a", 1]]}))
    assert drive.read_file_text("F1")["text"] == "[sheet S]\na\t1"


def test_native_slides_export_as_pptx(drive):
    drive.serve({"mimeType": PRESENTATION_MIME}, make_pptx([["Hi"], ["Bye"]]))
    out = drive.read_file_text("F1", pages="2")
    drive.svc.files().export_media.assert_called_with(fileId="F1", mimeType=PPTX_MIME)
    assert out["text"] == "[slide 2]\nBye" and out["total"] == 2


def test_native_sheet_returns_every_sheet(drive):
    drive.serve({"mimeType": SHEET_MIME}, make_xlsx({"Budget": [["x"]], "Notes": [["y"]]}))
    out = drive.read_file_text("F1")
    drive.svc.files().export_media.assert_called_with(fileId="F1", mimeType=XLSX_MIME)
    assert "[sheet Budget]\nx" in out["text"] and "[sheet Notes]\ny" in out["text"]


def test_native_doc_is_plain_text(drive):
    drive.serve({"mimeType": DOC_MIME}, b"Doc body")
    assert drive.read_file_text("F1")["text"] == "Doc body"
    drive.svc.files().export_media.assert_called_with(fileId="F1", mimeType="text/plain")
    with pytest.raises(ValueError, match="only to PDF"):
        drive.read_file_text("F1", pages="1")


def test_oversized_document_fails_before_download(drive):
    drive.serve({"mimeType": PDF_MIME, "size": str(DOCUMENT_READ_MAX_BYTES + 1)})
    with pytest.raises(ValueError, match="download_file"):
        drive.read_file_text("F1")
    assert drive.downloads == []


def test_plain_text_keeps_one_mib_cap(drive):
    drive.serve({"mimeType": "text/plain", "size": str(TEXT_READ_MAX_BYTES + 1)})
    with pytest.raises(ValueError, match="exceeding read_file_text cap"):
        drive.read_file_text("F1")
    drive.serve({"mimeType": "text/plain", "size": "3"}, b"abc")
    assert drive.read_file_text("F1")["text"] == "abc"
    with pytest.raises(ValueError, match="only to PDF"):
        drive.read_file_text("F1", pages="1")


def test_unsupported_binary_names_download_file(drive):
    drive.serve({"mimeType": "image/png", "size": "4"}, b"\x89PNG")
    with pytest.raises(ValueError, match="download_file"):
        drive.read_file_text("F1")


def test_folder_is_refused(drive):
    drive.serve({"mimeType": FOLDER_MIME})
    with pytest.raises(ValueError, match="list_folder_tree"):
        drive.read_file_text("F1")


# --- tools (protocol level) ---------------------------------------------------


@pytest.fixture
def fake(monkeypatch):
    fake = MagicMock(spec=DriveAPI)
    monkeypatch.setattr(server, "_api", lambda account=None: (fake, "d@x.com"))
    return fake


@pytest.mark.anyio
async def test_read_file_text_tool_passes_pages(fake):
    fake.read_file_text.return_value = {"fileId": "F1", "text": "[slide 2]\nx", "total": 5}
    env = envelope(await server.mcp.call_tool("read_file_text", {"file_id": "F1", "pages": "2"}))
    fake.read_file_text.assert_called_once_with("F1", pages="2")
    assert env["data"]["total"] == 5


@pytest.mark.anyio
async def test_read_files_reports_errors_and_dedupes(fake):
    def read(fid):
        if fid == "BAD":
            raise ValueError("read_file_text cannot read a folder")
        return {"fileId": fid, "text": f"text {fid}"}

    fake.read_file_text.side_effect = read
    env = envelope(await server.mcp.call_tool(
        "read_files", {"file_ids": ["A", "BAD", "C", "A"]}
    ))
    data = env["data"]
    assert [f["fileId"] for f in data["files"]] == ["A", "C"]
    assert "folder" in data["errors"]["BAD"]
    assert fake.read_file_text.call_count == 3


@pytest.mark.anyio
async def test_read_files_total_budget(fake):
    fake.read_file_text.side_effect = lambda fid: {"fileId": fid, "text": "x" * 1500}
    env = envelope(await server.mcp.call_tool(
        "read_files", {"file_ids": ["A", "B", "C"], "max_chars_total": 1000}
    ))
    data = env["data"]
    assert len(data["files"]) == 1
    first = data["files"][0]
    assert len(first["text"]) == 1000 and first["truncated"] and first["total_chars"] == 1500
    assert data["skipped"] == ["B", "C"]


@pytest.mark.anyio
async def test_read_files_per_file_limit(fake):
    fake.read_file_text.side_effect = lambda fid: {"fileId": fid, "text": "y" * 50}
    env = envelope(await server.mcp.call_tool(
        "read_files", {"file_ids": ["A", "B"], "max_chars_per_file": 20}
    ))
    assert [len(f["text"]) for f in env["data"]["files"]] == [20, 20]
    assert "skipped" not in env["data"]


@pytest.mark.anyio
async def test_read_files_needs_ids(fake):
    with pytest.raises(Exception, match="at least one file"):
        await server.mcp.call_tool("read_files", {"file_ids": []})


# --- list_folder_tree ------------------------------------------------------------


def _tree_api(monkeypatch, root, children):
    """children: {folder_id: [pages of file dicts]}; records every list query."""
    svc = MagicMock()
    monkeypatch.setattr("google_auth_core.get_service", lambda *a, **k: svc)
    files = svc.files.return_value
    files.get.return_value.execute.return_value = root
    queries = []

    def list_(**params):
        parent = params["q"].split("'")[1]
        pages = children.get(parent, [[]])
        n = int(params.get("pageToken") or 0)
        page = {"files": pages[n]}
        if n + 1 < len(pages):
            page["nextPageToken"] = str(n + 1)
        queries.append(params)
        return MagicMock(execute=MagicMock(return_value=page))

    files.list.side_effect = list_
    return DriveAPI("x@x.com"), queries


def _folder(id, name):
    return {"id": id, "name": name, "mimeType": FOLDER_MIME}


def test_folder_tree_nesting_and_paths(monkeypatch):
    api, queries = _tree_api(monkeypatch, _folder("P", "Project"), {
        "P": [[_folder("S", "Specs"), {"id": "a", "name": "a.pdf", "mimeType": PDF_MIME}]],
        "S": [[{"id": "b", "name": "b.docx", "mimeType": DOCX_MIME}]],
    })
    out = api.list_folder_tree("P")
    assert [(f["name"], f["path"]) for f in out["files"]] == [
        ("a.pdf", "Project"), ("b.docx", "Project/Specs"),
    ]
    assert out["truncated"] is False and out["folder"] == {"id": "P", "name": "Project"}
    assert all("trashed = false" in q["q"] for q in queries)
    assert "md5Checksum" in queries[0]["fields"] and "webViewLink" in queries[0]["fields"]


def test_folder_tree_follows_pages(monkeypatch):
    api, _ = _tree_api(monkeypatch, _folder("P", "P"), {
        "P": [[{"id": "1", "name": "1", "mimeType": PDF_MIME}],
              [{"id": "2", "name": "2", "mimeType": PDF_MIME}]],
    })
    assert [f["id"] for f in api.list_folder_tree("P")["files"]] == ["1", "2"]


def test_folder_tree_truncates_at_max_files(monkeypatch):
    many = [{"id": str(i), "name": str(i), "mimeType": PDF_MIME} for i in range(5)]
    api, _ = _tree_api(monkeypatch, _folder("P", "P"), {"P": [many]})
    out = api.list_folder_tree("P", max_files=3)
    assert len(out["files"]) == 3 and out["truncated"] is True


def test_folder_reached_twice_is_listed_once(monkeypatch):
    shared = _folder("S", "Shared")
    api, queries = _tree_api(monkeypatch, _folder("P", "P"), {
        "P": [[_folder("A", "A"), _folder("B", "B")]],
        "A": [[shared]],
        "B": [[shared]],
        "S": [[{"id": "x", "name": "x", "mimeType": PDF_MIME}]],
        # A loop back to the root must not be followed either.
        "X": [[_folder("P", "P")]],
    })
    out = api.list_folder_tree("P")
    assert [f["id"] for f in out["files"]] == ["x"]
    listed = [q["q"].split("'")[1] for q in queries]
    assert listed.count("S") == 1 and listed.count("P") == 1


def test_folder_tree_depth_limit(monkeypatch):
    api, _ = _tree_api(monkeypatch, _folder("P", "P"), {
        "P": [[_folder("D1", "D1")]],
        "D1": [[_folder("D2", "D2"), {"id": "f1", "name": "f1", "mimeType": PDF_MIME}]],
        "D2": [[{"id": "f2", "name": "f2", "mimeType": PDF_MIME}]],
    })
    assert [f["id"] for f in api.list_folder_tree("P", max_depth=1)["files"]] == ["f1"]


def test_folder_tree_rejects_a_file(monkeypatch):
    api, _ = _tree_api(monkeypatch, {"id": "F", "name": "a.pdf", "mimeType": PDF_MIME}, {})
    with pytest.raises(ValueError, match="not a folder"):
        api.list_folder_tree("F")


@pytest.mark.anyio
async def test_list_folder_tree_tool(fake):
    fake.list_folder_tree.return_value = {"folder": {"id": "P"}, "files": [], "truncated": False}
    env = envelope(await server.mcp.call_tool(
        "list_folder_tree", {"folder_id": "P", "max_files": 50}
    ))
    fake.list_folder_tree.assert_called_once_with("P", max_files=50, max_depth=10)
    assert env["ok"] and env["account"] == "d@x.com"
