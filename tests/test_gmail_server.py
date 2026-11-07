"""Tests for the Gmail MCP server."""
from __future__ import annotations

import json
import os
import sys

import pytest

from google_workspace_mcp.gmail import server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeAPI:
    """Minimal stand-in for GmailAPI that records calls and returns canned values."""

    def __init__(self):
        self.calls: dict = {}
        self.duplicate_attachments = 1

    def _record(self, method, **kwargs):
        self.calls[method] = kwargs

    # READ methods
    def get_profile(self):
        self._record("get_profile")
        return {"emailAddress": "test@x.com", "messagesTotal": 42}

    def list_messages(self, max_results=10, label_ids=None, query=None):
        self._record("list_messages", max_results=max_results, label_ids=label_ids, query=query)
        return [{"id": "msg1"}, {"id": "msg2"}]

    def list_messages_page(self, max_results=10, label_ids=None, query=None, page_token=None):
        self._record(
            "list_messages_page",
            max_results=max_results,
            label_ids=label_ids,
            query=query,
            page_token=page_token,
        )
        out = {"items": [{"id": "msg1"}, {"id": "msg2"}]}
        if page_token is None:
            out["nextPageToken"] = "tok_page2"
        return out

    def search_with_details(self, max_results=10, label_ids=None, query=None, format="metadata", page_token=None):
        self._record(
            "search_with_details",
            max_results=max_results,
            query=query,
            format=format,
            page_token=page_token,
        )
        return {"items": [{"id": "msg1", "snippet": "Hello"}]}

    def get_message(self, message_id, format="full"):
        self._record("get_message", message_id=message_id, format=format)
        return {"id": message_id, "snippet": "Hi"}

    def get_message_text(self, message_id):
        self._record("get_message_text", message_id=message_id)
        return {
            "id": message_id,
            "threadId": "t1",
            "labelIds": ["INBOX"],
            "subject": "Hi",
            "from": "a@x.com",
            "to": "b@x.com",
            "cc": "",
            "date": "Thu, 13 Aug 2026",
            "snippet": "hello",
            "body_text": "hello",
            "attachments": [],
        }

    def get_thread(self, thread_id, format="full"):
        self._record("get_thread", thread_id=thread_id, format=format)
        return {"id": thread_id, "messages": [{"id": "abc"}]}

    def get_thread_text(self, thread_id):
        self._record("get_thread_text", thread_id=thread_id)
        return {
            "id": thread_id,
            "historyId": "9",
            "messages": [
                {
                    "id": "abc",
                    "threadId": thread_id,
                    "subject": "Hi",
                    "from": "a@x.com",
                    "body_text": "hello",
                    "attachments": [],
                }
            ],
        }

    def list_threads(self, max_results=10, query=None):
        self._record("list_threads", max_results=max_results, query=query)
        return [{"id": "thread1"}]

    def list_threads_page(self, max_results=10, query=None, page_token=None):
        self._record("list_threads_page", max_results=max_results, query=query, page_token=page_token)
        return {"items": [{"id": "thread1"}]}

    def list_labels(self):
        self._record("list_labels")
        return [{"id": "INBOX", "name": "INBOX"}]

    def get_label(self, label_id):
        self._record("get_label", label_id=label_id)
        return {"id": label_id, "name": "MyLabel"}

    def list_drafts(self, max_results=10):
        self._record("list_drafts", max_results=max_results)
        return [{"id": "draft1"}]

    def list_drafts_page(self, max_results=10, page_token=None):
        self._record("list_drafts_page", max_results=max_results, page_token=page_token)
        return {"items": [{"id": "draft1"}]}

    def get_draft(self, draft_id):
        self._record("get_draft", draft_id=draft_id)
        return {"id": draft_id}

    def list_filters(self):
        self._record("list_filters")
        return [{"id": "filter1"}]

    def get_filter(self, filter_id):
        self._record("get_filter", filter_id=filter_id)
        return {"id": filter_id}

    def get_changes_start_token(self):
        self._record("get_changes_start_token")
        return {"start_history_token": "100", "email": "test@x.com"}

    def list_changes(
        self,
        start_history_token,
        history_types=None,
        label_id=None,
        max_results=100,
        page_token=None,
    ):
        self._record(
            "list_changes",
            start_history_token=start_history_token,
            history_types=history_types,
            label_id=label_id,
            max_results=max_results,
            page_token=page_token,
        )
        return {"changes": [], "new_history_token": "101"}

    # WRITE methods
    def send_message(self, to, subject, body, attachments=None, cc=None, html=False):
        self._record("send_message", to=to, subject=subject, body=body, cc=cc, html=html, attachments=attachments)
        return {"id": "sent1", "threadId": "t1"}

    def reply_to_message(self, message_id, body, reply_all=False, additional_cc=None, attachments=None):
        self._record(
            "reply_to_message",
            message_id=message_id,
            body=body,
            reply_all=reply_all,
            additional_cc=additional_cc,
            attachments=attachments,
        )
        return {"id": "sent2"}

    def draft_reply(self, message_id, body, reply_all=False, additional_cc=None, attachments=None):
        self._record(
            "draft_reply",
            message_id=message_id,
            body=body,
            reply_all=reply_all,
            additional_cc=additional_cc,
            attachments=attachments,
        )
        return {"id": "draft_reply1"}

    def send_draft(self, draft_id):
        self._record("send_draft", draft_id=draft_id)
        return {"id": "sent_draft1"}

    def delete_draft(self, draft_id):
        self._record("delete_draft", draft_id=draft_id)
        return None

    def download_attachment(self, message_id, output_dir=None, attachment_id=None):
        self._record(
            "download_attachment",
            message_id=message_id,
            output_dir=output_dir,
            attachment_id=attachment_id,
        )
        path = os.path.join(output_dir, "a.txt")
        with open(path, "w") as f:
            f.write("x")
        files = [{"filename": "a.txt", "path": path, "mime_type": "text/plain", "size": 1}]
        # Mirrors the real API: same-named attachments land on one path.
        return files * self.duplicate_attachments

    def forward_message(self, message_id, to, body=None, attachments=None):
        self._record("forward_message", message_id=message_id, to=to, body=body, attachments=attachments)
        return {"id": "sent3"}

    def create_draft(self, to, subject, body, attachments=None, cc=None, html=False):
        self._record("create_draft", to=to, subject=subject, body=body, cc=cc, html=html)
        return {"id": "draft_new"}

    def update_draft(self, draft_id, to=None, subject=None, body=None, attachments=None, cc=None, html=False):
        self._record("update_draft", draft_id=draft_id, to=to, subject=subject, body=body, cc=cc, html=html)
        return {"id": draft_id}

    def modify_message(self, message_id, add_label_ids=None, remove_label_ids=None):
        self._record("modify_message", message_id=message_id, add_label_ids=add_label_ids, remove_label_ids=remove_label_ids)
        return {"id": message_id, "labelIds": []}

    def mark_as_read(self, message_id):
        self._record("mark_as_read", message_id=message_id)
        return {"id": message_id}

    def archive_message(self, message_id):
        self._record("archive_message", message_id=message_id)
        return {"id": message_id}

    def star_message(self, message_id):
        self._record("star_message", message_id=message_id)
        return {"id": message_id}

    def unstar_message(self, message_id):
        self._record("unstar_message", message_id=message_id)
        return {"id": message_id}

    def mark_as_spam(self, message_id):
        self._record("mark_as_spam", message_id=message_id)
        return {"id": message_id}

    def unmark_spam(self, message_id):
        self._record("unmark_spam", message_id=message_id)
        return {"id": message_id}

    def create_label(self, name, message_list_visibility="show", label_list_visibility="labelShow", color=None):
        self._record("create_label", name=name)
        return {"id": "label_new", "name": name}

    def update_label(self, label_id, name=None, message_list_visibility=None, label_list_visibility=None, color=None):
        self._record("update_label", label_id=label_id, name=name)
        return {"id": label_id}

    def create_filter(self, criteria, action):
        self._record("create_filter", criteria=criteria, action=action)
        return {"id": "filter_new"}

    def block_sender(self, email):
        self._record("block_sender", email=email)
        return {"id": "filter_block"}

    def batch_modify_messages(self, message_ids, add_label_ids=None, remove_label_ids=None):
        self._record("batch_modify_messages", message_ids=message_ids)
        return {"modified": len(message_ids), "errors": []}

    # DESTRUCTIVE methods
    def trash_message(self, message_id):
        self._record("trash_message", message_id=message_id)
        return None

    def untrash_message(self, message_id):
        self._record("untrash_message", message_id=message_id)
        return None

    def delete_message(self, message_id):
        self._record("delete_message", message_id=message_id)
        return None

    def delete_label(self, label_id):
        self._record("delete_label", label_id=label_id)
        return None

    def delete_filter(self, filter_id):
        self._record("delete_filter", filter_id=filter_id)
        return None

    def batch_trash_messages(self, message_ids):
        self._record("batch_trash_messages", message_ids=message_ids)
        return {"trashed": len(message_ids), "errors": []}

    def batch_untrash_messages(self, message_ids):
        self._record("batch_untrash_messages", message_ids=message_ids)
        return {"untrashed": len(message_ids), "errors": []}

    def batch_delete_messages(self, message_ids):
        self._record("batch_delete_messages", message_ids=message_ids)
        return {"deleted": len(message_ids), "errors": []}


