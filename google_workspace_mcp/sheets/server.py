"""Google Sheets MCP server: read/write values and manage sheet structure."""
from __future__ import annotations

import functools
import json

from google_auth_core import InvalidArgumentError

from ..core import DEFAULT_MAX_CHARS, build_server, cap_items, get_api, ok, run_tool
from ..core import register as _register
from .sheets_api import SheetsAPI

mcp = build_server(
    "gsheets-mcp",
    "Google Sheets: read/write values, formulas, filters, and table formatting for one or more accounts. "
    "Ranges use A1 notation. When writing tabular data, prefer write_table (or update_range followed by "
    "format_table / optimize_layout) so the sheet stays human-readable: content-aware column widths, "
    "wrapped long text, alternating row colors. Do not freeze rows/columns unless the user asks.",
    service_name="sheets",
)


# Every tool here names Drive files, so a client scope's drive_folders limit it.
register = functools.partial(_register, drive_scoped=True)


def _api(account=None):
    return get_api("sheets", SheetsAPI, account)


# --- READ tools ---

@register(mcp, core=True)
def get_spreadsheet(account: str | None = None, *, spreadsheet_id: str, include_grid_data: bool = False) -> dict:
    """Return spreadsheet metadata (title, sheets list, properties). Set include_grid_data=True to also return cell data."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.get_spreadsheet(spreadsheet_id, include_grid_data))
    return ok(resolved, data)


_ROWS_HINT = (
    "Rows were cut to fit max_chars. Read the rest with a narrower A1 range that "
    "starts next_offset rows below the start of the requested range."
)


def _cap_rows(value_range: dict, max_chars: int | None) -> bool:
    """Trim value_range["values"] in place to fit max_chars; True when rows were cut."""
    rows = value_range.get("values") or []
    kept, meta = cap_items(rows, max_chars)
    if not meta:
        return False
    value_range["values"] = kept
    value_range.update(truncated=True, total_rows=len(rows), next_offset=meta["next_offset"])
    return True


@register(mcp, core=True)
def read_range(account: str | None = None, *, spreadsheet_id: str, range: str, value_render_option: str = "FORMATTED_VALUE", max_chars: int | None = None) -> dict:
    """Read cell values from a range (A1 notation, e.g. 'Sheet1!A1:C10').

    Rows are capped at max_chars of JSON (default 100000). When truncated=true,
    next_offset is the number of rows returned; read the rest with a narrower range.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.read_range(spreadsheet_id, range, value_render_option))
    if _cap_rows(data, max_chars):
        return ok(resolved, data, truncated=True, hint=_ROWS_HINT)
    return ok(resolved, data)


