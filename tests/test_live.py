"""Opt-in live smoke tests against real Google accounts.

Skipped unless GOOGLE_MCP_LIVE=1 and a default account is authenticated
(run `google-auth login` first). These hit the real APIs, so they are never
run in CI.

Env:
  GOOGLE_MCP_LIVE=1                 enable these tests
  GOOGLE_MCP_TEST_ACCOUNT=<email>  account/alias to use (else default)
  GOOGLE_MCP_TEST_SHEET_ID=<id>    an existing scratch spreadsheet for the
                                   Sheets round-trip (avoids leaving litter)
  GOOGLE_MCP_TEST_ACCOUNT_2=<email> second account, for the isolation check

The Slides round trip needs the Google Slides API enabled in the OAuth
client's Cloud project.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("GOOGLE_MCP_LIVE"),
    reason="set GOOGLE_MCP_LIVE=1 (and authenticate) to run live tests",
)

ACCOUNT = os.getenv("GOOGLE_MCP_TEST_ACCOUNT")


def test_gmail_profile_live():
    from google_workspace_mcp.gmail import server

    api, resolved = server._api(ACCOUNT)
    profile = api.get_profile()
    assert profile.get("emailAddress")


def test_gmail_change_feed_live():
    from google_workspace_mcp.gmail import server

    api, _ = server._api(ACCOUNT)
    baseline = api.get_changes_start_token()
    token = baseline["start_history_token"]
    assert token
    changes = api.list_changes(token)
    assert isinstance(changes.get("changes"), list)


def test_calendar_list_live():
    from google_workspace_mcp.calendar import server

    api, _ = server._api(ACCOUNT)
    calendars = api.list_calendars()
    assert isinstance(calendars, (list, dict))


def test_calendar_change_feed_baseline_live():
    from google_workspace_mcp.calendar import server

    api, _ = server._api(ACCOUNT)
    result = api.list_event_changes(calendar_id="primary", max_results=10)
    assert result.get("new_sync_token") or result.get("next_page_token") is not None or result.get("events") is not None


def test_sheets_roundtrip_live():
    sheet_id = os.getenv("GOOGLE_MCP_TEST_SHEET_ID")
    if not sheet_id:
        pytest.skip("set GOOGLE_MCP_TEST_SHEET_ID to a scratch spreadsheet")
    from google_workspace_mcp.sheets import server

    api, _ = server._api(ACCOUNT)
    api.update_range(sheet_id, "A1", [["mcp-live"]])
    got = api.read_range(sheet_id, "A1")
    assert got.get("values") == [["mcp-live"]]
    comment = api.add_comment(sheet_id, "live note", "A1")
    comment_id = comment["id"]
    try:
        listed = api.list_comments(sheet_id)
        found = next(c for c in listed["comments"] if c["id"] == comment_id)
        assert found["place"] == {"sheetsCell": "A1"}
    finally:
        api.delete_comment(sheet_id, comment_id)
    api.clear_range(sheet_id, "A1")


def test_multi_account_isolation_live():
    account_2 = os.getenv("GOOGLE_MCP_TEST_ACCOUNT_2")
    if not (ACCOUNT and account_2):
        pytest.skip("set GOOGLE_MCP_TEST_ACCOUNT and GOOGLE_MCP_TEST_ACCOUNT_2")
    from google_workspace_mcp.gmail import server

    api1, r1 = server._api(ACCOUNT)
    api2, r2 = server._api(account_2)
    assert r1 != r2
    assert api1.get_profile()["emailAddress"] != api2.get_profile()["emailAddress"]


def test_docs_roundtrip_live():
    from google_workspace_mcp.docs import server as docs
    from google_workspace_mcp.drive import server as drive

    docs_api, _ = docs._api(ACCOUNT)
    drive_api, _ = drive._api(ACCOUNT)

    created = docs_api.create_document("mcp-live-docs-test")
    doc_id = created["documentId"]
    try:
        docs_api.append_text(doc_id, "MCP live test\n")
        read = docs_api.get_document_text(doc_id)
        assert "MCP live test" in read["text"]

        quote = "MCP live test"
        comment = docs_api.add_comment(doc_id, "live note", quote=quote)
        comment_id = comment["id"]
        listed = docs_api.list_comments(doc_id)
        found = next(c for c in listed["comments"] if c["id"] == comment_id)
        assert quote in (found.get("quotedText") or "")
        assert found.get("place") and "docsRange" in found["place"]
        docs_api.reply_to_comment(doc_id, comment_id, content="live reply")
        docs_api.delete_comment(doc_id, comment_id)

        content_map = docs_api.get_content_map(doc_id)
        assert any(el["type"] == "paragraph" for el in content_map["elements"])

        docs_api.set_page_layout(doc_id, page_preset="A4", margin_top_pt=72, margin_bottom_pt=72)
        docs_api.format_text(doc_id, 1, 5, bold=True)

        # Table: insert at body start, then populate from content map indices.
        docs_api.insert_table(doc_id, rows=2, columns=2, index=1)
        refreshed = docs_api.get_content_map(doc_id)
        table_el = next(el for el in refreshed["elements"] if el["type"] == "table")
        docs_api.populate_table(doc_id, table_el["startIndex"], [["H1", "H2"], ["A", "B"]])

        footer = docs_api.setup_footer(doc_id, text="Footer — ")
        assert footer["footerId"]

        # insert_page_number is not supported by the public Docs API (see unit test).
        final = docs_api.get_document_text(doc_id)
        assert "MCP live test" in final["text"]
    finally:
        drive_api.delete_file(doc_id)


def test_insert_page_number_not_supported_live():
    """Document that insertPageNumber is rejected by the live Docs API."""
    from googleapiclient.errors import HttpError

    from google_workspace_mcp.docs import server as docs
    from google_workspace_mcp.drive import server as drive

    docs_api, _ = docs._api(ACCOUNT)
    drive_api, _ = drive._api(ACCOUNT)

    created = docs_api.create_document("mcp-live-page-number-test")
    doc_id = created["documentId"]
    try:
        resp = docs_api.create_footer(doc_id)
        footer_id = resp["replies"][0]["createFooter"]["footerId"]
        with pytest.raises(HttpError, match="insertPageNumber"):
            docs_api.insert_page_number(doc_id, footer_id=footer_id, index=0)
    finally:
        drive_api.delete_file(doc_id)


def test_drive_roundtrip_live(tmp_path):
    from google_workspace_mcp.drive import server as drive

    drive_api, _ = drive._api(ACCOUNT)

    listed = drive_api.list_files(page_size=1)
    assert "files" in listed

    folder = drive_api.create_folder("mcp-live-folder")
    folder_id = folder["id"]
    file_id = None
    try:
        got = drive_api.get_file(folder_id)
        assert got["name"] == "mcp-live-folder"

        upload_path = tmp_path / "mcp-live.txt"
        upload_path.write_text("drive live")
        uploaded = drive_api.upload_file(str(upload_path), parent_id=folder_id)
        file_id = uploaded["id"]

        text = drive_api.read_file_text(file_id)
        assert text["text"] == "drive live"

        renamed = drive_api.rename_file(file_id, "mcp-live-renamed.txt")
        assert renamed["name"] == "mcp-live-renamed.txt"
    finally:
        if file_id:
            drive_api.delete_file(file_id)
        drive_api.delete_file(folder_id)


def test_drive_change_feed_live(tmp_path):
    from google_workspace_mcp.drive import server as drive

    drive_api, _ = drive._api(ACCOUNT)

    baseline = drive_api.get_changes_start_token()
    page_token = baseline["start_page_token"]
    assert page_token

    upload_path = tmp_path / "mcp-change-feed.txt"
    upload_path.write_text("change feed probe")
    file_id = None
    try:
        uploaded = drive_api.upload_file(str(upload_path))
        file_id = uploaded["id"]

        seen = False
        token = page_token
        for _ in range(20):
            page = drive_api.list_changes(token)
            if any(c.get("file_id") == file_id for c in page.get("changes", [])):
                seen = True
                break
            token = page.get("next_page_token") or page.get("new_start_token")
            if not token:
                break
        assert seen, "uploaded file did not appear in Drive change feed"
    finally:
        if file_id:
            drive_api.delete_file(file_id)


def test_slides_roundtrip_live():
    """Build a deck from an outline, read it, edit it, and delete it."""
    from google_workspace_mcp.drive import server as drive
    from google_workspace_mcp.slides import server as slides

    slides_api, _ = slides._api(ACCOUNT)
    drive_api, _ = drive._api(ACCOUNT)

    created = slides_api.create_presentation_from_outline("mcp-live-slides-test", [
        {"title": "MCP live", "layout": "TITLE"},
        {"title": "Done", "body": "Summary\n- done\n  - nested", "notes": "live notes"},
    ])
    deck_id = created["presentationId"]
    try:
        read = slides_api.read_presentation(deck_id)
        assert read["slideCount"] == 2
        first, second = read["slides"]
        assert first["title"] == "MCP live"
        assert second["body"] == "Summary\n- done\n  - nested"
        assert second["notes"] == "live notes"

        added = slides_api.add_slides(deck_id, [{"title": "Extra"}], position=1,
                                      revision_id=read["revisionId"])
        updated = slides_api.update_slide_text(deck_id, second["id"], body="- changed",
                                               revision_id=added["revisionId"])
        assert updated["updated"] == ["body"]
        with pytest.raises(Exception, match="changed after revision"):
            slides_api.update_slide_text(deck_id, second["id"], title="stale",
                                         revision_id=read["revisionId"])
        replaced = slides_api.replace_all_text(deck_id, "changed", "replaced")
        assert replaced["occurrencesChanged"] == 1
        slides_api.delete_slide(deck_id, added["slideIds"][0])
        final = slides_api.read_presentation(deck_id)
        assert [s.get("title") for s in final["slides"]] == ["MCP live", "Done"]
        assert final["slides"][1]["body"] == "- replaced"
    finally:
        drive_api.delete_file(deck_id)
