"""Google Slides API wrapper: read a deck as text, build slides from an outline, edit slide text.

Slides are built from the deck's own layouts (so the theme applies), and text
goes into the layout's placeholders through ``placeholderIdMappings``. A slide
body is plain lines: a line starting with ``- `` is a bullet, nested one level
per two spaces of indent before the dash.
"""
from __future__ import annotations

import re
import uuid

import google_auth_core as core

from ..docs.docs_api import _is_revision_mismatch
from ..drive.drive_api import place_in_folder

TITLE_TYPES = ("TITLE", "CENTERED_TITLE")
BODY_TYPES = ("BODY", "SUBTITLE")
BULLET_PRESET = "BULLET_DISC_CIRCLE_SQUARE"
# Slides nests bullets up to this level.
MAX_NESTING = 8
# One batchUpdate per call; this bounds its size.
MAX_SLIDES_PER_CALL = 100
ITEM_KEYS = ("title", "body", "notes", "layout")

_BULLET = re.compile(r"^( *)- (.*)$")
_NOTES_FIELDS = "revisionId,slides(objectId,slideProperties/notesPage/notesProperties)"


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _new_id() -> str:
    """An object id Slides accepts (5-50 chars of [A-Za-z0-9_-:], letter first)."""
    return "gw" + uuid.uuid4().hex[:16]


def _norm(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())


