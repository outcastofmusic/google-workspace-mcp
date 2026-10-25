"""Google Drive API wrapper (search, read, upload, organize, share)."""
from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import tempfile

import google_auth_core as core
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from ..core.paths import free_path, safe_path
from . import extract

GOOGLE_APPS_PREFIX = "application/vnd.google-apps."
FOLDER_MIME = "application/vnd.google-apps.folder"
DOCUMENT_MIME = "application/vnd.google-apps.document"
SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"
PRESENTATION_MIME = "application/vnd.google-apps.presentation"

# Local extensions -> source media mime when converting into Google-native files.
CONVERSION_SOURCE_MIMES: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

CONVERSION_DEFAULT_SOURCE: dict[str, str] = {
    DOCUMENT_MIME: "text/plain",
    SPREADSHEET_MIME: "text/csv",
    PRESENTATION_MIME: "application/pdf",
}

FILE_LIST_FIELDS = "nextPageToken, files(id, name, mimeType, size, modifiedTime, parents)"
FOLDER_TREE_FIELDS = "nextPageToken, files(id, name, mimeType, size, modifiedTime, md5Checksum, webViewLink)"
FILE_METADATA_FIELDS = "id, name, mimeType, size, modifiedTime, parents, webViewLink, trashed, owners, shared"
CHANGE_LIST_FIELDS = (
    "nextPageToken,newStartPageToken,"
    "changes(fileId,removed,time,file(id,name,mimeType,modifiedTime,trashed,parents))"
)
COMMENT_FIELDS = (
    "id,content,author(displayName),createdTime,modifiedTime,resolved,deleted,"
    "anchor,quotedFileContent(value,mimeType),"
    "replies(id,content,author(displayName),createdTime,modifiedTime,action,deleted)"
)
COMMENT_LIST_FIELDS = f"nextPageToken, comments({COMMENT_FIELDS})"
REPLY_FIELDS = "id,content,author(displayName),createdTime,modifiedTime,action,deleted"

# Max bytes read_file_text will download (1 MiB); max_chars/offset page through it.
TEXT_READ_MAX_BYTES = 1_048_576
# Max bytes read_file_text downloads for PDF/Office files and native exports.
DOCUMENT_READ_MAX_BYTES = 25 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 8 * 1024 * 1024

