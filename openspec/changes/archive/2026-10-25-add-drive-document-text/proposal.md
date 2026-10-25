# Proposal

## Why

An agent can read only plain-text Drive files and native Docs and Sheets inline; PDF, Word,
PowerPoint, Excel and native Slides files come back as a local path the client may not be able
to open. mini-web-ui's planned Drive notebooks need the text of up to 10 such documents per
notebook, fetched by the client itself, so the Drive server must return document text directly,
in bounded pieces, and list the files in a folder tree.

## What Changes

- `read_file_text` (read-only, changed) also returns the text of PDF, docx, pptx and xlsx
  files and of native Slides, with a marker before every page, slide or sheet. Native Sheets
  return every sheet instead of only the first. A new optional `pages` argument limits PDFs and
  presentations to a range such as `3-7`. Documents up to 25 MiB are read; plain-text files keep
  their 1 MiB cap.
- `read_files` (read-only, new) reads several files in one call, with a per-file and a total
  character budget, and reports failed and skipped files separately.
- `list_folder_tree` (read-only, new) lists every file under a folder, including subfolders,
  with each file's folder path, modified time, checksum and Drive link.
- **BREAKING** (minor): `read_file_text` on a native Sheet returns tab-separated text with a
  `[sheet Name]` marker per sheet instead of the first sheet as CSV.
- New dependency: `pypdf` (BSD-3-Clause, pure Python) for PDF text. Office formats are parsed
  with the standard library.

## Capabilities

### New Capabilities
- `drive-files`: reading file text from Drive and listing folder contents, the part of the
  Drive server that clients use to bring documents into a conversation.

### Modified Capabilities

## Impact

- Code: `google_workspace_mcp/drive/` (new `extract.py`; `drive_api.py`; `server.py`).
- Tools: `read_file_text` changed; `read_files` and `list_folder_tree` added. All read-only, so
  they stay available under `GOOGLE_MCP_READONLY=1`. `read_file_text` stays in the core toolset;
  the two new tools are not in it.
- Dependencies: `pypdf` added to `pyproject.toml`.
- Clients: mini-web-ui's Drive notebooks (a later change in that repo) will call
  `list_folder_tree` and `read_files`.
