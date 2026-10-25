"""Tests for the Google Slides MCP server and its API wrapper (never the network)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, create_autospec

import google_auth_core as core
import httplib2
import pytest
from googleapiclient.errors import HttpError
from mcp.types import TextContent

from google_workspace_mcp.slides import server, slides_api
from google_workspace_mcp.slides.slides_api import SlidesAPI, parse_body

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def envelope(res):
    for b in res.content:
        if isinstance(b, TextContent):
            return json.loads(b.text)
    raise AssertionError("no parsable content")


# --- fixture decks -------------------------------------------------------------

def _ph(object_id, kind, index=0):
    return {"objectId": object_id, "shape": {"placeholder": {"type": kind, "index": index}}}


LAYOUTS = [
    {"objectId": "L_TITLE", "layoutProperties": {"name": "TITLE", "displayName": "Title slide"},
     "pageElements": [_ph("l1", "CENTERED_TITLE"), _ph("l2", "SUBTITLE")]},
    {"objectId": "L_TB", "layoutProperties": {"name": "TITLE_AND_BODY", "displayName": "Title and body"},
     "pageElements": [_ph("l3", "TITLE"), _ph("l4", "BODY")]},
    {"objectId": "L_SEC", "layoutProperties": {"name": "SECTION_HEADER", "displayName": "Section header"},
     "pageElements": [_ph("l5", "TITLE")]},
    {"objectId": "L_BLANK", "layoutProperties": {"name": "BLANK", "displayName": "Blank"},
     "pageElements": []},
]


def _text(*paragraphs):
    """Shape text from (content, nesting level or None) paragraphs."""
    elements = []
    for content, level in paragraphs:
        marker = {"style": {}}
        if level is not None:
            marker["bullet"] = {"listId": "x"} if level == 0 else {"listId": "x", "nestingLevel": level}
        elements.append({"paragraphMarker": marker})
        elements.append({"textRun": {"content": content + "\n"}})
    return {"textElements": elements}


def _shape(object_id, kind, *paragraphs):
    el = {"objectId": object_id, "shape": {"text": _text(*paragraphs)}}
    if kind:
        el["shape"]["placeholder"] = {"type": kind}
    return el


def _slide(object_id, layout, elements, notes=None, notes_id=None):
    notes_id = notes_id or f"{object_id}_n"
    notes_els = [_shape(notes_id, "BODY", (notes, None))] if notes else []
    return {
        "objectId": object_id,
        "pageElements": elements,
        "slideProperties": {
            "layoutObjectId": layout,
            "notesPage": {
                "notesProperties": {"speakerNotesObjectId": notes_id},
                "pageElements": notes_els,
            },
        },
    }


def _deck(slides, revision="rev1"):
    return {"presentationId": "P1", "title": "Deck", "revisionId": revision,
            "layouts": LAYOUTS, "slides": slides}


def _api(monkeypatch):
    slides_svc, drive_svc = MagicMock(), MagicMock()
    monkeypatch.setattr(
        "google_auth_core.get_service",
        lambda name, version="v1", account=None: drive_svc if name == "drive" else slides_svc,
    )
    drive_svc.files().get().execute.return_value = {"parents": ["root"]}
    return SlidesAPI("x@x.com"), slides_svc, drive_svc


def _ids(monkeypatch):
    counter = iter(range(1, 1000))
    monkeypatch.setattr(slides_api, "_new_id", lambda: f"gwslide{next(counter)}")


def _batches(svc):
    """Every batchUpdate body sent, in order."""
    return [c.kwargs["body"] for c in svc.presentations().batchUpdate.call_args_list if c.kwargs]


# --- body outline --------------------------------------------------------------

def test_parse_body_nests_bullets_with_tabs_and_keeps_plain_lines():
    text, runs = parse_body("Intro\n- done\n  - OCR timeout fixed\n    - deeper\nOutro\n- last\n\n")
    assert text == "Intro\ndone\n\tOCR timeout fixed\n\t\tdeeper\nOutro\nlast"
    # Runs are UTF-16 ranges of consecutive bullet lines, before tabs are removed.
    assert runs == [(6, 38), (45, 49)]
    assert text[6:38] == "done\n\tOCR timeout fixed\n\t\tdeeper"
    assert text[45:49] == "last"


def test_parse_body_counts_utf16():
    text, runs = parse_body("- 😀 smile\n- next")
    assert runs == [(0, 13)]  # the emoji is two UTF-16 units: 2 + 6 + 1 + 4


def test_body_requests_bullet_last_run_first():
    reqs = slides_api.body_requests("B", "- a\nplain\n- b")
    kinds = [next(iter(r)) for r in reqs]
    assert kinds == ["insertText", "deleteParagraphBullets", "createParagraphBullets",
                     "createParagraphBullets"]
    starts = [r["createParagraphBullets"]["textRange"]["startIndex"] for r in reqs[2:]]
    assert starts == [8, 0]


# --- building from an outline ----------------------------------------------------

OUTLINE = [
    {"title": "DDB status", "layout": "TITLE"},
    {"title": "Done", "body": "- done\n  - OCR timeout fixed", "notes": "Mention the OCR fix"},
    {"title": "Next", "body": "Plan\n- ship"},
]


def _created():
    return {"presentationId": "P9", "title": "DDB status", "revisionId": "r0", "layouts": LAYOUTS,
            "slides": [{"objectId": "p"}]}


def test_outline_builds_one_batch_then_notes_then_sits_in_folder(monkeypatch):
    _ids(monkeypatch)
    api, svc, drive = _api(monkeypatch)
    svc.presentations().create().execute.return_value = _created()
    svc.presentations().get().execute.return_value = {
        "revisionId": "r1",
        "slides": [_slide(f"gwslide{i}", "L_TB", [], notes_id=f"notes{i}") for i in (1, 2, 3)],
    }
    svc.presentations().batchUpdate().execute.side_effect = [
        {"writeControl": {"requiredRevisionId": "r1"}},
        {"writeControl": {"requiredRevisionId": "r2"}},
    ]
    svc.presentations().batchUpdate.reset_mock()

    out = api.create_presentation_from_outline("DDB status", OUTLINE, folder_id="DDB")

    assert out["presentationId"] == "P9" and out["revisionId"] == "r2"
    assert out["slideIds"] == ["gwslide1", "gwslide2", "gwslide3"]
    first, notes = _batches(svc)
    reqs = first["requests"]
    assert "writeControl" not in first
    # Slide 1: the TITLE layout, title into its centered title.
    assert reqs[0] == {"createSlide": {
        "objectId": "gwslide1", "slideLayoutReference": {"layoutId": "L_TITLE"},
        "placeholderIdMappings": [{"layoutPlaceholder": {"type": "CENTERED_TITLE", "index": 0},
                                   "objectId": "gwslide1_t"}],
    }}
    assert reqs[1] == {"insertText": {"objectId": "gwslide1_t", "insertionIndex": 0,
                                      "text": "DDB status"}}
    # Slide 2: TITLE_AND_BODY by default, body with a nested bullet.
    assert reqs[2]["createSlide"]["slideLayoutReference"] == {"layoutId": "L_TB"}
    assert [m["layoutPlaceholder"]["type"] for m in reqs[2]["createSlide"]["placeholderIdMappings"]] \
        == ["TITLE", "BODY"]
    assert reqs[4] == {"insertText": {"objectId": "gwslide2_b", "insertionIndex": 0,
                                      "text": "done\n\tOCR timeout fixed"}}
    assert reqs[5] == {"deleteParagraphBullets": {"objectId": "gwslide2_b",
                                                  "textRange": {"type": "ALL"}}}
    assert reqs[6] == {"createParagraphBullets": {
        "objectId": "gwslide2_b",
        "textRange": {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 23},
        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
    }}
    # The default slide of the new deck goes last, after the new ones exist.
    assert reqs[-1] == {"deleteObject": {"objectId": "p"}}
    assert sum("createSlide" in r for r in reqs) == 3
    # Notes need the notes shape ids, so they follow in a second, guarded batch.
    assert notes == {"requests": [{"insertText": {"objectId": "notes2", "insertionIndex": 0,
                                                  "text": "Mention the OCR fix"}}],
                     "writeControl": {"requiredRevisionId": "r1"}}
    assert svc.presentations().get.call_args.kwargs["fields"] == slides_api._NOTES_FIELDS
    drive.files().update.assert_called_with(
        fileId="P9", addParents="DDB", removeParents="root", fields="id, parents",
        supportsAllDrives=True,
    )


def test_outline_without_notes_sends_one_batch(monkeypatch):
    api, svc, _drive = _api(monkeypatch)
    svc.presentations().create().execute.return_value = _created()
    svc.presentations().batchUpdate().execute.return_value = {}
    svc.presentations().batchUpdate.reset_mock()
    api.create_presentation_from_outline("T", [{"title": "Only"}])
    assert len(_batches(svc)) == 1
    svc.presentations().get.assert_not_called()


def _failing_move(drive):
    calls = []

    def update(**kwargs):
        calls.append(kwargs)
        request = MagicMock()
        if "addParents" in kwargs:
            request.execute.side_effect = RuntimeError("move failed")
        return request

    drive.files().update.side_effect = update
    return calls


def test_outline_trashes_the_deck_when_the_move_fails(monkeypatch):
    api, svc, drive = _api(monkeypatch)
    svc.presentations().create().execute.return_value = _created()
    calls = _failing_move(drive)
    with pytest.raises(RuntimeError, match="move failed"):
        api.create_presentation_from_outline("T", OUTLINE, folder_id="DDB")
    assert calls[-1] == {"fileId": "P9", "body": {"trashed": True}, "supportsAllDrives": True}
    assert _batches(svc) == []


def test_create_presentation_moves_and_trashes_on_failure(monkeypatch):
    api, svc, drive = _api(monkeypatch)
    svc.presentations().create().execute.return_value = _created()
    out = api.create_presentation("T", "DDB")
    assert out["presentationId"] == "P9" and out["layouts"][:2] == ["TITLE", "TITLE_AND_BODY"]
    assert "slides" not in out  # compact result, not the raw deck
    drive.files().update.assert_called_with(
        fileId="P9", addParents="DDB", removeParents="root", fields="id, parents",
        supportsAllDrives=True,
    )
    calls = _failing_move(drive)
    with pytest.raises(RuntimeError, match="move failed"):
        api.create_presentation("T", "DDB")
    assert calls[-1] == {"fileId": "P9", "body": {"trashed": True}, "supportsAllDrives": True}


def test_unknown_layout_names_the_decks_layouts_and_trashes(monkeypatch):
    api, svc, drive = _api(monkeypatch)
    svc.presentations().create().execute.return_value = _created()
    with pytest.raises(ValueError) as err:
        api.create_presentation_from_outline("T", [{"title": "x", "layout": "TWO_COLUMNS"}])
    assert "TWO_COLUMNS" in str(err.value)
    assert "TITLE, TITLE_AND_BODY, SECTION_HEADER, BLANK" in str(err.value)
    assert _batches(svc) == []
    assert drive.files().update.call_args.kwargs["body"] == {"trashed": True}


def test_layout_matches_display_name_and_missing_placeholder_is_named(monkeypatch):
    deck = _deck([])
    reqs, _made = slides_api.outline_requests(deck, [{"title": "x", "layout": "section header"}])
    assert reqs[0]["createSlide"]["slideLayoutReference"] == {"layoutId": "L_SEC"}
    with pytest.raises(ValueError, match="layout BLANK has no title placeholder; layouts with one: "
                                         "TITLE, TITLE_AND_BODY, SECTION_HEADER"):
        slides_api.outline_requests(deck, [{"title": "x", "layout": "BLANK"}])
    with pytest.raises(ValueError, match="SECTION_HEADER has no body placeholder"):
        slides_api.outline_requests(deck, [{"title": "x", "body": "y", "layout": "SECTION_HEADER"}])


@pytest.mark.parametrize("slides, problem", [
    ([], "non-empty list"),
    ("x", "non-empty list"),
    ([{"body": "x"}], r"slides\[0\] needs a title"),
    ([{"title": "x", "text": "y"}], r"unknown keys \['text'\]"),
    ([{"title": "x", "body": ["y"]}], r"slides\[0\]\.body must be a string"),
    ([{"title": "x"}] * 101, "at most 100"),
])
def test_bad_outline_items_fail_with_the_problem(slides, problem):
    with pytest.raises(ValueError, match=problem):
        slides_api.check_items(slides)


def test_add_slides_at_position_guards_with_the_read(monkeypatch):
    _ids(monkeypatch)
    api, svc, _drive = _api(monkeypatch)
    svc.presentations().get().execute.return_value = _deck([_slide("s1", "L_TB", [])], "rev5")
    svc.presentations().batchUpdate().execute.return_value = {"writeControl": {"requiredRevisionId": "rev6"}}
    svc.presentations().batchUpdate.reset_mock()
    out = api.add_slides("P1", [{"title": "a"}, {"title": "b", "body": "- x"}], position=0)
    assert out == {"presentationId": "P1", "slideIds": ["gwslide1", "gwslide2"], "revisionId": "rev6"}
    (body,) = _batches(svc)
    assert body["writeControl"] == {"requiredRevisionId": "rev5"}
    creates = [r["createSlide"] for r in body["requests"] if "createSlide" in r]
    assert [c["insertionIndex"] for c in creates] == [0, 1]
    with pytest.raises(ValueError, match="position must be between 0 and 1"):
        api.add_slides("P1", [{"title": "a"}], position=5)


# --- reading -------------------------------------------------------------------

def _rich_deck():
    group = {"objectId": "g", "elementGroup": {"children": [_shape("box", None, ("Footnote", None))]}}
    table = {"objectId": "tbl", "table": {"tableRows": [
        {"tableCells": [{"text": _text(("A", None))}, {"text": _text(("B", None))}]},
        {"tableCells": [{"text": _text(("1", None))}, {}]},
    ]}}
    return _deck([
        _slide("s1", "L_TITLE", [_shape("t1", "CENTERED_TITLE", ("DDB status", None)),
                                 _shape("st", "SUBTITLE", ("October", None))]),
        _slide("s2", "L_TB", [
            _shape("t2", "TITLE", ("Done", None)),
            _shape("b2", "BODY", ("Summary", None), ("done", 0), ("OCR timeout fixed", 1)),
            group, table,
            {"objectId": "img", "image": {}},
        ], notes="Mention the OCR fix"),
        _slide("s3", "L_BLANK", []),
    ])


def test_read_presentation_returns_text_by_slide(monkeypatch):
    api, svc, _drive = _api(monkeypatch)
    svc.presentations().get().execute.return_value = _rich_deck()
    out = api.read_presentation("P1")
    assert out["title"] == "Deck" and out["revisionId"] == "rev1" and out["slideCount"] == 3
    assert out["layouts"] == ["TITLE", "TITLE_AND_BODY", "SECTION_HEADER", "BLANK"]
    s1, s2, s3 = out["slides"]
    assert s1 == {"index": 0, "id": "s1", "layout": "TITLE", "title": "DDB status", "body": "October"}
    assert s2["body"] == "Summary\n- done\n  - OCR timeout fixed"
    assert s2["other"] == [{"shape": "box", "text": "Footnote"}, {"shape": "tbl", "text": "A | B\n1 |"}]
    assert s2["notes"] == "Mention the OCR fix"
    # Other text follows the body, notes come last.
    assert list(s2) == ["index", "id", "layout", "title", "body", "other", "notes"]
    assert s3 == {"index": 2, "id": "s3", "layout": "BLANK"}


def test_read_body_round_trips_the_outline(monkeypatch):
    """The body read back is the outline that built it."""
    outline = "Plan\n- ship\n  - beta\nThanks"
    text, runs = parse_body(outline)
    # What Slides holds after the batch: tabs removed, nesting levels set.
    paragraphs = []
    for line in text.split("\n"):
        level = len(line) - len(line.lstrip("\t"))
        bullet = any(s <= text.index(line) < e for s, e in runs)
        paragraphs.append((line.lstrip("\t"), level if bullet else None))
    assert slides_api._outline_text(_text(*paragraphs)) == outline


def _long_deck(n=80, chars=640):
    slides = []
    for i in range(n):
        body = ("x" * (chars - 20)) + f" slide {i:03d}"
        slides.append(_slide(f"s{i}", "L_TB", [_shape(f"t{i}", "TITLE", (f"T{i}", None)),
                                               _shape(f"b{i}", "BODY", (body, None))]))
    return _deck(slides)


@pytest.mark.anyio
async def test_long_deck_stops_at_the_budget_with_the_next_slide(monkeypatch):
    api, svc, _drive = _api(monkeypatch)
    deck = _long_deck()
    total = sum(len(s["pageElements"][1]["shape"]["text"]["textElements"][1]["textRun"]["content"])
                for s in deck["slides"])
    assert total >= 50_000
    svc.presentations().get().execute.return_value = deck
    monkeypatch.setattr(server, "_api", lambda account=None: (api, "x@x.com"))
    env = envelope(await server.mcp.call_tool("read_presentation", {"presentation_id": "P1"}))
    kept = env["data"]["slides"]
    assert env["truncated"] is True and env["total_items"] == 80
    assert env["next_offset"] == len(kept) == kept[-1]["index"] + 1
    assert 20 < len(kept) < 80
    assert len(json.dumps(kept)) <= 20_000
    env2 = envelope(await server.mcp.call_tool(
        "read_presentation", {"presentation_id": "P1", "offset": env["next_offset"]}))
    assert env2["data"]["slides"][0]["index"] == env["next_offset"]


@pytest.mark.anyio
async def test_one_huge_slide_is_cut_to_the_budget(monkeypatch):
    api, svc, _drive = _api(monkeypatch)
    svc.presentations().get().execute.return_value = _long_deck(n=1, chars=60_000)
    monkeypatch.setattr(server, "_api", lambda account=None: (api, "x@x.com"))
    env = envelope(await server.mcp.call_tool(
        "read_presentation", {"presentation_id": "P1", "max_chars": 5000}))
    (slide,) = env["data"]["slides"]
    assert slide["cut"] is True and slide["body"].endswith("[cut]") and slide["title"] == "T0"
    assert len(json.dumps(slide)) <= 5000


# --- changing a deck -------------------------------------------------------------

def test_update_slide_text_replaces_title_body_and_notes(monkeypatch):
    api, svc, _drive = _api(monkeypatch)
    svc.presentations().get().execute.return_value = _rich_deck()
    svc.presentations().batchUpdate().execute.return_value = {"writeControl": {"requiredRevisionId": "rev2"}}
    svc.presentations().batchUpdate.reset_mock()
    out = api.update_slide_text("P1", "s2", title="Shipped", body="- a\n  - b", notes="",
                                revision_id="rev1")
    assert out == {"presentationId": "P1", "slideId": "s2", "updated": ["title", "body", "notes"],
                   "revisionId": "rev2"}
    (body,) = _batches(svc)
    assert body["writeControl"] == {"requiredRevisionId": "rev1"}
    assert body["requests"] == [
        {"deleteText": {"objectId": "t2", "textRange": {"type": "ALL"}}},
        {"insertText": {"objectId": "t2", "insertionIndex": 0, "text": "Shipped"}},
        {"deleteText": {"objectId": "b2", "textRange": {"type": "ALL"}}},
        {"insertText": {"objectId": "b2", "insertionIndex": 0, "text": "a\n\tb"}},
        {"deleteParagraphBullets": {"objectId": "b2", "textRange": {"type": "ALL"}}},
        {"createParagraphBullets": {"objectId": "b2", "textRange": {
            "type": "FIXED_RANGE", "startIndex": 0, "endIndex": 4},
            "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}},
        {"deleteText": {"objectId": "s2_n", "textRange": {"type": "ALL"}}},
    ]


def test_update_notes_on_a_slide_without_a_notes_shape_inserts_only(monkeypatch):
    api, svc, _drive = _api(monkeypatch)
    svc.presentations().get().execute.return_value = _rich_deck()
    svc.presentations().batchUpdate().execute.return_value = {}
    svc.presentations().batchUpdate.reset_mock()
    api.update_slide_text("P1", "s1", notes="hello")
    (body,) = _batches(svc)
    assert body == {"requests": [{"insertText": {"objectId": "s1_n", "insertionIndex": 0,
                                                 "text": "hello"}}],
                    "writeControl": {"requiredRevisionId": "rev1"}}


def test_stale_write_changes_nothing(monkeypatch):
    """Scenario: the user edited the deck in the browser after the model's read."""
    api, svc, _drive = _api(monkeypatch)
    svc.presentations().get().execute.return_value = _rich_deck()  # now at rev1
    svc.presentations().batchUpdate.reset_mock()
    with pytest.raises(core.InvalidArgumentError, match="Presentation P1 changed after revision old"):
        api.update_slide_text("P1", "s2", title="x", revision_id="old")
    assert _batches(svc) == []


