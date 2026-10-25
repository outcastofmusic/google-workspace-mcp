"""Google Docs MCP server: create, read, and edit documents for one or more accounts."""
from __future__ import annotations

import functools
import json

from ..core import build_server, cap_items, cap_text, get_api, ok, run_tool
from ..core import register as _register
from .docs_api import DocsAPI

mcp = build_server(
    "gdocs-mcp",
    "Google Docs: create, read, and edit documents for one or more Google accounts. "
    "For formatted content (headings, lists, tables, bold), prefer the markdown tools "
    "(create_document_from_markdown, replace_document_with_markdown, append_markdown) "
    "over element-by-element insert/format calls. To style or link text by content, use "
    "format_matches; for other index-based writes, read revisionId from find_text or "
    "get_content_map and pass it as required_revision_id.",
    service_name="docs",
)


# Every tool here names Drive files, so a client scope's drive_folders limit it.
register = functools.partial(_register, drive_scoped=True)


def _api(account=None):
    return get_api("docs", DocsAPI, account)


# --- read ---
@register(mcp)
def get_document(
    account: str | None = None,
    *,
    document_id: str,
    max_chars: int | None = None,
    offset: int = 0,
) -> dict:
    """Get a document's raw Docs API JSON (title, body content, styles). Large and verbose.

    To read a doc prefer read_document_as_markdown; to find indices for edits
    prefer get_content_map. Body content is capped at max_chars (default 100000)
    of JSON; when truncated=true, call again with offset=next_offset (a body
    element index) for the next elements.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.get_document(document_id))
    body = data.get("body") or {}
    body["content"], meta = cap_items(
        body.get("content") or [], max_chars, offset,
        hint="Call get_document again with offset=next_offset, or use "
        "read_document_as_markdown / get_content_map for a compact view.",
    )
    data["body"] = body
    data = {"documentId": data.get("documentId"), "revisionId": data.get("revisionId"), **data}
    return ok(resolved, data, **meta)


@register(mcp, core=True)
def read_document(
    account: str | None = None,
    *,
    document_id: str,
    max_chars: int | None = None,
    offset: int = 0,
) -> dict:
    """Read a document's plain text (paragraphs and table cells).

    Returns up to max_chars characters (default 100000) from offset; when
    truncated=true, call again with offset=next_offset.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.get_document_text(document_id))
    data["text"], meta = cap_text(
        data["text"], max_chars, offset,
        hint="Call read_document again with offset=next_offset.",
    )
    return ok(resolved, data, **meta)


@register(mcp, core=True)
def read_document_as_markdown(
    account: str | None = None,
    *,
    document_id: str,
    max_chars: int | None = None,
    offset: int = 0,
) -> dict:
    """Read a document as markdown, preserving headings, lists, tables, and links (unlike read_document's plain text).

    Returns up to max_chars characters (default 100000) from offset; when
    truncated=true, call again with offset=next_offset.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.read_document_as_markdown(document_id))
    data["markdown"], meta = cap_text(
        data["markdown"], max_chars, offset,
        hint="Call read_document_as_markdown again with offset=next_offset.",
    )
    return ok(resolved, data, **meta)


@register(mcp, core=True)
def get_content_map(account: str | None = None, *, document_id: str, include_headers_footers: bool = True) -> dict:
    """Return a structured index map of document elements (paragraphs, tables, indices, text previews).

    Includes revisionId: pass it as required_revision_id to index-based writes so
    they fail rather than land at stale positions if the doc changes meanwhile.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.get_content_map(document_id, include_headers_footers))
    return ok(resolved, data)