@register(mcp, core=True)
def batch_read(account: str | None = None, *, spreadsheet_id: str, ranges: list[str], value_render_option: str = "FORMATTED_VALUE", max_chars: int | None = None) -> dict:
    """Read cell values from multiple ranges in one request (A1 notation).

    All ranges share one max_chars budget of JSON (default 100000). Any range
    cut short is marked truncated=true with total_rows and next_offset (rows
    returned); read the rest with narrower ranges.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.batch_read(spreadsheet_id, ranges, value_render_option))
    budget = DEFAULT_MAX_CHARS if max_chars is None else max_chars
    if budget < 1:
        raise InvalidArgumentError("max_chars must be at least 1")
    truncated = False
    for vr in data.get("valueRanges") or []:
        if budget <= 0:
            rows = vr.get("values") or []
            if rows:
                vr.update(values=[], truncated=True, total_rows=len(rows), next_offset=0)
                truncated = True
            continue
        truncated |= _cap_rows(vr, budget)
        budget -= len(json.dumps(vr.get("values") or [], default=str))
    if truncated:
        return ok(resolved, data, truncated=True, hint=_ROWS_HINT)
    return ok(resolved, data)


# --- WRITE (mutating) tools ---

@register(mcp, mutating=True, core=True)
def create_spreadsheet(account: str | None = None, *, title: str, folder_id: str | None = None) -> dict:
    """Create a new Google Spreadsheet with the given title (optionally inside folder_id) and return its metadata."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.create_spreadsheet(title, folder_id))
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def update_range(account: str | None = None, *, spreadsheet_id: str, range: str, values: list[list], value_input_option: str = "USER_ENTERED") -> dict:
    """Write a 2D array of values to a range (A1 notation). Overwrites existing cells."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.update_range(spreadsheet_id, range, values, value_input_option))
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def batch_update_values(account: str | None = None, *, spreadsheet_id: str, data: list[dict], value_input_option: str = "USER_ENTERED") -> dict:
    """Write values to multiple ranges in one request. Each item in data must have 'range' and 'values' keys."""
    api, resolved = _api(account)
    result = run_tool(lambda: api.batch_update_values(spreadsheet_id, data, value_input_option))
    return ok(resolved, result)


@register(mcp, mutating=True, core=True)
def append_rows(account: str | None = None, *, spreadsheet_id: str, range: str, values: list[list], value_input_option: str = "USER_ENTERED") -> dict:
    """Append rows after the last row with data in the given range (A1 notation)."""
    api, resolved = _api(account)
    result = run_tool(lambda: api.append_rows(spreadsheet_id, range, values, value_input_option))
    return ok(resolved, result)


@register(mcp, mutating=True, core=True)
def add_sheet(account: str | None = None, *, spreadsheet_id: str, title: str) -> dict:
    """Add a new sheet (tab) to an existing spreadsheet."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.add_sheet(spreadsheet_id, title))
    return ok(resolved, data)


@register(mcp, mutating=True)
def rename_sheet(account: str | None = None, *, spreadsheet_id: str, sheet_id: int, new_title: str) -> dict:
    """Rename a sheet (tab) by its numeric sheet ID."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.rename_sheet(spreadsheet_id, sheet_id, new_title))
    return ok(resolved, data)


# --- DESTRUCTIVE tools ---

@register(mcp, mutating=True, destructive=True, core=True)
def clear_range(account: str | None = None, *, spreadsheet_id: str, range: str) -> dict:
    """Clear values from a range (formatting is kept)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.clear_range(spreadsheet_id, range))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_sheet(account: str | None = None, *, spreadsheet_id: str, sheet_id: int) -> dict:
    """Delete a sheet (tab) by its numeric sheet ID."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_sheet(spreadsheet_id, sheet_id))
    return ok(resolved, data)


# --- FORMATTING / STRUCTURE (mutating) tools ---

@register(mcp, mutating=True, core=True)
def format_cells(account: str | None = None, *, spreadsheet_id: str, range: str, bold: bool | None = None, italic: bool | None = None, strikethrough: bool | None = None, underline: bool | None = None, font_size: int | None = None, text_color: str | None = None, background_color: str | None = None, number_format: str | None = None, horizontal_alignment: str | None = None, vertical_alignment: str | None = None, wrap: bool | None = None) -> dict:
    """Apply formatting to a range (A1 notation). Colors are hex (e.g. '#FF0000'); horizontal_alignment is LEFT/CENTER/RIGHT; vertical_alignment is TOP/MIDDLE/BOTTOM; wrap=True enables text wrap."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.format_cells(spreadsheet_id, range, bold, italic, strikethrough, underline, font_size, text_color, background_color, number_format, horizontal_alignment, vertical_alignment, wrap))
    return ok(resolved, data)


@register(mcp, mutating=True)
def sort_range(account: str | None = None, *, spreadsheet_id: str, range: str, column: int = 0, ascending: bool = True) -> dict:
    """Sort a range (A1 notation) by an absolute 0-based sheet column index, ascending or descending."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.sort_range(spreadsheet_id, range, column, ascending))
    return ok(resolved, data)


@register(mcp, mutating=True)
def set_basic_filter(account: str | None = None, *, spreadsheet_id: str, range: str, filter_specs: list[dict] | None = None, sort_specs: list[dict] | None = None) -> dict:
    """Set a basic filter over a range (A1 notation). filter_specs: [{column: 0, hidden_values: ['x']}] or [{column: 1, condition_type: 'TEXT_CONTAINS', values: ['foo']}]. sort_specs: [{column: 0, ascending: true}]."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.set_basic_filter(spreadsheet_id, range, filter_specs, sort_specs))
    return ok(resolved, data)


