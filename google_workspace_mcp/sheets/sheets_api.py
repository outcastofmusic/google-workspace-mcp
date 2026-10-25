"""Minimal Google Sheets API wrapper (values + structure operations)."""
from __future__ import annotations

import re

import google_auth_core as core
from google.auth.transport.requests import Request as GoogleAuthRequest

from ..drive.drive_api import DriveAPI, place_in_folder

# Width heuristic for the default Sheets font (Arial 10):
# ~7 px per character plus ~14 px of cell padding/border.
CHAR_PX = 7
CELL_PADDING_PX = 14
CHART_IMAGE_TIMEOUT_SECONDS = 30
_CHART_TYPES = frozenset({"COLUMN", "BAR", "LINE", "AREA", "PIE"})
_STACKED_CHART_TYPES = frozenset({"COLUMN", "BAR", "AREA"})
_BARE_CELLS_RE = re.compile(r"^[A-Za-z]{0,3}\d*(:[A-Za-z]{0,3}\d*)?$")
# Boolean conditional-format condition types -> number of values each takes.
_CF_CONDITIONS = {
    "NUMBER_GREATER": 1, "NUMBER_GREATER_THAN_EQ": 1, "NUMBER_LESS": 1, "NUMBER_LESS_THAN_EQ": 1,
    "NUMBER_EQ": 1, "NUMBER_NOT_EQ": 1, "NUMBER_BETWEEN": 2, "NUMBER_NOT_BETWEEN": 2,
    "TEXT_CONTAINS": 1, "TEXT_NOT_CONTAINS": 1, "TEXT_STARTS_WITH": 1, "TEXT_ENDS_WITH": 1, "TEXT_EQ": 1,
    "BLANK": 0, "NOT_BLANK": 0, "CUSTOM_FORMULA": 1,
}


def _col_to_index(letters):
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch.upper()) - ord("A") + 1)
    return idx - 1


def _index_to_col(idx):
    letters = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def plan_column_layout(values, min_width=48, max_width=320):
    """Pick a pixel width and wrap flag per column from formatted cell values.

    Width fits the longest line in the column ("auto-fit"), clamped to
    [min_width, max_width]; columns whose content exceeds the cap are flagged
    for wrapping instead of growing wider. Empty columns are skipped so their
    existing width is left untouched. Returns [{"index", "width", "wrap"}].
    """
    n_cols = max((len(r) for r in values), default=0)
    plans = []
    for col in range(n_cols):
        max_len = 0
        for row in values:
            cell = row[col] if col < len(row) else ""
            text = cell if isinstance(cell, str) else str(cell)
            for line in text.splitlines():
                max_len = max(max_len, len(line))
        if max_len == 0:
            continue
        needed = CELL_PADDING_PX + CHAR_PX * max_len
        plans.append({
            "index": col,
            "width": max(min_width, min(max_width, needed)),
            "wrap": needed > max_width,
        })
    return plans


