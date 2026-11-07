"""Gmail history-based change feed (subclass of upstream GmailAPI)."""
from __future__ import annotations

import base64
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, getaddresses
from html import unescape

from gmail_cli.api import GmailAPI
from googleapiclient.errors import HttpError

_HISTORY_EXPIRED = (
    "start_history_token expired or invalid; call get_changes_start_token for a "
    "fresh baseline, then do a full listing to resynchronize"
)


def normalize_history_records(history_records):
    """Flatten Gmail history[] records into typed change entries."""
    changes = []
    for record in history_records or []:
        for entry in record.get("messagesAdded", []):
            msg = entry.get("message", {})
            changes.append(
                {
                    "type": "message_added",
                    "message_id": msg.get("id"),
                    "thread_id": msg.get("threadId"),
                    "label_ids": msg.get("labelIds", []),
                }
            )
        for entry in record.get("messagesDeleted", []):
            msg = entry.get("message", {})
            changes.append(
                {
                    "type": "message_deleted",
                    "message_id": msg.get("id"),
                    "thread_id": msg.get("threadId"),
                }
            )
        for entry in record.get("labelsAdded", []):
            msg = entry.get("message", {})
            changes.append(
                {
                    "type": "labels_added",
                    "message_id": msg.get("id"),
                    "label_ids": entry.get("labelIds", []),
                }
            )
        for entry in record.get("labelsRemoved", []):
            msg = entry.get("message", {})
            changes.append(
                {
                    "type": "labels_removed",
                    "message_id": msg.get("id"),
                    "label_ids": entry.get("labelIds", []),
                }
            )
    return changes


def _decode_body_data(data):
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", "replace")


def _html_to_text(html_body):
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", html_body)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _header_value(headers, name):
    target = name.lower()
    for header in headers or []:
        if (header.get("name") or "").lower() == target:
            return header.get("value") or ""
    return ""


_TEXT_MIMES = frozenset({"text/plain", "text/html"})


def _walk_parts(part, plains, htmls, attachments):
    if not part:
        return
    body = part.get("body") or {}
    mime = (part.get("mimeType") or "").split(";", 1)[0].strip().lower()
    filename = part.get("filename") or ""
    attachment_id = body.get("attachmentId")
    if attachment_id or (filename and mime not in _TEXT_MIMES):
        attachments.append(
            {
                "filename": filename,
                "mimeType": part.get("mimeType") or "",
                "size": body.get("size", 0),
                "attachmentId": attachment_id or "",
            }
        )
        return
    data = body.get("data")
    if data:
        if mime == "text/plain":
            plains.append(_decode_body_data(data))
        elif mime == "text/html":
            htmls.append(_decode_body_data(data))
    for child in part.get("parts") or []:
        _walk_parts(child, plains, htmls, attachments)


def project_message_text(message):
    payload = message.get("payload") or {}
    headers = payload.get("headers")
    plains, htmls, attachments = [], [], []
    _walk_parts(payload, plains, htmls, attachments)
    if plains:
        body_text = plains[0]
    elif htmls:
        body_text = _html_to_text(htmls[0])
    else:
        body_text = ""
    return {
        "id": message.get("id"),
        "threadId": message.get("threadId"),
        "labelIds": message.get("labelIds") or [],
        "subject": _header_value(headers, "Subject"),
        "from": _header_value(headers, "From"),
        "to": _header_value(headers, "To"),
        "cc": _header_value(headers, "Cc"),
        "date": _header_value(headers, "Date"),
        "snippet": message.get("snippet") or "",
        "body_text": body_text,
        "attachments": attachments,
    }


def project_message_record(message):
    """A message as the search tools return it: get_message's keys, without the body.

    Gmail's metadata resource carries every header (34 on a typical message:
    Received, DKIM, ARC), about 7,500 characters; ten of them overflowed a
    client's 50,000-character result cap.
    """
    headers = (message.get("payload") or {}).get("headers")
    record = {
        "id": message.get("id"),
        "threadId": message.get("threadId"),
        "labelIds": message.get("labelIds") or [],
        "subject": _header_value(headers, "Subject"),
        "from": _header_value(headers, "From"),
        "to": _header_value(headers, "To"),
        "date": _header_value(headers, "Date"),
        "snippet": message.get("snippet") or "",
    }
    cc = _header_value(headers, "Cc")
    if cc:
        record["cc"] = cc
    if "account" in message:
        record["account"] = message["account"]
    return record


def address_header(value):
    """Render To/Cc so only display names get RFC 2047 encoded.

    Upstream sets raw strings on compat32 MIME objects, which encode the whole
    header (address included) once any character is non-ASCII, e.g. a Hebrew
    display name. Gmail then rejects it with "Invalid To header".
    """
    if not value:
        return value
    values = [value] if isinstance(value, str) else list(value)
    return ", ".join(formataddr(pair) for pair in getaddresses(values) if pair[1])