@register(mcp, mutating=True)
def clear_basic_filter(account: str | None = None, *, spreadsheet_id: str, range: str) -> dict:
    """Clear the basic filter from the sheet that contains the given range (A1 notation)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.clear_basic_filter(spreadsheet_id, range))
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def find_replace(account: str | None = None, *, spreadsheet_id: str, find: str, replacement: str = "", range: str | None = None, all_sheets: bool = False, match_case: bool = False, match_entire_cell: bool = False, search_by_regex: bool = False, include_formulas: bool = False) -> dict:
    """Find and replace text. Scope to a range (A1, e.g. 'Sheet1!A1:C9'), a whole sheet (bare tab name like 'Sheet1'), or set all_sheets=True. search_by_regex treats 'find' as a regex; include_formulas also searches formula text. Returns counts of changes."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.find_replace(spreadsheet_id, find, replacement, range, all_sheets, match_case, match_entire_cell, search_by_regex, include_formulas))
    return ok(resolved, data)


@register(mcp, mutating=True)
def copy_paste(account: str | None = None, *, spreadsheet_id: str, source_range: str, destination_range: str, paste_type: str = "PASTE_NORMAL", transpose: bool = False) -> dict:
    """Copy a range (A1) to a destination range, within or across sheets (use a sheet prefix in either A1). paste_type: PASTE_NORMAL/PASTE_VALUES/PASTE_FORMAT/PASTE_FORMULA/PASTE_NO_BORDERS/PASTE_DATA_VALIDATION/PASTE_CONDITIONAL_FORMATTING. transpose=True flips rows and columns."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.copy_paste(spreadsheet_id, source_range, destination_range, paste_type, transpose))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def cut_paste(account: str | None = None, *, spreadsheet_id: str, source_range: str, destination: str, paste_type: str = "PASTE_NORMAL") -> dict:
    """Move a range (A1) to a destination anchor cell (single cell A1, e.g. 'Sheet2!A1'), clearing the source. paste_type matches copy_paste."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.cut_paste(spreadsheet_id, source_range, destination, paste_type))
    return ok(resolved, data)


@register(mcp, mutating=True)
def merge_cells(account: str | None = None, *, spreadsheet_id: str, range: str, merge_type: str = "MERGE_ALL") -> dict:
    """Merge cells in a range (A1 notation). merge_type is MERGE_ALL/MERGE_COLUMNS/MERGE_ROWS."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.merge_cells(spreadsheet_id, range, merge_type))
    return ok(resolved, data)


@register(mcp, mutating=True)
def unmerge_cells(account: str | None = None, *, spreadsheet_id: str, range: str) -> dict:
    """Unmerge any merged cells within a range (A1 notation)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.unmerge_cells(spreadsheet_id, range))
    return ok(resolved, data)


@register(mcp, mutating=True)
def resize_columns(account: str | None = None, *, spreadsheet_id: str, range: str, width: int | None = None) -> dict:
    """Resize the columns covered by a range (e.g. 'Sheet1!A:C' or 'Sheet1!B2:D9') to a pixel width. Omit width to auto-fit each column to its content."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.resize_dimension(spreadsheet_id, range, "COLUMNS", width))
    return ok(resolved, data)


@register(mcp, mutating=True)
def resize_rows(account: str | None = None, *, spreadsheet_id: str, range: str, height: int | None = None) -> dict:
    """Resize the rows covered by a range (e.g. 'Sheet1!2:10') to a pixel height. Omit height to auto-fit each row to its content."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.resize_dimension(spreadsheet_id, range, "ROWS", height))
    return ok(resolved, data)


