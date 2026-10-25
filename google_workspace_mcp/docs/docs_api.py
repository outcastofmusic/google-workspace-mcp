"""Google Docs API wrapper (create, read, edit, format, layout, headers/footers)."""
from __future__ import annotations

import io
import re
from urllib.parse import quote as url_quote

import google_auth_core as core
from googleapiclient.http import MediaIoBaseUpload

from ..drive.drive_api import DriveAPI, place_in_folder

PAGE_PRESETS = {
    "LETTER": (612, 792),
    "A4": (595.28, 841.89),
    "LEGAL": (612, 1008),
    "TABLOID": (792, 1224),
}

NUMBERED_BULLET_PRESETS = {
    "NUMBERED_DECIMAL_ALPHA_ROMAN",
    "NUMBERED_DECIMAL_ALPHA_ROMAN_PARENS",
    "NUMBERED_DECIMAL_NESTED",
    "NUMBERED_UPPERALPHA_ALPHA_ROMAN",
    "NUMBERED_UPPERROMAN_UPPERALPHA_DECIMAL",
    "NUMBERED_ZERODECIMAL_ALPHA_ROMAN",
}

DOCUMENT_MIME = "application/vnd.google-apps.document"

# Stands in for each index an inline image, footnote reference, or other
# non-text element occupies, so indices stay right and searches can split on it.
OBJECT_PLACEHOLDER = "\ufffc"

DEFAULT_MAX_MATCHES = 200

# One atomic batchUpdate per format_matches call; a loose regex on a long doc
# could otherwise send tens of thousands of requests.
DEFAULT_MAX_FORMAT_MATCHES = 500

# Matches are searched within one paragraph and between non-text elements only.
_SEARCHABLE_CHUNK = re.compile(f"[^\n{OBJECT_PLACEHOLDER}]+")


