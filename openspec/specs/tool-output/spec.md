# tool-output Specification

## Purpose
How a tool's result reaches the client as text: one line of compact JSON for a dict or a list.

## Requirements

### Requirement: Compact JSON results
A tool result that is a dict or a list SHALL reach the client as one text block holding its JSON
with no indentation and no space after separators, serialised as the MCP library does otherwise:
non-ASCII characters unescaped, and values JSON cannot hold turned to strings. The parsed JSON
SHALL equal what the indented text held.

#### Scenario: Fifteen calendar records
- **WHEN** `list_events` returns the 15 records of the test fixture
- **THEN** the text holds no newline, parses to the same envelope, and holds at most 4,700 characters

#### Scenario: Greek text
- **WHEN** a result holds `Καλημέρα`
- **THEN** the text holds `Καλημέρα`, not `Κ...`

### Requirement: Other results unchanged
A tool result that is not a dict or a list SHALL reach the client as before, and a Python caller
of a tool function SHALL still get the function's own return value.

#### Scenario: A direct call
- **WHEN** a test calls `calendar.list_events_all_accounts(...)` in process
- **THEN** it gets a dict