def test_revision_mismatch_from_slides_becomes_actionable(monkeypatch):
    api, svc, _drive = _api(monkeypatch)
    content = (b'{"error": {"code": 400, "message": "The required revision ID \'r1\' does not match '
               b'the latest revision.", "status": "INVALID_ARGUMENT"}}')
    svc.presentations().batchUpdate().execute.side_effect = HttpError(
        httplib2.Response({"status": 400}), content)
    with pytest.raises(core.InvalidArgumentError, match="Re-read it with read_presentation"):
        api.delete_slide("P1", "s2", revision_id="r1")


@pytest.mark.parametrize("call, problem", [
    (lambda api: api.update_slide_text("P1", "nope", title="x"), "slide nope is not in"),
    (lambda api: api.update_slide_text("P1", "s3", title="x"), "slide s3 has no title placeholder"),
    (lambda api: api.update_slide_text("P1", "s2"), "pass title, body or notes"),
])
def test_update_slide_text_errors(monkeypatch, call, problem):
    api, svc, _drive = _api(monkeypatch)
    svc.presentations().get().execute.return_value = _rich_deck()
    with pytest.raises(ValueError, match=problem):
        call(api)


def test_replace_delete_and_raw_batch(monkeypatch):
    api, svc, _drive = _api(monkeypatch)
    svc.presentations().batchUpdate().execute.return_value = {
        "replies": [{"replaceAllText": {"occurrencesChanged": 3}}],
        "writeControl": {"requiredRevisionId": "r2"},
    }
    svc.presentations().batchUpdate.reset_mock()
    out = api.replace_all_text("P1", "Q3", "Q4", match_case=True, revision_id="r1")
    assert out == {"presentationId": "P1", "occurrencesChanged": 3, "revisionId": "r2"}
    assert api.delete_slide("P1", "s2")["deleted"] == "s2"
    api.batch_update("P1", [{"deleteObject": {"objectId": "img"}}], "r2")
    replace, delete, raw = _batches(svc)
    assert replace == {"requests": [{"replaceAllText": {
        "containsText": {"text": "Q3", "matchCase": True}, "replaceText": "Q4"}}],
        "writeControl": {"requiredRevisionId": "r1"}}
    assert delete == {"requests": [{"deleteObject": {"objectId": "s2"}}]}
    assert raw["writeControl"] == {"requiredRevisionId": "r2"}
    with pytest.raises(ValueError, match="non-empty list"):
        api.batch_update("P1", [])
    with pytest.raises(ValueError, match="find must not be empty"):
        api.replace_all_text("P1", "")