@register(mcp, mutating=True)
def hide_columns(account: str | None = None, *, spreadsheet_id: str, range: str, hidden: bool = True) -> dict:
    """Hide the columns covered by a column range (e.g. 'Sheet1!C:D'). Pass hidden=False to unhide them."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.set_dimension_visibility(spreadsheet_id, range, "COLUMNS", hidden))
    return ok(resolved, data)


@register(mcp, mutating=True)
def hide_rows(account: str | None = None, *, spreadsheet_id: str, range: str, hidden: bool = True) -> dict:
    """Hide the rows covered by a row range (e.g. 'Sheet1!3:5'). Pass hidden=False to unhide them."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.set_dimension_visibility(spreadsheet_id, range, "ROWS", hidden))
    return ok(resolved, data)


@register(mcp, mutating=True)
def freeze_panes(account: str | None = None, *, spreadsheet_id: str, range: str, rows: int | None = None, cols: int | None = None) -> dict:
    """Freeze the first N rows and/or columns of the sheet containing the given range (A1 notation). Pass 0 to unfreeze. Rarely needed: only freeze when the user explicitly asks, or headers genuinely scroll out of view on a very large sheet."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.freeze_panes(spreadsheet_id, range, rows, cols))
    return ok(resolved, data)


@register(mcp, mutating=True)
def insert_rows(account: str | None = None, *, spreadsheet_id: str, range: str, inherit_from_before: bool = False) -> dict:
    """Insert blank rows at the position covered by a row range (e.g. 'Sheet1!3:5' inserts 3 rows starting at row 3). Existing rows shift down."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.insert_dimension(spreadsheet_id, range, "ROWS", inherit_from_before))
    return ok(resolved, data)


@register(mcp, mutating=True)
def insert_columns(account: str | None = None, *, spreadsheet_id: str, range: str, inherit_from_before: bool = False) -> dict:
    """Insert blank columns at the position covered by a column range (e.g. 'Sheet1!C:D' inserts 2 columns at C). Existing columns shift right."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.insert_dimension(spreadsheet_id, range, "COLUMNS", inherit_from_before))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_rows(account: str | None = None, *, spreadsheet_id: str, range: str) -> dict:
    """Delete the rows covered by a row range (e.g. 'Sheet1!3:5'). Rows below shift up."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_dimension(spreadsheet_id, range, "ROWS"))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_columns(account: str | None = None, *, spreadsheet_id: str, range: str) -> dict:
    """Delete the columns covered by a column range (e.g. 'Sheet1!C:D'). Columns to the right shift left."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_dimension(spreadsheet_id, range, "COLUMNS"))
    return ok(resolved, data)


@register(mcp, mutating=True)
def set_borders(account: str | None = None, *, spreadsheet_id: str, range: str, style: str = "SOLID", color: str = "#000000", top: bool = True, bottom: bool = True, left: bool = True, right: bool = True, inner: bool = False) -> dict:
    """Draw borders around a range (A1 notation). style is SOLID/SOLID_MEDIUM/SOLID_THICK/DASHED/DOTTED/DOUBLE; color is hex. Set inner=True to also draw gridlines between cells."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.set_borders(spreadsheet_id, range, style, color, top, bottom, left, right, inner))
    return ok(resolved, data)


@register(mcp, mutating=True)
def set_data_validation(account: str | None = None, *, spreadsheet_id: str, range: str, allowed_values: list[str] | None = None, show_dropdown: bool = True) -> dict:
    """Restrict cells in a range to a list of allowed values, shown as an in-cell dropdown. Pass an empty list to clear validation."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.set_data_validation(spreadsheet_id, range, allowed_values, show_dropdown))
    return ok(resolved, data)


@register(mcp, mutating=True)
def duplicate_sheet(account: str | None = None, *, spreadsheet_id: str, sheet_id: int, new_title: str | None = None) -> dict:
    """Duplicate a sheet (tab) by its numeric sheet ID, optionally giving the copy a new title."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.duplicate_sheet(spreadsheet_id, sheet_id, new_title))
    return ok(resolved, data)