def make_fake_api():
    """Return (fake_api_instance, patched _api callable)."""
    fake = FakeAPI()
    return fake, lambda account=None: (fake, "test@x.com")


def parse_result(result):
    """Extract the dict from call_tool's TextContent list."""
    assert len(result.content) > 0
    text = result.content[0].text
    return json.loads(text)


# ---------------------------------------------------------------------------
# Test 1: import + tool list
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_tool_names():
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}

    expected_read = {
        "get_profile", "list_messages", "search_messages", "get_message",
        "get_thread", "list_threads", "list_labels", "get_label",
        "list_drafts", "get_draft", "list_filters", "get_filter",
        "download_attachment",
        "get_changes_start_token", "list_changes",
        "search_contacts",
    }
    expected_write = {
        "send_message", "reply_to_message", "forward_message",
        "create_draft", "update_draft", "draft_reply", "send_draft",
        "modify_labels", "mark_read",
        "archive_message", "star_message", "unstar_message",
        "mark_as_spam", "unmark_spam",
        "create_label", "update_label", "create_filter", "block_sender",
        "batch_modify_labels",
    }
    expected_destructive = {
        "trash_message", "untrash_message", "delete_message",
        "delete_label", "delete_filter", "delete_draft",
        "batch_trash_messages", "batch_untrash_messages", "batch_delete_messages",
    }
    expected_common = {"list_accounts", "auth_status", "whoami"}

    for name in expected_read | expected_write | expected_destructive | expected_common:
        assert name in names, f"Tool '{name}' missing from server"


