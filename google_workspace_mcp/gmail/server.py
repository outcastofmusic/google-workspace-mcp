"""Gmail MCP server: read, search, send, label, and manage messages."""
from __future__ import annotations

import json
import os
import shutil
import tempfile

from ..contacts.tools import register_contacts_tools
from ..core import (
    DEFAULT_MAX_CHARS,
    build_server,
    cap_items,
    cap_text,
    get_api,
    ok,
    register,
    run_tool,
)
from ..core.fanout import fan_out, merged, tag
from ..core.paths import free_path, safe_dir, safe_path, safe_paths
from .changes_api import GmailChangesAPI

mcp = build_server(
    "gmail-mcp",
    "Gmail: read, search, send, draft, label, and manage messages for one or more Google accounts.",
    service_name="gmail",
)
register_contacts_tools(mcp, service_name="gmail")


def _api(account=None):
    return get_api("gmail", GmailChangesAPI, account)


# ---------------------------------------------------------------------------
# READ tools (no mutating flag)
# ---------------------------------------------------------------------------

@register(mcp)
def get_profile(account: str | None = None) -> dict:
    """Get Gmail profile info (email address, message count, thread count, history ID)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.get_profile())
    return ok(resolved, data)


@register(mcp, core=True)
def list_messages(
    account: str | None = None,
    query: str | None = None,
    label_ids: list[str] | None = None,
    max_results: int = 10,
    page_token: str | None = None,
) -> dict:
    """List messages, optionally filtered by a Gmail search query (e.g. 'from:bob is:unread').

    Pass page_token from a previous response's next_page_token to continue.
    """
    api, resolved = _api(account)
    page = run_tool(
        lambda: api.list_messages_page(
            max_results=max_results, label_ids=label_ids, query=query, page_token=page_token
        )
    )
    return ok(resolved, page["items"], next_page_token=page.get("nextPageToken"))


@register(mcp, core=True)
def search_messages(
    account: str | None = None,
    query: str | None = None,
    label_ids: list[str] | None = None,
    max_results: int = 10,
    format: str = "metadata",
    page_token: str | None = None,
) -> dict:
    """Search messages and return full details (headers, snippet, labels) in one call.

    Pass page_token from a previous response's next_page_token to continue.
    """
    api, resolved = _api(account)
    page = run_tool(
        lambda: api.search_with_details(
            max_results=max_results,
            label_ids=label_ids,
            query=query,
            format=format,
            page_token=page_token,
        )
    )
    return ok(resolved, page["items"], next_page_token=page.get("nextPageToken"))



@register(mcp)
def search_messages_all_accounts(
    accounts: list[str] | None = None,
    *,
    query: str,
    max_results_per_account: int = 10,
    format: str = "metadata",
) -> dict:
    """Search messages in every configured account (or only `accounts`) and merge them, newest first.

    Each item carries an `account` key. An account that fails is listed under
    `errors`; the others still return. Use search_messages to page one account.
    """
    results, errors = fan_out(
        "gmail",
        GmailChangesAPI,
        accounts,
        lambda api: api.search_with_details(
            max_results=max_results_per_account, label_ids=None, query=query,
            format=format, page_token=None,
        ),
    )
    items = [m for acct, page in results.items() for m in tag(acct, page["items"])]
    items.sort(key=lambda m: int(m.get("internalDate") or 0), reverse=True)
    return merged(items, results, errors)

_ATTACHMENT_HINT = "Inline attachment data was dropped to fit max_chars; fetch files with download_attachment."


def _drop_attachment_data(part: dict) -> bool:
    """Remove inline base64 data from attachment parts (format=full); True if any was removed."""
    dropped = False
    body = part.get("body") or {}
    if part.get("filename") and "data" in body:
        del body["data"]
        dropped = True
    for child in part.get("parts") or []:
        dropped |= _drop_attachment_data(child)
    return dropped


def _too_big(data, max_chars: int | None) -> bool:
    return len(json.dumps(data, default=str)) > (DEFAULT_MAX_CHARS if max_chars is None else max_chars)


@register(mcp, core=True)
def get_message(
    account: str | None = None,
    *,
    message_id: str,
    format: str = "text",
    max_chars: int | None = None,
    offset: int = 0,
) -> dict:
    """Get a specific message by ID.

    format: text (default, readable body), full (raw MIME), metadata, minimal, or raw. Use text unless you need MIME parts.
    Output is capped at max_chars (default 100000): for text the body and for raw
    the base64 string page via offset/next_offset when truncated=true; for full,
    inline attachment data is dropped (content_trimmed=true; use download_attachment).
    """
    api, resolved = _api(account)
    kind = (format or "text").strip().lower()
    meta: dict = {}
    if kind == "text":
        data = run_tool(lambda: api.get_message_text(message_id))
        data["body_text"], meta = cap_text(
            data["body_text"], max_chars, offset,
            hint="Call get_message again with offset=next_offset for more of the body.",
        )
    else:
        data = run_tool(lambda: api.get_message(message_id, format=kind))
        if kind == "raw":
            data["raw"], meta = cap_text(
                data.get("raw") or "", max_chars, offset,
                hint="Call get_message again with offset=next_offset, or use format=text.",
            )
        elif kind == "full" and _too_big(data, max_chars) and _drop_attachment_data(data.get("payload") or {}):
            meta = {"content_trimmed": True, "hint": _ATTACHMENT_HINT}
    return ok(resolved, data, **meta)


@register(mcp)
def list_threads(
    account: str | None = None,
    query: str | None = None,
    max_results: int = 10,
    page_token: str | None = None,
) -> dict:
    """List email threads, optionally filtered by a Gmail search query.

    Pass page_token from a previous response's next_page_token to continue.
    """
    api, resolved = _api(account)
    page = run_tool(
        lambda: api.list_threads_page(max_results=max_results, query=query, page_token=page_token)
    )
    return ok(resolved, page["items"], next_page_token=page.get("nextPageToken"))


@register(mcp, core=True)
def get_thread(
    account: str | None = None,
    *,
    thread_id: str,
    format: str = "text",
    max_chars: int | None = None,
    offset: int = 0,
) -> dict:
    """Get a whole conversation (all messages in the thread) by ID in one call.

    format: text (default, readable bodies), full (raw MIME), metadata, or minimal. Use text unless you need MIME parts.
    Output is capped at max_chars (default 100000). Long threads return whole
    messages from offset; when truncated=true, call again with offset=next_offset
    (a message index). A single oversized body is cut (body_truncated=true; page
    it with get_message), and format=full drops inline attachment data; either
    sets content_trimmed=true.
    """
    api, resolved = _api(account)
    kind = (format or "text").strip().lower()
    notes = []
    if kind == "text":
        data = run_tool(lambda: api.get_thread_text(thread_id))
        for m in data.get("messages") or []:
            m["body_text"], body_meta = cap_text(m["body_text"], max_chars)
            if body_meta:
                m.update(body_truncated=True, body_total_chars=body_meta["total_chars"])
    else:
        data = run_tool(lambda: api.get_thread(thread_id, format=kind))
        if kind == "full" and _too_big(data, max_chars):
            if any([_drop_attachment_data(m.get("payload") or {}) for m in data.get("messages") or []]):
                notes = [_ATTACHMENT_HINT]
    data["messages"], meta = cap_items(data.get("messages") or [], max_chars, offset)
    if any(m.get("body_truncated") for m in data["messages"]):
        notes.append("Bodies marked body_truncated were cut; page through them with get_message and offset.")
    if notes:
        # Content was reduced in place; truncated stays reserved for "more messages via next_offset".
        meta["content_trimmed"] = True
    if meta.get("truncated"):
        notes.append("Call get_thread again with offset=next_offset for the next messages.")
    if notes:
        meta["hint"] = " ".join(notes)
    return ok(resolved, data, **meta)


@register(mcp, core=True)
def download_attachment(
    account: str | None = None,
    *,
    message_id: str,
    output_dir: str | None = None,
    attachment_id: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Save a message's attachment(s) to local disk and return their paths.

    Reads from Gmail and writes the files to output_dir (defaults to the system
    temp dir, created if needed) using each attachment's own filename. Pass
    attachment_id to save only that one. Existing files in output_dir are not
    replaced unless overwrite=True; in the temp dir a clash gets a new name. Returns a list of {"filename", "path", "mime_type", "size"} dicts.
    """
    api, resolved = _api(account)

    def fetch():
        out_dir = safe_dir(output_dir) if output_dir else tempfile.gettempdir()
        # Download into a private dir first so a name clash is caught before
        # any existing file is touched.
        with tempfile.TemporaryDirectory() as staging:
            fetched = api.download_attachment(message_id, output_dir=staging, attachment_id=attachment_id)
            # Same-named attachments share one staged file (last one wins).
            files = list({f["path"]: f for f in fetched}.values())
            dests = []
            for f in files:
                dest = os.path.join(out_dir, f["filename"])
                if not output_dir and not overwrite:
                    dest = free_path(dest)
                dests.append(safe_path(dest, write=True, overwrite=overwrite))
            os.makedirs(out_dir, exist_ok=True)
            for f, dest in zip(files, dests):
                shutil.move(f["path"], dest)
                f["path"] = dest
        return files

    data = run_tool(fetch)
    return ok(resolved, data)


