# Spec Delta

## Purpose
Lets an MCP client bring Drive documents into a conversation: read a file's text, part of it or
several files at once, and list what a folder holds, without saving anything to local disk.

## ADDED Requirements

### Requirement: Read document text
The Drive server SHALL return the text of a file through `read_file_text` for plain UTF-8 text
files, PDF, Word (docx), PowerPoint (pptx) and Excel (xlsx) files, and native Google Docs,
Sheets and Slides. The text of a PDF SHALL carry a `[page N]` marker before each page, a
presentation a `[slide N]` marker before each slide, and a spreadsheet a `[sheet Name]` marker
before each sheet. A presentation's speaker notes SHALL follow its slide's text. Spreadsheet
rows SHALL be tab-separated, and every sheet SHALL be included.

#### Scenario: PDF text with page markers
- **WHEN** the client calls `read_file_text` on a 3-page PDF
- **THEN** the result's text holds `[page 1]`, `[page 2]` and `[page 3]`, each followed by that page's text, and the result reports `total` 3

#### Scenario: Native Sheet with two sheets
- **WHEN** the client calls `read_file_text` on a native Sheet with sheets `Budget` and `Notes`
- **THEN** the text holds `[sheet Budget]` and `[sheet Notes]`, each followed by that sheet's rows

#### Scenario: Unsupported binary file
- **WHEN** the client calls `read_file_text` on a PNG image
- **THEN** the call fails with an error that names `download_file`

### Requirement: Read part of a document
`read_file_text` SHALL accept an optional `pages` argument for PDFs and presentations, as a
list of 1-based numbers and ranges such as `3-7` or `1,4,9-10`, and SHALL return only those
pages or slides. The result SHALL report the document's total page or slide count and the
numbers it returned. Pages beyond the end SHALL be ignored, a range wholly outside the document
SHALL fail, and `pages` on any other file type SHALL fail. Long text SHALL still be paged with
`max_chars` and `offset`.

#### Scenario: Page range of a PDF
- **WHEN** the client calls `read_file_text` with `pages` `2-3` on a 10-page PDF
- **THEN** the text holds only `[page 2]` and `[page 3]`, and the result reports `total` 10 and `selected` [2, 3]

#### Scenario: Pages on a Word file
- **WHEN** the client passes `pages` for a docx file
- **THEN** the call fails with an error saying pages applies only to PDFs and presentations

### Requirement: Read several files at once
The Drive server SHALL provide `read_files`, which takes a list of file IDs for one account and
returns each file's text as `read_file_text` would. Each file SHALL be cut at a per-file
character limit (default 100,000) and marked truncated when cut. Once a total character budget
(default 400,000) is spent, the remaining files SHALL be listed as skipped, unread. A file that
fails SHALL be listed with its error and SHALL NOT stop the others. Repeated IDs SHALL be read
once.

#### Scenario: One file fails
- **WHEN** the client calls `read_files` with three IDs and the second is a folder
- **THEN** the result holds the first and third files' text and an error entry for the second

#### Scenario: Total budget reached
- **WHEN** the total budget is 1,000 characters and the first file has 1,500 characters
- **THEN** the first file is returned cut to 1,000 characters and marked truncated, and every other file is listed as skipped

### Requirement: List a folder tree
The Drive server SHALL provide `list_folder_tree`, which returns every file under a folder,
including files in subfolders, without listing folders themselves as files. Each file SHALL
carry its ID, name, MIME type, size, modified time, checksum when Drive has one, web link, and
the path of folder names from the given folder down to it. Listing SHALL stop at a file limit
(default 500) and report that it was truncated, and SHALL NOT descend more than a depth limit
(default 10). Trashed files SHALL be left out. A folder reached twice SHALL be listed once.

#### Scenario: Nested folder
- **WHEN** folder `Project` holds `a.pdf` and a subfolder `Specs` holding `b.docx`
- **THEN** the result lists `a.pdf` with path `Project` and `b.docx` with path `Project/Specs`, and no entry for `Specs` itself

#### Scenario: Not a folder
- **WHEN** the client calls `list_folder_tree` with the ID of a PDF
- **THEN** the call fails with an error saying the ID is not a folder

### Requirement: Bounded and safe extraction
Reading document text SHALL NOT write the file to local disk. It SHALL refuse documents larger
than 25 MiB and plain-text files larger than 1 MiB before downloading them, SHALL refuse an
Office file whose contents expand beyond 200 MiB, and SHALL refuse any Office XML part that
declares a DOCTYPE. A password-protected PDF SHALL fail with an error that says so, and a PDF
with no text layer SHALL return its page markers with a note that the pages are probably
scanned images.

#### Scenario: Oversized PDF
- **WHEN** the client calls `read_file_text` on a 30 MiB PDF
- **THEN** the call fails before downloading, with an error that names `download_file`

#### Scenario: Office file with a DOCTYPE
- **WHEN** a docx file's `word/document.xml` declares a DOCTYPE
- **THEN** the call fails and no entity in it is expanded

#### Scenario: Scanned PDF
- **WHEN** the client reads a PDF whose pages hold only images
- **THEN** the text holds the page markers and a note that the pages are probably scanned images
