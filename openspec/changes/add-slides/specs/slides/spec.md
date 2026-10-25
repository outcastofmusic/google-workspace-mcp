## ADDED Requirements

### Requirement: Slides server
The distribution SHALL ship `gslides-mcp`, an MCP server for Google Slides that follows the tool
contract of the other servers: `account` first, required ids keyword-only, every Google call
through `get_api` and `run_tool`, results through `ok`, the read-only gate, the safety policy and
the client scope. It SHALL expose the common tools, and `gworkspace-mcp` SHALL include its tools.
Under `GOOGLE_MCP_READONLY` only `read_presentation` SHALL be registered.

#### Scenario: Read-only mode
- **WHEN** `gslides-mcp` starts with `GOOGLE_MCP_READONLY=1`
- **THEN** `list_tools` holds `read_presentation` and the common tools, and no tool that writes

### Requirement: Reading a deck
`read_presentation` SHALL return the deck's title, the names of its layouts, and for each slide
its object id, layout name, title text, body text and speaker notes text, in slide order. Text of
other shapes SHALL follow the body, marked by shape. The result SHALL stop at `max_chars`
(default 20,000) with `truncated: true` and the index of the first slide left out.

#### Scenario: Long deck
- **WHEN** a deck of 80 slides holds 50,000 characters and the model calls `read_presentation` with no `max_chars`
- **THEN** the result holds the slides that fit in 20,000 characters, `truncated: true`, and the index of the next slide

### Requirement: Building a deck from an outline
`create_presentation_from_outline` SHALL create a deck titled `title` whose slides follow
`slides`, each with a `title`, an optional `body`, optional `notes` and an optional `layout`
(default `TITLE_AND_BODY`, or `TITLE` for a slide with no body). A body line that starts with
`- ` SHALL become a bullet, nested one level per two spaces of indent before the dash; any other
line SHALL be a paragraph. With `folder_id` the deck SHALL be moved into that folder, and trashed
when the move fails. `add_slides` SHALL add slides from the same items to an existing deck, at
the end or at `position`. An unknown layout SHALL fail the call, naming the deck's layouts.

#### Scenario: Status deck in a project folder
- **WHEN** the model calls `create_presentation_from_outline` with title `DDB status`, `folder_id` of the DDB folder, and three slides whose bodies hold `- done` and `  - OCR timeout fixed`
- **THEN** the deck sits in the DDB folder with three slides, and the body of each shows a bullet with a nested bullet

### Requirement: Changing a deck
`update_slide_text` SHALL replace the title, body or notes of one slide, each only when given.
`replace_all_text` SHALL replace every match in the deck and return how many it changed.
`delete_slide` SHALL delete one slide. `batch_update` SHALL send raw Slides requests. Each write
that follows a read SHALL accept the read's `revision_id` and fail, changing nothing, when the
deck changed since. `update_slide_text`, `replace_all_text`, `delete_slide` and `batch_update`
SHALL be marked destructive.

#### Scenario: Stale write
- **WHEN** the model read a deck, the user edited it in the browser, and the model calls `update_slide_text` with the old `revision_id`
- **THEN** the call fails with an error saying the deck changed, and the slide keeps the user's edit