@register(mcp, core=True)
def find_text(
    account: str | None = None,
    *,
    document_id: str,
    pattern: str,
    regex: bool = False,
    match_case: bool = True,
    max_matches: int = 200,
) -> dict:
    """Find text in the body (table cells included) and return each match's start/end index, a context snippet, and the doc's revisionId.

    Much smaller than get_document. pattern is literal unless regex=true. Indices
    are UTF-16 Docs indices, ready for format_text / delete_range; pass revisionId
    as required_revision_id so a write fails if the doc changed after this read.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.find_text(document_id, pattern, regex, match_case, max_matches))
    return ok(resolved, data)


# --- write ---
@register(mcp, mutating=True)
def create_document(account: str | None = None, *, title: str, folder_id: str | None = None) -> dict:
    """Create a new (empty) Google Doc with the given title, optionally inside folder_id."""
    api, resolved = _api(account)
    return ok(resolved, run_tool(lambda: api.create_document(title, folder_id)))


@register(mcp, mutating=True, core=True)
def create_document_from_markdown(account: str | None = None, *, title: str, markdown: str, folder_id: str | None = None) -> dict:
    """Create a Google Doc from markdown text: headings, bold/italic, lists, links, and tables become native Docs formatting. Prefer this over create_document + insert/format calls for formatted content."""
    api, resolved = _api(account)
    return ok(resolved, run_tool(lambda: api.create_document_from_markdown(title, markdown, folder_id)))


@register(mcp, mutating=True, destructive=True, core=True)
def replace_document_with_markdown(account: str | None = None, *, document_id: str, markdown: str) -> dict:
    """Replace a document's ENTIRE content by re-importing markdown. Comments/suggestions are lost."""
    api, resolved = _api(account)
    return ok(resolved, run_tool(lambda: api.replace_document_with_markdown(document_id, markdown)))


@register(mcp, mutating=True, destructive=True, core=True)
def append_markdown(account: str | None = None, *, document_id: str, markdown: str) -> dict:
    """Append markdown to the end of a document. Round-trips the whole doc through markdown export/import, so elements markdown cannot express (comments, positioned images) are lost."""
    api, resolved = _api(account)
    return ok(resolved, run_tool(lambda: api.append_markdown(document_id, markdown)))


@register(mcp, mutating=True)
def append_text(account: str | None = None, *, document_id: str, text: str) -> dict:
    """Append text to the end of a document."""
    api, resolved = _api(account)
    return ok(resolved, run_tool(lambda: api.append_text(document_id, text)))


@register(mcp, mutating=True, core=True)
def insert_text(account: str | None = None, *, document_id: str, text: str, index: int = 1, required_revision_id: str | None = None) -> dict:
    """Insert text at a specific character index (1 = start of body)."""
    api, resolved = _api(account)
    return ok(resolved, run_tool(lambda: api.insert_text(document_id, text, index, required_revision_id=required_revision_id)))


@register(mcp, mutating=True, core=True)
def replace_all_text(account: str | None = None, *, document_id: str, find: str, replace: str = "", match_case: bool = False) -> dict:
    """Replace every occurrence of a string throughout the document."""
    api, resolved = _api(account)
    return ok(resolved, run_tool(lambda: api.replace_all_text(document_id, find, replace, match_case)))


# --- format / structure ---
@register(mcp, mutating=True, core=True)
def format_text(account: str | None = None, *, document_id: str, start_index: int, end_index: int, bold: bool | None = None, italic: bool | None = None, underline: bool | None = None, strikethrough: bool | None = None, font_size: float | None = None, font_family: str | None = None, link_url: str | None = None, foreground_color: str | None = None, background_color: str | None = None, segment_id: str | None = None, required_revision_id: str | None = None, clear_link: bool = False) -> dict:
    """Style a character range (bold/italic/underline/strikethrough, font size/family in PT, link URL, hex foreground/background colors). Use segment_id for header/footer text.

    clear_link=true removes links in the range, text run by text run (a single
    whole-range clear is ignored by Docs inside tables). To style text by
    content instead of indices, prefer format_matches.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.format_text(document_id, start_index, end_index, bold, italic, underline, strikethrough, font_size, font_family, link_url, foreground_color, background_color, segment_id, required_revision_id=required_revision_id, clear_link=clear_link))
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def format_matches(account: str | None = None, *, document_id: str, pattern: str, regex: bool = False, match_case: bool = True, link_url_template: str | None = None, clear_link: bool = False, bold: bool | None = None, italic: bool | None = None, underline: bool | None = None, strikethrough: bool | None = None, font_size: float | None = None, font_family: str | None = None, foreground_color: str | None = None, background_color: str | None = None, required_revision_id: str | None = None, max_matches: int = 500) -> dict:
    """Style every match of pattern in the body (table cells included) in one call, with no index math.

    Positions are resolved on the server from a fresh read and written in one
    batchUpdate guarded by that read's revision, so they cannot land on stale
    indices. pattern is literal unless regex=true. link_url_template links each
    match, with {match} replaced by the URL-encoded matched text (e.g. pattern
    "LIG-\\d+", regex=true, link_url_template "https://linear.app/acme/issue/{match}").
    clear_link removes links from the matches, run by run. Returns matchCount and
    the new revisionId. Matches never cross a paragraph, table cell, or inline
    image. Fails without writing if there are more than max_matches matches.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.format_matches(
        document_id, pattern, regex, match_case, link_url_template, clear_link, bold, italic, underline,
        strikethrough, font_size, font_family, foreground_color, background_color, required_revision_id,
        max_matches,
    ))
    return ok(resolved, data)