@register(mcp, mutating=True)
def add_banding(account: str | None = None, *, spreadsheet_id: str, range: str, header_color: str | None = None, first_band_color: str = "#FFFFFF", second_band_color: str = "#F3F3F3", footer_color: str | None = None, band_rows: bool = True) -> dict:
    """Add alternating row (or column) colors to a range. header_color styles the first row; first/second_band_color alternate thereafter."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.add_banding(spreadsheet_id, range, header_color, first_band_color, second_band_color, footer_color, band_rows))
    return ok(resolved, data)


@register(mcp, mutating=True)
def update_banding(account: str | None = None, *, spreadsheet_id: str, banded_range_id: int, header_color: str | None = None, first_band_color: str | None = None, second_band_color: str | None = None, footer_color: str | None = None) -> dict:
    """Update colors on an existing banded range by bandedRangeId."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.update_banding(spreadsheet_id, banded_range_id, header_color, first_band_color, second_band_color, footer_color))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_banding(account: str | None = None, *, spreadsheet_id: str, banded_range_id: int) -> dict:
    """Remove a banded (alternating color) range by its bandedRangeId (from get_spreadsheet or add_banding response)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_banding(spreadsheet_id, banded_range_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def add_table(account: str | None = None, *, spreadsheet_id: str, range: str, name: str, header_color: str = "#355468", first_band_color: str = "#FFFFFF", second_band_color: str = "#F3F3F3", column_names: list[str] | None = None) -> dict:
    """Create a native Google Sheets table with header and alternating row colors. column_names sets header labels by column index."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.add_table(spreadsheet_id, range, name, header_color, first_band_color, second_band_color, column_names))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_table(account: str | None = None, *, spreadsheet_id: str, table_id: str) -> dict:
    """Delete a native table by its tableId (from the add_table reply, or get_spreadsheet where each sheet lists its 'tables'). Cell values remain; the table structure is removed."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_table(spreadsheet_id, table_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def update_table(account: str | None = None, *, spreadsheet_id: str, table_id: str, name: str | None = None, range: str | None = None, header_color: str | None = None, first_band_color: str | None = None, second_band_color: str | None = None, footer_color: str | None = None, column_properties: list[dict] | None = None) -> dict:
    """Update an existing native table's settings by tableId. name renames it; range (A1) resizes/moves it; header/first_band/second_band/footer_color recolor it (hex). column_properties sets per-column settings: [{column_index, column_name, column_type, values}] where column_type is TEXT/DOUBLE/PERCENT/DATE/BOOLEAN/DROPDOWN and values builds a dropdown."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.update_table(spreadsheet_id, table_id, name, range, header_color, first_band_color, second_band_color, footer_color, column_properties))
    return ok(resolved, data)