def _is_revision_mismatch(exc):
    """True when Docs rejected a write because writeControl.requiredRevisionId is stale."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        status = int(status)
    except (TypeError, ValueError):
        return False
    # Real wording (checked live): "The required revision ID '...' does not match the latest revision."
    return status == 400 and "required revision" in core.map_exception(exc).args[0].lower()


def _stale_revision_error(document_id, revision_id):
    return core.InvalidArgumentError(
        f"Document {document_id} changed after revision {revision_id} was read, so "
        "nothing was written (indices from that read may now point at the wrong text). "
        "Re-read it with find_text or get_content_map and retry with the new revisionId."
    )


class DocsAPI:
    def __init__(self, account=None):
        self.account = account
        self.service = core.get_service("docs", "v1", account=account)

    def _batch(self, document_id, requests, required_revision_id=None):
        body = {"requests": requests}
        if required_revision_id:
            body["writeControl"] = {"requiredRevisionId": required_revision_id}
        try:
            return self.service.documents().batchUpdate(
                documentId=document_id, body=body
            ).execute()
        except Exception as e:  # noqa: BLE001 - only the revision mismatch is rewritten
            if required_revision_id and _is_revision_mismatch(e):
                raise _stale_revision_error(document_id, required_revision_id) from e
            raise

    def batch_update(self, document_id, requests, required_revision_id=None):
        """Pass raw batchUpdate request dicts through to the Docs API."""
        if not isinstance(requests, list) or not requests:
            raise ValueError("requests must be a non-empty list")
        return self._batch(document_id, requests, required_revision_id)

    @staticmethod
    def _pt(points):
        return {"magnitude": points, "unit": "PT"}

    def _size_pt(self, width, height):
        return {"width": self._pt(width), "height": self._pt(height)}

    def create_document(self, title, folder_id=None):
        """Create an empty Doc; with ``folder_id``, move it there (trashed if the move fails)."""
        created = self.service.documents().create(body={"title": title}).execute()
        if folder_id:
            place_in_folder(self._drive(), created["documentId"], folder_id)
        return created

    def get_document(self, document_id):
        return self.service.documents().get(documentId=document_id).execute()

    @staticmethod
    def _guard_revision(document_id, doc, required_revision_id):
        """Return the revision to write against, failing early if the caller's read is already stale.

        Tools that compute indices from their own read guard the write with that
        read's revision, so an edit landing in between makes the write fail instead
        of hitting shifted positions.
        """
        current = doc.get("revisionId")
        if required_revision_id and current and required_revision_id != current:
            raise _stale_revision_error(document_id, required_revision_id)
        if not (required_revision_id or current):
            raise ValueError(
                f"Docs returned no revisionId for {document_id} (the account may lack edit "
                "access), so this write cannot be guarded against concurrent edits."
            )
        return required_revision_id or current

    def get_document_text(self, document_id):
        """Extract the plain text of a document (paragraphs + table cells)."""
        doc = self.get_document(document_id)

        def walk(elements):
            parts = []
            for el in elements:
                para = el.get("paragraph")
                if para:
                    for e in para.get("elements", []):
                        tr = e.get("textRun")
                        if tr and tr.get("content"):
                            parts.append(tr["content"])
                table = el.get("table")
                if table:
                    for row in table.get("tableRows", []):
                        for cell in row.get("tableCells", []):
                            parts.append(walk(cell.get("content", [])))
            return "".join(parts)

        text = walk(doc.get("body", {}).get("content", []))
        return {"documentId": document_id, "title": doc.get("title"), "text": text}

    def insert_text(self, document_id, text, index=1, segment_id=None, required_revision_id=None):
        location = {"index": index}
        if segment_id:
            location["segmentId"] = segment_id
        return self._batch(document_id, [{"insertText": {"location": location, "text": text}}], required_revision_id)

    def append_text(self, document_id, text):
        doc = self.get_document(document_id)
        content = doc.get("body", {}).get("content", [])
        end = content[-1].get("endIndex", 2) - 1 if content else 1
        if end < 1:
            end = 1
        revision_id = self._guard_revision(document_id, doc, None)
        return self.insert_text(document_id, text, index=end, required_revision_id=revision_id)

    def replace_all_text(self, document_id, find, replace, match_case=False):
        return self._batch(document_id, [{"replaceAllText": {
            "containsText": {"text": find, "matchCase": match_case},
            "replaceText": replace,
        }}])

    def _hex_to_rgb_color(self, hex_str):
        h = hex_str.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) != 6:
            raise ValueError(f"invalid hex color: {hex_str!r}")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return {"red": r / 255, "green": g / 255, "blue": b / 255}

    def format_text(self, document_id, start_index, end_index, bold=None, italic=None, underline=None,
                    strikethrough=None, font_size=None, font_family=None, link_url=None,
                    foreground_color=None, background_color=None, segment_id=None,
                    required_revision_id=None, clear_link=False):
        if clear_link and link_url is not None:
            raise ValueError("pass link_url or clear_link, not both")
        text_style, fields = self._text_style(
            bold, italic, underline, strikethrough, font_size, font_family, link_url,
            foreground_color, background_color,
        )
        if not fields and not clear_link:
            raise ValueError("format_text requires at least one style attribute")
        text_range = {"startIndex": start_index, "endIndex": end_index}
        if segment_id:
            text_range["segmentId"] = segment_id
        requests = []
        if fields:
            requests.append({"updateTextStyle": {
                "range": text_range,
                "textStyle": text_style,
                "fields": ",".join(fields),
            }})
        if clear_link:
            doc = self.get_document(document_id)
            required_revision_id = self._guard_revision(document_id, doc, required_revision_id)
            text, indexes, runs = self._text_index_map(self._segment_content(doc, segment_id))
            segments = self._run_segments(text, indexes, runs, start_index, end_index)
            if not segments:
                raise ValueError(f"no text between indices {start_index} and {end_index}")
            requests.extend(self._clear_link_requests(segments, segment_id))
        return self._batch(document_id, requests, required_revision_id)

    def _text_style(self, bold=None, italic=None, underline=None, strikethrough=None, font_size=None,
                    font_family=None, link_url=None, foreground_color=None, background_color=None):
        text_style = {}
        fields = []
        if bold is not None:
            text_style["bold"] = bold
            fields.append("bold")
        if italic is not None:
            text_style["italic"] = italic
            fields.append("italic")
        if underline is not None:
            text_style["underline"] = underline
            fields.append("underline")
        if strikethrough is not None:
            text_style["strikethrough"] = strikethrough
            fields.append("strikethrough")
        if font_size is not None:
            text_style["fontSize"] = {"magnitude": font_size, "unit": "PT"}
            fields.append("fontSize")
        if font_family is not None:
            text_style["weightedFontFamily"] = {"fontFamily": font_family}
            fields.append("weightedFontFamily")
        if link_url is not None:
            text_style["link"] = {"url": link_url}
            fields.append("link")
        if foreground_color is not None:
            text_style["foregroundColor"] = {"color": {"rgbColor": self._hex_to_rgb_color(foreground_color)}}
            fields.append("foregroundColor")
        if background_color is not None:
            text_style["backgroundColor"] = {"color": {"rgbColor": self._hex_to_rgb_color(background_color)}}
            fields.append("backgroundColor")
        return text_style, fields

    def set_paragraph_style(self, document_id, start_index, end_index, named_style, required_revision_id=None):
        valid = {"NORMAL_TEXT", "TITLE", "SUBTITLE", "HEADING_1", "HEADING_2", "HEADING_3", "HEADING_4", "HEADING_5", "HEADING_6"}
        if named_style not in valid:
            raise ValueError(f"named_style must be one of {sorted(valid)}")
        return self.update_paragraph_style(document_id, start_index, end_index, named_style_type=named_style,
                                           required_revision_id=required_revision_id)

    def update_paragraph_style(self, document_id, start_index, end_index, named_style_type=None,
                               alignment=None, indent_start_pt=None, indent_end_pt=None,
                               space_above_pt=None, space_below_pt=None, line_spacing=None,
                               line_spacing_mode=None, segment_id=None, required_revision_id=None):
        paragraph_style = {}
        fields = []
        if named_style_type is not None:
            paragraph_style["namedStyleType"] = named_style_type
            fields.append("namedStyleType")
        if alignment is not None:
            valid_align = {"START", "END", "CENTER", "JUSTIFIED"}
            if alignment not in valid_align:
                raise ValueError(f"alignment must be one of {sorted(valid_align)}")
            paragraph_style["alignment"] = alignment
            fields.append("alignment")
        if indent_start_pt is not None:
            paragraph_style["indentStart"] = self._pt(indent_start_pt)
            fields.append("indentStart")
        if indent_end_pt is not None:
            paragraph_style["indentEnd"] = self._pt(indent_end_pt)
            fields.append("indentEnd")
        if space_above_pt is not None:
            paragraph_style["spaceAbove"] = self._pt(space_above_pt)
            fields.append("spaceAbove")
        if space_below_pt is not None:
            paragraph_style["spaceBelow"] = self._pt(space_below_pt)
            fields.append("spaceBelow")
        if line_spacing is not None:
            paragraph_style["lineSpacing"] = line_spacing
            fields.append("lineSpacing")
        if line_spacing_mode is not None:
            valid_modes = {"AT_LEAST", "EXACTLY", "MULTIPLE"}
            if line_spacing_mode not in valid_modes:
                raise ValueError(f"line_spacing_mode must be one of {sorted(valid_modes)}")
            paragraph_style["spacingMode"] = line_spacing_mode
            fields.append("spacingMode")
        if not fields:
            raise ValueError("update_paragraph_style requires at least one style attribute")
        text_range = {"startIndex": start_index, "endIndex": end_index}
        if segment_id:
            text_range["segmentId"] = segment_id
        return self._batch(document_id, [{"updateParagraphStyle": {
            "range": text_range,
            "paragraphStyle": paragraph_style,
            "fields": ",".join(fields),
        }}], required_revision_id)

    def set_page_layout(self, document_id, page_preset=None, width_pt=None, height_pt=None,
                        margin_top_pt=None, margin_bottom_pt=None, margin_left_pt=None, margin_right_pt=None,
                        margin_header_pt=None, margin_footer_pt=None, flip_page_orientation=None):
        document_style = {}
        fields = []
        if page_preset is not None:
            if page_preset not in PAGE_PRESETS:
                raise ValueError(f"page_preset must be one of {sorted(PAGE_PRESETS)}")
            width_pt, height_pt = PAGE_PRESETS[page_preset]
        if width_pt is not None or height_pt is not None:
            if width_pt is None or height_pt is None:
                raise ValueError("width_pt and height_pt must both be set (or use page_preset)")
            document_style["pageSize"] = self._size_pt(width_pt, height_pt)
            fields.append("pageSize")
        for attr, value in (
            ("marginTop", margin_top_pt),
            ("marginBottom", margin_bottom_pt),
            ("marginLeft", margin_left_pt),
            ("marginRight", margin_right_pt),
            ("marginHeader", margin_header_pt),
            ("marginFooter", margin_footer_pt),
        ):
            if value is not None:
                document_style[attr] = self._pt(value)
                fields.append(attr)
        if flip_page_orientation is not None:
            document_style["flipPageOrientation"] = flip_page_orientation
            fields.append("flipPageOrientation")
        if not fields:
            raise ValueError("set_page_layout requires at least one layout attribute")
        return self._batch(document_id, [{"updateDocumentStyle": {
            "documentStyle": document_style,
            "fields": ",".join(fields),
        }}])

    def flip_page_orientation(self, document_id, flip=True):
        """Toggle between portrait and landscape (swaps page width/height)."""
        return self.set_page_layout(document_id, flip_page_orientation=flip)

    def create_header(self, document_id, header_type="DEFAULT"):
        if header_type not in {"DEFAULT"}:
            raise ValueError("header_type must be DEFAULT")
        return self._batch(document_id, [{"createHeader": {"type": header_type}}])

    def create_footer(self, document_id, footer_type="DEFAULT"):
        if footer_type not in {"DEFAULT"}:
            raise ValueError("footer_type must be DEFAULT")
        return self._batch(document_id, [{"createFooter": {"type": footer_type}}])

    def delete_header(self, document_id, header_id):
        return self._batch(document_id, [{"deleteHeader": {"headerId": header_id}}])

    def delete_footer(self, document_id, footer_id):
        return self._batch(document_id, [{"deleteFooter": {"footerId": footer_id}}])

    def setup_header(self, document_id, text, header_type="DEFAULT", index=0):
        resp = self.create_header(document_id, header_type)
        header_id = resp["replies"][0]["createHeader"]["headerId"]
        insert_resp = self.insert_text(document_id, text, index=index, segment_id=header_id)
        return {"headerId": header_id, "createHeader": resp, "insertText": insert_resp}

    def setup_footer(self, document_id, text, footer_type="DEFAULT", index=0):
        resp = self.create_footer(document_id, footer_type)
        footer_id = resp["replies"][0]["createFooter"]["footerId"]
        insert_resp = self.insert_text(document_id, text, index=index, segment_id=footer_id)
        return {"footerId": footer_id, "createFooter": resp, "insertText": insert_resp}

    def insert_inline_image(self, document_id, uri, index=1, width_pt=None, height_pt=None, segment_id=None, required_revision_id=None):
        location = {"index": index}
        if segment_id:
            location["segmentId"] = segment_id
        req = {"insertInlineImage": {"location": location, "uri": uri}}
        if width_pt is not None or height_pt is not None:
            size = {}
            if width_pt is not None:
                size["width"] = self._pt(width_pt)
            if height_pt is not None:
                size["height"] = self._pt(height_pt)
            req["insertInlineImage"]["objectSize"] = size
        return self._batch(document_id, [req], required_revision_id)

    def insert_chart_image(self, document_id, uri, index=1, width_pt=468, height_pt=280, segment_id=None, required_revision_id=None):
        """Insert a chart as an inline image (Google Docs has no native chart API).

        Render the chart elsewhere (Sheets export, matplotlib, etc.) and pass a public image URL.
        """
        return self.insert_inline_image(
            document_id, uri, index=index, width_pt=width_pt, height_pt=height_pt, segment_id=segment_id,
            required_revision_id=required_revision_id,
        )

    def insert_table(self, document_id, rows, columns, index=1, segment_id=None, required_revision_id=None):
        location = {"index": index}
        if segment_id:
            location["segmentId"] = segment_id
        return self._batch(document_id, [{"insertTable": {
            "rows": rows,
            "columns": columns,
            "location": location,
        }}], required_revision_id)

    def insert_page_break(self, document_id, index=1, segment_id=None, required_revision_id=None):
        location = {"index": index}
        if segment_id:
            location["segmentId"] = segment_id
        return self._batch(document_id, [{"insertPageBreak": {"location": location}}], required_revision_id)

    def insert_bullets(self, document_id, start_index, end_index, bullet_preset="BULLET_DISC_CIRCLE_SQUARE", segment_id=None, required_revision_id=None):
        text_range = {"startIndex": start_index, "endIndex": end_index}
        if segment_id:
            text_range["segmentId"] = segment_id
        return self._batch(document_id, [{"createParagraphBullets": {
            "range": text_range,
            "bulletPreset": bullet_preset,
        }}], required_revision_id)

    def insert_numbered_list(self, document_id, start_index, end_index,
                             preset="NUMBERED_DECIMAL_ALPHA_ROMAN", segment_id=None, required_revision_id=None):
        """Apply numbered list formatting to paragraphs in a range.

        Valid presets: NUMBERED_DECIMAL_ALPHA_ROMAN, NUMBERED_DECIMAL_ALPHA_ROMAN_PARENS,
        NUMBERED_DECIMAL_NESTED, NUMBERED_UPPERALPHA_ALPHA_ROMAN,
        NUMBERED_UPPERROMAN_UPPERALPHA_DECIMAL, NUMBERED_ZERODECIMAL_ALPHA_ROMAN.
        """
        if preset not in NUMBERED_BULLET_PRESETS:
            raise ValueError(f"preset must be one of {sorted(NUMBERED_BULLET_PRESETS)}")
        return self.insert_bullets(document_id, start_index, end_index, preset, segment_id, required_revision_id)

    def remove_bullets(self, document_id, start_index, end_index, segment_id=None, required_revision_id=None):
        text_range = {"startIndex": start_index, "endIndex": end_index}
        if segment_id:
            text_range["segmentId"] = segment_id
        return self._batch(document_id, [{"deleteParagraphBullets": {"range": text_range}}], required_revision_id)

    def delete_range(self, document_id, start_index, end_index, segment_id=None, required_revision_id=None):
        text_range = {"startIndex": start_index, "endIndex": end_index}
        if segment_id:
            text_range["segmentId"] = segment_id
        return self._batch(document_id, [{"deleteContentRange": {"range": text_range}}], required_revision_id)

    # --- content map / table helpers ---

    @staticmethod
    def _paragraph_text(paragraph):
        parts = []
        for elem in paragraph.get("elements", []):
            tr = elem.get("textRun")
            if tr and tr.get("content"):
                parts.append(tr["content"])
            if elem.get("autoText"):
                parts.append(f"[{elem['autoText'].get('type', 'AUTO')}]")
        return "".join(parts)

    @classmethod
    def _text_preview(cls, elements, max_len=80):
        parts = []
        for el in elements:
            para = el.get("paragraph")
            if para:
                parts.append(cls._paragraph_text(para))
            table = el.get("table")
            if table:
                for row in table.get("tableRows", []):
                    for cell in row.get("tableCells", []):
                        parts.append(cls._text_preview(cell.get("content", [])))
        text = "".join(parts).replace("\n", " ").strip()
        if len(text) > max_len:
            return text[: max_len - 3] + "..."
        return text

    @classmethod
    def _map_table_cells(cls, table):
        cells = []
        for row_idx, row in enumerate(table.get("tableRows", [])):
            for col_idx, cell in enumerate(row.get("tableCells", [])):
                content = cell.get("content", [])
                entry = {"row": row_idx, "column": col_idx}
                if content:
                    entry["startIndex"] = content[0].get("startIndex")
                    entry["endIndex"] = content[-1].get("endIndex")
                    entry["textPreview"] = cls._text_preview(content)
                cells.append(entry)
        return cells

    @classmethod
    def _walk_content(cls, content, segment_id=None, segment_type="body", elements=None):
        if elements is None:
            elements = []
        for el in content:
            entry = {
                "type": "unknown",
                "startIndex": el.get("startIndex"),
                "endIndex": el.get("endIndex"),
                "segmentId": segment_id,
                "segmentType": segment_type,
            }
            if el.get("paragraph"):
                para = el["paragraph"]
                entry["type"] = "paragraph"
                entry["textPreview"] = cls._paragraph_text(para).replace("\n", " ").strip()
                style = para.get("paragraphStyle", {})
                if style.get("namedStyleType"):
                    entry["namedStyleType"] = style["namedStyleType"]
            elif el.get("table"):
                table = el["table"]
                entry["type"] = "table"
                entry["rows"] = table.get("rows")
                entry["columns"] = table.get("columns")
                entry["cells"] = cls._map_table_cells(table)
            elif el.get("sectionBreak"):
                entry["type"] = "sectionBreak"
            elif el.get("tableOfContents"):
                entry["type"] = "tableOfContents"
            elif el.get("pageBreak"):
                entry["type"] = "pageBreak"
            elements.append(entry)
        return elements

    def get_content_map(self, document_id, include_headers_footers=True):
        """Walk document structure and return indexed elements for agent-friendly editing."""
        doc = self.get_document(document_id)
        elements = self._walk_content(doc.get("body", {}).get("content", []), segment_type="body")
        if include_headers_footers:
            for header_id, header in (doc.get("headers") or {}).items():
                self._walk_content(
                    header.get("content", []),
                    segment_id=header_id,
                    segment_type="header",
                    elements=elements,
                )
            for footer_id, footer in (doc.get("footers") or {}).items():
                self._walk_content(
                    footer.get("content", []),
                    segment_id=footer_id,
                    segment_type="footer",
                    elements=elements,
                )
        return {
            "documentId": document_id,
            "revisionId": doc.get("revisionId"),
            "title": doc.get("title"),
            "elements": elements,
        }

    def _table_start_location(self, table_start_index, segment_id=None):
        loc = {"index": table_start_index}
        if segment_id:
            loc["segmentId"] = segment_id
        return loc

    def _table_range(self, table_start_index, row, column, row_span=1, column_span=1, segment_id=None):
        return {
            "tableCellLocation": {
                "tableStartLocation": self._table_start_location(table_start_index, segment_id),
                "rowIndex": row,
                "columnIndex": column,
            },
            "rowSpan": row_span,
            "columnSpan": column_span,
        }

    def _find_table(self, content, table_start_index):
        for el in content:
            if el.get("startIndex") == table_start_index and el.get("table"):
                return el["table"]
        return None

    def populate_table(self, document_id, table_start_index, rows, segment_id=None, required_revision_id=None):
        """Fill table cells with text. Rows is a list of row lists of strings."""
        if not rows or not isinstance(rows, list):
            raise ValueError("rows must be a non-empty list of row lists")
        doc = self.get_document(document_id)
        revision_id = self._guard_revision(document_id, doc, required_revision_id)
        content = self._segment_content(doc, segment_id)
        table = self._find_table(content, table_start_index)
        if table is None:
            raise ValueError(f"no table found at startIndex {table_start_index}")
        table_rows = table.get("tableRows", [])
        if len(rows) != len(table_rows):
            raise ValueError(
                f"rows has {len(rows)} row(s) but table has {len(table_rows)} row(s)"
            )
        inserts = []
        for row_idx, row_values in enumerate(rows):
            table_cells = table_rows[row_idx].get("tableCells", [])
            if len(row_values) != len(table_cells):
                raise ValueError(
                    f"row {row_idx} has {len(row_values)} value(s) but table has {len(table_cells)} column(s)"
                )
            for col_idx, text in enumerate(row_values):
                cell_content = table_cells[col_idx].get("content", [])
                if not cell_content:
                    raise ValueError(f"cell ({row_idx}, {col_idx}) has no content to insert into")
                index = cell_content[0].get("startIndex")
                if index is None:
                    raise ValueError(f"cell ({row_idx}, {col_idx}) missing startIndex")
                location = {"index": index}
                if segment_id:
                    location["segmentId"] = segment_id
                inserts.append((index, str(text)))
        requests = [
            {"insertText": {"location": {"index": idx, **({"segmentId": segment_id} if segment_id else {})}, "text": text}}
            for idx, text in sorted(inserts, key=lambda item: item[0], reverse=True)
        ]
        return self._batch(document_id, requests, revision_id)

    def merge_table_cells(self, document_id, table_start_index, row, column, row_span, column_span,
                          segment_id=None, required_revision_id=None):
        return self._batch(document_id, [{
            "mergeTableCells": {
                "tableRange": self._table_range(
                    table_start_index, row, column, row_span, column_span, segment_id,
                ),
            },
        }], required_revision_id)

    def format_table_cells(self, document_id, table_start_index, row, column, row_span=1, column_span=1,
                           background_color=None, border_color=None, border_width_pt=None, segment_id=None, required_revision_id=None):
        table_cell_style = {}
        fields = []
        if background_color is not None:
            table_cell_style["backgroundColor"] = {
                "color": {"rgbColor": self._hex_to_rgb_color(background_color)},
            }
            fields.append("backgroundColor")
        if border_color is not None or border_width_pt is not None:
            border = {"dashStyle": "SOLID"}
            if border_color is not None:
                border["color"] = {"color": {"rgbColor": self._hex_to_rgb_color(border_color)}}
            if border_width_pt is not None:
                border["width"] = self._pt(border_width_pt)
            for side in ("borderTop", "borderBottom", "borderLeft", "borderRight"):
                table_cell_style[side] = border
                fields.append(side)
        if not fields:
            raise ValueError("format_table_cells requires at least one style attribute")
        return self._batch(document_id, [{
            "updateTableCellStyle": {
                "tableRange": self._table_range(
                    table_start_index, row, column, row_span, column_span, segment_id,
                ),
                "tableCellStyle": table_cell_style,
                "fields": ",".join(fields),
            },
        }], required_revision_id)

    def insert_page_number(self, document_id, footer_id, index=0):
        """Insert a dynamic page number field into a footer segment.

        Note: the public Google Docs API currently rejects ``insertPageNumber``
        (400 Unknown name). Kept for forward compatibility; use the Docs UI or
        a template with page numbers pre-configured until Google exposes this request.
        """
        location = {"segmentId": footer_id, "index": index}
        return self._batch(document_id, [{"insertPageNumber": {"location": location}}])

    def _drive_comments(self):
        inst = getattr(self, "_drive_comments_api", None)
        if inst is None:
            inst = DriveAPI(self.account)
            self._drive_comments_api = inst
        return inst

    def list_comments(self, document_id, include_deleted=False, page_size=20, page_token=None):
        return self._drive_comments().list_comments(
            document_id, include_deleted, page_size, page_token,
        )

    @staticmethod
    def _utf16_width(ch):
        return 2 if ord(ch) > 0xFFFF else 1

    @staticmethod
    def _text_index_map(content):
        """Flatten paragraphs (including table cells) into text plus a Docs index per character.

        Returns ``(text, indexes, runs)``: ``indexes[i]`` is the UTF-16 document
        index of ``text[i]`` and ``runs[i]`` numbers the text run it came from.
        Non-text paragraph elements (inline images, footnote references, rules)
        become OBJECT_PLACEHOLDER characters with run ``None``, one per index
        they occupy, so later indices stay right and no match spans them.
        """
        chars = []
        indexes = []
        runs = []

        def walk(elements):
            for el in elements:
                para = el.get("paragraph")
                if para:
                    for e in para.get("elements", []):
                        # Docs omits zero-valued indices, which is where a header or footer starts.
                        start = e.get("startIndex", 0)
                        tr = e.get("textRun")
                        if tr is None:
                            width = (e.get("endIndex") or start) - start
                            chars.append(OBJECT_PLACEHOLDER * width)
                            indexes.extend(range(start, start + width))
                            runs.extend([None] * width)
                            continue
                        content = tr.get("content") or ""
                        run = len(chars)
                        chars.append(content)
                        idx = start
                        for ch in content:
                            indexes.append(idx)
                            runs.append(run)
                            idx += DocsAPI._utf16_width(ch)
                table = el.get("table")
                if table:
                    for row in table.get("tableRows", []):
                        for cell in row.get("tableCells", []):
                            walk(cell.get("content", []))

        walk(content or [])
        return "".join(chars), indexes, runs

    @staticmethod
    def _body_content(doc):
        return ((doc or {}).get("body") or {}).get("content") or []

    @staticmethod
    def _segment_content(doc, segment_id=None):
        if not segment_id:
            return DocsAPI._body_content(doc)
        segment = (doc.get("headers") or {}).get(segment_id) or (doc.get("footers") or {}).get(segment_id)
        if not segment:
            raise ValueError(f"segment_id {segment_id!r} not found in document headers/footers")
        return segment.get("content", [])

    @staticmethod
    def _span_range(text, indexes, pos, end):
        """Docs [startIndex, endIndex) for the characters text[pos:end]."""
        return indexes[pos], indexes[end - 1] + DocsAPI._utf16_width(text[end - 1])

    @staticmethod
    def _find_quote(text, indexes, quote, occurrence):
        if occurrence < 1:
            raise ValueError("occurrence must be at least 1")
        start = 0
        found = 0
        while True:
            pos = text.find(quote, start)
            if pos < 0:
                break
            found += 1
            if found == occurrence:
                return DocsAPI._span_range(text, indexes, pos, pos + len(quote))
            start = pos + 1
        if found == 0:
            raise ValueError(f"quote {quote!r} not found in document")
        raise ValueError(
            f"occurrence {occurrence} is out of range; found {found} match(es) for {quote!r}"
        )

    @staticmethod
    def _find_matches(text, indexes, pattern, regex=False, match_case=True):
        """Every non-empty, non-overlapping match as {text, startIndex, endIndex, pos, end}.

        pos/end are offsets into ``text`` (code points); startIndex/endIndex are
        the Docs UTF-16 indices to send back in requests.
        """
        if not pattern:
            raise ValueError("pattern must be a non-empty string")
        flags = 0 if match_case else re.IGNORECASE
        try:
            rx = re.compile(pattern if regex else re.escape(pattern), flags)
        except re.error as e:
            raise ValueError(f"invalid regex {pattern!r}: {e}") from e
        matches = []
        # A match that crossed a paragraph or cell break or an inline image would
        # style structural indices, so each chunk is searched on its own and ^/$
        # anchor to paragraph edges.
        for chunk in _SEARCHABLE_CHUNK.finditer(text):
            offset = chunk.start()
            for m in rx.finditer(chunk.group()):
                if m.end() == m.start():
                    continue
                pos, end = offset + m.start(), offset + m.end()
                start_index, end_index = DocsAPI._span_range(text, indexes, pos, end)
                matches.append({
                    "text": m.group(0), "startIndex": start_index, "endIndex": end_index,
                    "pos": pos, "end": end,
                })
        return matches

    @staticmethod
    def _run_segments(text, indexes, runs, start_index, end_index):
        """Split [start_index, end_index) into one (start, end) range per text run it touches.

        Clearing a link with an empty textStyle over a range that crosses table
        structure is silently ignored by Docs, so link clears go run by run.
        """
        segments = []
        for ch, idx, run in zip(text, indexes, runs):
            if run is None or not start_index <= idx < end_index:
                continue
            width = DocsAPI._utf16_width(ch)
            if segments and segments[-1][0] == run and segments[-1][2] == idx:
                segments[-1][2] = idx + width
            else:
                segments.append([run, idx, idx + width])
        return [(seg_start, seg_end) for _, seg_start, seg_end in segments]

    @staticmethod
    def _clear_link_requests(segments, segment_id=None):
        requests = []
        for seg_start, seg_end in segments:
            text_range = {"startIndex": seg_start, "endIndex": seg_end}
            if segment_id:
                text_range["segmentId"] = segment_id
            requests.append({"updateTextStyle": {"range": text_range, "textStyle": {}, "fields": "link"}})
        return requests

    def find_text(self, document_id, pattern, regex=False, match_case=True,
                  max_matches=DEFAULT_MAX_MATCHES, context_chars=40):
        """Find matches in the body (table cells included) with their indices and the doc revision."""
        doc = self.get_document(document_id)
        text, indexes, _runs = self._text_index_map(self._body_content(doc))
        matches = self._find_matches(text, indexes, pattern, regex, match_case)
        shown = []
        for m in matches[:max_matches]:
            context = text[max(0, m["pos"] - context_chars):m["end"] + context_chars]
            shown.append({
                "text": m["text"], "startIndex": m["startIndex"], "endIndex": m["endIndex"],
                "context": context.replace("\n", " "),
            })
        return {
            "documentId": document_id,
            "revisionId": doc.get("revisionId"),
            "title": doc.get("title"),
            "matchCount": len(matches),
            "matches": shown,
            "truncated": len(matches) > len(shown),
        }

    def format_matches(self, document_id, pattern, regex=False, match_case=True, link_url_template=None,
                       clear_link=False, bold=None, italic=None, underline=None, strikethrough=None,
                       font_size=None, font_family=None, foreground_color=None, background_color=None,
                       required_revision_id=None, max_matches=DEFAULT_MAX_FORMAT_MATCHES, max_listed=50):
        """Style every match, resolving positions from a fresh read and guarding the write with its revision."""
        if link_url_template is not None and clear_link:
            raise ValueError("pass link_url_template or clear_link, not both")
        text_style, fields = self._text_style(
            bold, italic, underline, strikethrough, font_size, font_family, None,
            foreground_color, background_color,
        )
        if link_url_template is not None:
            fields.append("link")
        if not fields and not clear_link:
            raise ValueError("format_matches requires a style attribute, link_url_template, or clear_link")
        doc = self.get_document(document_id)
        revision_id = self._guard_revision(document_id, doc, required_revision_id)
        text, indexes, runs = self._text_index_map(self._body_content(doc))
        matches = self._find_matches(text, indexes, pattern, regex, match_case)
        if len(matches) > max_matches:
            raise ValueError(
                f"{len(matches)} matches for {pattern!r} exceeds max_matches={max_matches}; nothing "
                "was written. Narrow the pattern, or raise max_matches if that many is intended."
            )
        requests = []
        for m in matches:
            match_range = {"startIndex": m["startIndex"], "endIndex": m["endIndex"]}
            if fields:
                style = dict(text_style)
                if link_url_template is not None:
                    style["link"] = {"url": link_url_template.replace("{match}", url_quote(m["text"], safe=""))}
                requests.append({"updateTextStyle": {
                    "range": match_range, "textStyle": style, "fields": ",".join(fields),
                }})
            if clear_link:
                requests.extend(self._clear_link_requests(
                    self._run_segments(text, indexes, runs, m["startIndex"], m["endIndex"])
                ))
        new_revision = revision_id
        if requests:
            resp = self._batch(document_id, requests, revision_id)
            new_revision = ((resp or {}).get("writeControl") or {}).get("requiredRevisionId") or revision_id
        listed = [
            {"text": m["text"], "startIndex": m["startIndex"], "endIndex": m["endIndex"]}
            for m in matches[:max_listed]
        ]
        return {
            "documentId": document_id,
            "revisionId": new_revision,
            "matchCount": len(matches),
            "requestCount": len(requests),
            "matches": listed,
            "truncated": len(matches) > len(listed),
        }

    @staticmethod
    def _text_for_range(text, indexes, start_index, end_index):
        return "".join(
            ch for ch, idx in zip(text, indexes)
            if start_index <= idx < end_index and ch != OBJECT_PLACEHOLDER
        )

    def add_comment(
        self, document_id, content, start_index=None, end_index=None, quote=None, occurrence=1,
    ):
        quoted_text = None
        anchor = None
        if quote:
            if occurrence < 1:
                raise ValueError("occurrence must be at least 1")
            doc = self.get_document(document_id)
            text, indexes, _runs = self._text_index_map(self._body_content(doc))
            start_index, end_index = self._find_quote(text, indexes, quote, occurrence)
            anchor = {"docsRange": {"startIndex": start_index, "endIndex": end_index}}
            quoted_text = quote
        else:
            if (start_index is None) != (end_index is None):
                raise ValueError("start_index and end_index must both be set")
            if start_index is not None:
                if start_index >= end_index:
                    raise ValueError("start_index must be less than end_index")
                anchor = {"docsRange": {"startIndex": start_index, "endIndex": end_index}}
                doc = self.get_document(document_id)
                text, indexes, _runs = self._text_index_map(self._body_content(doc))
                extracted = self._text_for_range(text, indexes, start_index, end_index)
                if extracted:
                    quoted_text = extracted
        return self._drive_comments().create_comment(
            document_id, content, anchor, quoted_text=quoted_text,
        )

    def reply_to_comment(self, document_id, comment_id, content=None, action=None):
        return self._drive_comments().reply_to_comment(document_id, comment_id, content, action)

    def delete_comment(self, document_id, comment_id):
        return self._drive_comments().delete_comment(document_id, comment_id)

    # --- markdown (via Drive import/export conversion) ---

    def _drive(self, account=None):
        return core.get_service("drive", "v3", account=account or self.account)

    def _markdown_media(self, markdown):
        return MediaIoBaseUpload(
            io.BytesIO(markdown.encode("utf-8")), mimetype="text/markdown", resumable=True
        )

    def create_document_from_markdown(self, title, markdown, folder_id=None):
        """Create a Google Doc by importing markdown (Drive converts it to native formatting)."""
        body = {"name": title, "mimeType": DOCUMENT_MIME}
        if folder_id:
            body["parents"] = [folder_id]
        created = self._drive().files().create(
            body=body,
            media_body=self._markdown_media(markdown),
            fields="id, name, mimeType, webViewLink",
            supportsAllDrives=True,
        ).execute()
        return {
            "documentId": created.get("id"),
            "title": created.get("name"),
            "webViewLink": created.get("webViewLink"),
        }

    def replace_document_with_markdown(self, document_id, markdown):
        """Replace a document's entire content by re-importing markdown."""
        updated = self._drive().files().update(
            fileId=document_id,
            media_body=self._markdown_media(markdown),
            fields="id, name, mimeType",
            supportsAllDrives=True,
        ).execute()
        return {
            "documentId": updated.get("id"),
            "title": updated.get("name"),
            "replaced": True,
        }

    def read_document_as_markdown(self, document_id):
        """Export a document as markdown text."""
        data = self._drive().files().export(
            fileId=document_id, mimeType="text/markdown"
        ).execute()
        text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        return {"documentId": document_id, "markdown": text}

    def append_markdown(self, document_id, markdown):
        """Append markdown by exporting the doc as markdown, concatenating, and re-importing.

        The whole document round-trips through markdown, so elements markdown
        cannot express (comments, suggestions, positioned images) are lost.
        """
        current = self.read_document_as_markdown(document_id)["markdown"]
        combined = markdown if not current.strip() else current.rstrip("\n") + "\n\n" + markdown
        updated = self.replace_document_with_markdown(document_id, combined)
        return {"documentId": document_id, "title": updated.get("title"), "appended": True}

    def _upload_public_image_uri(self, image_bytes, name="chart.png", account=None):
        drive = self._drive(account)
        media = MediaIoBaseUpload(io.BytesIO(image_bytes), mimetype="image/png", resumable=True)
        created = drive.files().create(
            body={"name": name},
            media_body=media,
            fields="id",
        ).execute()
        file_id = created["id"]
        drive.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            sendNotificationEmail=False,
        ).execute()
        uri = f"https://drive.google.com/uc?export=view&id={file_id}"
        return file_id, uri

    def insert_sheets_chart(self, document_id, spreadsheet_id, chart_id, index=1,
                            width_pt=468, height_pt=280, account=None, make_public=False):
        """Export a Sheets chart as PNG and insert it into the document.

        Docs can only fetch a public image URL. Pass make_public=True to upload
        a PNG to Drive, grant anyone/reader, and return fileId so it can be
        revoked. Default make_public=False raises ValueError and does not upload.
        """
        if not make_public:
            raise ValueError(
                "Docs can only fetch a public image URL. Pass make_public=True "
                "to upload a PNG to Drive, grant anyone/reader, and return the "
                "file id so it can be revoked."
            )
        from ..sheets.sheets_api import SheetsAPI

        resolved_account = account or self.account
        sheets = SheetsAPI(resolved_account)
        image_bytes = sheets.fetch_chart_image_bytes(spreadsheet_id, chart_id)
        file_id, uri = self._upload_public_image_uri(
            image_bytes,
            name=f"chart-{chart_id}.png",
            account=resolved_account,
        )
        result = self.insert_chart_image(
            document_id, uri, index=index, width_pt=width_pt, height_pt=height_pt,
        )
        return {"imageUri": uri, "fileId": file_id, "insertInlineImage": result}
