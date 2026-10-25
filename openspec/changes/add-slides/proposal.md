# Proposal

## Why

The user asked on 2026-10-25 that agents in mini-web-ui build Docs, Slides and Sheets saved back
to Drive. Docs and Sheets have servers; Slides has none, and `read_file_text` reads a deck's text
but nothing writes one. An agent that drafts a pitch or a status deck for a client project has no
way to produce it.

## What Changes

- A sixth Google server, `gslides-mcp` (`google_workspace_mcp/slides/`), on Slides API v1, with
  the same core contract, read-only gate, safety policy and client scope as the others. The
  combined `gworkspace-mcp` gains its tools.
- Tools:

| Tool | Kind | Effect |
|---|---|---|
| `read_presentation` | read-only, core | The deck's title, its layouts, and per slide its id, layout, title, body and speaker notes as text, within a character budget |
| `create_presentation_from_outline` | mutating, core | A new deck from a title and a list of slides (title, body, notes, optional layout), optionally inside `folder_id` |
| `create_presentation` | mutating | An empty deck, optionally inside `folder_id` |
| `add_slides` | mutating, core | Slides from the same outline items, at the end or at a position |
| `update_slide_text` | mutating, destructive, core | Replaces the title, body or notes of one slide |
| `replace_all_text` | mutating, destructive | Replaces every match of a text in the deck |
| `delete_slide` | mutating, destructive | Deletes one slide |
| `batch_update` | mutating, destructive | Raw Slides requests, for what the others do not cover |

- A slide body is plain lines: a line that starts with `- ` becomes a bullet, two spaces of indent
  per level below it. Writes are guarded with the revision of the read they follow, as in Docs.
- Client scope: `presentation_id` joins the parameters checked against the scope's folders, and
  the create tools need `folder_id` inside the scope.
- No new scope: the token's `drive` scope covers the Slides API, so no account logs in again.
- No new dependency: `google-api-python-client` already serves Slides v1.

Considered and left out:

- Images, charts, tables and themes as tools; `batch_update` covers them for now.
- Markdown with bold, links and nesting in bodies; plain lines with bullets keep the schema small.
- Exporting to PDF or pptx; `export_file` in the Drive server already does it.

## Capabilities

### New Capabilities

- `slides`: the Slides server and its tools.

### Modified Capabilities

- `client-scope`: `presentation_id` is checked, and the Slides create tools need a parent.

## Impact

- New `google_workspace_mcp/slides/` (`server.py`, `slides_api.py`); entry point `gslides-mcp` in
  `pyproject.toml`; the combined server registers the module.
- `core/runtime.py`: `SCOPED_FILE_PARAMS` and `_CREATE_PARENT`.
- Tests: `tests/test_slides_server.py` with `MagicMock(spec=SlidesAPI)`, the drift test, the
  required-ids schema test, the integration test's list of servers, and the scope tests.
- README, CLAUDE.md, `.mcp.json` example.