@register(mcp, core=True)
def list_labels(account: str | None = None) -> dict:
    """List all labels (system and user-created) in the mailbox."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.list_labels())
    return ok(resolved, data)


@register(mcp)
def get_label(account: str | None = None, *, label_id: str) -> dict:
    """Get a specific label by ID."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.get_label(label_id))
    return ok(resolved, data)


@register(mcp, core=True)
def list_drafts(
    account: str | None = None,
    max_results: int = 10,
    page_token: str | None = None,
) -> dict:
    """List draft messages.

    Pass page_token from a previous response's next_page_token to continue.
    """
    api, resolved = _api(account)
    page = run_tool(
        lambda: api.list_drafts_page(max_results=max_results, page_token=page_token)
    )
    return ok(resolved, page["items"], next_page_token=page.get("nextPageToken"))


@register(mcp)
def get_draft(account: str | None = None, *, draft_id: str) -> dict:
    """Get a specific draft by ID."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.get_draft(draft_id))
    return ok(resolved, data)


@register(mcp)
def list_filters(account: str | None = None) -> dict:
    """List all Gmail filters configured for this account."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.list_filters())
    return ok(resolved, data)


@register(mcp)
def get_filter(account: str | None = None, *, filter_id: str) -> dict:
    """Get a specific Gmail filter by ID."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.get_filter(filter_id))
    return ok(resolved, data)


@register(mcp)
def get_changes_start_token(account: str | None = None) -> dict:
    """Get a change-feed baseline token for the mailbox. Save the returned
    start_history_token and pass it to list_changes on the next run to get
    only what changed in between."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.get_changes_start_token())
    return ok(resolved, data)