@register(mcp, mutating=True)
def set_paragraph_style(account: str | None = None, *, document_id: str, start_index: int, end_index: int, named_style: str = "NORMAL_TEXT", required_revision_id: str | None = None) -> dict:
    """Apply a paragraph named style (NORMAL_TEXT, TITLE, SUBTITLE, HEADING_1..HEADING_6) to a range."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.set_paragraph_style(document_id, start_index, end_index, named_style, required_revision_id=required_revision_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def update_paragraph_style(account: str | None = None, *, document_id: str, start_index: int, end_index: int, named_style_type: str | None = None, alignment: str | None = None, indent_start_pt: float | None = None, indent_end_pt: float | None = None, space_above_pt: float | None = None, space_below_pt: float | None = None, line_spacing: float | None = None, line_spacing_mode: str | None = None, segment_id: str | None = None, required_revision_id: str | None = None) -> dict:
    """Apply paragraph formatting: alignment, indents/spacing in PT, line_spacing (100=normal), line_spacing_mode (AT_LEAST/EXACTLY/MULTIPLE), named style type."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.update_paragraph_style(document_id, start_index, end_index, named_style_type, alignment, indent_start_pt, indent_end_pt, space_above_pt, space_below_pt, line_spacing, line_spacing_mode, segment_id, required_revision_id=required_revision_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def set_page_layout(account: str | None = None, *, document_id: str, page_preset: str | None = None, width_pt: float | None = None, height_pt: float | None = None, margin_top_pt: float | None = None, margin_bottom_pt: float | None = None, margin_left_pt: float | None = None, margin_right_pt: float | None = None, margin_header_pt: float | None = None, margin_footer_pt: float | None = None, flip_page_orientation: bool | None = None) -> dict:
    """Set page size and margins. Use page_preset (LETTER, A4, LEGAL, TABLOID) or explicit width_pt/height_pt. Margins are in points (72 PT = 1 inch)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.set_page_layout(document_id, page_preset, width_pt, height_pt, margin_top_pt, margin_bottom_pt, margin_left_pt, margin_right_pt, margin_header_pt, margin_footer_pt, flip_page_orientation))
    return ok(resolved, data)


@register(mcp, mutating=True)
def flip_page_orientation(account: str | None = None, *, document_id: str, flip: bool = True) -> dict:
    """Toggle page orientation (portrait ↔ landscape) by swapping width and height."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.flip_page_orientation(document_id, flip))
    return ok(resolved, data)


@register(mcp, mutating=True)
def setup_header(account: str | None = None, *, document_id: str, text: str, header_type: str = "DEFAULT", index: int = 0) -> dict:
    """Create a default header and insert text. Returns headerId for further edits."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.setup_header(document_id, text, header_type, index))
    return ok(resolved, data)


@register(mcp, mutating=True)
def setup_footer(account: str | None = None, *, document_id: str, text: str, footer_type: str = "DEFAULT", index: int = 0) -> dict:
    """Create a default footer and insert text. Returns footerId for further edits."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.setup_footer(document_id, text, footer_type, index))
    return ok(resolved, data)


@register(mcp, mutating=True)
def create_header(account: str | None = None, *, document_id: str, header_type: str = "DEFAULT") -> dict:
    """Create an empty header (DEFAULT type). Fails if one already exists."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.create_header(document_id, header_type))
    return ok(resolved, data)


@register(mcp, mutating=True)
def create_footer(account: str | None = None, *, document_id: str, footer_type: str = "DEFAULT") -> dict:
    """Create an empty footer (DEFAULT type). Fails if one already exists."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.create_footer(document_id, footer_type))
    return ok(resolved, data)