# format alias -> export mimeType, keyed by source Google Apps mimeType.
EXPORT_FORMATS: dict[str, dict[str, str]] = {
    "application/vnd.google-apps.document": {
        "markdown": "text/markdown",
        "md": "text/markdown",
        "pdf": "application/pdf",
        "txt": "text/plain",
        "plain": "text/plain",
    },
    "application/vnd.google-apps.spreadsheet": {
        "csv": "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    },
    "application/vnd.google-apps.presentation": {
        "pdf": "application/pdf",
    },
}


def _is_google_native(mime_type: str | None) -> bool:
    return bool(mime_type and mime_type.startswith(GOOGLE_APPS_PREFIX))


def _sanitize_filename(name: str) -> str:
    base = os.path.basename(name) or "download"
    return re.sub(r'[^\w.\- ]', "_", base)


def _default_output_path(name: str, suffix: str = "") -> str:
    out_dir = tempfile.gettempdir()
    os.makedirs(out_dir, exist_ok=True)
    stem, ext = os.path.splitext(_sanitize_filename(name))
    if suffix and not ext:
        ext = suffix if suffix.startswith(".") else f".{suffix}"
    # Never replace an earlier download; still apply the store/root guard.
    return safe_path(free_path(os.path.join(out_dir, f"{stem}{ext}")), write=True)


def _resolve_export_mime(source_mime: str, fmt: str) -> str:
    mapping = EXPORT_FORMATS.get(source_mime)
    if not mapping:
        raise ValueError(
            f"export_file does not support source mimeType {source_mime!r}; "
            f"supported: {sorted(EXPORT_FORMATS)}"
        )
    export_mime = mapping.get(fmt.lower())
    if not export_mime:
        raise ValueError(
            f"unknown export format {fmt!r} for {source_mime!r}; "
            f"allowed: {sorted(mapping)}"
        )
    return export_mime


def _resolve_upload_mimes(local_path: str, mime_type: str | None) -> tuple[str | None, str]:
    """Return (metadata mimeType for create body, media upload mimeType).

    When ``mime_type`` is a Google-native target (Doc/Sheet/Slide), Drive converts
    the uploaded bytes from the resolved *source* media type.
    """
    guessed, _ = mimetypes.guess_type(local_path)
    ext = os.path.splitext(local_path)[1].lower()

    if mime_type and _is_google_native(mime_type):
        if mime_type == FOLDER_MIME:
            raise ValueError("upload_file cannot create a folder; use create_folder")
        source_mime = CONVERSION_SOURCE_MIMES.get(ext) or guessed
        if not source_mime or _is_google_native(source_mime):
            source_mime = CONVERSION_DEFAULT_SOURCE.get(mime_type, "text/plain")
        return mime_type, source_mime

    media_mime = mime_type or guessed or "application/octet-stream"
    return None, media_mime


def normalize_drive_change(change):
    """Normalize a Drive changes.list entry for agent consumption."""
    file_obj = change.get("file")
    removed = bool(change.get("removed"))
    if file_obj and file_obj.get("trashed"):
        removed = True
    if removed and not file_obj:
        norm_file = None
    elif file_obj:
        norm_file = {
            "id": file_obj.get("id"),
            "name": file_obj.get("name"),
            "mimeType": file_obj.get("mimeType"),
            "modifiedTime": file_obj.get("modifiedTime"),
            "trashed": file_obj.get("trashed"),
            "parents": file_obj.get("parents"),
        }
    else:
        norm_file = None
    return {
        "file_id": change.get("fileId"),
        "removed": removed,
        "time": change.get("time"),
        "file": norm_file,
    }


def _download_to_bytes(request, max_bytes: int) -> bytes:
    """Download into memory, giving up as soon as the content passes max_bytes."""
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request, chunksize=DOWNLOAD_CHUNK_BYTES)
    done = False
    while not done:
        _, done = downloader.next_chunk()
        if buf.tell() > max_bytes:
            raise ValueError(
                f"file content is over {max_bytes} bytes, exceeding read_file_text cap; "
                "use download_file or export_file instead"
            )
    return buf.getvalue()


def _download_to_file(request, path: str) -> int:
    """Stream a download straight to disk so big files never sit in memory.

    Writes to a temp file next to ``path`` and renames it into place only on
    success, so a failed download never truncates or deletes an existing file.
    """
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            downloader = MediaIoBaseDownload(f, request, chunksize=DOWNLOAD_CHUNK_BYTES)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            size = f.tell()
        os.replace(tmp, path)
        return size
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def place_in_folder(drive_service, file_id: str, folder_id: str) -> None:
    """Move a file just created elsewhere (Docs and Sheets create calls cannot
    set a parent) into ``folder_id``.

    If the move fails, the new file is trashed and the error re-raised, so a
    failed call leaves nothing behind outside the folder.
    """
    files = drive_service.files()
    try:
        meta = files.get(fileId=file_id, fields="parents", supportsAllDrives=True).execute()
        files.update(
            fileId=file_id,
            addParents=folder_id,
            removeParents=",".join(meta.get("parents") or []),
            fields="id, parents",
            supportsAllDrives=True,
        ).execute()
    except Exception:
        try:
            files.update(
                fileId=file_id, body={"trashed": True}, supportsAllDrives=True
            ).execute()
        except Exception:  # noqa: BLE001 - report the move failure, not the cleanup
            pass
        raise