@register(mcp)
def list_changes(
    account: str | None = None,
    *,
    start_history_token: str,
    history_types: list[str] | None = None,
    label_id: str | None = None,
    max_results: int = 100,
    page_token: str | None = None,
) -> dict:
    """List mailbox changes since start_history_token (from
    get_changes_start_token or a previous list_changes). Returns typed
    entries (message_added / message_deleted / labels_added / labels_removed)
    plus new_history_token to save for the next poll. Page through
    next_page_token before saving the new token. history_types filters to
    e.g. ['messageAdded']; label_id filters to one label (e.g. 'INBOX')."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.list_changes(
            start_history_token,
            history_types=history_types,
            label_id=label_id,
            max_results=max_results,
            page_token=page_token,
        )
    )
    return ok(resolved, data)


# ---------------------------------------------------------------------------
# WRITE tools (mutating=True)
# ---------------------------------------------------------------------------

@register(mcp, mutating=True, destructive=True, open_world=True, core=True)
def send_message(
    account: str | None = None,
    *,
    to: str,
    subject: str = "",
    body: str = "",
    cc: str | None = None,
    attachments: list[str] | None = None,
    html: bool = False,
) -> dict:
    """Send an email message.

    attachments: list of LOCAL file paths to attach (e.g. ["/Users/me/deck.pdf"]).
    The files are read from disk on the server, so large binaries are fine.
    html: set True to send `body` as HTML (inline images via <img src>, links,
    formatting). A plain-text fallback is generated automatically.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.send_message(to=to, subject=subject, body=body, cc=cc, attachments=safe_paths(attachments), html=html))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True, open_world=True, core=True)