@register(mcp, mutating=True)
def insert_inline_image(account: str | None = None, *, document_id: str, uri: str, index: int = 1, width_pt: float | None = None, height_pt: float | None = None, segment_id: str | None = None, required_revision_id: str | None = None) -> dict:
    """Insert an inline image from a public URI (PNG/JPEG/GIF, max 2KB URL). Optional width/height in PT. Use segment_id for header/footer images."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.insert_inline_image(document_id, uri, index, width_pt, height_pt, segment_id, required_revision_id=required_revision_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def insert_chart_image(account: str | None = None, *, document_id: str, uri: str, index: int = 1, width_pt: float = 468, height_pt: float = 280, segment_id: str | None = None, required_revision_id: str | None = None) -> dict:
    """Insert a chart rendered as an image (export from Sheets or another tool, then pass a public image URL). Default size fits a letter-width page."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.insert_chart_image(document_id, uri, index, width_pt, height_pt, segment_id, required_revision_id=required_revision_id))
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def insert_table(account: str | None = None, *, document_id: str, rows: int = 2, columns: int = 2, index: int = 1, segment_id: str | None = None, required_revision_id: str | None = None) -> dict:
    """Insert an empty table at the given body index (or in a header/footer via segment_id)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.insert_table(document_id, rows, columns, index, segment_id, required_revision_id=required_revision_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def insert_page_break(account: str | None = None, *, document_id: str, index: int = 1, segment_id: str | None = None, required_revision_id: str | None = None) -> dict:
    """Insert a page break at the given index (body or header/footer via segment_id)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.insert_page_break(document_id, index, segment_id, required_revision_id=required_revision_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def insert_bullets(account: str | None = None, *, document_id: str, start_index: int, end_index: int, bullet_preset: str = "BULLET_DISC_CIRCLE_SQUARE", segment_id: str | None = None, required_revision_id: str | None = None) -> dict:
    """Turn the paragraphs in a range into a bulleted/numbered list (e.g. BULLET_DISC_CIRCLE_SQUARE, NUMBERED_DECIMAL_ALPHA_ROMAN)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.insert_bullets(document_id, start_index, end_index, bullet_preset, segment_id, required_revision_id=required_revision_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def insert_numbered_list(account: str | None = None, *, document_id: str, start_index: int, end_index: int, preset: str = "NUMBERED_DECIMAL_ALPHA_ROMAN", segment_id: str | None = None, required_revision_id: str | None = None) -> dict:
    """Apply numbered list formatting. Presets: NUMBERED_DECIMAL_ALPHA_ROMAN, NUMBERED_DECIMAL_ALPHA_ROMAN_PARENS, NUMBERED_DECIMAL_NESTED, NUMBERED_UPPERALPHA_ALPHA_ROMAN, NUMBERED_UPPERROMAN_UPPERALPHA_DECIMAL, NUMBERED_ZERODECIMAL_ALPHA_ROMAN."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.insert_numbered_list(document_id, start_index, end_index, preset, segment_id, required_revision_id=required_revision_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def populate_table(account: str | None = None, *, document_id: str, table_start_index: int, rows: list[list[str]], segment_id: str | None = None, required_revision_id: str | None = None) -> dict:
    """Fill table cells with text. Use get_content_map to find table_start_index and dimensions."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.populate_table(document_id, table_start_index, rows, segment_id, required_revision_id=required_revision_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def merge_table_cells(account: str | None = None, *, document_id: str, table_start_index: int, row: int = 0, column: int = 0, row_span: int = 1, column_span: int = 1, segment_id: str | None = None, required_revision_id: str | None = None) -> dict:
    """Merge a rectangular range of table cells."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.merge_table_cells(document_id, table_start_index, row, column, row_span, column_span, segment_id, required_revision_id=required_revision_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def format_table_cells(account: str | None = None, *, document_id: str, table_start_index: int, row: int = 0, column: int = 0, row_span: int = 1, column_span: int = 1, background_color: str | None = None, border_color: str | None = None, border_width_pt: float | None = None, segment_id: str | None = None, required_revision_id: str | None = None) -> dict:
    """Style table cells: hex background_color, border_color, border_width_pt."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.format_table_cells(document_id, table_start_index, row, column, row_span, column_span, background_color, border_color, border_width_pt, segment_id, required_revision_id=required_revision_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def insert_page_number(account: str | None = None, *, document_id: str, footer_id: str, index: int = 0) -> dict:
    """The public Google Docs API currently rejects insertPageNumber (400 Unknown name).

    Insert a dynamic page number into an existing footer (footer_id from setup_footer/create_footer).
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.insert_page_number(document_id, footer_id, index))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def batch_update(account: str | None = None, *, document_id: str, requests: list[dict] | str, required_revision_id: str | None = None) -> dict:
    """Execute raw Docs batchUpdate requests. Pass a list of request dicts or a JSON string.

    Pass required_revision_id (the revisionId from the read you computed indices
    from) so the write fails instead of landing at shifted positions if the doc
    changed in between. To style text by content, prefer format_matches.
    """
    api, resolved = _api(account)

    def _call():
        raw = requests
        if isinstance(raw, str):
            raw = json.loads(raw)
        return api.batch_update(document_id, raw, required_revision_id)

    data = run_tool(_call)
    return ok(resolved, data)