class SheetsAPI:
    def __init__(self, account=None):
        self.account = account
        self.service = core.get_service("sheets", "v4", account=account)

    # --- values ---
    def read_range(self, spreadsheet_id, range, value_render_option="FORMATTED_VALUE"):
        return self.service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=range, valueRenderOption=value_render_option
        ).execute()

    def batch_read(self, spreadsheet_id, ranges, value_render_option="FORMATTED_VALUE"):
        return self.service.spreadsheets().values().batchGet(
            spreadsheetId=spreadsheet_id, ranges=ranges, valueRenderOption=value_render_option
        ).execute()

    def update_range(self, spreadsheet_id, range, values, value_input_option="USER_ENTERED"):
        return self.service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=range, valueInputOption=value_input_option,
            body={"values": values},
        ).execute()

    def batch_update_values(self, spreadsheet_id, data, value_input_option="USER_ENTERED"):
        # data: list of {"range": "A1:B2", "values": [[...],[...]]}
        return self.service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": value_input_option, "data": data},
        ).execute()

    def append_rows(self, spreadsheet_id, range, values, value_input_option="USER_ENTERED"):
        return self.service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id, range=range, valueInputOption=value_input_option,
            insertDataOption="INSERT_ROWS", body={"values": values},
        ).execute()

    def clear_range(self, spreadsheet_id, range):
        return self.service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=range, body={}
        ).execute()

    # --- spreadsheet / structure ---
    def create_spreadsheet(self, title, folder_id=None):
        """Create a spreadsheet; with ``folder_id``, move it there (trashed if the move fails)."""
        created = self.service.spreadsheets().create(
            body={"properties": {"title": title}}
        ).execute()
        if folder_id:
            drive = core.get_service("drive", "v3", account=self.account)
            place_in_folder(drive, created["spreadsheetId"], folder_id)
        return created

    def get_spreadsheet(self, spreadsheet_id, include_grid_data=False):
        return self.service.spreadsheets().get(
            spreadsheetId=spreadsheet_id, includeGridData=include_grid_data
        ).execute()

    def add_sheet(self, spreadsheet_id, title):
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
        ).execute()

    def rename_sheet(self, spreadsheet_id, sheet_id, new_title):
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"updateSheetProperties": {"properties": {"sheetId": sheet_id, "title": new_title}, "fields": "title"}}]},
        ).execute()

    def delete_sheet(self, spreadsheet_id, sheet_id):
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"deleteSheet": {"sheetId": sheet_id}}]},
        ).execute()

    # --- formatting / structure helpers ---
    def _hex_to_color(self, hex_str):
        h = hex_str.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) != 6:
            raise ValueError(f"invalid hex color: {hex_str!r}")
        return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}

    def _hex_to_color_style(self, hex_str):
        return {"rgbColor": self._hex_to_color(hex_str)}

    def _batch(self, spreadsheet_id, requests):
        if not requests:
            return {"replies": []}
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests}
        ).execute()

    def batch_update(self, spreadsheet_id, requests):
        """Pass raw batchUpdate request dicts through to the Sheets API."""
        if not isinstance(requests, list) or not requests:
            raise ValueError("requests must be a non-empty list")
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests}
        ).execute()

    def _cell_part_to_grid(self, sheet_id, cell_part):
        grid = {"sheetId": sheet_id}
        start, _, end = cell_part.partition(":")
        end = end or start

        def split_cell(cell):
            letters = "".join(c for c in cell if c.isalpha())
            digits = "".join(c for c in cell if c.isdigit())
            return letters, digits

        start_letters, start_digits = split_cell(start)
        end_letters, end_digits = split_cell(end)
        if start_letters:
            grid["startColumnIndex"] = _col_to_index(start_letters)
        if end_letters:
            grid["endColumnIndex"] = _col_to_index(end_letters) + 1
        if start_digits:
            grid["startRowIndex"] = int(start_digits) - 1
        if end_digits:
            grid["endRowIndex"] = int(end_digits)
        return grid

    def _resolve_a1(self, spreadsheet_id, a1, meta=None):
        sheet_name, _, cell_part = a1.rpartition("!")
        if meta is None:
            meta = self.get_spreadsheet(spreadsheet_id)
        sheets = meta.get("sheets", [])
        if sheet_name:
            sheet_name = sheet_name.strip("'\"")
            sheet_id = next(s["properties"]["sheetId"] for s in sheets if s["properties"]["title"] == sheet_name)
        else:
            sheet_id = sheets[0]["properties"]["sheetId"]
            sheet_name = sheets[0]["properties"]["title"]
        grid = self._cell_part_to_grid(sheet_id, cell_part)
        start, _, end = cell_part.partition(":")
        end = end or start
        start_row = int("".join(c for c in start if c.isdigit()) or "1")
        end_row = int("".join(c for c in end if c.isdigit()) or str(start_row))
        start_col = "".join(c for c in start if c.isalpha())
        end_col = "".join(c for c in end if c.isalpha())
        return sheet_name, sheet_id, grid, start_row, end_row, start_col, end_col

    def _quoted_sheet_range(self, sheet_name, cell_range):
        escaped = sheet_name.replace("'", "''")
        return f"'{escaped}'!{cell_range}"

    def _a1_to_grid_range(self, spreadsheet_id, a1, meta=None):
        _, _, grid, _, _, _, _ = self._resolve_a1(spreadsheet_id, a1, meta)
        return grid

    def _parse_a1_local(self, a1):
        """Parse an A1 string without any API call.
        Returns (sheet_name, start_col, start_row, end_col, end_row) as strings."""
        sheet_name, sep, cell_part = a1.rpartition("!")
        sheet_name = sheet_name.strip("'\"") if sep else ""
        start, _, end = cell_part.partition(":")
        end = end or start

        def split(cell):
            return ("".join(c for c in cell if c.isalpha()),
                    "".join(c for c in cell if c.isdigit()))

        start_col, start_row = split(start)
        end_col, end_row = split(end)
        return sheet_name, start_col, start_row, end_col, end_row

    def _anchor(self, sheet_name, col, row):
        cell = f"{col}{row}"
        return self._quoted_sheet_range(sheet_name, cell) if sheet_name else cell

    def _a1_to_grid_coordinate(self, spreadsheet_id, a1, meta=None):
        _, sheet_id, grid, _, _, _, _ = self._resolve_a1(spreadsheet_id, a1, meta)
        return {
            "sheetId": sheet_id,
            "rowIndex": grid.get("startRowIndex", 0),
            "columnIndex": grid.get("startColumnIndex", 0),
        }

    def _format_cells_request(self, grid, bold=None, italic=None, strikethrough=None, underline=None,
                              font_size=None, text_color=None, background_color=None, number_format=None,
                              horizontal_alignment=None, vertical_alignment=None, wrap=None):
        fmt = {}
        text_format = {}
        fields = []
        if bold is not None:
            text_format["bold"] = bold
            fields.append("userEnteredFormat.textFormat.bold")
        if italic is not None:
            text_format["italic"] = italic
            fields.append("userEnteredFormat.textFormat.italic")
        if strikethrough is not None:
            text_format["strikethrough"] = strikethrough
            fields.append("userEnteredFormat.textFormat.strikethrough")
        if underline is not None:
            text_format["underline"] = underline
            fields.append("userEnteredFormat.textFormat.underline")
        if font_size is not None:
            text_format["fontSize"] = font_size
            fields.append("userEnteredFormat.textFormat.fontSize")
        if text_color is not None:
            text_format["foregroundColorStyle"] = self._hex_to_color_style(text_color)
            fields.append("userEnteredFormat.textFormat.foregroundColorStyle")
        if text_format:
            fmt["textFormat"] = text_format
        if background_color is not None:
            fmt["backgroundColorStyle"] = self._hex_to_color_style(background_color)
            fields.append("userEnteredFormat.backgroundColorStyle")
        if number_format is not None:
            if number_format in {"NUMBER", "CURRENCY", "PERCENT", "DATE", "TIME", "DATE_TIME", "SCIENTIFIC"}:
                fmt["numberFormat"] = {"type": number_format}
            else:
                fmt["numberFormat"] = {"type": "NUMBER", "pattern": number_format}
            fields.append("userEnteredFormat.numberFormat")
        if horizontal_alignment is not None:
            fmt["horizontalAlignment"] = horizontal_alignment
            fields.append("userEnteredFormat.horizontalAlignment")
        if vertical_alignment is not None:
            fmt["verticalAlignment"] = vertical_alignment
            fields.append("userEnteredFormat.verticalAlignment")
        if wrap is not None:
            fmt["wrapStrategy"] = "WRAP" if wrap else "OVERFLOW_CELL"
            fields.append("userEnteredFormat.wrapStrategy")
        if not fields:
            raise ValueError("format_cells requires at least one formatting parameter")
        return {"repeatCell": {"range": grid, "cell": {"userEnteredFormat": fmt}, "fields": ",".join(fields)}}

    def format_cells(self, spreadsheet_id, range, bold=None, italic=None, strikethrough=None, underline=None,
                     font_size=None, text_color=None, background_color=None, number_format=None,
                     horizontal_alignment=None, vertical_alignment=None, wrap=None):
        grid = self._a1_to_grid_range(spreadsheet_id, range)
        req = self._format_cells_request(
            grid, bold, italic, strikethrough, underline, font_size, text_color, background_color,
            number_format, horizontal_alignment, vertical_alignment, wrap,
        )
        return self._batch(spreadsheet_id, [req])

    def _banding_properties(self, header_color=None, first_band_color=None, second_band_color=None, footer_color=None):
        props = {}
        if header_color is not None:
            props["headerColorStyle"] = self._hex_to_color_style(header_color)
        if first_band_color is not None:
            props["firstBandColorStyle"] = self._hex_to_color_style(first_band_color)
        if second_band_color is not None:
            props["secondBandColorStyle"] = self._hex_to_color_style(second_band_color)
        if footer_color is not None:
            props["footerColorStyle"] = self._hex_to_color_style(footer_color)
        if not props:
            raise ValueError("banding requires at least one color")
        return props

    def _banding_request(self, grid, header_color=None, first_band_color="#FFFFFF",
                         second_band_color="#F3F3F3", footer_color=None, band_rows=True):
        props = self._banding_properties(header_color, first_band_color, second_band_color, footer_color)
        banded = {"range": grid}
        if band_rows:
            banded["rowProperties"] = props
        else:
            banded["columnProperties"] = props
        return {"addBanding": {"bandedRange": banded}}

    def add_banding(self, spreadsheet_id, range, header_color=None, first_band_color="#FFFFFF",
                    second_band_color="#F3F3F3", footer_color=None, band_rows=True):
        grid = self._a1_to_grid_range(spreadsheet_id, range)
        return self._batch(spreadsheet_id, [self._banding_request(
            grid, header_color, first_band_color, second_band_color, footer_color, band_rows,
        )])

    def update_banding(self, spreadsheet_id, banded_range_id, header_color=None, first_band_color=None,
                       second_band_color=None, footer_color=None):
        props = self._banding_properties(header_color, first_band_color, second_band_color, footer_color)
        banded = {"bandedRangeId": banded_range_id, "rowProperties": props}
        fields = ",".join(f"rowProperties.{k}" for k in props)
        return self._batch(spreadsheet_id, [{"updateBanding": {"bandedRange": banded, "fields": fields}}])

    def delete_banding(self, spreadsheet_id, banded_range_id):
        return self._batch(spreadsheet_id, [{"deleteBanding": {"bandedRangeId": banded_range_id}}])

    def _build_filter_criteria(self, spec):
        criteria = {}
        if spec.get("hidden_values") is not None:
            criteria["hiddenValues"] = spec["hidden_values"]
        condition_type = spec.get("condition_type")
        if condition_type:
            values = spec.get("values", [])
            criteria["condition"] = {
                "type": condition_type,
                "values": [{"userEnteredValue": str(v)} for v in values],
            }
        return criteria

    def _build_filter_specs(self, filter_specs):
        built = []
        for spec in filter_specs:
            criteria = self._build_filter_criteria(spec)
            if not criteria:
                raise ValueError("each filter_spec needs hidden_values and/or condition_type + values")
            built.append({"columnIndex": spec["column"], "filterCriteria": criteria})
        return built

    def sort_range(self, spreadsheet_id, range, column, ascending=True):
        grid = self._a1_to_grid_range(spreadsheet_id, range)
        req = {"sortRange": {"range": grid, "sortSpecs": [{"dimensionIndex": column, "sortOrder": "ASCENDING" if ascending else "DESCENDING"}]}}
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": [req]}
        ).execute()

    def set_basic_filter(self, spreadsheet_id, range, filter_specs=None, sort_specs=None):
        grid = self._a1_to_grid_range(spreadsheet_id, range)
        filt = {"range": grid}
        if filter_specs:
            filt["filterSpecs"] = self._build_filter_specs(filter_specs)
        if sort_specs:
            filt["sortSpecs"] = [
                {
                    "dimensionIndex": s["column"],
                    "sortOrder": "ASCENDING" if s.get("ascending", True) else "DESCENDING",
                }
                for s in sort_specs
            ]
        return self._batch(spreadsheet_id, [{"setBasicFilter": {"filter": filt}}])

    def clear_basic_filter(self, spreadsheet_id, range):
        grid = self._a1_to_grid_range(spreadsheet_id, range)
        req = {"clearBasicFilter": {"sheetId": grid["sheetId"]}}
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": [req]}
        ).execute()

    def find_replace(self, spreadsheet_id, find, replacement, range=None, all_sheets=False,
                     match_case=False, match_entire_cell=False, search_by_regex=False,
                     include_formulas=False):
        fr = {
            "find": find,
            "replacement": replacement,
            "matchCase": match_case,
            "matchEntireCell": match_entire_cell,
            "searchByRegex": search_by_regex,
            "includeFormulas": include_formulas,
        }
        if all_sheets:
            fr["allSheets"] = True
        elif range:
            grid = self._grid_or_sheet(spreadsheet_id, range)
            if set(grid) == {"sheetId"}:
                fr["sheetId"] = grid["sheetId"]
            else:
                fr["range"] = grid
        else:
            raise ValueError("find_replace requires range or all_sheets=True")
        return self._batch(spreadsheet_id, [{"findReplace": fr}])

    def copy_paste(self, spreadsheet_id, source_range, destination_range,
                   paste_type="PASTE_NORMAL", transpose=False):
        meta = self.get_spreadsheet(spreadsheet_id)
        req = {"copyPaste": {
            "source": self._a1_to_grid_range(spreadsheet_id, source_range, meta),
            "destination": self._a1_to_grid_range(spreadsheet_id, destination_range, meta),
            "pasteType": paste_type,
            "pasteOrientation": "TRANSPOSE" if transpose else "NORMAL",
        }}
        return self._batch(spreadsheet_id, [req])

    def cut_paste(self, spreadsheet_id, source_range, destination, paste_type="PASTE_NORMAL"):
        meta = self.get_spreadsheet(spreadsheet_id)
        req = {"cutPaste": {
            "source": self._a1_to_grid_range(spreadsheet_id, source_range, meta),
            "destination": self._a1_to_grid_coordinate(spreadsheet_id, destination, meta),
            "pasteType": paste_type,
        }}
        return self._batch(spreadsheet_id, [req])

    def merge_cells(self, spreadsheet_id, range, merge_type="MERGE_ALL"):
        grid = self._a1_to_grid_range(spreadsheet_id, range)
        req = {"mergeCells": {"range": grid, "mergeType": merge_type}}
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": [req]}
        ).execute()

    def unmerge_cells(self, spreadsheet_id, range):
        grid = self._a1_to_grid_range(spreadsheet_id, range)
        req = {"unmergeCells": {"range": grid}}
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": [req]}
        ).execute()

    # --- dimensions (columns / rows) ---
    def _dimension_range(self, spreadsheet_id, range, dimension):
        grid = self._a1_to_grid_range(spreadsheet_id, range)
        if dimension == "COLUMNS":
            start, end = grid.get("startColumnIndex"), grid.get("endColumnIndex")
            hint = "column letters (e.g. 'Sheet1!A:C')"
        else:
            start, end = grid.get("startRowIndex"), grid.get("endRowIndex")
            hint = "row numbers (e.g. 'Sheet1!2:5')"
        if start is None or end is None:
            raise ValueError(f"range must include {hint}")
        return {"sheetId": grid["sheetId"], "dimension": dimension, "startIndex": start, "endIndex": end}

    def resize_dimension(self, spreadsheet_id, range, dimension, pixel_size=None):
        dim = self._dimension_range(spreadsheet_id, range, dimension)
        if pixel_size is None:
            req = {"autoResizeDimensions": {"dimensions": dim}}
        else:
            req = {"updateDimensionProperties": {"range": dim, "properties": {"pixelSize": pixel_size}, "fields": "pixelSize"}}
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": [req]}
        ).execute()

    def insert_dimension(self, spreadsheet_id, range, dimension, inherit_from_before=False):
        dim = self._dimension_range(spreadsheet_id, range, dimension)
        req = {"insertDimension": {"range": dim, "inheritFromBefore": inherit_from_before}}
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": [req]}
        ).execute()

    def delete_dimension(self, spreadsheet_id, range, dimension):
        dim = self._dimension_range(spreadsheet_id, range, dimension)
        req = {"deleteDimension": {"range": dim}}
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": [req]}
        ).execute()

    def insert_rows(self, spreadsheet_id, range, inherit_from_before=False):
        return self.insert_dimension(spreadsheet_id, range, "ROWS", inherit_from_before)

    def insert_columns(self, spreadsheet_id, range, inherit_from_before=False):
        return self.insert_dimension(spreadsheet_id, range, "COLUMNS", inherit_from_before)

    def delete_rows(self, spreadsheet_id, range):
        return self.delete_dimension(spreadsheet_id, range, "ROWS")

    def delete_columns(self, spreadsheet_id, range):
        return self.delete_dimension(spreadsheet_id, range, "COLUMNS")

    def set_dimension_visibility(self, spreadsheet_id, range, dimension, hidden):
        dim = self._dimension_range(spreadsheet_id, range, dimension)
        req = {"updateDimensionProperties": {"range": dim, "properties": {"hiddenByUser": hidden}, "fields": "hiddenByUser"}}
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": [req]}
        ).execute()

    # --- sheet-level helpers ---
    def freeze_panes(self, spreadsheet_id, range, rows=None, cols=None):
        grid = self._a1_to_grid_range(spreadsheet_id, range)
        props = {"sheetId": grid["sheetId"], "gridProperties": {}}
        fields = []
        if rows is not None:
            props["gridProperties"]["frozenRowCount"] = rows
            fields.append("gridProperties.frozenRowCount")
        if cols is not None:
            props["gridProperties"]["frozenColumnCount"] = cols
            fields.append("gridProperties.frozenColumnCount")
        if not fields:
            raise ValueError("freeze_panes requires rows and/or cols")
        req = {"updateSheetProperties": {"properties": props, "fields": ",".join(fields)}}
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": [req]}
        ).execute()

    def duplicate_sheet(self, spreadsheet_id, sheet_id, new_title=None):
        body = {"sourceSheetId": sheet_id}
        if new_title:
            body["newSheetName"] = new_title
        return self._batch(spreadsheet_id, [{"duplicateSheet": body}])

    def add_table(self, spreadsheet_id, range, name, header_color="#355468", first_band_color="#FFFFFF",
                  second_band_color="#F3F3F3", column_names=None):
        grid = self._a1_to_grid_range(spreadsheet_id, range)
        table = {
            "range": grid,
            "rowsProperties": {
                "headerColorStyle": self._hex_to_color_style(header_color),
                "firstBandColorStyle": self._hex_to_color_style(first_band_color),
                "secondBandColorStyle": self._hex_to_color_style(second_band_color),
            },
        }
        if name:
            table["name"] = name
        if column_names:
            table["columnProperties"] = [
                {"columnIndex": i, "columnName": col_name} for i, col_name in enumerate(column_names)
            ]
        return self._batch(spreadsheet_id, [{"addTable": {"table": table}}])

    def delete_table(self, spreadsheet_id, table_id):
        return self._batch(spreadsheet_id, [{"deleteTable": {"tableId": table_id}}])

    def _build_table_columns(self, specs):
        cols = []
        for s in specs:
            col = {"columnIndex": s["column_index"]}
            if s.get("column_name") is not None:
                col["columnName"] = s["column_name"]
            if s.get("column_type") is not None:
                col["columnType"] = s["column_type"]
            if s.get("values"):
                col["dataValidationRule"] = {"condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": str(v)} for v in s["values"]],
                }}
            cols.append(col)
        return cols

    def update_table(self, spreadsheet_id, table_id, name=None, range=None,
                     header_color=None, first_band_color=None, second_band_color=None,
                     footer_color=None, column_properties=None):
        table = {"tableId": table_id}
        fields = []
        if name is not None:
            table["name"] = name
            fields.append("name")
        if range is not None:
            table["range"] = self._a1_to_grid_range(spreadsheet_id, range)
            fields.append("range")
        if any(c is not None for c in (header_color, first_band_color, second_band_color, footer_color)):
            table["rowsProperties"] = self._banding_properties(
                header_color, first_band_color, second_band_color, footer_color,
            )
            fields.append("rowsProperties")
        if column_properties is not None:
            table["columnProperties"] = self._build_table_columns(column_properties)
            fields.append("columnProperties")
        if not fields:
            raise ValueError("update_table requires at least one field to update")
        return self._batch(spreadsheet_id, [{"updateTable": {"table": table, "fields": ",".join(fields)}}])

    def format_table(self, spreadsheet_id, range, header_color="#355468", header_text_color="#FFFFFF",
                     first_band_color="#FFFFFF", second_band_color="#F3F3F3", wrap=True, auto_resize_columns=True,
                     add_filter=True, add_borders=True, freeze_header=False, max_column_width=320):
        """Apply common table styling in a single batchUpdate: header, bands, wrap, filter, borders, sizing."""
        meta = self.get_spreadsheet(spreadsheet_id)
        sheet_name, sheet_id, grid, start_row, end_row, start_col, end_col = self._resolve_a1(
            spreadsheet_id, range, meta,
        )
        header_grid = {**grid, "startRowIndex": start_row - 1, "endRowIndex": start_row}
        data_start = start_row + 1
        has_data_rows = data_start <= end_row
        data_grid = (
            {**grid, "startRowIndex": start_row, "endRowIndex": end_row}
            if has_data_rows else None
        )

        requests = [
            self._format_cells_request(
                header_grid, bold=True, background_color=header_color,
                text_color=header_text_color, wrap=wrap, horizontal_alignment="CENTER",
            ),
        ]
        if has_data_rows:
            requests.append(self._banding_request(
                grid, header_color=header_color,
                first_band_color=first_band_color, second_band_color=second_band_color,
            ))
            if wrap and not auto_resize_columns:
                requests.append(self._format_cells_request(data_grid, wrap=True))
        if add_filter:
            requests.append({"setBasicFilter": {"filter": {"range": grid}}})
        if add_borders:
            border = {"style": "SOLID", "color": self._hex_to_color("#CCCCCC")}
            requests.append({"updateBorders": {
                "range": grid,
                "top": border, "bottom": border, "left": border, "right": border,
                "innerHorizontal": border, "innerVertical": border,
            }})
        if auto_resize_columns and grid.get("startColumnIndex") is not None:
            values = self.read_range(spreadsheet_id, range, "FORMATTED_VALUE").get("values") or []
            _, layout = self._layout_requests(
                sheet_id, values, grid["startColumnIndex"],
                start_row - 1, end_row, max_width=max_column_width,
            )
            requests.extend(layout)
        if freeze_header:
            requests.append({"updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": start_row}},
                "fields": "gridProperties.frozenRowCount",
            }})
        result = self._batch(spreadsheet_id, requests)
        return {
            "sheet": sheet_name,
            "range": self._quoted_sheet_range(sheet_name, f"{start_col}{start_row}:{end_col}{end_row}"),
            "requests_sent": len(requests),
            "result": result,
        }

    def _layout_requests(self, sheet_id, values, col_offset, row_start, row_end,
                         min_width=48, max_width=320, resize_rows=True):
        """Turn plan_column_layout output into batchUpdate requests.

        Per column: set pixel width; set wrapStrategy WRAP + TOP alignment when
        content exceeds the width cap, else OVERFLOW_CELL (normalizes previous
        blanket wrapping). Optionally auto-fit row heights afterwards.
        """
        plans = plan_column_layout(values, min_width, max_width)
        requests = []
        for p in plans:
            col = col_offset + p["index"]
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": col, "endIndex": col + 1},
                "properties": {"pixelSize": p["width"]},
                "fields": "pixelSize",
            }})
            cell_range = {"sheetId": sheet_id, "startRowIndex": row_start, "endRowIndex": row_end,
                          "startColumnIndex": col, "endColumnIndex": col + 1}
            if p["wrap"]:
                requests.append({"repeatCell": {
                    "range": cell_range,
                    "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
                    "fields": "userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment",
                }})
            else:
                requests.append({"repeatCell": {
                    "range": cell_range,
                    "cell": {"userEnteredFormat": {"wrapStrategy": "OVERFLOW_CELL"}},
                    "fields": "userEnteredFormat.wrapStrategy",
                }})
        if resize_rows and values:
            requests.append({"autoResizeDimensions": {"dimensions": {
                "sheetId": sheet_id, "dimension": "ROWS",
                "startIndex": row_start, "endIndex": row_end,
            }}})
        return plans, requests

    def optimize_layout(self, spreadsheet_id, range, max_column_width=320,
                        min_column_width=48, resize_rows=True):
        """Size columns to content (capped), wrap only over-cap columns, auto-fit rows."""
        meta = self.get_spreadsheet(spreadsheet_id)
        if "!" in range:
            sheet_name, sheet_id, grid, *_ = self._resolve_a1(spreadsheet_id, range, meta)
            read_a1 = range
            col_offset = grid.get("startColumnIndex", 0)
            row_start = grid.get("startRowIndex", 0)
            row_end_hint = grid.get("endRowIndex")
        else:
            sheet_name = range.strip("'\"")
            matches = [s["properties"]["sheetId"] for s in meta.get("sheets", [])
                       if s["properties"]["title"] == sheet_name]
            if not matches:
                raise ValueError(f"no sheet named {sheet_name!r}")
            sheet_id = matches[0]
            read_a1 = "'" + sheet_name.replace("'", "''") + "'"
            col_offset = 0
            row_start = 0
            row_end_hint = None
        values = self.read_range(spreadsheet_id, read_a1, "FORMATTED_VALUE").get("values") or []
        row_end = row_end_hint or (row_start + len(values))
        plans, requests = self._layout_requests(
            sheet_id, values, col_offset, row_start, row_end,
            min_column_width, max_column_width, resize_rows,
        )
        if not requests:
            return {"sheet": sheet_name, "columns": [], "requests_sent": 0}
        result = self._batch(spreadsheet_id, requests)
        return {
            "sheet": sheet_name,
            "columns": [
                {"column": _index_to_col(col_offset + p["index"]),
                 "width": p["width"], "wrap": p["wrap"]}
                for p in plans
            ],
            "requests_sent": len(requests),
            "result": result,
        }

    def write_table(self, spreadsheet_id, anchor, values, style="native", name=None,
                    header_color="#355468", first_band_color="#FFFFFF",
                    second_band_color="#F3F3F3", max_column_width=320):
        """Write values (first row = header) at an anchor cell, style them as a
        table, and optimize the layout — one call from data to readable sheet."""
        if not values or not any(row for row in values):
            raise ValueError("write_table requires a non-empty 2D values array (first row = header)")
        sheet_name, start_col, start_row, end_col, end_row = self._parse_a1_local(anchor)
        if not (start_col and start_row and start_col == end_col and start_row == end_row):
            raise ValueError(f"anchor must be a single cell like 'Sheet1!A1', got {anchor!r}")
        n_rows = len(values)
        n_cols = max(len(r) for r in values)
        last_col = _index_to_col(_col_to_index(start_col) + n_cols - 1)
        last_row = int(start_row) + n_rows - 1
        cell_range = f"{start_col}{start_row}:{last_col}{last_row}"
        full_range = self._quoted_sheet_range(sheet_name, cell_range) if sheet_name else cell_range
        self.update_range(spreadsheet_id, full_range, values)
        table_id = None
        if style == "native":
            reply = self.add_table(spreadsheet_id, full_range, name,
                                   header_color, first_band_color, second_band_color)
            replies = reply.get("replies") or []
            if replies and replies[0].get("addTable"):
                table_id = replies[0]["addTable"].get("table", {}).get("tableId")
        elif style == "banded":
            self.format_table(spreadsheet_id, full_range, header_color=header_color,
                              first_band_color=first_band_color, second_band_color=second_band_color,
                              auto_resize_columns=False)
        elif style != "plain":
            raise ValueError("style must be one of native/banded/plain")
        layout = self.optimize_layout(spreadsheet_id, full_range, max_column_width=max_column_width)
        return {"range": full_range, "rows": n_rows, "columns": n_cols,
                "style": style, "tableId": table_id, "layout": layout}

    def write_formulas(self, spreadsheet_id, range, formulas, value_input_option="USER_ENTERED"):
        """Write formula strings (e.g. '=SUM(A2:A10)') to a range. Same as update_range with USER_ENTERED."""
        return self.update_range(spreadsheet_id, range, formulas, value_input_option)

    def read_formulas(self, spreadsheet_id, range):
        return self.read_range(spreadsheet_id, range, value_render_option="FORMULA")

    # --- text editing ---
    def _read_text_grid(self, spreadsheet_id, range):
        resp = self.read_range(spreadsheet_id, range, value_render_option="FORMULA")
        return resp.get("values") or []

    def _assert_single_cell(self, cell):
        name, sc, sr, ec, er = self._parse_a1_local(cell)
        if not (sc and sr and sc == ec and sr == er):
            raise ValueError(f"expected a single cell like 'Sheet1!B2', got {cell!r}")

    def _drive_comments(self):
        inst = getattr(self, "_drive_comments_api", None)
        if inst is None:
            inst = DriveAPI(self.account)
            self._drive_comments_api = inst
        return inst

    def list_comments(self, spreadsheet_id, include_deleted=False, page_size=20, page_token=None):
        return self._drive_comments().list_comments(
            spreadsheet_id, include_deleted, page_size, page_token,
        )

    def add_comment(self, spreadsheet_id, content, cell=None):
        anchor = None
        quoted_text = None
        if cell:
            self._assert_single_cell(cell)
            anchor = {"sheetsCell": cell}
            resp = self.read_range(spreadsheet_id, cell)
            values = resp.get("values") or []
            raw = values[0][0] if (values and values[0]) else ""
            text = "" if raw is None else str(raw)
            if text:
                quoted_text = text
        return self._drive_comments().create_comment(
            spreadsheet_id, content, anchor, quoted_text=quoted_text,
        )

    def reply_to_comment(self, spreadsheet_id, comment_id, content=None, action=None):
        return self._drive_comments().reply_to_comment(
            spreadsheet_id, comment_id, content, action,
        )

    def delete_comment(self, spreadsheet_id, comment_id):
        return self._drive_comments().delete_comment(spreadsheet_id, comment_id)

    def edit_cell(self, spreadsheet_id, cell, operation, find=None, replacement=None,
                  position=None, length=None, text=None, count=None):
        self._assert_single_cell(cell)
        resp = self.read_range(spreadsheet_id, cell, value_render_option="FORMULA")
        values = resp.get("values") or []
        raw = values[0][0] if (values and values[0]) else ""
        old = "" if raw is None else str(raw)

        if operation == "replace":
            if find is None:
                raise ValueError("edit_cell 'replace' requires 'find'")
            new = old.replace(find, replacement or "", -1 if count is None else count)
        elif operation == "insert":
            if position is None or text is None:
                raise ValueError("edit_cell 'insert' requires 'position' and 'text'")
            pos = max(0, min(position, len(old)))
            new = old[:pos] + text + old[pos:]
        elif operation == "delete":
            if position is None or length is None:
                raise ValueError("edit_cell 'delete' requires 'position' and 'length'")
            pos = max(0, min(position, len(old)))
            new = old[:pos] + old[pos + max(0, length):]
        elif operation == "append":
            if text is None:
                raise ValueError("edit_cell 'append' requires 'text'")
            new = old + text
        elif operation == "prepend":
            if text is None:
                raise ValueError("edit_cell 'prepend' requires 'text'")
            new = text + old
        elif operation == "newline":
            new = old + "\n" + (text or "")
        else:
            raise ValueError(
                "edit_cell operation must be one of "
                "replace/insert/delete/append/prepend/newline"
            )

        changed = new != old
        if changed:
            self.update_range(spreadsheet_id, cell, [[new]], "USER_ENTERED")
        return {"cell": cell, "old": old, "new": new, "changed": changed}

    def transform_text(self, spreadsheet_id, range, transform):
        funcs = {
            "upper": str.upper,
            "lower": str.lower,
            "title": str.title,
            "capitalize": str.capitalize,
            "trim": str.strip,
            "collapse_spaces": lambda s: re.sub(r"\s+", " ", s).strip(),
        }
        if transform not in funcs:
            raise ValueError(f"transform must be one of {sorted(funcs)}")
        fn = funcs[transform]
        grid = self._read_text_grid(spreadsheet_id, range)
        out = [
            [fn(cell) if (isinstance(cell, str) and not cell.startswith("=")) else cell for cell in row]
            for row in grid
        ]
        if not out:
            return {"updatedCells": 0}
        return self.update_range(spreadsheet_id, range, out, "USER_ENTERED")

    def regex_replace(self, spreadsheet_id, range, pattern, replacement, count=0, ignore_case=False):
        try:
            rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}")
        grid = self._read_text_grid(spreadsheet_id, range)
        out = [
            [rx.sub(replacement, cell, count=count) if (isinstance(cell, str) and not cell.startswith("=")) else cell
             for cell in row]
            for row in grid
        ]
        if not out:
            return {"updatedCells": 0}
        return self.update_range(spreadsheet_id, range, out, "USER_ENTERED")

    def _assert_single_column(self, range, op):
        name, sc, sr, ec, er = self._parse_a1_local(range)
        if not (sc and sc == ec):
            raise ValueError(f"{op} expects a single column like 'Sheet1!A1:A20', got {range!r}")
        return name, sc, sr

    def split_column(self, spreadsheet_id, range, delimiter, max_splits=-1):
        sheet_name, start_col, start_row = self._assert_single_column(range, "split_column")
        grid = self._read_text_grid(spreadsheet_id, range)
        rows = []
        width = 1
        for row in grid:
            cell = row[0] if row else ""
            s = cell if isinstance(cell, str) else str(cell)
            parts = s.split(delimiter, max_splits) if s != "" else [""]
            width = max(width, len(parts))
            rows.append(parts)
        out = [r + [""] * (width - len(r)) for r in rows]
        if not out:
            return {"updatedCells": 0}
        anchor = self._anchor(sheet_name, start_col, start_row)
        return self.update_range(spreadsheet_id, anchor, out, "USER_ENTERED")

    def join_columns(self, spreadsheet_id, range, separator=" ", target_range=None):
        sheet_name, start_col, start_row, _, _ = self._parse_a1_local(range)
        grid = self._read_text_grid(spreadsheet_id, range)
        out = []
        for row in grid:
            parts = [(c if isinstance(c, str) else str(c)) for c in row]
            parts = [p for p in parts if p != ""]
            out.append([separator.join(parts)])
        if not out:
            return {"updatedCells": 0}
        target = target_range or self._anchor(sheet_name, start_col, start_row)
        return self.update_range(spreadsheet_id, target, out, "USER_ENTERED")

    def regex_extract(self, spreadsheet_id, range, pattern, group=0, target_range=None):
        sheet_name, start_col, start_row = self._assert_single_column(range, "regex_extract")
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}")
        grid = self._read_text_grid(spreadsheet_id, range)
        out = []
        for row in grid:
            cell = row[0] if row else ""
            s = cell if isinstance(cell, str) else str(cell)
            m = rx.search(s)
            if m:
                try:
                    out.append([m.group(group)])
                except IndexError:
                    raise ValueError(f"invalid capture group {group}")
            else:
                out.append([""])
        if not out:
            return {"updatedCells": 0}
        target = target_range or self._anchor(sheet_name, start_col, start_row)
        return self.update_range(spreadsheet_id, target, out, "USER_ENTERED")

    def add_chart(self, spreadsheet_id, range, chart_type="COLUMN", title=None, stacked=False):
        chart_type = (chart_type or "COLUMN").upper()
        if chart_type not in _CHART_TYPES:
            raise ValueError(f"chart_type must be one of {sorted(_CHART_TYPES)}")
        grid = self._a1_to_grid_range(spreadsheet_id, range)
        start_col = grid["startColumnIndex"]
        end_col = grid["endColumnIndex"]
        domain_grid = {**grid, "startColumnIndex": start_col, "endColumnIndex": start_col + 1}
        series_grids = []
        col = start_col + 1
        while col < end_col:
            series_grids.append({**grid, "startColumnIndex": col, "endColumnIndex": col + 1})
            col += 1
        if not series_grids:
            series_grids = [domain_grid]
        basic_chart = {
            "chartType": chart_type,
            "legendPosition": "RIGHT_LEGEND" if chart_type == "PIE" else "BOTTOM_LEGEND",
            "headerCount": 1,
            "domains": [{"domain": {"sourceRange": {"sources": [domain_grid]}}}],
            "series": [
                {"series": {"sourceRange": {"sources": [series_grid]}}}
                for series_grid in series_grids
            ],
        }
        if stacked and chart_type in _STACKED_CHART_TYPES:
            basic_chart["stackedType"] = "STACKED"
        if chart_type == "PIE":
            spec = {"pieChart": {
                "legendPosition": "RIGHT_LEGEND",
                "domain": {"sourceRange": {"sources": [domain_grid]}},
                "series": {"sourceRange": {"sources": [series_grids[0]]}},
            }}
        else:
            spec = {"basicChart": basic_chart}
        if title:
            spec["title"] = title
        result = self._batch(spreadsheet_id, [{"addChart": {"chart": {
            "spec": spec,
            "position": {"overlayPosition": {"anchorCell": {
                "sheetId": grid["sheetId"],
                "rowIndex": grid.get("startRowIndex", 0),
                "columnIndex": end_col,
            }}},
        }}}])
        replies = result.get("replies") or []
        chart_id = None
        if replies:
            chart_id = replies[0].get("addChart", {}).get("chart", {}).get("chartId")
        return {"chartId": chart_id, "replies": replies}

    # --- chart export (for Docs insert_sheets_chart) ---

    @staticmethod
    def get_chart_image_url(spreadsheet_id, chart_id):
        """Return the authenticated chart export URL (PNG). Requires sign-in when fetched."""
        return (
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
            f"/chart?oid={chart_id}&format=image"
        )

    def fetch_chart_image_bytes(self, spreadsheet_id, chart_id):
        """Download chart PNG bytes using the authenticated account."""
        url = self.get_chart_image_url(spreadsheet_id, chart_id)
        creds, _ = core.get_credentials(self.account, service_name="sheets")
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
        import urllib.request

        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {creds.token}"})
        with urllib.request.urlopen(req, timeout=CHART_IMAGE_TIMEOUT_SECONDS) as resp:
            data = resp.read()
        if not data:
            raise ValueError(f"empty chart image response for chart_id={chart_id!r}")
        return data

    def set_borders(self, spreadsheet_id, range, style="SOLID", color="#000000",
                    top=True, bottom=True, left=True, right=True, inner=False):
        grid = self._a1_to_grid_range(spreadsheet_id, range)
        border = {"style": style, "color": self._hex_to_color(color)}
        body = {"range": grid}
        if top:
            body["top"] = border
        if bottom:
            body["bottom"] = border
        if left:
            body["left"] = border
        if right:
            body["right"] = border
        if inner:
            body["innerHorizontal"] = border
            body["innerVertical"] = border
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": [{"updateBorders": body}]}
        ).execute()

    def set_data_validation(self, spreadsheet_id, range, allowed_values=None, show_dropdown=True):
        grid = self._a1_to_grid_range(spreadsheet_id, range)
        if allowed_values:
            rule = {
                "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": str(v)} for v in allowed_values]},
                "showCustomUi": show_dropdown,
                "strict": True,
            }
            req = {"setDataValidation": {"range": grid, "rule": rule}}
        else:
            req = {"setDataValidation": {"range": grid}}
        return self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": [req]}
        ).execute()

    # --- conditional formats, protected ranges, named ranges ---
    def _get_fields(self, spreadsheet_id, fields):
        return self.service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields=fields).execute()

    def _grid_or_sheet(self, spreadsheet_id, a1, meta=None):
        """GridRange for an A1 range, or {"sheetId": id} (the whole sheet) for a bare sheet title."""
        if meta is None:
            meta = self.get_spreadsheet(spreadsheet_id)
        titles = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}
        if "!" not in a1:
            title = a1.strip("'\"").replace("''", "'")
            if title in titles:
                return {"sheetId": titles[title]}
            # Without this, a typo like "Sheet9" parses as column SHEET row 9 on the first sheet.
            if not _BARE_CELLS_RE.match(a1):
                raise ValueError(f"unknown sheet {a1!r}; sheets are {sorted(titles)}")
        return self._a1_to_grid_range(spreadsheet_id, a1, meta)

    def _grid_to_a1(self, sheet_title, grid):
        sc, ec = grid.get("startColumnIndex"), grid.get("endColumnIndex")
        sr, er = grid.get("startRowIndex"), grid.get("endRowIndex")
        has_cols = sc is not None or ec is not None
        has_rows = sr is not None or er is not None
        if not has_cols and not has_rows:
            return self._quoted_sheet_range(sheet_title, "")[:-1]
        start = (_index_to_col(sc or 0) if has_cols else "") + (str((sr or 0) + 1) if has_rows else "")
        end = (_index_to_col(ec - 1) if ec is not None else "") + (str(er) if er is not None else "")
        single_cell = has_cols and has_rows and start == end
        return self._quoted_sheet_range(sheet_title, start if single_cell else f"{start}:{end}")

    def add_conditional_format(self, spreadsheet_id, range, condition, values=None, background_color=None,
                               text_color=None, bold=None, italic=None, min_color="#FFFFFF", mid_color=None,
                               max_color="#57BB8A", index=0):
        condition = (condition or "").upper()
        if condition != "COLOR_SCALE" and condition not in _CF_CONDITIONS:
            raise ValueError(f"condition must be COLOR_SCALE or one of {sorted(_CF_CONDITIONS)}")
        values = [str(v) for v in (values or [])]
        if condition == "COLOR_SCALE":
            gradient = {
                "minpoint": {"type": "MIN", "colorStyle": self._hex_to_color_style(min_color)},
                "maxpoint": {"type": "MAX", "colorStyle": self._hex_to_color_style(max_color)},
            }
            if mid_color:
                gradient["midpoint"] = {
                    "type": "PERCENTILE", "value": "50", "colorStyle": self._hex_to_color_style(mid_color),
                }
            rule = {"gradientRule": gradient}
        else:
            expected = _CF_CONDITIONS[condition]
            if len(values) != expected:
                raise ValueError(f"{condition} takes {expected} value(s), got {len(values)}")
            fmt = {}
            text_format = {}
            if bold is not None:
                text_format["bold"] = bold
            if italic is not None:
                text_format["italic"] = italic
            if text_color is not None:
                text_format["foregroundColorStyle"] = self._hex_to_color_style(text_color)
            if text_format:
                fmt["textFormat"] = text_format
            if background_color is not None:
                fmt["backgroundColorStyle"] = self._hex_to_color_style(background_color)
            if not fmt:
                raise ValueError("conditional format needs background_color, text_color, bold, or italic")
            cond = {"type": condition}
            if values:
                cond["values"] = [{"userEnteredValue": v} for v in values]
            rule = {"booleanRule": {"condition": cond, "format": fmt}}
        sheet_name, _, grid, _, _, _, _ = self._resolve_a1(spreadsheet_id, range)
        rule["ranges"] = [grid]
        result = self._batch(spreadsheet_id, [{"addConditionalFormatRule": {"rule": rule, "index": index}}])
        return {"sheet": sheet_name, "index": index, "replies": result.get("replies", [])}

    def list_conditional_formats(self, spreadsheet_id, sheet=None):
        meta = self._get_fields(spreadsheet_id, "sheets(properties(sheetId,title),conditionalFormats)")
        out = []
        for s in meta.get("sheets", []):
            title = s["properties"]["title"]
            if sheet and title != sheet:
                continue
            for i, rule in enumerate(s.get("conditionalFormats", [])):
                entry = {"sheet": title, "index": i,
                         "ranges": [self._grid_to_a1(title, g) for g in rule.get("ranges", [])]}
                entry.update({k: v for k, v in rule.items() if k != "ranges"})
                out.append(entry)
        return {"conditionalFormats": out}

    def delete_conditional_format(self, spreadsheet_id, sheet, index):
        grid = self._grid_or_sheet(spreadsheet_id, sheet)
        if set(grid) != {"sheetId"}:
            raise ValueError(f"sheet must be a sheet title, got {sheet!r}")
        return self._batch(spreadsheet_id, [{"deleteConditionalFormatRule": {
            "sheetId": grid["sheetId"], "index": index,
        }}])

    def protect_range(self, spreadsheet_id, range, description=None, warning_only=False, editors=None):
        # The API rejects editors on a warning-only range; fail before the round trip.
        if warning_only and editors:
            raise ValueError("editors cannot be set when warning_only=True")
        protected = {"range": self._grid_or_sheet(spreadsheet_id, range), "warningOnly": warning_only}
        if description:
            protected["description"] = description
        if editors:
            protected["editors"] = {"users": list(editors)}
        result = self._batch(spreadsheet_id, [{"addProtectedRange": {"protectedRange": protected}}])
        replies = result.get("replies") or [{}]
        pr = replies[0].get("addProtectedRange", {}).get("protectedRange", {})
        return {"protectedRangeId": pr.get("protectedRangeId"), "replies": replies}

    def list_protected_ranges(self, spreadsheet_id):
        meta = self._get_fields(spreadsheet_id, "sheets(properties(sheetId,title),protectedRanges)")
        out = []
        for s in meta.get("sheets", []):
            title = s["properties"]["title"]
            for pr in s.get("protectedRanges", []):
                out.append({
                    "protectedRangeId": pr.get("protectedRangeId"),
                    "range": self._grid_to_a1(title, pr.get("range", {})),
                    "description": pr.get("description"),
                    "warningOnly": pr.get("warningOnly", False),
                    "editors": pr.get("editors", {}),
                    "requestingUserCanEdit": pr.get("requestingUserCanEdit"),
                })
        return {"protectedRanges": out}

    def unprotect_range(self, spreadsheet_id, protected_range_id):
        return self._batch(spreadsheet_id, [{"deleteProtectedRange": {"protectedRangeId": protected_range_id}}])

    def add_named_range(self, spreadsheet_id, name, range):
        grid = self._grid_or_sheet(spreadsheet_id, range)
        result = self._batch(spreadsheet_id, [{"addNamedRange": {"namedRange": {"name": name, "range": grid}}}])
        replies = result.get("replies") or [{}]
        nr = replies[0].get("addNamedRange", {}).get("namedRange", {})
        return {"namedRangeId": nr.get("namedRangeId"), "name": name, "replies": replies}

    def list_named_ranges(self, spreadsheet_id):
        meta = self._get_fields(spreadsheet_id, "namedRanges,sheets(properties(sheetId,title))")
        # The API omits sheetId when it is 0 (the default first sheet).
        titles = {s["properties"].get("sheetId", 0): s["properties"]["title"] for s in meta.get("sheets", [])}
        out = []
        for nr in meta.get("namedRanges", []):
            grid = nr.get("range", {})
            title = titles.get(grid.get("sheetId", 0), "")
            out.append({"namedRangeId": nr.get("namedRangeId"), "name": nr.get("name"),
                        "range": self._grid_to_a1(title, grid)})
        return {"namedRanges": out}

    def update_named_range(self, spreadsheet_id, named_range_id, name=None, range=None):
        named = {"namedRangeId": named_range_id}
        fields = []
        if name:
            named["name"] = name
            fields.append("name")
        if range:
            named["range"] = self._grid_or_sheet(spreadsheet_id, range)
            fields.append("range")
        if not fields:
            raise ValueError("update_named_range requires name or range")
        return self._batch(spreadsheet_id, [{"updateNamedRange": {
            "namedRange": named, "fields": ",".join(fields),
        }}])

    def delete_named_range(self, spreadsheet_id, named_range_id):
        return self._batch(spreadsheet_id, [{"deleteNamedRange": {"namedRangeId": named_range_id}}])