def reply_to_message(
    account: str | None = None,
    *,
    message_id: str,
    body: str,
    reply_all: bool = False,
    additional_cc: str | None = None,
    attachments: list[str] | None = None,
) -> dict:
    """Reply to a message; set reply_all=True to reply to all recipients.

    attachments: list of LOCAL file paths to attach (read from disk server-side).
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.reply_to_message(message_id, body=body, reply_all=reply_all, additional_cc=additional_cc, attachments=safe_paths(attachments)))
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def draft_reply(
    account: str | None = None,
    *,
    message_id: str,
    body: str,
    reply_all: bool = False,
    additional_cc: str | None = None,
    attachments: list[str] | None = None,
) -> dict:
    """Create a DRAFT reply kept in the original thread (review before sending).

    Same threading as reply_to_message (In-Reply-To/References + threadId) but
    saves a draft instead of sending, so it threads correctly when you send it
    from Gmail. Set reply_all=True to CC the original recipients.
    attachments: list of LOCAL file paths to attach (read from disk server-side).
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.draft_reply(message_id, body=body, reply_all=reply_all, additional_cc=additional_cc, attachments=safe_paths(attachments)))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True, open_world=True, core=True)
def forward_message(
    account: str | None = None,
    *,
    message_id: str,
    to: str,
    body: str | None = None,
    attachments: list[str] | None = None,
) -> dict:
    """Forward a message to another recipient.

    attachments: list of LOCAL file paths to attach (read from disk server-side).
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.forward_message(message_id, to=to, body=body, attachments=safe_paths(attachments)))
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def create_draft(
    account: str | None = None,
    to: str = "",
    subject: str = "",
    body: str = "",
    cc: str | None = None,
    attachments: list[str] | None = None,
    html: bool = False,
) -> dict:
    """Create a draft message (supports cc + attachments together).

    attachments: list of LOCAL file paths to attach (read from disk server-side).
    html: set True to store `body` as HTML (inline images via <img src>, links,
    formatting). A plain-text fallback is generated automatically.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.create_draft(to=to, subject=subject, body=body, cc=cc, attachments=safe_paths(attachments), html=html))
    return ok(resolved, data)


