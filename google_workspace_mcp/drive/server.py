"""Google Drive MCP server: search, read, upload, organize, and share files."""
from __future__ import annotations

from ..core import build_server, cap_text, get_api, ok, register, run_tool
from ..core.fanout import fan_out, merged, tag
from ..core.paths import safe_path
from .drive_api import DriveAPI

mcp = build_server(
    "gdrive-mcp",
    "Google Drive: search, read, upload, organize, and share files for one or more Google accounts.",
    service_name="drive",
)


def _api(account=None):
    return get_api("drive", DriveAPI, account)


# --- read ---
@register(mcp, core=True)
def search_files(
    account: str | None = None,
    query: str | None = None,
    name: str | None = None,
    mime_type: str | None = None,
    parent_id: str | None = None,
    full_text: str | None = None,
    drive_id: str | None = None,
    page_size: int = 25,
    page_token: str | None = None,
) -> dict:
    """Search Drive by name, mimeType, parent folder, full-text, and/or a raw q= query.

    Set drive_id to search one shared drive. Omit to search the user corpus (My Drive plus shared-drive files in that corpus).
    Pass page_token from a previous response's next_page_token to continue.
    """
    api, resolved = _api(account)
    raw = run_tool(
        lambda: api.search_files(
            query=query, name=name, mime_type=mime_type,
            parent_id=parent_id, full_text=full_text, drive_id=drive_id,
            page_size=page_size, page_token=page_token,
        )
    )
    next_token = raw.pop("nextPageToken", None)
    return ok(resolved, raw, next_page_token=next_token)



@register(mcp)
def search_files_all_accounts(
    accounts: list[str] | None = None,
    query: str | None = None,
    name: str | None = None,
    mime_type: str | None = None,
    full_text: str | None = None,
    page_size_per_account: int = 25,
) -> dict:
    """Search Drive in every configured account (or only `accounts`) and merge the files, newest first.

    Takes the same filters as search_files. Each file carries an `account` key;
    an account that fails is listed under `errors`.
    """
    results, errors = fan_out(
        "drive",
        DriveAPI,
        accounts,
        lambda api: api.search_files(
            query=query, name=name, mime_type=mime_type, parent_id=None,
            full_text=full_text, drive_id=None, page_size=page_size_per_account,
            page_token=None,
        ),
    )
    items = [f for acct, raw in results.items() for f in tag(acct, raw.get("files", []))]
    items.sort(key=lambda f: f.get("modifiedTime") or "", reverse=True)
    return merged(items, results, errors)

@register(mcp, core=True)
def list_files(
    account: str | None = None,
    folder_id: str | None = None,
    drive_id: str | None = None,
    page_size: int = 25,
    page_token: str | None = None,
) -> dict:
    """List recent files, optionally restricted to a folder's children.

    Set drive_id to list one shared drive. Omit to list the user corpus (My Drive plus shared-drive files in that corpus).
    Pass page_token from a previous response's next_page_token to continue.
    """
    api, resolved = _api(account)
    raw = run_tool(
        lambda: api.list_files(
            folder_id=folder_id, drive_id=drive_id, page_size=page_size, page_token=page_token,
        )
    )
    next_token = raw.pop("nextPageToken", None)
    return ok(resolved, raw, next_page_token=next_token)


@register(mcp)
def list_drives(
    account: str | None = None,
    page_size: int = 25,
    page_token: str | None = None,
) -> dict:
    """List shared drives the account can access.

    Pass page_token from a previous response's next_page_token to continue.
    """
    api, resolved = _api(account)
    raw = run_tool(lambda: api.list_drives(page_size=page_size, page_token=page_token))
    next_token = raw.pop("nextPageToken", None)
    return ok(resolved, raw, next_page_token=next_token)