# --- the server --------------------------------------------------------------------

@pytest.fixture
def fake(monkeypatch):
    # autospec keeps the tools' calls in step with SlidesAPI's real signatures.
    api = create_autospec(SlidesAPI, instance=True)
    monkeypatch.setattr(server, "_api", lambda account=None: (api, "me@x.com"))
    return api


TOOLS = {
    "read_presentation": (False, False, True),
    "create_presentation": (True, False, False),
    "create_presentation_from_outline": (True, False, True),
    "add_slides": (True, False, True),
    "update_slide_text": (True, True, True),
    "replace_all_text": (True, True, False),
    "delete_slide": (True, True, False),
    "batch_update": (True, True, False),
}


@pytest.mark.anyio
async def test_tools_registered_with_flags():
    tools = {t.name: t for t in await server.mcp.list_tools()}
    assert set(tools) == set(TOOLS) | {"list_accounts", "whoami", "auth_status"}
    for name, (mutating, destructive, _core) in TOOLS.items():
        tool = tools[name]
        assert list(tool.input_schema["properties"])[0] == "account", name
        assert tool.annotations.read_only_hint is (not mutating), name
        if mutating:
            assert tool.annotations.destructive_hint is destructive, name
        assert ("[DESTRUCTIVE]" in tool.description) is destructive, name