# ---------------------------------------------------------------------------
# Test 2: read tool returns correct envelope
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_list_messages_envelope(monkeypatch):
    fake, patched = make_fake_api()
    monkeypatch.setattr(server, "_api", patched)

    result = await server.mcp.call_tool("list_messages", {"query": "is:unread"})
    data = parse_result(result)

    assert data["ok"] is True
    assert data["account"] == "test@x.com"
    assert isinstance(data["data"], list)
    assert data["data"][0]["id"] == "msg1"

    # confirm underlying api was called with the query
    assert fake.calls["list_messages_page"]["query"] == "is:unread"
    assert data["next_page_token"] == "tok_page2"


@pytest.mark.anyio
async def test_list_messages_page_token_passed_through(monkeypatch):
    fake, patched = make_fake_api()
    monkeypatch.setattr(server, "_api", patched)

    result = await server.mcp.call_tool("list_messages", {"page_token": "tok_page2"})
    data = parse_result(result)

    assert data["ok"] is True
    assert "next_page_token" not in data
    assert fake.calls["list_messages_page"]["page_token"] == "tok_page2"


# ---------------------------------------------------------------------------
# Test 3: mutating tool calls underlying api with correct args
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_send_message_args(monkeypatch):
    fake, patched = make_fake_api()
    monkeypatch.setattr(server, "_api", patched)

    result = await server.mcp.call_tool(
        "send_message",
        {"to": "alice@example.com", "subject": "Hello", "body": "World", "cc": "bob@example.com"},
    )
    data = parse_result(result)

    assert data["ok"] is True
    assert data["data"]["id"] == "sent1"

    call = fake.calls["send_message"]
    assert call["to"] == "alice@example.com"
    assert call["subject"] == "Hello"
    assert call["body"] == "World"
    assert call["cc"] == "bob@example.com"


# ---------------------------------------------------------------------------
# Test 4: get_message format routing
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_get_message_defaults_to_text(monkeypatch):
    fake, patched = make_fake_api()
    monkeypatch.setattr(server, "_api", patched)

    result = await server.mcp.call_tool("get_message", {"message_id": "abc"})
    data = parse_result(result)

    assert data["ok"] is True
    assert data["data"]["body_text"] == "hello"
    assert fake.calls["get_message_text"]["message_id"] == "abc"
    assert "get_message" not in fake.calls