@register(mcp, mutating=True)
def format_table(account: str | None = None, *, spreadsheet_id: str, range: str, header_color: str = "#355468", header_text_color: str = "#FFFFFF", first_band_color: str = "#FFFFFF", second_band_color: str = "#F3F3F3", wrap: bool = True, auto_resize_columns: bool = True, add_filter: bool = True, add_borders: bool = True, freeze_header: bool = False, max_column_width: int = 320) -> dict:
    """One-shot table styling: formatted header, alternating row colors, filter dropdowns, borders, and content-aware column widths (capped at max_column_width px; over-cap columns wrap). Set freeze_header=True only if the user asks for frozen headers."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.format_table(spreadsheet_id, range, header_color, header_text_color, first_band_color, second_band_color, wrap, auto_resize_columns, add_filter, add_borders, freeze_header, max_column_width))
    return ok(resolved, data)


@register(mcp, mutating=True)
def optimize_layout(account: str | None = None, *, spreadsheet_id: str, range: str, max_column_width: int = 320, min_column_width: int = 48, resize_rows: bool = True) -> dict:
    """Make a sheet readable in one call: size each column to its content (capped at max_column_width px), wrap only over-cap columns (top-aligned), and auto-fit row heights. Pass an A1 range or a bare tab name for the whole sheet. Use after writing data."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.optimize_layout(spreadsheet_id, range, max_column_width, min_column_width, resize_rows))
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def write_table(account: str | None = None, *, spreadsheet_id: str, anchor: str, values: list[list], style: str = "native", name: str | None = None, header_color: str = "#355468", first_band_color: str = "#FFFFFF", second_band_color: str = "#F3F3F3", max_column_width: int = 320) -> dict:
    """Write a 2D array (first row = headers) at an anchor cell (e.g. 'Sheet1!A1') and make it readable in one shot: native Sheets table (style='native'), alternating-color banding (style='banded'), or values only (style='plain'), plus content-aware column widths and wrapping. Prefer this over update_range for tabular data. Overwrites cells in the target range. If a native table already overlaps the range, use style='banded' or update_table instead."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.write_table(spreadsheet_id, anchor, values, style, name, header_color, first_band_color, second_band_color, max_column_width))
    return ok(resolved, data)


@register(mcp)
def read_formulas(account: str | None = None, *, spreadsheet_id: str, range: str) -> dict:
    """Read formula strings from a range (A1 notation), e.g. '=SUM(A2:A10)'."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.read_formulas(spreadsheet_id, range))
    return ok(resolved, data)


@register(mcp, mutating=True)
def write_formulas(account: str | None = None, *, spreadsheet_id: str, range: str, formulas: list[list]) -> dict:
    """Write formula strings to a range (A1 notation). Pass 2D array with '=' prefixes, e.g. [['=SUM(A2:A10)', '=A2*B2']]."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.write_formulas(spreadsheet_id, range, formulas))
    return ok(resolved, data)


@register(mcp, mutating=True)
def add_chart(account: str | None = None, *, spreadsheet_id: str, range: str, chart_type: str = "COLUMN", title: str | None = None, stacked: bool = False) -> dict:
    """Create a chart from a range (A1 with sheet name, e.g. Sheet1!A1:C10). chart_type is COLUMN/BAR/LINE/AREA/PIE. First column is the X axis; other columns are series."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.add_chart(spreadsheet_id, range, chart_type, title, stacked))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def batch_update(account: str | None = None, *, spreadsheet_id: str, requests: list[dict] | str) -> dict:
    """Execute raw Sheets batchUpdate requests. Pass a list of request dicts or a JSON string."""
    api, resolved = _api(account)

    def _call():
        raw = requests
        if isinstance(raw, str):
            raw = json.loads(raw)
        return api.batch_update(spreadsheet_id, raw)

    data = run_tool(_call)
    return ok(resolved, data)


# --- TEXT EDITING (mutating) tools ---

@register(mcp, mutating=True)
def edit_cell(account: str | None = None, *, spreadsheet_id: str, cell: str, operation: str, find: str | None = None, replacement: str | None = None, position: int | None = None, length: int | None = None, text: str | None = None, count: int | None = None) -> dict:
    """Edit the text of a single cell in place. operation is one of replace/insert/delete/append/prepend/newline. Reads the cell's literal text (the formula string for formula cells), applies the edit, writes it back. 'newline' adds an in-cell line break (pair with format_cells wrap=True). Returns {cell, old, new, changed}."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.edit_cell(spreadsheet_id, cell, operation, find, replacement, position, length, text, count))
    return ok(resolved, data)


@register(mcp, mutating=True)
def transform_text(account: str | None = None, *, spreadsheet_id: str, range: str, transform: str) -> dict:
    """Bulk text transform across a range: upper/lower/title/capitalize/trim/collapse_spaces. Transforms literal text only; cells holding a formula ('=...') and numbers are left unchanged."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.transform_text(spreadsheet_id, range, transform))
    return ok(resolved, data)