class DriveAPI:
    def __init__(self, account=None):
        self.account = account
        self.service = core.get_service("drive", "v3", account=account)

    def _files(self):
        return self.service.files()

    def _all_drives(self, **extra):
        return {"supportsAllDrives": True, **extra}

    def search_files(
        self,
        query: str | None = None,
        name: str | None = None,
        mime_type: str | None = None,
        parent_id: str | None = None,
        full_text: str | None = None,
        drive_id: str | None = None,
        page_size: int = 25,
        page_token: str | None = None,
    ):
        parts = ["trashed = false"]
        if query:
            parts.append(f"({query})")
        if name:
            escaped = name.replace("'", "\\'")
            parts.append(f"name contains '{escaped}'")
        if mime_type:
            parts.append(f"mimeType = '{mime_type}'")
        if parent_id:
            parts.append(f"'{parent_id}' in parents")
        if full_text:
            escaped = full_text.replace("'", "\\'")
            parts.append(f"fullText contains '{escaped}'")
        q = " and ".join(parts)
        params = self._all_drives(
            q=q,
            pageSize=page_size,
            fields=FILE_LIST_FIELDS,
            includeItemsFromAllDrives=True,
        )
        if drive_id:
            params["corpora"] = "drive"
            params["driveId"] = drive_id
        if page_token:
            params["pageToken"] = page_token
        return self._files().list(**params).execute()

    def list_files(
        self,
        folder_id: str | None = None,
        drive_id: str | None = None,
        page_size: int = 25,
        page_token: str | None = None,
    ):
        q = "trashed = false"
        if folder_id:
            q += f" and '{folder_id}' in parents"
        params = self._all_drives(
            q=q,
            orderBy="modifiedTime desc",
            pageSize=page_size,
            fields=FILE_LIST_FIELDS,
            includeItemsFromAllDrives=True,
        )
        if drive_id:
            params["corpora"] = "drive"
            params["driveId"] = drive_id
        if page_token:
            params["pageToken"] = page_token
        return self._files().list(**params).execute()

    def list_drives(self, page_size: int = 25, page_token: str | None = None):
        params: dict = {
            "pageSize": page_size,
            "fields": "nextPageToken, drives(id, name)",
        }
        if page_token:
            params["pageToken"] = page_token
        return self.service.drives().list(**params).execute()

    def get_file(self, file_id: str):
        return self._files().get(
            **self._all_drives(fileId=file_id, fields=FILE_METADATA_FIELDS)
        ).execute()

    def get_parents(self, file_id: str) -> list:
        """Parent folder ids of a file as this account sees them ([] for a root
        or a file only shared with the account). Used by the client scope."""
        meta = self._files().get(**self._all_drives(fileId=file_id, fields="id,parents")).execute()
        return list(meta.get("parents") or [])

    def download_file(self, file_id: str, output_path: str | None = None):
        meta = self.get_file(file_id)
        mime = meta.get("mimeType", "")
        if _is_google_native(mime):
            raise ValueError(
                f"download_file only supports binary files; {mime!r} is a Google-native "
                "file — use export_file or read_file_text instead"
            )
        if output_path is None:
            output_path = _default_output_path(meta.get("name", file_id))
        else:
            out_dir = os.path.dirname(os.path.abspath(output_path))
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
        request = self._files().get_media(**self._all_drives(fileId=file_id))
        size = _download_to_file(request, output_path)
        return {"path": output_path, "bytes": size, "name": meta.get("name"), "mimeType": mime}

    def export_file(self, file_id: str, fmt: str, output_path: str | None = None):
        meta = self.get_file(file_id)
        source_mime = meta.get("mimeType", "")
        export_mime = _resolve_export_mime(source_mime, fmt)
        if output_path is None:
            ext = mimetypes.guess_extension(export_mime) or f".{fmt.lower()}"
            output_path = _default_output_path(meta.get("name", file_id), suffix=ext)
        else:
            out_dir = os.path.dirname(os.path.abspath(output_path))
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
        request = self._files().export_media(fileId=file_id, mimeType=export_mime)
        size = _download_to_file(request, output_path)
        return {
            "path": output_path,
            "bytes": size,
            "name": meta.get("name"),
            "sourceMimeType": source_mime,
            "exportMimeType": export_mime,
        }

    def read_file_text(
        self,
        file_id: str,
        max_bytes: int = TEXT_READ_MAX_BYTES,
        pages: str | None = None,
    ):
        """Text of one file: plain text as is; PDF, Word, PowerPoint, Excel and
        native Docs, Sheets and Slides extracted with page/slide/sheet markers."""
        meta = self.get_file(file_id)
        mime = meta.get("mimeType", "")
        base = {"fileId": file_id, "name": meta.get("name"), "mimeType": mime}
        if mime == FOLDER_MIME:
            raise ValueError("read_file_text cannot read a folder; use list_folder_tree")
        if mime == "application/vnd.google-apps.document":
            if pages:
                raise ValueError("pages applies only to PDF files and presentations")
            request = self._files().export_media(fileId=file_id, mimeType="text/plain")
            data = _download_to_bytes(request, max_bytes=DOCUMENT_READ_MAX_BYTES)
            return {**base, "bytes": len(data), "text": data.decode("utf-8", errors="replace")}
        native_as = {
            "application/vnd.google-apps.spreadsheet": extract.XLSX_MIME,
            PRESENTATION_MIME: extract.PPTX_MIME,
        }
        if mime in native_as or mime in extract.EXTRACTABLE:
            if mime in native_as:
                request = self._files().export_media(fileId=file_id, mimeType=native_as[mime])
                kind = native_as[mime]
            else:
                if int(meta.get("size") or 0) > DOCUMENT_READ_MAX_BYTES:
                    raise ValueError(
                        f"file is {meta['size']} bytes, exceeding the read_file_text cap of "
                        f"{DOCUMENT_READ_MAX_BYTES} for documents; use download_file instead"
                    )
                request = self._files().get_media(**self._all_drives(fileId=file_id))
                kind = mime
            data = _download_to_bytes(request, DOCUMENT_READ_MAX_BYTES)
            out = extract.extract(data, kind, pages)
            return {**base, "bytes": len(data), **out}
        if _is_google_native(mime):
            raise ValueError(f"read_file_text cannot extract text from {mime!r}; use export_file")
        if pages:
            raise ValueError("pages applies only to PDF files and presentations")
        if int(meta.get("size") or 0) > max_bytes:
            raise ValueError(
                f"file is {meta['size']} bytes, exceeding read_file_text cap of {max_bytes}; "
                "use download_file or export_file instead"
            )
        request = self._files().get_media(**self._all_drives(fileId=file_id))
        data = _download_to_bytes(request, max_bytes)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                "file is not UTF-8 text or a supported document; use download_file"
            ) from exc
        return {**base, "bytes": len(data), "text": text}

    def list_folder_tree(self, folder_id: str, max_files: int = 500, max_depth: int = 10):
        """Every non-folder file under ``folder_id``, breadth first, with its folder path."""
        root = self.get_file(folder_id)
        if root.get("mimeType") != FOLDER_MIME:
            raise ValueError(f"{folder_id} is not a folder")
        files: list[dict] = []
        seen = {folder_id}
        queue = [(folder_id, root.get("name") or "", 0)]
        truncated = False
        while queue and not truncated:
            current, path, depth = queue.pop(0)
            token = None
            while True:
                params = self._all_drives(
                    q=f"trashed = false and '{current}' in parents",
                    pageSize=1000,
                    orderBy="folder,name",
                    fields=FOLDER_TREE_FIELDS,
                    includeItemsFromAllDrives=True,
                )
                if token:
                    params["pageToken"] = token
                page = self._files().list(**params).execute()
                for f in page.get("files", []):
                    if f.get("mimeType") == FOLDER_MIME:
                        if depth + 1 <= max_depth and f["id"] not in seen:
                            seen.add(f["id"])
                            queue.append((f["id"], f"{path}/{f.get('name', '')}", depth + 1))
                        continue
                    if len(files) >= max_files:
                        truncated = True
                        break
                    files.append({**f, "path": path})
                token = page.get("nextPageToken")
                if truncated or not token:
                    break
        return {"folder": {"id": folder_id, "name": root.get("name")}, "files": files,
                "truncated": truncated}

    def upload_file(
        self,
        local_path: str,
        name: str | None = None,
        parent_id: str | None = None,
        mime_type: str | None = None,
    ):
        if not os.path.isfile(local_path):
            raise ValueError(f"local path does not exist or is not a file: {local_path!r}")
        file_name = name or os.path.basename(local_path)
        target_mime, media_mime = _resolve_upload_mimes(local_path, mime_type)
        body: dict = {"name": file_name}
        if target_mime:
            body["mimeType"] = target_mime
        if parent_id:
            body["parents"] = [parent_id]
        media = MediaFileUpload(local_path, mimetype=media_mime, resumable=True)
        created = self._files().create(
            **self._all_drives(body=body, media_body=media, fields=FILE_METADATA_FIELDS)
        ).execute()
        return created

    def update_file_content(self, file_id: str, local_path: str, mime_type: str | None = None):
        if not os.path.isfile(local_path):
            raise ValueError(f"local path does not exist or is not a file: {local_path!r}")
        meta = self.get_file(file_id)
        mime = meta.get("mimeType", "")
        if _is_google_native(mime):
            raise ValueError(
                f"update_file_content cannot replace bytes on Google-native files ({mime!r}); "
                "edit via the Docs/Sheets/Slides APIs or export, edit locally, and re-upload"
            )
        guessed, _ = mimetypes.guess_type(local_path)
        media_mime = mime_type or guessed or mime or "application/octet-stream"
        media = MediaFileUpload(local_path, mimetype=media_mime, resumable=True)
        return self._files().update(
            **self._all_drives(fileId=file_id, media_body=media, fields=FILE_METADATA_FIELDS)
        ).execute()

    def create_folder(self, name: str, parent_id: str | None = None):
        body: dict = {"name": name, "mimeType": FOLDER_MIME}
        if parent_id:
            body["parents"] = [parent_id]
        return self._files().create(
            **self._all_drives(body=body, fields=FILE_METADATA_FIELDS)
        ).execute()

    def move_file(self, file_id: str, new_parent_id: str):
        meta = self.get_file(file_id)
        previous_parents = ",".join(meta.get("parents", []))
        return self._files().update(
            **self._all_drives(
                fileId=file_id,
                addParents=new_parent_id,
                removeParents=previous_parents,
                fields=FILE_METADATA_FIELDS,
            )
        ).execute()

    def rename_file(self, file_id: str, name: str):
        return self._files().update(
            **self._all_drives(fileId=file_id, body={"name": name}, fields=FILE_METADATA_FIELDS)
        ).execute()

    def copy_file(self, file_id: str, name: str | None = None, parent_id: str | None = None):
        body: dict = {}
        if name:
            body["name"] = name
        if parent_id:
            body["parents"] = [parent_id]
        return self._files().copy(
            **self._all_drives(fileId=file_id, body=body, fields=FILE_METADATA_FIELDS)
        ).execute()

    def share_file(
        self,
        file_id: str,
        role: str = "reader",
        email: str | None = None,
        anyone: bool = False,
        send_notification: bool = True,
    ):
        if anyone:
            body = {"type": "anyone", "role": role}
        elif email:
            body = {"type": "user", "role": role, "emailAddress": email}
        else:
            raise ValueError("share_file requires email or anyone=True")
        params = self._all_drives(
            fileId=file_id,
            body=body,
            fields="id, type, role, emailAddress",
        )
        if not anyone:
            params["sendNotificationEmail"] = send_notification
        return self.service.permissions().create(**params).execute()

    def list_permissions(self, file_id: str):
        perms = []
        page_token = None
        while True:
            params = self._all_drives(
                fileId=file_id,
                fields="nextPageToken, permissions(id, type, role, emailAddress, displayName)",
            )
            if page_token:
                params["pageToken"] = page_token
            result = self.service.permissions().list(**params).execute()
            perms.extend(result.get("permissions") or [])
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return {"permissions": perms}

    def unshare_file(
        self,
        file_id: str,
        email: str | None = None,
        permission_id: str | None = None,
        anyone: bool = False,
    ):
        if permission_id:
            return self._delete_permission(file_id, permission_id)
        if not anyone and not email:
            raise ValueError("unshare_file requires permission_id, email, or anyone=True")
        want = email.lower() if email else None
        page_token = None
        match = None
        while True:
            params = self._all_drives(
                fileId=file_id,
                fields="nextPageToken, permissions(id, type, role, emailAddress)",
            )
            if page_token:
                params["pageToken"] = page_token
            result = self.service.permissions().list(**params).execute()
            perms = result.get("permissions") or []
            if anyone:
                match = next((p for p in perms if p.get("type") == "anyone"), None)
            elif want:
                match = next(
                    (p for p in perms if (p.get("emailAddress") or "").lower() == want),
                    None,
                )
            if match:
                break
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        if not match or not match.get("id"):
            raise ValueError("no matching permission")
        return self._delete_permission(file_id, match["id"])

    def _delete_permission(self, file_id: str, permission_id: str):
        self.service.permissions().delete(
            **self._all_drives(fileId=file_id, permissionId=permission_id)
        ).execute()
        return {"fileId": file_id, "permissionId": permission_id, "removed": True}

    def get_changes_start_token(self):
        result = self.service.changes().getStartPageToken(**self._all_drives()).execute()
        return {"start_page_token": result["startPageToken"]}

    def list_changes(
        self,
        page_token,
        page_size=100,
        include_removed=True,
        restrict_to_my_drive=False,
    ):
        if not page_token:
            raise ValueError("page_token is required")
        result = self.service.changes().list(
            **self._all_drives(
                pageToken=page_token,
                pageSize=page_size,
                includeRemoved=include_removed,
                restrictToMyDrive=restrict_to_my_drive,
                includeItemsFromAllDrives=True,
                fields=CHANGE_LIST_FIELDS,
            )
        ).execute()
        return {
            "changes": [normalize_drive_change(c) for c in result.get("changes", [])],
            "next_page_token": result.get("nextPageToken"),
            "new_start_token": result.get("newStartPageToken"),
        }

    def trash_file(self, file_id: str):
        return self._files().update(
            **self._all_drives(
                fileId=file_id, body={"trashed": True}, fields=FILE_METADATA_FIELDS
            )
        ).execute()

    def untrash_file(self, file_id: str):
        return self._files().update(
            **self._all_drives(
                fileId=file_id, body={"trashed": False}, fields=FILE_METADATA_FIELDS
            )
        ).execute()

    def delete_file(self, file_id: str):
        self._files().delete(**self._all_drives(fileId=file_id)).execute()
        return {"fileId": file_id, "deleted": True}

    def list_comments(self, file_id, include_deleted=False, page_size=20, page_token=None):
        params = {
            "fileId": file_id,
            "includeDeleted": include_deleted,
            "pageSize": page_size,
            "fields": COMMENT_LIST_FIELDS,
        }
        if page_token:
            params["pageToken"] = page_token
        result = self.service.comments().list(**params).execute()
        out = {
            "comments": [self._normalize_comment(c) for c in result.get("comments", [])],
        }
        if "nextPageToken" in result:
            out["nextPageToken"] = result["nextPageToken"]
        return out

    def create_comment(self, file_id, content, anchor=None, quoted_text=None):
        if not content:
            raise ValueError("content is required")
        body = {"content": content}
        if anchor is not None:
            body["anchor"] = anchor if isinstance(anchor, str) else json.dumps(anchor)
        if quoted_text:
            body["quotedFileContent"] = {"mimeType": "text/plain", "value": quoted_text}
        raw = self.service.comments().create(
            fileId=file_id, body=body, fields=COMMENT_FIELDS,
        ).execute()
        return self._normalize_comment(raw)

    def reply_to_comment(self, file_id, comment_id, content=None, action=None):
        if action is not None and action not in {"resolve", "reopen"}:
            raise ValueError('action must be "resolve" or "reopen"')
        body = {}
        if content:
            body["content"] = content
        if action:
            body["action"] = action
        if not body:
            raise ValueError("reply_to_comment requires content or action")
        raw = self.service.replies().create(
            fileId=file_id, commentId=comment_id, body=body, fields=REPLY_FIELDS,
        ).execute()
        return self._normalize_reply(raw)

    def delete_comment(self, file_id, comment_id):
        self.service.comments().delete(fileId=file_id, commentId=comment_id).execute()
        return {"id": comment_id, "deleted": True}

    @staticmethod
    def _parse_place(anchor):
        if isinstance(anchor, dict):
            parsed = anchor
        elif isinstance(anchor, str) and anchor:
            try:
                parsed = json.loads(anchor)
            except json.JSONDecodeError:
                return None
        else:
            return None
        if isinstance(parsed, dict) and ("docsRange" in parsed or "sheetsCell" in parsed):
            return parsed
        return None

    @staticmethod
    def _author_name(author):
        if isinstance(author, dict):
            return author.get("displayName") or ""
        return author or ""

    @classmethod
    def _normalize_reply(cls, reply):
        reply = reply or {}
        return {
            "id": reply.get("id"),
            "content": reply.get("content"),
            "author": cls._author_name(reply.get("author")),
            "createdTime": reply.get("createdTime"),
            "modifiedTime": reply.get("modifiedTime"),
            "action": reply.get("action"),
            "deleted": bool(reply.get("deleted")),
        }

    @classmethod
    def _normalize_comment(cls, comment):
        comment = comment or {}
        quoted = comment.get("quotedFileContent") or {}
        quoted_text = quoted.get("value") if isinstance(quoted, dict) else None
        return {
            "id": comment.get("id"),
            "content": comment.get("content"),
            "author": cls._author_name(comment.get("author")),
            "createdTime": comment.get("createdTime"),
            "modifiedTime": comment.get("modifiedTime"),
            "resolved": bool(comment.get("resolved")),
            "deleted": bool(comment.get("deleted")),
            "anchor": comment.get("anchor"),
            "place": cls._parse_place(comment.get("anchor")),
            "quotedText": quoted_text,
            "replies": [cls._normalize_reply(r) for r in comment.get("replies") or []],
        }

    # --- batch helpers ---
    @staticmethod
    def _run_batch(items, fn):
        """Apply ``fn`` to each item, isolating per-item failures.

        Returns a summary with a per-item result list; a single bad item is
        reported as ``{"ok": False, "error": ...}`` instead of aborting the rest.
        """
        results = []
        succeeded = 0
        for item in items:
            try:
                res = fn(item)
                results.append({"ok": True, **res})
                succeeded += 1
            except core.GoogleCoreError as e:
                results.append({"ok": False, "error": str(e), "item": item})
            except Exception as e:  # noqa: BLE001 - normalize to a readable message
                results.append({"ok": False, "error": str(core.map_exception(e)), "item": item})
        return {
            "total": len(items),
            "succeeded": succeeded,
            "failed": len(items) - succeeded,
            "results": results,
        }

    def batch_move_files(self, items):
        """Move many files. ``items``: list of ``{file_id, new_parent_id}``."""
        if not isinstance(items, list) or not items:
            raise ValueError(
                "batch_move_files requires a non-empty list of {file_id, new_parent_id}"
            )

        def _one(item):
            file_id = item.get("file_id")
            new_parent_id = item.get("new_parent_id")
            if not file_id or not new_parent_id:
                raise ValueError(f"each item needs file_id and new_parent_id; got {item!r}")
            data = self.move_file(file_id, new_parent_id)
            return {"file_id": file_id, "name": data.get("name"), "parents": data.get("parents")}

        return self._run_batch(items, _one)

    def batch_create_folders(self, items):
        """Create many folders. ``items``: list of ``{name, parent_id?}``."""
        if not isinstance(items, list) or not items:
            raise ValueError(
                "batch_create_folders requires a non-empty list of {name, parent_id?}"
            )

        def _one(item):
            name = item.get("name")
            if not name:
                raise ValueError(f"each item needs a name; got {item!r}")
            data = self.create_folder(name, parent_id=item.get("parent_id"))
            return {"id": data.get("id"), "name": data.get("name"), "parents": data.get("parents")}

        return self._run_batch(items, _one)

    def batch_trash_files(self, file_ids):
        """Trash many files (reversible). ``file_ids``: list of ids."""
        if not isinstance(file_ids, list) or not file_ids:
            raise ValueError("batch_trash_files requires a non-empty list of file_ids")

        def _one(file_id):
            data = self.trash_file(file_id)
            return {"file_id": file_id, "name": data.get("name"), "trashed": data.get("trashed")}

        return self._run_batch(file_ids, _one)

    def batch_delete_files(self, file_ids):
        """Permanently delete many files. ``file_ids``: list of ids."""
        if not isinstance(file_ids, list) or not file_ids:
            raise ValueError("batch_delete_files requires a non-empty list of file_ids")

        def _one(file_id):
            self.delete_file(file_id)
            return {"file_id": file_id, "deleted": True}

        return self._run_batch(file_ids, _one)
