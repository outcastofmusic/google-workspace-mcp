# Design

## Context

`DriveAPI.read_file_text` (drive/drive_api.py) exports native Docs as text/plain and native
Sheets as text/csv (first sheet only), downloads other files into memory up to 1 MiB, and
decodes them as UTF-8. Everything else must go through `download_file`/`export_file`, which
write to local disk and return a path. Tool output is paged by `core.cap_text`. Downloads use
`_download_to_bytes`, which stops as soon as the content passes its byte cap.

## Goals / Non-Goals

**Goals:**
- Text of the common document formats in the tool result, never via local disk.
- Page and slide ranges, so a client can read part of a long PDF or deck.
- A folder tree with what a client needs to detect changed files later (modified time, checksum).

**Non-Goals:**
- OCR of scanned PDFs.
- Images inside documents, tables rendered as layout, or legacy .doc/.ppt/.xls formats.
- Fan-out across accounts for these tools: a client names one account per call, as
  mini-web-ui's notebooks will tag each source with its account.

## Decisions

- **2026-10-25 — pypdf for PDF text.** Pure Python, BSD-3-Clause, no system libraries, handles
  page-by-page extraction so a page range costs only those pages. Rejected: pdfminer.six
  (slower, MIT, larger) and PyMuPDF (AGPL, native wheels).
- **2026-10-25 — Standard-library zip and XML for docx, pptx and xlsx.** These formats are zip
  archives of XML; reading paragraphs, slide text, notes and cells needs a few dozen lines.
  Rejected: python-docx, python-pptx and openpyxl, three dependencies (lxml among them) for
  text extraction only.
- **2026-10-25 — Native Slides and Sheets export as pptx and xlsx, then reuse the same
  extractors.** One code path gives native files the same slide and sheet markers, every sheet,
  and speaker notes. Rejected: Slides text/plain export (no slide boundaries) and per-sheet CSV
  export through the Sheets API (one call per sheet, needs sheet IDs).
- **2026-10-25 — Separate byte caps: 25 MiB for documents, 1 MiB for plain text.** Documents
  compress text heavily, so 1 MiB of PDF is often a few pages. 25 MiB keeps a single call's
  memory bounded on the user's machine. Rejected: one shared cap.
- **2026-10-25 — `read_files` takes one account and a total character budget.** It keeps a
  single call's output bounded (default 400,000 characters, about 100k tokens, half of the
  200k context of the main model) and reports skipped files so the client can fetch them in a
  second call. Rejected: an unbounded batch, and a cross-account batch, which would break the
  one-account-per-call tool contract.
- **2026-10-25 — `list_folder_tree` walks breadth-first with a seen set.** Drive allows a file
  in several folders, so a folder can be reached twice; the seen set prevents loops and
  duplicates. The file and depth limits bound the number of API calls.

### Untrusted content

Files in shared folders come from other people and may be built to attack a parser or the agent.
- Office XML parts that declare a DOCTYPE are refused before parsing; entities can only be
  declared in a DOCTYPE, so entity-expansion attacks cannot run. External entities are never
  fetched by the standard parser.
- Uncompressed reads from one Office file are capped at 200 MiB (zip bombs); downloads are
  capped before and during transfer.
- Nothing is written to disk, so a crafted file name cannot place a file anywhere.
- Extracted text is returned as data; prompt injection inside a document is the client's
  concern, as it is for mail. mini-web-ui's per-call approval (a later change) guards the
  mutating tools an injected instruction might reach for.

## Risks / Trade-offs

- [pypdf misses text in some PDFs (unusual encodings, text as vector paths)] → the result still
  marks every page, and an empty extraction adds the scanned-pages note so the agent can say so.
- [The native Sheet output format changes] → noted as BREAKING in the proposal; the only known
  client (mini-web-ui) does not yet use it.
- [Native exports are limited by Google to about 10 MB] → Google's error is passed through,
  naming the file.
- [A 25 MiB document is held in memory while extracted] → bounded per call; calls on one
  account are serialized by the client lock.

## Migration Plan

Additive except the native-Sheet text format. Rollback is reverting the change; no stored data.