@pytest.mark.anyio
async def test_get_message_format_full(monkeypatch):
    fake, patched = make_fake_api()
    monkeypatch.setattr(server, "_api", patched)

    result = await server.mcp.call_tool("get_message", {"message_id": "abc", "format": "full"})
    data = parse_result(result)

    assert data["ok"] is True
    assert fake.calls["get_message"] == {"message_id": "abc", "format": "full"}
    assert "get_message_text" not in fake.calls


@pytest.mark.anyio
async def test_get_thread_defaults_to_text(monkeypatch):
    fake, patched = make_fake_api()
    monkeypatch.setattr(server, "_api", patched)

    result = await server.mcp.call_tool("get_thread", {"thread_id": "t1"})
    data = parse_result(result)

    assert data["ok"] is True
    assert data["data"]["messages"][0]["body_text"] == "hello"
    assert fake.calls["get_thread_text"]["thread_id"] == "t1"
    assert "get_thread" not in fake.calls


@pytest.mark.anyio
async def test_get_thread_format_full(monkeypatch):
    fake, patched = make_fake_api()
    monkeypatch.setattr(server, "_api", patched)

    result = await server.mcp.call_tool("get_thread", {"thread_id": "t1", "format": "full"})
    data = parse_result(result)

    assert data["ok"] is True
    assert fake.calls["get_thread"] == {"thread_id": "t1", "format": "full"}
    assert "get_thread_text" not in fake.calls


# ---------------------------------------------------------------------------
# Test 5: get_profile read tool
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_get_profile(monkeypatch):
    fake, patched = make_fake_api()
    monkeypatch.setattr(server, "_api", patched)

    result = await server.mcp.call_tool("get_profile", {})
    data = parse_result(result)

    assert data["ok"] is True
    assert data["data"]["emailAddress"] == "test@x.com"
    assert data["data"]["messagesTotal"] == 42


# ---------------------------------------------------------------------------
# Test 6: destructive tool is registered and works correctly
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_delete_message(monkeypatch):
    fake, patched = make_fake_api()
    monkeypatch.setattr(server, "_api", patched)

    result = await server.mcp.call_tool("delete_message", {"message_id": "abc"})
    data = parse_result(result)

    assert data["ok"] is True
    assert fake.calls["delete_message"]["message_id"] == "abc"


# ---------------------------------------------------------------------------
# Test 7: READONLY env hides mutating tools (subprocess approach)
# ---------------------------------------------------------------------------