@pytest.mark.anyio
@pytest.mark.parametrize("tool, args, method, expected", [
    ("create_presentation", {"title": "T", "folder_id": "F"}, "create_presentation", ("T", "F")),
    ("create_presentation_from_outline", {"title": "T", "slides": [{"title": "a"}]},
     "create_presentation_from_outline", ("T", [{"title": "a"}], None)),
    ("add_slides", {"presentation_id": "P", "slides": [{"title": "a"}], "position": 2,
                    "revision_id": "r"}, "add_slides", ("P", [{"title": "a"}], 2, "r")),
    ("update_slide_text", {"presentation_id": "P", "slide_id": "s", "body": "- x"},
     "update_slide_text", ("P", "s", None, "- x", None, None)),
    ("replace_all_text", {"presentation_id": "P", "find": "a", "replace": "b"},
     "replace_all_text", ("P", "a", "b", False, None)),
    ("delete_slide", {"presentation_id": "P", "slide_id": "s", "revision_id": "r"},
     "delete_slide", ("P", "s", "r")),
    ("batch_update", {"presentation_id": "P", "requests": '[{"deleteObject": {"objectId": "s"}}]'},
     "batch_update", ("P", [{"deleteObject": {"objectId": "s"}}], None)),
])
async def test_each_tool_calls_the_api_and_wraps_the_result(fake, tool, args, method, expected):
    getattr(fake, method).return_value = {"presentationId": "P"}
    env = envelope(await server.mcp.call_tool(tool, args))
    assert env == {"ok": True, "account": "me@x.com", "data": {"presentationId": "P"}}
    getattr(fake, method).assert_called_once_with(*expected)