@register(mcp, core=True)
def get_file(account: str | None = None, *, file_id: str) -> dict:
    """Get full metadata for one file id."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.get_file(file_id))
    return ok(resolved, data)


@register(mcp, core=True)
def download_file(
    account: str | None = None,
    *,
    file_id: str,
    output_path: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Download a binary (non-Google-native) file to a local path.

    Writes to output_path, or to the system temp dir using the file's name when
    omitted (a clash there gets a new name). An existing output_path is not
    replaced unless overwrite=True.
    Returns {path, bytes, name, mimeType}. Google Docs/Sheets/Slides must use
    export_file instead.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.download_file(
        file_id,
        output_path=safe_path(output_path, write=True, overwrite=overwrite) if output_path else None,
    ))
    return ok(resolved, data)


@register(mcp, core=True)
def export_file(
    account: str | None = None,
    *,
    file_id: str,
    fmt: str = "pdf",
    output_path: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Export a Google-native file (Doc/Sheet/Slide) to md/pdf/txt/csv/xlsx on disk.

    fmt: markdown/md, pdf, txt/plain (Docs); csv/xlsx (Sheets); pdf (Slides).
    Writes to output_path, or to the system temp dir when omitted (a clash there
    gets a new name). An existing output_path is not replaced unless overwrite=True.
    Returns {path, bytes, name, sourceMimeType, exportMimeType}.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.export_file(
        file_id, fmt,
        output_path=safe_path(output_path, write=True, overwrite=overwrite) if output_path else None,
    ))
    return ok(resolved, data)


@register(mcp, core=True)
def read_file_text(
    account: str | None = None,
    *,
    file_id: str,
    max_chars: int | None = None,
    offset: int = 0,
) -> dict:
    """Read small text inline (UTF-8 binary files, or Docs/Sheets exported as text).

    Returns up to max_chars characters (default 100000) starting at offset. When
    more remains the envelope has truncated=true and next_offset; call again with
    offset=next_offset for the next chunk. Files over 1 MiB raise an error — use
    download_file or export_file instead. Slides and other non-text native types
    are not supported.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.read_file_text(file_id))
    data["text"], meta = cap_text(
        data["text"], max_chars, offset,
        hint="Call read_file_text again with offset=next_offset, or save the whole "
        "file with download_file/export_file.",
    )
    return ok(resolved, data, **meta)


@register(mcp)
def list_permissions(account: str | None = None, *, file_id: str) -> dict:
    """List permissions on a file."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.list_permissions(file_id))
    return ok(resolved, data)


@register(mcp)
def list_comments(
    account: str | None = None,
    *,
    file_id: str,
    include_deleted: bool = False,
    page_size: int = 20,
    page_token: str | None = None,
) -> dict:
    """List comment threads on a file (Drive discussions, not in-editor highlights).

    Pass page_token from a previous response's next_page_token to continue.
    """
    api, resolved = _api(account)
    raw = run_tool(
        lambda: api.list_comments(file_id, include_deleted, page_size, page_token)
    )
    next_token = raw.pop("nextPageToken", None)
    return ok(resolved, raw, next_page_token=next_token)


@register(mcp)
def get_changes_start_token(account: str | None = None) -> dict:
    """Get a change-feed baseline token for Drive. Save the returned
    start_page_token and pass it to list_changes on the next run."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.get_changes_start_token())
    return ok(resolved, data)


@register(mcp)
def list_changes(
    account: str | None = None,
    *,
    page_token: str,
    page_size: int = 100,
    include_removed: bool = True,
    restrict_to_my_drive: bool = False,
) -> dict:
    """List Drive changes since page_token. Returns change entries plus
    exactly one of next_page_token (more pages now: call again with it) or
    new_start_token (caught up: save it for the next poll)."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.list_changes(
            page_token,
            page_size=page_size,
            include_removed=include_removed,
            restrict_to_my_drive=restrict_to_my_drive,
        )
    )
    return ok(resolved, data)


# --- write ---
@register(mcp, mutating=True, core=True)
def upload_file(
    account: str | None = None,
    *,
    local_path: str,
    name: str | None = None,
    parent_id: str | None = None,
    mime_type: str | None = None,
) -> dict:
    """Upload a local file to Drive. Optional parent_id and explicit mime_type.

    Set mime_type to a Google Workspace type (e.g. application/vnd.google-apps.document)
    to upload and convert a local file into a native Doc, Sheet, or Slide.
    """
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.upload_file(safe_path(local_path), name=name, parent_id=parent_id, mime_type=mime_type)
    )
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def update_file_content(
    account: str | None = None,
    *,
    file_id: str,
    local_path: str,
    mime_type: str | None = None,
) -> dict:
    """Replace an existing binary file's content from a local path.

    Cannot update Google-native files (Docs/Sheets/Slides/folders) — those must
    be edited via their respective APIs or re-uploaded as new files.
    """
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.update_file_content(file_id, safe_path(local_path), mime_type=mime_type)
    )
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def create_folder(
    account: str | None = None,
    *,
    name: str,
    parent_id: str | None = None,
) -> dict:
    """Create a folder; optional parent_id to nest inside an existing folder."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.create_folder(name, parent_id=parent_id))
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def move_file(
    account: str | None = None,
    *,
    file_id: str,
    new_parent_id: str,
) -> dict:
    """Move a file to a new parent folder (reparents; removes old parents)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.move_file(file_id, new_parent_id))
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def rename_file(account: str | None = None, *, file_id: str, name: str) -> dict:
    """Rename a file."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.rename_file(file_id, name))
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def copy_file(
    account: str | None = None,
    *,
    file_id: str,
    name: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Copy a file; optional new name and/or destination parent folder."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.copy_file(file_id, name=name, parent_id=parent_id))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True, open_world=True, core=True)
def share_file(
    account: str | None = None,
    *,
    file_id: str,
    role: str = "reader",
    email: str | None = None,
    anyone: bool = False,
    send_notification: bool = True,
) -> dict:
    """Share a file with an email address (role: reader/writer/commenter) or anyone link."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.share_file(
            file_id, role=role, email=email, anyone=anyone,
            send_notification=send_notification,
        )
    )
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def unshare_file(
    account: str | None = None,
    *,
    file_id: str,
    email: str | None = None,
    permission_id: str | None = None,
    anyone: bool = False,
) -> dict:
    """Remove a permission from a file by email, permission_id, or the anyone link."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.unshare_file(
            file_id, email=email, permission_id=permission_id, anyone=anyone,
        )
    )
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def trash_file(account: str | None = None, *, file_id: str) -> dict:
    """Move a file to trash (reversible from the Drive UI)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.trash_file(file_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def untrash_file(account: str | None = None, *, file_id: str) -> dict:
    """Restore a file from trash."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.untrash_file(file_id))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_file(account: str | None = None, *, file_id: str) -> dict:
    """Permanently delete a file (cannot be undone)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_file(file_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def add_comment(
    account: str | None = None,
    *,
    file_id: str,
    content: str,
    quoted_text: str | None = None,
) -> dict:
    """Add a comment on a file. Google will not highlight in the editor; optional quoted_text is remembered as the quoted snippet."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.create_comment(file_id, content, quoted_text=quoted_text))
    return ok(resolved, data)