class GmailChangesAPI(GmailAPI):
    """GmailAPI plus the history-based change feed."""

    def send_message(self, to, subject, body, attachments=None, cc=None, html=False):
        return super().send_message(
            address_header(to), subject, body, attachments=attachments, cc=address_header(cc), html=html
        )

    def create_draft(self, to, subject, body, attachments=None, cc=None, html=False):
        return super().create_draft(
            address_header(to), subject, body, attachments=attachments, cc=address_header(cc), html=html
        )

    def update_draft(self, draft_id, to=None, subject=None, body=None, attachments=None, cc=None, html=False):
        return super().update_draft(
            draft_id, to=address_header(to), subject=subject, body=body,
            attachments=attachments, cc=address_header(cc), html=html,
        )

    def forward_message(self, message_id, to, body=None, attachments=None):
        return super().forward_message(message_id, address_header(to), body=body, attachments=attachments)

    def _build_reply(self, message_id, body, reply_all, additional_cc, attachments):
        # Mirrors upstream reply_to_message/draft_reply, but the recipients are
        # read from the original's headers, so they go through address_header.
        original = self.get_message(message_id, format="full")
        headers = original.get("payload", {}).get("headers", [])
        subject = _header_value(headers, "Subject")
        if not subject.startswith("Re: "):
            subject = f"Re: {subject}"

        if attachments:
            reply = MIMEMultipart()
            reply.attach(MIMEText(body, "plain"))
            self._attach_files(reply, attachments)
        else:
            reply = MIMEText(body)
        reply["to"] = address_header(_header_value(headers, "From"))
        reply["subject"] = subject

        cc_list = []
        if reply_all and _header_value(headers, "Cc"):
            cc_list.append(_header_value(headers, "Cc"))
        if additional_cc:
            cc_list.append(additional_cc)
        if cc_list:
            reply["cc"] = address_header(cc_list)

        message_id_header = _header_value(headers, "Message-ID")
        if message_id_header:
            reply["In-Reply-To"] = message_id_header
            reply["References"] = message_id_header

        return {
            "raw": base64.urlsafe_b64encode(reply.as_bytes()).decode(),
            "threadId": original.get("threadId"),
        }

    def reply_to_message(self, message_id, body, reply_all=False, additional_cc=None, attachments=None):
        message = self._build_reply(message_id, body, reply_all, additional_cc, attachments)
        try:
            return self.service.users().messages().send(userId=self.user_id, body=message).execute()
        except HttpError as error:
            raise Exception(f"Failed to reply to message: {error}")

    def draft_reply(self, message_id, body, reply_all=False, additional_cc=None, attachments=None):
        message = self._build_reply(message_id, body, reply_all, additional_cc, attachments)
        try:
            return self.service.users().drafts().create(
                userId=self.user_id, body={"message": message}
            ).execute()
        except HttpError as error:
            raise Exception(f"Failed to create draft reply: {error}")

    def _fetch_attachment_data(self, message_id, attachment_id):
        result = self.service.users().messages().attachments().get(
            userId=self.user_id, messageId=message_id, id=attachment_id
        ).execute()
        return result.get("data") or ""

    def _fill_hosted_text_body(self, message_id, projected):
        if projected.get("body_text"):
            return projected
        kept = []
        for att in projected.get("attachments") or []:
            mime = (att.get("mimeType") or "").split(";", 1)[0].strip().lower()
            attachment_id = att.get("attachmentId")
            if not projected.get("body_text") and mime in _TEXT_MIMES and attachment_id and not att.get("filename"):
                data = self._fetch_attachment_data(message_id, attachment_id)
                if mime == "text/plain":
                    projected["body_text"] = _decode_body_data(data)
                else:
                    projected["body_text"] = _html_to_text(_decode_body_data(data))
                continue
            kept.append(att)
        projected["attachments"] = kept
        return projected

    def get_message_text(self, message_id):
        return self._fill_hosted_text_body(
            message_id, project_message_text(self.get_message(message_id, format="full"))
        )

    def get_thread_text(self, thread_id):
        thread = self.get_thread(thread_id, format="full")
        return {
            "id": thread.get("id"),
            "historyId": thread.get("historyId"),
            "messages": [
                self._fill_hosted_text_body(m.get("id"), project_message_text(m))
                for m in thread.get("messages") or []
            ],
        }

    def get_changes_start_token(self):
        profile = self.service.users().getProfile(userId=self.user_id).execute()
        return {
            "start_history_token": profile["historyId"],
            "email": profile["emailAddress"],
        }

    def list_changes(
        self,
        start_history_token,
        history_types=None,
        label_id=None,
        max_results=100,
        page_token=None,
    ):
        if not start_history_token:
            raise ValueError("start_history_token is required")
        params = {
            "userId": self.user_id,
            "startHistoryId": start_history_token,
            "maxResults": max_results,
        }
        if history_types:
            params["historyTypes"] = history_types
        if label_id:
            params["labelId"] = label_id
        if page_token:
            params["pageToken"] = page_token
        try:
            result = self.service.users().history().list(**params).execute()
        except HttpError as error:
            if error.resp.status == 404:
                raise ValueError(_HISTORY_EXPIRED) from error
            raise
        return {
            "changes": normalize_history_records(result.get("history")),
            "next_page_token": result.get("nextPageToken"),
            "new_history_token": result.get("historyId"),
        }