@register(mcp, mutating=True)
def regex_replace(account: str | None = None, *, spreadsheet_id: str, range: str, pattern: str, replacement: str = "", count: int = 0, ignore_case: bool = False) -> dict:
    r"""Regex find/replace across a range using Python re. Supports \1 / \g<name> backreferences (unlike native find_replace). count=0 replaces all matches in each cell. Cells holding a formula ('=...') are skipped."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.regex_replace(spreadsheet_id, range, pattern, replacement, count, ignore_case))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def split_column(account: str | None = None, *, spreadsheet_id: str, range: str, delimiter: str, max_splits: int = -1) -> dict:
    """Split a single column's cells by a delimiter into adjacent columns (like Sheets 'Split text to columns'). OVERWRITES columns to the right of the source column. max_splits=-1 means no limit."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.split_column(spreadsheet_id, range, delimiter, max_splits))
    return ok(resolved, data)


@register(mcp, mutating=True)
def join_columns(account: str | None = None, *, spreadsheet_id: str, range: str, separator: str = " ", target_range: str | None = None) -> dict:
    """Join each row of a multi-column range into one string (separator placed between non-empty cells) and write it to target_range. target_range defaults to the range's first column (overwriting it)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.join_columns(spreadsheet_id, range, separator, target_range))
    return ok(resolved, data)


@register(mcp, mutating=True)
def regex_extract(account: str | None = None, *, spreadsheet_id: str, range: str, pattern: str, group: int = 0, target_range: str | None = None) -> dict:
    """Extract the first regex match (or a capture group) from each cell in a single column into target_range. group=0 is the whole match. target_range defaults in-place (overwriting the source column). Non-matching cells become ''."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.regex_extract(spreadsheet_id, range, pattern, group, target_range))
    return ok(resolved, data)


@register(mcp)
def list_comments(
    account: str | None = None,
    *,
    spreadsheet_id: str,
    include_deleted: bool = False,
    page_size: int = 20,
    page_token: str | None = None,
) -> dict:
    """List comment threads on a spreadsheet (Drive discussions, not cell markers).

    Pass page_token from a previous response's next_page_token to continue.
    """
    api, resolved = _api(account)
    raw = run_tool(lambda: api.list_comments(spreadsheet_id, include_deleted, page_size, page_token))
    next_token = raw.pop("nextPageToken", None)
    return ok(resolved, raw, next_page_token=next_token)


@register(mcp, mutating=True)
def add_comment(
    account: str | None = None,
    *,
    spreadsheet_id: str,
    content: str,
    cell: str | None = None,
) -> dict:
    """Add a comment thread on a spreadsheet. cell (A1, e.g. Sheet1!B2) is remembered, not a Sheets pin."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.add_comment(spreadsheet_id, content, cell))
    return ok(resolved, data)


@register(mcp, mutating=True)
def reply_to_comment(
    account: str | None = None,
    *,
    spreadsheet_id: str,
    comment_id: str,
    content: str | None = None,
    action: str | None = None,
) -> dict:
    """Reply to a spreadsheet discussion, or pass action='resolve' / 'reopen'."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.reply_to_comment(spreadsheet_id, comment_id, content, action))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_comment(account: str | None = None, *, spreadsheet_id: str, comment_id: str) -> dict:
    """Delete a spreadsheet discussion thread."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_comment(spreadsheet_id, comment_id))
    return ok(resolved, data)


# --- CONDITIONAL FORMATS / PROTECTED RANGES / NAMED RANGES ---

@register(mcp, mutating=True)
def add_conditional_format(
    account: str | None = None,
    *,
    spreadsheet_id: str,
    range: str,
    condition: str,
    values: list[str | int | float] | None = None,
    background_color: str | None = None,
    text_color: str | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    min_color: str = "#FFFFFF",
    mid_color: str | None = None,
    max_color: str = "#57BB8A",
    index: int = 0,
) -> dict:
    """Add a conditional format rule to a range: highlight cells matching a condition, or a COLOR_SCALE gradient.

    condition: NUMBER_GREATER / NUMBER_GREATER_THAN_EQ / NUMBER_LESS / NUMBER_LESS_THAN_EQ / NUMBER_EQ /
    NUMBER_NOT_EQ / NUMBER_BETWEEN / NUMBER_NOT_BETWEEN (2 values) / TEXT_CONTAINS / TEXT_NOT_CONTAINS /
    TEXT_STARTS_WITH / TEXT_ENDS_WITH / TEXT_EQ / BLANK / NOT_BLANK (no values) / CUSTOM_FORMULA
    (values=['=$B2>100'], relative to the range's top-left cell) / COLOR_SCALE (uses min/mid/max_color).
    Boolean rules need at least one of background_color, text_color, bold, italic. index 0 = highest priority.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.add_conditional_format(
        spreadsheet_id, range, condition, values, background_color, text_color, bold, italic,
        min_color, mid_color, max_color, index,
    ))
    return ok(resolved, data)