@register(mcp, mutating=True)
def insert_sheets_chart(account: str | None = None, *, document_id: str, spreadsheet_id: str, chart_id: int, index: int = 1, width_pt: float = 468, height_pt: float = 280, make_public: bool = False) -> dict:
    """Insert a Sheets chart. Default refuses. make_public=True uploads a PNG, grants anyone/reader, and returns fileId so you can revoke it.

    Docs can only fetch a public image URL. Pass make_public=True to publish.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.insert_sheets_chart(document_id, spreadsheet_id, chart_id, index, width_pt, height_pt, account=resolved, make_public=make_public))
    return ok(resolved, data)


@register(mcp, mutating=True)
def remove_bullets(account: str | None = None, *, document_id: str, start_index: int, end_index: int, segment_id: str | None = None, required_revision_id: str | None = None) -> dict:
    """Remove bullets/numbering from paragraphs in a range."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.remove_bullets(document_id, start_index, end_index, segment_id, required_revision_id=required_revision_id))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_range(account: str | None = None, *, document_id: str, start_index: int, end_index: int, segment_id: str | None = None, required_revision_id: str | None = None) -> dict:
    """Delete content in the given character index range (body or header/footer via segment_id)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_range(document_id, start_index, end_index, segment_id, required_revision_id=required_revision_id))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_header(account: str | None = None, *, document_id: str, header_id: str) -> dict:
    """Delete a header by its headerId."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_header(document_id, header_id))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_footer(account: str | None = None, *, document_id: str, footer_id: str) -> dict:
    """Delete a footer by its footerId."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_footer(document_id, footer_id))
    return ok(resolved, data)


@register(mcp)
def list_comments(
    account: str | None = None,
    *,
    document_id: str,
    include_deleted: bool = False,
    page_size: int = 20,
    page_token: str | None = None,
) -> dict:
    """List comment threads on a document (Drive discussions, not in-editor highlights).

    Pass page_token from a previous response's next_page_token to continue.
    """
    api, resolved = _api(account)
    raw = run_tool(lambda: api.list_comments(document_id, include_deleted, page_size, page_token))
    next_token = raw.pop("nextPageToken", None)
    return ok(resolved, raw, next_page_token=next_token)


@register(mcp, mutating=True)
def add_comment(
    account: str | None = None,
    *,
    document_id: str,
    content: str,
    start_index: int | None = None,
    end_index: int | None = None,
    quote: str | None = None,
    occurrence: int = 1,
) -> dict:
    """Add a comment on a sentence or words via quote (or indexes). Google will not highlight in the editor; the comment quotes those words and remembers the range."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.add_comment(document_id, content, start_index, end_index, quote, occurrence)
    )
    return ok(resolved, data)


@register(mcp, mutating=True)
def reply_to_comment(
    account: str | None = None,
    *,
    document_id: str,
    comment_id: str,
    content: str | None = None,
    action: str | None = None,
) -> dict:
    """Reply to a document discussion, or pass action='resolve' / 'reopen'."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.reply_to_comment(document_id, comment_id, content, action))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_comment(account: str | None = None, *, document_id: str, comment_id: str) -> dict:
    """Delete a document discussion thread."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_comment(document_id, comment_id))
    return ok(resolved, data)


def main():
    mcp.run()