@pytest.mark.anyio
async def test_read_presentation_tool_small_deck_is_not_truncated(fake):
    fake.read_presentation.return_value = {"presentationId": "P", "slides": [{"index": 0, "id": "s"}]}
    env = envelope(await server.mcp.call_tool("read_presentation", {"presentation_id": "P"}))
    assert env["data"]["slides"] == [{"index": 0, "id": "s"}] and "truncated" not in env
    fake.read_presentation.assert_called_once_with("P")


@pytest.mark.anyio
async def test_bad_outline_reaches_the_model_as_an_error(monkeypatch):
    api, svc, _drive = _api(monkeypatch)
    monkeypatch.setattr(server, "_api", lambda account=None: (api, "me@x.com"))
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="needs a title"):
        await server.mcp.call_tool("create_presentation_from_outline",
                                   {"title": "T", "slides": [{"body": "x"}]})
    svc.presentations().create.assert_not_called()


def test_read_only_mode_registers_only_read_presentation():
    """Scenario: gslides-mcp under GOOGLE_MCP_READONLY=1."""
    code = (
        "import asyncio, json\n"
        "from google_workspace_mcp.slides import server\n"
        "print(json.dumps(sorted(t.name for t in asyncio.run(server.mcp.list_tools()))))\n"
    )
    env = {**{k: v for k, v in os.environ.items() if k != "GOOGLE_MCP_TOOLSET"},
           "GOOGLE_MCP_READONLY": "1"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["auth_status", "list_accounts", "read_presentation", "whoami"]


# --- the README's outline example -------------------------------------------------

def test_readme_outline_example_builds_the_expected_requests(monkeypatch):
    readme = (ROOT / "README.md").read_text()
    blocks = [b for b in re.findall(r"```json\n(.*?)```", readme, re.S)
              if "create_presentation_from_outline" in b]
    assert blocks, "README has no create_presentation_from_outline example"
    example = json.loads(blocks[0])
    assert example["name"] == "create_presentation_from_outline"
    args = example["arguments"]
    _ids(monkeypatch)
    reqs, made = slides_api.outline_requests(_deck([]), slides_api.check_items(args["slides"]))
    creates = [r["createSlide"] for r in reqs if "createSlide" in r]
    assert [c["slideLayoutReference"]["layoutId"] for c in creates] == ["L_TITLE", "L_TB", "L_TB"]
    bodies = [r["insertText"]["text"] for r in reqs
              if "insertText" in r and r["insertText"]["objectId"].endswith("_b")]
    assert bodies[0] == "October 2026"
    assert bodies[1] == "OCR pipeline\ndone\n\tOCR timeout fixed\n\tretry on 429"
    bullets = [r["createParagraphBullets"]["textRange"] for r in reqs if "createParagraphBullets" in r]
    assert bullets[0] == {"type": "FIXED_RANGE", "startIndex": 13, "endIndex": 50}
    assert [m["notes"] for m in made] == ["", "Mention the OCR fix", ""]