@register(mcp, mutating=True)
def reply_to_comment(
    account: str | None = None,
    *,
    file_id: str,
    comment_id: str,
    content: str | None = None,
    action: str | None = None,
) -> dict:
    """Reply to a Drive discussion, or pass action='resolve' / 'reopen'."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.reply_to_comment(file_id, comment_id, content, action))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_comment(account: str | None = None, *, file_id: str, comment_id: str) -> dict:
    """Delete a Drive discussion thread."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_comment(file_id, comment_id))
    return ok(resolved, data)


# --- batch ---
def _coalesce_move_items(items, file_ids, new_parent_id):
    if items:
        return items
    if file_ids and new_parent_id:
        return [{"file_id": fid, "new_parent_id": new_parent_id} for fid in file_ids]
    raise ValueError(
        "provide items=[{file_id, new_parent_id}, ...] or file_ids=[...] with new_parent_id"
    )


def _batch_envelope(resolved, data):
    if data.get("total", 0) > 0 and data.get("succeeded", 0) == 0:
        return {"ok": False, "account": resolved, "data": data}
    return ok(resolved, data)


@register(mcp, mutating=True)
def batch_move_files(
    account: str | None = None,
    items: list | None = None,
    file_ids: list | None = None,
    new_parent_id: str | None = None,
) -> dict:
    """Move many files in one call.

    Pass items=[{file_id, new_parent_id}, ...] for per-file destinations, or
    file_ids=[...] with a shared new_parent_id. Per-file errors are reported
    individually without aborting the rest; returns {total, succeeded, failed, results}.
    If every item fails, ok is false; per-item errors still live in data.results.
    """
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.batch_move_files(_coalesce_move_items(items, file_ids, new_parent_id))
    )
    return _batch_envelope(resolved, data)


@register(mcp, mutating=True)
def batch_create_folders(account: str | None = None, *, items: list) -> dict:
    """Create many folders in one call.

    items=[{name, parent_id?}, ...]. Returns each created folder's id so you can
    follow up with batch_move_files. Per-folder errors are reported individually.
    If every item fails, ok is false; per-item errors still live in data.results.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.batch_create_folders(items))
    return _batch_envelope(resolved, data)


@register(mcp, mutating=True)
def batch_trash_files(account: str | None = None, *, file_ids: list) -> dict:
    """Trash many files in one call (reversible). file_ids=[...].

    Per-file errors are reported individually; returns {total, succeeded, failed, results}.
    If every item fails, ok is false; per-item errors still live in data.results.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.batch_trash_files(file_ids))
    return _batch_envelope(resolved, data)


@register(mcp, mutating=True, destructive=True)
def batch_delete_files(account: str | None = None, *, file_ids: list) -> dict:
    """Permanently delete many files in one call. file_ids=[...].

    Per-file errors are reported individually; returns {total, succeeded, failed, results}.
    If every item fails, ok is false; per-item errors still live in data.results.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.batch_delete_files(file_ids))
    return _batch_envelope(resolved, data)


def main():
    mcp.run()
