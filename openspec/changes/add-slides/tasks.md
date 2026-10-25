# Tasks

## 1. Server and API wrapper

- [ ] 1.1 Add `slides/slides_api.py` (`SlidesAPI` on Slides v1 through `core.get_service`): read, create, outline build, add slides, update slide text, replace all text, delete slide, batch update, all writes with `requiredRevisionId`. Verify: unit tests with a mocked service check the batch requests for an outline with nested bullets, notes and a `TITLE` slide, and the move into `folder_id` with trash on failure.
- [ ] 1.2 Add `slides/server.py` with the eight tools on the tool contract, `drive_scoped=True`, `core=True` on the four the proposal names, destructive marks as listed; entry point `gslides-mcp`; register the module in `gworkspace-mcp`. Verify: `tests/test_slides_server.py` with `MagicMock(spec=SlidesAPI)` for each tool, the Read-only mode scenario, the drift test, `test_required_ids_are_required_in_schema`, and the integration test's list of servers.
- [ ] 1.3 `read_presentation` text, layouts and the character budget. Verify: the Long deck scenario on a fixture deck JSON, and an unknown-layout error naming the layouts.

## 2. Scope

- [ ] 2.1 Add `presentation_id` to `SCOPED_FILE_PARAMS` and the two create tools to `_CREATE_PARENT`. Verify: scope tests for the Deck outside the project scenario, a create without `folder_id` under a scope, and a create inside it.

## 3. Docs and gates

- [ ] 3.1 README (the server, its tools, the outline format, the `.mcp.json` example) and CLAUDE.md (architecture block and the scope parameters). Verify: the README's outline example parses into the requests the unit test expects.
- [ ] 3.2 Run `ruff check .` and `pytest`; push, open the PR, and verify CI passes on Python 3.10-3.13.
- [ ] 3.3 Run the opt-in live test (`GOOGLE_MCP_LIVE=1`) that builds and deletes a deck, when a token is available; otherwise note that it was not run.