@register(mcp)
def list_conditional_formats(account: str | None = None, *, spreadsheet_id: str, sheet: str | None = None) -> dict:
    """List conditional format rules per sheet with their index (used by delete_conditional_format)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.list_conditional_formats(spreadsheet_id, sheet))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_conditional_format(account: str | None = None, *, spreadsheet_id: str, sheet: str, index: int) -> dict:
    """Delete the conditional format rule at index on a sheet (by title). Later rules shift up one index."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_conditional_format(spreadsheet_id, sheet, index))
    return ok(resolved, data)


@register(mcp, mutating=True)
def protect_range(
    account: str | None = None,
    *,
    spreadsheet_id: str,
    range: str,
    description: str | None = None,
    warning_only: bool = False,
    editors: list[str] | None = None,
) -> dict:
    """Protect a range (A1) or a whole sheet (bare sheet title) from edits. Returns protectedRangeId.

    editors: emails allowed to edit (you and the owner always can). warning_only=True only shows a
    warning on edit and cannot be combined with editors.
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.protect_range(spreadsheet_id, range, description, warning_only, editors))
    return ok(resolved, data)


@register(mcp)
def list_protected_ranges(account: str | None = None, *, spreadsheet_id: str) -> dict:
    """List protected ranges and sheets with their protectedRangeId, A1 range, editors and warning_only flag."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.list_protected_ranges(spreadsheet_id))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def unprotect_range(account: str | None = None, *, spreadsheet_id: str, protected_range_id: int) -> dict:
    """Remove a protection by protectedRangeId (see list_protected_ranges), so anyone with edit access can edit it."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.unprotect_range(spreadsheet_id, protected_range_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def add_named_range(account: str | None = None, *, spreadsheet_id: str, name: str, range: str) -> dict:
    """Name a range (A1, or a bare sheet title for the whole sheet) for use in formulas. Returns namedRangeId."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.add_named_range(spreadsheet_id, name, range))
    return ok(resolved, data)


@register(mcp)
def list_named_ranges(account: str | None = None, *, spreadsheet_id: str) -> dict:
    """List named ranges with their namedRangeId, name and A1 range."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.list_named_ranges(spreadsheet_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def update_named_range(
    account: str | None = None,
    *,
    spreadsheet_id: str,
    named_range_id: str,
    name: str | None = None,
    range: str | None = None,
) -> dict:
    """Rename a named range and/or point it at a new range (A1 or bare sheet title)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.update_named_range(spreadsheet_id, named_range_id, name, range))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_named_range(account: str | None = None, *, spreadsheet_id: str, named_range_id: str) -> dict:
    """Delete a named range by namedRangeId. Formulas that use the name will show #NAME? errors."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_named_range(spreadsheet_id, named_range_id))
    return ok(resolved, data)


def main():
    mcp.run()
