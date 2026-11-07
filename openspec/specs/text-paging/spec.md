# text-paging Specification

## Purpose
The default size of a page of text, small enough that its paging keys survive a client's result cap.

## Requirements

### Requirement: Forty-thousand-character pages
A paged text tool SHALL return at most 40,000 characters of text by default, and SHALL mark a
longer text `truncated` with a `next_offset`, so the paging keys survive a client that cuts a
result at 50,000 characters. `read_files` SHALL cut each file at the same default and stop at it in total. A `max_chars` the
caller gives SHALL win.

#### Scenario: A 60,000-character file
- **WHEN** `read_file_text` reads a file of 60,000 characters with no `max_chars`
- **THEN** the result holds the first 40,000 characters, `truncated` true and `next_offset` 40000