@register(mcp, mutating=True)
def update_draft(
    account: str | None = None,
    *,
    draft_id: str,
    to: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    cc: str | None = None,
    attachments: list[str] | None = None,
    html: bool = False,
) -> dict:
    """Update an existing draft, preserving fields you don't pass.

    Only provided fields change; To/Subject/Body/Cc and existing attachments are
    kept otherwise (e.g. pass only `attachments` to add a file without wiping the
    rest). Pass `attachments` to replace the file set; omit it to keep existing.
    attachments: list of LOCAL file paths (read from disk server-side).
    html: set True when `body` is HTML (inline images via <img src>, links,
    formatting). A plain-text fallback is generated automatically.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.update_draft(draft_id, to=to, subject=subject, body=body, cc=cc, attachments=safe_paths(attachments), html=html))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True, open_world=True, core=True)
def send_draft(account: str | None = None, *, draft_id: str) -> dict:
    """Send an existing draft, completing the draft -> review -> send loop.

    Sends the draft as-is (threaded if it was a threaded draft). Returns the
    sent message.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.send_draft(draft_id))
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def modify_labels(
    account: str | None = None,
    *,
    message_id: str,
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
) -> dict:
    """Add or remove labels on a message."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.modify_message(message_id, add_label_ids=add_label_ids, remove_label_ids=remove_label_ids))
    return ok(resolved, data)


@register(mcp, mutating=True)
def mark_read(account: str | None = None, *, message_id: str) -> dict:
    """Mark a message as read (removes the UNREAD label)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.mark_as_read(message_id))
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def archive_message(account: str | None = None, *, message_id: str) -> dict:
    """Archive a message by removing it from the INBOX."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.archive_message(message_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def star_message(account: str | None = None, *, message_id: str) -> dict:
    """Star a message."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.star_message(message_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def unstar_message(account: str | None = None, *, message_id: str) -> dict:
    """Remove the star from a message."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.unstar_message(message_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def mark_as_spam(account: str | None = None, *, message_id: str) -> dict:
    """Mark a message as spam (adds SPAM label, removes INBOX)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.mark_as_spam(message_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def unmark_spam(account: str | None = None, *, message_id: str) -> dict:
    """Remove spam marking from a message (removes SPAM label, restores INBOX)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.unmark_spam(message_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def create_label(
    account: str | None = None,
    *,
    name: str,
    message_list_visibility: str = "show",
    label_list_visibility: str = "labelShow",
) -> dict:
    """Create a new label in the mailbox."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.create_label(name, message_list_visibility=message_list_visibility, label_list_visibility=label_list_visibility))
    return ok(resolved, data)


@register(mcp, mutating=True)
def update_label(
    account: str | None = None,
    *,
    label_id: str,
    name: str | None = None,
    message_list_visibility: str | None = None,
    label_list_visibility: str | None = None,
) -> dict:
    """Update an existing label's name or visibility settings."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.update_label(label_id, name=name, message_list_visibility=message_list_visibility, label_list_visibility=label_list_visibility))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True, open_world=True)
def create_filter(
    account: str | None = None,
    *,
    criteria: dict,
    action: dict,
) -> dict:
    """Create a Gmail filter with given criteria (from, to, subject) and action (addLabelIds, etc.)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.create_filter(criteria, action))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def block_sender(account: str | None = None, *, email: str) -> dict:
    """Block a sender by creating a filter that marks their emails as spam."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.block_sender(email))
    return ok(resolved, data)


@register(mcp, mutating=True)
def batch_modify_labels(
    account: str | None = None,
    *,
    message_ids: list[str],
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
) -> dict:
    """Add or remove labels on multiple messages in a single batch operation."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.batch_modify_messages(message_ids, add_label_ids=add_label_ids, remove_label_ids=remove_label_ids))
    return ok(resolved, data)


# ---------------------------------------------------------------------------
# DESTRUCTIVE tools (mutating=True, destructive=True)
# ---------------------------------------------------------------------------

@register(mcp, mutating=True, destructive=True, core=True)
def trash_message(account: str | None = None, *, message_id: str) -> dict:
    """Move a message to trash (can be recovered with untrash_message)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.trash_message(message_id))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def untrash_message(account: str | None = None, *, message_id: str) -> dict:
    """Restore a message from trash back to the inbox."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.untrash_message(message_id))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_message(account: str | None = None, *, message_id: str) -> dict:
    """Permanently delete a message (cannot be undone; use trash_message to recover later)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_message(message_id))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_label(account: str | None = None, *, label_id: str) -> dict:
    """Permanently delete a label from the mailbox."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_label(label_id))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_draft(account: str | None = None, *, draft_id: str) -> dict:
    """Permanently delete a draft (e.g. to clean up a superseded one)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_draft(draft_id))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_filter(account: str | None = None, *, filter_id: str) -> dict:
    """Permanently delete a Gmail filter."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_filter(filter_id))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def batch_trash_messages(
    account: str | None = None,
    *,
    message_ids: list[str],
) -> dict:
    """Move multiple messages to trash in a single batch operation."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.batch_trash_messages(message_ids))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def batch_untrash_messages(
    account: str | None = None,
    *,
    message_ids: list[str],
) -> dict:
    """Restore multiple messages from trash in a single batch operation."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.batch_untrash_messages(message_ids))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def batch_delete_messages(
    account: str | None = None,
    *,
    message_ids: list[str],
) -> dict:
    """Permanently delete multiple messages in a single batch operation (cannot be undone)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.batch_delete_messages(message_ids))
    return ok(resolved, data)


def main():
    mcp.run()