def parse_body(body: str) -> tuple[str, list[tuple[int, int]]]:
    """Turn outline lines into the text to insert and the bullet runs in it.

    A bullet line becomes its text after one tab per nesting level, which
    ``createParagraphBullets`` reads as the level and then removes. Returns
    ``(text, runs)``, each run the UTF-16 ``(start, end)`` of consecutive
    bullet lines.
    """
    lines = (body or "").replace("\r\n", "\n").replace("\r", "\n").rstrip().split("\n")
    out: list[str] = []
    runs: list[tuple[int, int]] = []
    pos = 0
    run_start = None
    for i, line in enumerate(lines):
        m = _BULLET.match(line)
        if m:
            level = min(len(m.group(1)) // 2, MAX_NESTING)
            line = "\t" * level + m.group(2)
            if run_start is None:
                run_start = pos
        elif run_start is not None:
            runs.append((run_start, pos - 1))  # up to the end of the previous line
            run_start = None
        out.append(line)
        pos += _utf16_len(line) + (1 if i < len(lines) - 1 else 0)
    if run_start is not None:
        runs.append((run_start, pos))
    return "\n".join(out), runs


def body_requests(object_id: str, body: str) -> list[dict]:
    """Requests that write an outline body into an empty shape."""
    text, runs = parse_body(body)
    if not text:
        return []
    requests = [
        {"insertText": {"objectId": object_id, "insertionIndex": 0, "text": text}},
        # Some themes bullet every body paragraph; plain lines must stay plain.
        {"deleteParagraphBullets": {"objectId": object_id, "textRange": {"type": "ALL"}}},
    ]
    # Last run first: removing a run's leading tabs shifts only the text after it.
    for start, end in reversed(runs):
        requests.append({"createParagraphBullets": {
            "objectId": object_id,
            "textRange": {"type": "FIXED_RANGE", "startIndex": start, "endIndex": max(end, start + 1)},
            "bulletPreset": BULLET_PRESET,
        }})
    return requests


def _insert(object_id: str, text: str) -> dict:
    return {"insertText": {"objectId": object_id, "insertionIndex": 0, "text": text}}


def _walk(elements):
    """Page elements in order, with the children of groups in place of the group."""
    for el in elements or []:
        children = (el.get("elementGroup") or {}).get("children")
        if children:
            yield from _walk(children)
        else:
            yield el


def _placeholder_type(el) -> str | None:
    return ((el.get("shape") or {}).get("placeholder") or {}).get("type")


def _find_placeholder(page, types):
    """The first element of ``page`` whose placeholder is one of ``types``, in that order."""
    elements = list(_walk(page.get("pageElements")))
    for wanted in types:
        for el in elements:
            if _placeholder_type(el) == wanted:
                return el
    return None


def _has_text(el) -> bool:
    for te in ((el.get("shape") or {}).get("text") or {}).get("textElements") or []:
        content = (te.get("textRun") or te.get("autoText") or {}).get("content") or ""
        if content.strip("\n"):
            return True
    return False


def _outline_text(text) -> str:
    """A shape's text as outline lines, bullets written back as ``- `` with indent."""
    markers: list = []
    chunks: list[str] = []
    for te in (text or {}).get("textElements") or []:
        if "paragraphMarker" in te:
            bullet = te["paragraphMarker"].get("bullet")
            markers.append(bullet.get("nestingLevel", 0) if bullet is not None else None)
        run = te.get("textRun") or te.get("autoText")
        if run:
            chunks.append(run.get("content") or "")
    paragraphs = "".join(chunks).split("\n")
    if paragraphs and paragraphs[-1] == "":
        paragraphs.pop()
    lines = []
    for i, para in enumerate(paragraphs):
        para = para.replace("\u000b", " ")
        level = markers[i] if i < len(markers) else None
        lines.append(f"{'  ' * level}- {para}" if level is not None else para)
    return "\n".join(lines).rstrip()


def _element_text(el) -> str:
    if "shape" in el:
        return _outline_text(el["shape"].get("text"))
    table = el.get("table")
    if table:
        rows = []
        for row in table.get("tableRows") or []:
            cells = [_outline_text(c.get("text")).replace("\n", " ") for c in row.get("tableCells") or []]
            rows.append(" | ".join(cells))
        return "\n".join(rows).strip()
    return ""


def _notes(slide) -> tuple[str | None, dict | None]:
    """``(speaker notes object id, its element or None)`` of a slide."""
    page = (slide.get("slideProperties") or {}).get("notesPage") or {}
    notes_id = (page.get("notesProperties") or {}).get("speakerNotesObjectId")
    for el in _walk(page.get("pageElements")):
        if notes_id and el.get("objectId") == notes_id:
            return notes_id, el
    return notes_id, None


def _layout_names(deck) -> list[str]:
    return [_layout_name(lay) for lay in deck.get("layouts") or []]


def _layout_name(layout) -> str:
    props = layout.get("layoutProperties") or {}
    return props.get("name") or props.get("displayName") or layout.get("objectId") or ""


def _find_layout(deck, wanted: str):
    key = _norm(wanted)
    for layout in deck.get("layouts") or []:
        props = layout.get("layoutProperties") or {}
        if key and key in (_norm(props.get("name")), _norm(props.get("displayName"))):
            return layout
    names = ", ".join(_layout_names(deck)) or "none"
    raise ValueError(f"layout {wanted!r} is not in this deck; its layouts: {names}")


def check_items(slides) -> list[dict]:
    """Validate outline items: a non-empty list of {title, body?, notes?, layout?}."""
    shape = "slides must be a non-empty list of {title, body?, notes?, layout?}"
    if not isinstance(slides, list) or not slides:
        raise ValueError(shape)
    if len(slides) > MAX_SLIDES_PER_CALL:
        raise ValueError(f"at most {MAX_SLIDES_PER_CALL} slides per call; add the rest with add_slides")
    for i, item in enumerate(slides):
        if not isinstance(item, dict):
            raise ValueError(f"slides[{i}]: {shape}")
        unknown = sorted(set(item) - set(ITEM_KEYS))
        if unknown:
            raise ValueError(f"slides[{i}] has unknown keys {unknown}; allowed: {list(ITEM_KEYS)}")
        if "title" not in item:
            raise ValueError(f"slides[{i}] needs a title")
        for key, value in item.items():
            if value is not None and not isinstance(value, str):
                raise ValueError(f"slides[{i}].{key} must be a string")
    return slides


def outline_requests(deck, slides, position=None) -> tuple[list[dict], list[dict]]:
    """The createSlide and text requests for outline items, in one batch.

    Returns ``(requests, made)``; ``made`` holds each new slide's id and notes,
    which need the notes shape id Slides assigns only once the slide exists.
    """
    requests: list[dict] = []
    made: list[dict] = []
    for i, item in enumerate(slides):
        title = item.get("title") or ""
        body = (item.get("body") or "").rstrip()
        layout = _find_layout(deck, item.get("layout") or ("TITLE_AND_BODY" if body else "TITLE"))
        name = _layout_name(layout)
        slide_id = _new_id()
        mappings, texts = [], []
        for text, types, suffix, what in ((title, TITLE_TYPES, "t", "title"),
                                          (body, BODY_TYPES, "b", "body")):
            if not text:
                continue
            el = _find_placeholder(layout, types)
            if el is None:
                with_it = [_layout_name(lay) for lay in deck.get("layouts") or []
                           if _find_placeholder(lay, types) is not None]
                raise ValueError(
                    f"slides[{i}]: layout {name} has no {what} placeholder; layouts with one: "
                    + (", ".join(with_it) or "none")
                )
            ph = el["shape"]["placeholder"]
            object_id = f"{slide_id}_{suffix}"
            mappings.append({
                "layoutPlaceholder": {"type": ph["type"], "index": ph.get("index", 0)},
                "objectId": object_id,
            })
            texts.append((object_id, text, what == "body"))
        create = {"objectId": slide_id, "slideLayoutReference": {"layoutId": layout["objectId"]}}
        if mappings:
            create["placeholderIdMappings"] = mappings
        if position is not None:
            create["insertionIndex"] = position + i
        requests.append({"createSlide": create})
        for object_id, text, is_body in texts:
            requests.extend(body_requests(object_id, text) if is_body else [_insert(object_id, text)])
        made.append({"slide_id": slide_id, "notes": item.get("notes") or ""})
    return requests, made


def _stale_revision_error(presentation_id, revision_id):
    return core.InvalidArgumentError(
        f"Presentation {presentation_id} changed after revision {revision_id} was read, so "
        "nothing was written. Re-read it with read_presentation and retry with the new revisionId."
    )


def _url(presentation_id: str) -> str:
    return f"https://docs.google.com/presentation/d/{presentation_id}/edit"


class SlidesAPI:
    def __init__(self, account=None):
        self.account = account
        self.service = core.get_service("slides", "v1", account=account)

    def _drive(self):
        return core.get_service("drive", "v3", account=self.account)

    def _presentations(self):
        return self.service.presentations()

    def _batch(self, presentation_id, requests, revision_id=None):
        body = {"requests": requests}
        if revision_id:
            body["writeControl"] = {"requiredRevisionId": revision_id}
        try:
            return self._presentations().batchUpdate(
                presentationId=presentation_id, body=body
            ).execute()
        except Exception as e:  # noqa: BLE001 - only the revision mismatch is rewritten
            if revision_id and _is_revision_mismatch(e):
                raise _stale_revision_error(presentation_id, revision_id) from e
            raise

    @staticmethod
    def _revision(resp, fallback=None):
        return ((resp or {}).get("writeControl") or {}).get("requiredRevisionId") or fallback

    @staticmethod
    def _guard(presentation_id, current, revision_id):
        """The revision to write against; fails early when the caller's read is stale."""
        if revision_id and current and revision_id != current:
            raise _stale_revision_error(presentation_id, revision_id)
        return revision_id or current

    def _trash(self, presentation_id):
        try:
            self._drive().files().update(
                fileId=presentation_id, body={"trashed": True}, supportsAllDrives=True
            ).execute()
        except Exception:  # noqa: BLE001 - report the original failure, not the cleanup
            pass

    def get_presentation(self, presentation_id, fields=None):
        kwargs = {"presentationId": presentation_id}
        if fields:
            kwargs["fields"] = fields
        return self._presentations().get(**kwargs).execute()

    # --- read ---

    def read_presentation(self, presentation_id):
        """The deck's title, layouts and per slide its id, layout, title, body, notes and other text."""
        deck = self.get_presentation(presentation_id)
        layouts = {lay.get("objectId"): _layout_name(lay) for lay in deck.get("layouts") or []}
        slides = []
        for i, slide in enumerate(deck.get("slides") or []):
            entry = {
                "index": i,
                "id": slide.get("objectId"),
                "layout": layouts.get((slide.get("slideProperties") or {}).get("layoutObjectId")),
            }
            title = body = None
            other = []
            for el in _walk(slide.get("pageElements")):
                text = _element_text(el)
                if not text:
                    continue
                kind = _placeholder_type(el)
                if kind in TITLE_TYPES and title is None:
                    title = text
                elif kind in BODY_TYPES and body is None:
                    body = text
                else:
                    other.append({"shape": el.get("objectId"), "text": text})
            _notes_id, notes_el = _notes(slide)
            notes = _element_text(notes_el) if notes_el else ""
            for key, value in (("title", title), ("body", body), ("other", other), ("notes", notes)):
                if value:
                    entry[key] = value
            slides.append(entry)
        return {
            "presentationId": deck.get("presentationId") or presentation_id,
            "title": deck.get("title"),
            "revisionId": deck.get("revisionId"),
            "layouts": list(layouts.values()),
            "slideCount": len(slides),
            "slides": slides,
        }

    # --- create ---

    def create_presentation(self, title, folder_id=None):
        """Create an empty deck; with ``folder_id``, move it there (trashed if the move fails)."""
        created = self._presentations().create(body={"title": title}).execute()
        pid = created["presentationId"]
        if folder_id:
            place_in_folder(self._drive(), pid, folder_id)
        return {
            "presentationId": pid,
            "title": created.get("title", title),
            "revisionId": created.get("revisionId"),
            "slideCount": len(created.get("slides") or []),
            "layouts": _layout_names(created),
            "url": _url(pid),
        }

    def create_presentation_from_outline(self, title, slides, folder_id=None):
        """Create a deck whose slides follow the outline; trashed if any later step fails."""
        check_items(slides)
        created = self._presentations().create(body={"title": title}).execute()
        pid = created["presentationId"]
        try:
            deck = created if created.get("layouts") else self.get_presentation(pid)
            requests, made = outline_requests(deck, slides)
            if folder_id:
                place_in_folder(self._drive(), pid, folder_id)
            # The new deck's default title slide goes once the outline's slides exist.
            requests += [{"deleteObject": {"objectId": s["objectId"]}}
                         for s in deck.get("slides") or [] if s.get("objectId")]
            resp = self._batch(pid, requests)
            resp = self._write_notes(pid, made) or resp
        except Exception:
            self._trash(pid)
            raise
        return {
            "presentationId": pid,
            "title": created.get("title", title),
            "url": _url(pid),
            "slideIds": [m["slide_id"] for m in made],
            "revisionId": self._revision(resp),
        }

    def _write_notes(self, presentation_id, made):
        """Insert speaker notes of new slides; returns the batch response, or None."""
        pending = [m for m in made if m["notes"]]
        if not pending:
            return None
        deck = self.get_presentation(presentation_id, fields=_NOTES_FIELDS)
        by_id = {s.get("objectId"): s for s in deck.get("slides") or []}
        requests = []
        for m in pending:
            notes_id, _el = _notes(by_id.get(m["slide_id"]) or {})
            if not notes_id:
                raise ValueError(f"slide {m['slide_id']} has no speaker notes shape")
            requests.append(_insert(notes_id, m["notes"]))
        return self._batch(presentation_id, requests, deck.get("revisionId"))

    # --- write ---

    def add_slides(self, presentation_id, slides, position=None, revision_id=None):
        """Add outline slides at the end, or from ``position`` (0 = first)."""
        check_items(slides)
        deck = self.get_presentation(presentation_id)
        rev = self._guard(presentation_id, deck.get("revisionId"), revision_id)
        count = len(deck.get("slides") or [])
        if position is not None and not 0 <= position <= count:
            raise ValueError(f"position must be between 0 and {count}")
        requests, made = outline_requests(deck, slides, position)
        resp = self._batch(presentation_id, requests, rev)
        resp = self._write_notes(presentation_id, made) or resp
        return {
            "presentationId": presentation_id,
            "slideIds": [m["slide_id"] for m in made],
            "revisionId": self._revision(resp),
        }

    def update_slide_text(self, presentation_id, slide_id, title=None, body=None, notes=None,
                          revision_id=None):
        """Replace the title, body (outline lines) or speaker notes of one slide, each only when given."""
        if title is None and body is None and notes is None:
            raise ValueError("pass title, body or notes")
        deck = self.get_presentation(presentation_id)
        rev = self._guard(presentation_id, deck.get("revisionId"), revision_id)
        slide = next((s for s in deck.get("slides") or [] if s.get("objectId") == slide_id), None)
        if slide is None:
            raise ValueError(
                f"slide {slide_id} is not in presentation {presentation_id}; "
                "read_presentation lists the slide ids"
            )
        requests: list[dict] = []
        updated: list[str] = []
        for what, text, types in (("title", title, TITLE_TYPES), ("body", body, BODY_TYPES)):
            if text is None:
                continue
            el = _find_placeholder(slide, types)
            if el is None:
                raise ValueError(f"slide {slide_id} has no {what} placeholder")
            if _has_text(el):
                requests.append({"deleteText": {"objectId": el["objectId"], "textRange": {"type": "ALL"}}})
            if what == "body":
                requests.extend(body_requests(el["objectId"], text))
            elif text:
                requests.append(_insert(el["objectId"], text))
            updated.append(what)
        if notes is not None:
            notes_id, el = _notes(slide)
            if not notes_id:
                raise ValueError(f"slide {slide_id} has no speaker notes shape")
            if el is not None and _has_text(el):
                requests.append({"deleteText": {"objectId": notes_id, "textRange": {"type": "ALL"}}})
            if notes:
                requests.append(_insert(notes_id, notes))
            updated.append("notes")
        resp = self._batch(presentation_id, requests, rev) if requests else None
        return {
            "presentationId": presentation_id,
            "slideId": slide_id,
            "updated": updated,
            "revisionId": self._revision(resp, deck.get("revisionId")),
        }

    def replace_all_text(self, presentation_id, find, replace="", match_case=False, revision_id=None):
        """Replace every match of ``find`` in the deck; returns how many changed."""
        if not find:
            raise ValueError("find must not be empty")
        resp = self._batch(presentation_id, [{"replaceAllText": {
            "containsText": {"text": find, "matchCase": match_case},
            "replaceText": replace,
        }}], revision_id)
        reply = ((resp or {}).get("replies") or [{}])[0].get("replaceAllText") or {}
        return {
            "presentationId": presentation_id,
            "occurrencesChanged": reply.get("occurrencesChanged", 0),
            "revisionId": self._revision(resp),
        }

    def delete_slide(self, presentation_id, slide_id, revision_id=None):
        resp = self._batch(presentation_id, [{"deleteObject": {"objectId": slide_id}}], revision_id)
        return {"presentationId": presentation_id, "deleted": slide_id,
                "revisionId": self._revision(resp)}

    def batch_update(self, presentation_id, requests, revision_id=None):
        """Pass raw Slides batchUpdate requests through."""
        if not isinstance(requests, list) or not requests:
            raise ValueError("requests must be a non-empty list")
        return self._batch(presentation_id, requests, revision_id)
