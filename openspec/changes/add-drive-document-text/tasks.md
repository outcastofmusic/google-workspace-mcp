# Tasks

## 1. Setup

- [x] 1.1 Add `pypdf` to `pyproject.toml` dependencies and verify `pip install -e .[dev]` installs it

## 2. Extraction

- [x] 2.1 Add `drive/extract.py` with page-range parsing and PDF, docx, pptx (with notes) and xlsx extractors, and verify with unit tests on small files built in the tests (pypdf-written PDF, zip-built Office files), including a range outside the document and a blank PDF page
- [x] 2.2 Guard extraction: DOCTYPE refusal, the 200 MiB uncompressed cap and password-protected PDFs, and verify each with a test that expects the error

## 3. Reading files

- [x] 3.1 Route `DriveAPI.read_file_text` by type: native Docs as text, native Sheets and Slides through xlsx and pptx export, PDF and Office files through the extractors with the 25 MiB cap, plain text with the 1 MiB cap; verify with mocked-service tests for each route and for the cap checked before download
- [x] 3.2 Add the `pages` argument to the `read_file_text` tool, keep `max_chars`/`offset` paging, and verify with protocol tests (`mcp.call_tool`) that pages reach the API and that the old Slides-rejection test now expects slide text
- [x] 3.3 Add the `read_files` tool with per-file and total budgets, errors and skipped lists, and verify with protocol tests for a failing file, the total budget and repeated IDs

## 4. Folder tree

- [x] 4.1 Add `DriveAPI.list_folder_tree` (breadth-first, paged, seen set, file and depth limits) and the `list_folder_tree` tool, and verify with mocked-service tests for nesting, truncation, a folder reached twice and a non-folder ID
- [x] 4.2 Add the new tools to `test_drive_server.py`'s registered-tools list and verify `test_integration.py`'s read-only gate test still passes with both tools present under `GOOGLE_MCP_READONLY=1`

## 5. Docs and checks

- [x] 5.1 Document the supported formats, `pages`, `read_files` and `list_folder_tree` in the README, and note the extractors in CLAUDE.md's architecture block; verify the README examples match the tool signatures
- [ ] 5.2 Run `ruff check .` and `pytest` locally and verify both pass, then open the PR and verify CI passes on Python 3.10-3.13