def test_readonly_hides_mutating_tools():
    """Check that mutating tools are absent when GOOGLE_MCP_READONLY=1."""
    import subprocess
    code = """
import os
os.environ["GOOGLE_MCP_READONLY"] = "1"

# Reload runtime so READONLY is re-evaluated
import importlib
import google_workspace_mcp.core.runtime as rt
importlib.reload(rt)

# Re-import server so register() uses fresh READONLY value
import google_workspace_mcp.gmail.server as srv
importlib.reload(srv)

import asyncio
async def main():
    tools = await srv.mcp.list_tools()
    names = [t.name for t in tools]
    # send_message is mutating — must not appear
    assert "send_message" not in names, f"send_message should be hidden; got {names}"
    # list_messages is read-only — must still appear
    assert "list_messages" in names, f"list_messages should be present; got {names}"
    print("OK")

asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"subprocess failed:\n{result.stdout}\n{result.stderr}"
    assert "OK" in result.stdout


@pytest.mark.anyio
async def test_send_message_refuses_token_store_attachment(monkeypatch, store_env, tmp_path):
    fake, patched = make_fake_api()
    monkeypatch.setattr(server, "_api", patched)
    secret = store_env / "credentials.json"
    secret.write_text("{}")
    ok_file = tmp_path / "deck.pdf"
    ok_file.write_text("pdf")

    with pytest.raises(Exception, match="token store"):
        await server.mcp.call_tool("send_message", {"to": "x@y.com", "attachments": [str(ok_file), str(secret)]})
    assert "send_message" not in fake.calls

    await server.mcp.call_tool("send_message", {"to": "x@y.com", "attachments": [str(ok_file)]})
    assert fake.calls["send_message"]["attachments"] == [str(ok_file)]


@pytest.mark.anyio
async def test_download_attachment_refuses_overwrite(monkeypatch, store_env, tmp_path):
    fake, patched = make_fake_api()
    monkeypatch.setattr(server, "_api", patched)
    existing = tmp_path / "a.txt"
    existing.write_text("keep me")

    with pytest.raises(Exception, match="overwrite=True"):
        await server.mcp.call_tool("download_attachment", {"message_id": "m1", "output_dir": str(tmp_path)})
    assert existing.read_text() == "keep me"

    data = parse_result(await server.mcp.call_tool(
        "download_attachment", {"message_id": "m1", "output_dir": str(tmp_path), "overwrite": True}
    ))
    assert data["data"][0]["path"] == str(existing)
    assert existing.read_text() == "x"


@pytest.mark.anyio
async def test_download_attachment_same_named_attachments(monkeypatch, store_env, tmp_path):
    fake, patched = make_fake_api()
    fake.duplicate_attachments = 2
    monkeypatch.setattr(server, "_api", patched)
    data = parse_result(await server.mcp.call_tool(
        "download_attachment", {"message_id": "m1", "output_dir": str(tmp_path)}
    ))
    assert [f["path"] for f in data["data"]] == [str(tmp_path / "a.txt")]
# ---------------------------------------------------------------------------
# Output caps (max_chars / offset)
# ---------------------------------------------------------------------------

def _full_message(mid, attachment_data):
    return {"id": mid, "payload": {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "text/plain", "filename": "", "body": {"data": "aGk="}},
        {"mimeType": "application/pdf", "filename": "a.pdf",
         "body": {"attachmentId": "att1", "size": 9, "data": attachment_data}},
    ]}}


@pytest.mark.anyio
async def test_get_message_text_body_pages_with_offset(monkeypatch):
    fake, patched = make_fake_api()
    fake.get_message_text = lambda message_id: {"id": message_id, "body_text": "abcdefghij"}
    monkeypatch.setattr(server, "_api", patched)

    data = parse_result(await server.mcp.call_tool(
        "get_message", {"message_id": "abc", "max_chars": 4, "offset": 4}))
    assert data["data"]["body_text"] == "efgh"
    assert data["truncated"] is True and data["next_offset"] == 8 and data["total_chars"] == 10


@pytest.mark.anyio
async def test_get_message_raw_is_capped(monkeypatch):
    fake, patched = make_fake_api()
    fake.get_message = lambda message_id, format="full": {"id": message_id, "raw": "R" * 50}
    monkeypatch.setattr(server, "_api", patched)

    data = parse_result(await server.mcp.call_tool(
        "get_message", {"message_id": "abc", "format": "raw", "max_chars": 20}))
    assert data["data"]["raw"] == "R" * 20
    assert data["next_offset"] == 20


@pytest.mark.anyio
async def test_get_message_full_drops_inline_attachment_data_when_too_big(monkeypatch):
    fake, patched = make_fake_api()
    fake.get_message = lambda message_id, format="full": _full_message(message_id, "Z" * 500)
    monkeypatch.setattr(server, "_api", patched)

    data = parse_result(await server.mcp.call_tool(
        "get_message", {"message_id": "abc", "format": "full", "max_chars": 300}))
    text_part, pdf_part = data["data"]["payload"]["parts"]
    assert text_part["body"]["data"] == "aGk="
    assert "data" not in pdf_part["body"] and pdf_part["body"]["attachmentId"] == "att1"
    assert data["content_trimmed"] is True and "truncated" not in data
    assert "download_attachment" in data["hint"]


@pytest.mark.anyio
async def test_get_message_full_small_keeps_attachment_data(monkeypatch):
    fake, patched = make_fake_api()
    fake.get_message = lambda message_id, format="full": _full_message(message_id, "Zm9v")
    monkeypatch.setattr(server, "_api", patched)

    data = parse_result(await server.mcp.call_tool("get_message", {"message_id": "abc", "format": "full"}))
    assert data["data"]["payload"]["parts"][1]["body"]["data"] == "Zm9v"
    assert "truncated" not in data


@pytest.mark.anyio
async def test_get_thread_pages_messages_and_cuts_long_bodies(monkeypatch):
    fake, patched = make_fake_api()
    fake.get_thread_text = lambda thread_id: {"id": thread_id, "messages": [
        {"id": "m0", "body_text": "x" * 500},
        {"id": "m1", "body_text": "short"},
        {"id": "m2", "body_text": "short"},
    ]}
    monkeypatch.setattr(server, "_api", patched)

    data = parse_result(await server.mcp.call_tool("get_thread", {"thread_id": "t1", "max_chars": 100}))
    msgs = data["data"]["messages"]
    assert [m["id"] for m in msgs] == ["m0"]
    assert msgs[0]["body_text"] == "x" * 100
    assert msgs[0]["body_truncated"] is True and msgs[0]["body_total_chars"] == 500
    assert data["truncated"] is True and data["next_offset"] == 1 and data["total_items"] == 3
    assert data["content_trimmed"] is True
    assert "get_message" in data["hint"] and "offset=next_offset" in data["hint"]

    data = parse_result(await server.mcp.call_tool(
        "get_thread", {"thread_id": "t1", "max_chars": 100, "offset": 1}))
    assert [m["id"] for m in data["data"]["messages"]] == ["m1", "m2"]
    assert data["truncated"] is False
    assert "content_trimmed" not in data and "hint" not in data


@pytest.mark.anyio
async def test_get_thread_full_drops_attachment_data_when_too_big(monkeypatch):
    fake, patched = make_fake_api()
    fake.get_thread = lambda thread_id, format="full": {
        "id": thread_id, "messages": [_full_message("m0", "Z" * 500)]}
    monkeypatch.setattr(server, "_api", patched)

    data = parse_result(await server.mcp.call_tool(
        "get_thread", {"thread_id": "t1", "format": "full", "max_chars": 300}))
    assert "data" not in data["data"]["messages"][0]["payload"]["parts"][1]["body"]
    assert data["content_trimmed"] is True and "truncated" not in data
    assert "download_attachment" in data["hint"] and "next_offset" not in data["hint"]


# ---------------------------------------------------------------------------
# Short message records from search (agent-ready-defaults)
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

from google_workspace_mcp.gmail.changes_api import project_message_record  # noqa: E402

RAW_MESSAGE = {
    "id": "m1", "threadId": "t1", "labelIds": ["INBOX", "UNREAD"], "snippet": "See you then",
    "sizeEstimate": 19969, "historyId": "301091", "internalDate": "1793979591000",
    "payload": {"mimeType": "multipart/alternative", "headers": [
        {"name": "Received", "value": "by 2002:a05 with SMTP id " + "x" * 300},
        {"name": "Subject", "value": "Hi"},
        {"name": "From", "value": "A <a@x.org>"},
        {"name": "To", "value": "b@x.org"},
        {"name": "Date", "value": "Fri, 06 Nov 2026 07:39:51 -0800"},
        {"name": "DKIM-Signature", "value": "v=1; " + "y" * 400},
    ]},
}


def test_message_record_keys():
    assert project_message_record(RAW_MESSAGE) == {
        "id": "m1", "threadId": "t1", "labelIds": ["INBOX", "UNREAD"], "subject": "Hi",
        "from": "A <a@x.org>", "to": "b@x.org", "date": "Fri, 06 Nov 2026 07:39:51 -0800",
        "snippet": "See you then",
    }


def test_message_record_cc_only_when_present():
    with_cc = {**RAW_MESSAGE, "payload": {"headers": [{"name": "cc", "value": "c@x.org"}]}}
    assert project_message_record(with_cc)["cc"] == "c@x.org"
    assert "cc" not in project_message_record(RAW_MESSAGE)


@pytest.mark.anyio
async def test_search_messages_returns_records(monkeypatch):
    fake, patched = make_fake_api()
    fake.search_with_details = lambda **kw: (fake._record("search_with_details", **kw),
                                             {"items": [RAW_MESSAGE], "nextPageToken": "p2"})[1]
    monkeypatch.setattr(server, "_api", patched)
    data = parse_result(await server.mcp.call_tool("search_messages", {"query": "from:a"}))
    assert data["data"] == [project_message_record(RAW_MESSAGE)]
    assert data["next_page_token"] == "p2"
    assert fake.calls["search_with_details"]["format"] == "metadata"
    tools = {t.name: t for t in await server.mcp.list_tools()}
    for name in ("search_messages", "search_messages_all_accounts"):
        assert "format" not in tools[name].input_schema["properties"], name
        assert "get_message" in tools[name].description, name


@pytest.mark.anyio
async def test_recorded_search_fits(monkeypatch):
    # The 10 messages a 7-day search returned on 2026-11-07, 322 headers in all, with every
    # header value, snippet and id replaced; raw, the result held 77,699 characters.
    raw = json.loads((Path(__file__).parent / "fixtures" / "mail_search.json").read_text())
    fake, patched = make_fake_api()
    fake.search_with_details = lambda **kw: {"items": raw}
    monkeypatch.setattr(server, "_api", patched)
    text = (await server.mcp.call_tool("search_messages", {"query": "newer_than:7d"})).content[0].text
    assert len(text) <= 8000, len(text)
