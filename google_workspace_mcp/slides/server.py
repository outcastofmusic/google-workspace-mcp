"""Google Slides MCP server: read, build and edit decks for one or more accounts."""
from __future__ import annotations

import functools
import json

from ..core import build_server, cap_items, get_api, ok, run_tool
from ..core import register as _register
from .slides_api import SlidesAPI

# read_presentation's default budget: a deck read must not flood a 200k context.
DEFAULT_READ_CHARS = 20_000
_CUT = " [cut]"

mcp = build_server(
    "gslides-mcp",
    "Google Slides: read, build and edit presentations for one or more Google accounts. "
    "Build a deck in one call with create_presentation_from_outline; a slide body is plain "
    "lines, '- ' starts a bullet and two spaces of indent before the dash nest it one level. "
    "Pass the revisionId of read_presentation as revision_id to writes so they fail instead "
    "of overwriting an edit made since.",
    service_name="slides",
)

# Every tool here names Drive files, so a client scope's drive_folders limit it.
register = functools.partial(_register, drive_scoped=True)


def _api(account=None):
    return get_api("slides", SlidesAPI, account)


def _fit(slide: dict, limit: int) -> dict:
    """Cut the text of one slide so its JSON fits ``limit`` characters."""
    if len(json.dumps(slide)) <= limit:
        return slide
    out = json.loads(json.dumps(slide))
    out["cut"] = True
    while (size := len(json.dumps(out))) > limit:
        texts = [(len(out[k]), k, None) for k in ("title", "body", "notes") if out.get(k)]
        texts += [(len(o["text"]), "other", i) for i, o in enumerate(out.get("other") or [])]
        if not texts:
            break
        n, key, i = max(texts)
        keep = n - (size - limit) - len(_CUT) - 8  # always < n, so each pass shrinks
        holder, field = (out["other"][i], "text") if key == "other" else (out, key)
        if keep > 0:
            holder[field] = holder[field].removesuffix(_CUT)[:keep] + _CUT
        elif key == "other":
            out["other"].pop(i)
        else:
            del out[key]
    return out


# --- read ---
@register(mcp, core=True)
def read_presentation(
    account: str | None = None,
    *,
    presentation_id: str,
    max_chars: int | None = None,
    offset: int = 0,
) -> dict:
    """Read a deck as text: title, layouts, revisionId, and per slide its id, layout, title, body, other text and speaker notes.

    Bodies come back as outline lines ('- ' bullets, two spaces per level).
    Returns whole slides from slide index offset while they fit max_chars
    (default 20000); when truncated=true, call again with offset=next_offset.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.read_presentation(presentation_id))
    limit = max_chars if max_chars is not None else DEFAULT_READ_CHARS
    slides, meta = cap_items(
        data["slides"], limit, offset,
        hint="Call read_presentation again with offset=next_offset.",
    )
    if len(slides) == 1:
        slides = [_fit(slides[0], limit)]
    data["slides"] = slides
    return ok(resolved, data, **meta)


# --- create ---
@register(mcp, mutating=True)
def create_presentation(account: str | None = None, *, title: str, folder_id: str | None = None) -> dict:
    """Create an empty presentation (one title slide) with the given title, optionally inside folder_id."""
    api, resolved = _api(account)
    return ok(resolved, run_tool(lambda: api.create_presentation(title, folder_id)))


@register(mcp, mutating=True, core=True)
def create_presentation_from_outline(
    account: str | None = None,
    *,
    title: str,
    slides: list[dict],
    folder_id: str | None = None,
) -> dict:
    """Create a deck from an outline in one call, optionally inside folder_id.

    slides: list of {title, body?, notes?, layout?}. body is plain lines; a line
    '- x' is a bullet and '  - y' nests under it (two spaces per level). layout
    is one of the deck's layouts (default TITLE_AND_BODY, or TITLE when there is
    no body; also SECTION_HEADER, TITLE_ONLY, BLANK). Returns presentationId,
    url, slideIds and revisionId.
    """
    api, resolved = _api(account)
    return ok(resolved, run_tool(
        lambda: api.create_presentation_from_outline(title, slides, folder_id)
    ))


# --- write ---
@register(mcp, mutating=True, core=True)
def add_slides(
    account: str | None = None,
    *,
    presentation_id: str,
    slides: list[dict],
    position: int | None = None,
    revision_id: str | None = None,
) -> dict:
    """Add slides from outline items ({title, body?, notes?, layout?}, as in create_presentation_from_outline) at the end, or from position (0 = first)."""
    api, resolved = _api(account)
    return ok(resolved, run_tool(
        lambda: api.add_slides(presentation_id, slides, position, revision_id)
    ))


@register(mcp, mutating=True, destructive=True, core=True)
def update_slide_text(
    account: str | None = None,
    *,
    presentation_id: str,
    slide_id: str,
    title: str | None = None,
    body: str | None = None,
    notes: str | None = None,
    revision_id: str | None = None,
) -> dict:
    """Replace the title, body (outline lines) or speaker notes of one slide; each changes only when given, and "" clears it."""
    api, resolved = _api(account)
    return ok(resolved, run_tool(
        lambda: api.update_slide_text(presentation_id, slide_id, title, body, notes, revision_id)
    ))


@register(mcp, mutating=True, destructive=True)
def replace_all_text(
    account: str | None = None,
    *,
    presentation_id: str,
    find: str,
    replace: str = "",
    match_case: bool = False,
    revision_id: str | None = None,
) -> dict:
    """Replace every occurrence of find in the deck (slides and notes); returns occurrencesChanged."""
    api, resolved = _api(account)
    return ok(resolved, run_tool(
        lambda: api.replace_all_text(presentation_id, find, replace, match_case, revision_id)
    ))


@register(mcp, mutating=True, destructive=True)
def delete_slide(
    account: str | None = None,
    *,
    presentation_id: str,
    slide_id: str,
    revision_id: str | None = None,
) -> dict:
    """Delete one slide by its id (from read_presentation)."""
    api, resolved = _api(account)
    return ok(resolved, run_tool(lambda: api.delete_slide(presentation_id, slide_id, revision_id)))


@register(mcp, mutating=True, destructive=True)
def batch_update(
    account: str | None = None,
    *,
    presentation_id: str,
    requests: list[dict] | str,
    revision_id: str | None = None,
) -> dict:
    """Execute raw Slides batchUpdate requests (images, tables, shapes, styling). Pass a list of request dicts or a JSON string."""
    api, resolved = _api(account)

    def _call():
        raw = requests
        if isinstance(raw, str):
            raw = json.loads(raw)
        return api.batch_update(presentation_id, raw, revision_id)

    return ok(resolved, run_tool(_call))


def main():
    mcp.run()
