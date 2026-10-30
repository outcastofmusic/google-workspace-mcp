# Design

## Context

The Docs server is the model: `docs_api.DocsAPI` wraps the API through `core.get_service`,
`create_document` moves a new file into `folder_id` with `place_in_folder` (trashing it when the
move fails), writes take `required_revision_id`, and tools use `core=True` for the small toolset.
`read_file_text` already extracts a native deck's text through Drive export. The client scope
checks the parameters in `runtime.SCOPED_FILE_PARAMS` and the create parents in `_CREATE_PARENT`.

## Goals / Non-Goals

**Goals:** an agent turns an outline into a deck in a project folder in one call, reads a deck
in bounded text, and edits slide text safely.

**Non-Goals:** images, charts, tables, themes as tools; rich text in bodies; export.

## Decisions

| Date | Decision | Alternative rejected |
|---|---|---|
| 2026-10-25 | Build slides from the deck's own layouts by predefined type (`TITLE`, `TITLE_AND_BODY`, `SECTION_HEADER`, `TITLE_ONLY`, `BLANK`), filling placeholders found through `placeholderIdMappings` | Creating text boxes at fixed coordinates, which ignores the theme and breaks on other page sizes |
| 2026-10-25 | One `batchUpdate` per call: create slides with placeholder id mappings, insert text, then `createParagraphBullets` and indent per nesting level | One request per slide, which multiplies quota use and can leave a deck half built |
| 2026-10-25 (apply) | Speaker notes go in a second `batchUpdate`, after a read of the new slides' notes ids (`fields` limited to them) and guarded by that read's revision. A deck built from an outline is trashed when any step after its creation fails | Notes in the first batch: Slides assigns the notes shape id only when the slide exists, and `createSlide` does not return it |
| 2026-10-25 (apply) | Nesting comes from leading tabs, which `createParagraphBullets` reads as the level and removes; runs are bulleted last first, so the removed tabs do not shift the ranges still to send. `deleteParagraphBullets` runs first, so plain lines stay plain in themes that bullet every body paragraph | Indenting with `updateParagraphStyle`, which sets the indent but not the bullet's nesting level |
| 2026-10-25 (apply) | `read_presentation` pages by slide with `max_chars` and `offset`, and returns `truncated`, `next_offset` and `total_items` through `cap_items`, as the other read tools do; one slide larger than the budget has its text cut and `cut: true` | A separate `start_slide`/`next_slide` pair, which differs from every other tool |
| 2026-10-25 | Bodies are plain lines with `- ` bullets | Markdown parsing as in Docs, which grows the schema and the code for little gain on slides |
| 2026-10-25 | Writes take `revision_id` and send it as `writeControl.requiredRevisionId` | Last write wins, which overwrites the user's edit in the browser |
| 2026-10-25 | `read_presentation` returns text by slide with a character budget and the next slide index | Returning the raw API object, which is 10-50x larger and floods a 200k context |
| 2026-10-25 | No new OAuth scope: the `drive` scope in every token covers the Slides API | Adding `presentations`, which would make every account log in again |

**Untrusted content.** Deck text is data the agent reads; it goes back as plain strings inside
the result, never into a description or a system message. The budget bounds what a hostile deck
can push into the context. Writes are mutating tools, so mini-web-ui asks before each, and the
client scope keeps them inside the project's folders.

## Risks / Trade-offs

- [A theme without a `TITLE_AND_BODY` layout] → the call fails naming the layouts the deck has;
  the model can pass one of them.
- [Placeholder text of the layout shows on an empty body] → a slide with no body uses `TITLE`.
- [Slides API quota] → one batch per call, two when slides carry notes; at most 100 slides per call.
- [Combined server names] → the Slides tool is `replace_text_in_deck`, so Docs' `replace_all_text`
  keeps its name in `gworkspace-mcp`. The combined server counts shared names over every tool a
  server declares (`runtime.declared_tools`), so a prefix no longer changes with the toolset.
- [Slides API not enabled] → the `drive` scope covers Slides, but the OAuth client's Cloud project
  must enable the Google Slides API; until then calls fail with Google's 403 naming the API. The
  README says so.

## Review fixes, 2026-10-25

An independent review reproduced these, now fixed and tested:

| Finding | Fix |
|---|---|
| `batch_update` could name a file outside the scope inside a request (`createSheetsChart.spreadsheetId`, a Drive `createVideo`) | The scope check reads every file id in `requests` (`runtime._request_files`) as it reads parameters |
| A connect failure after the slides batch made `run_tool` repeat all of `add_slides`, adding the slides twice; a failed notes step said nothing was written | A failure after a write raises `PartlyWritten`, a `GoogleCoreError` that `run_tool` never repeats, naming the slides added |
| `create_presentation_from_outline` moved the deck into the folder after building; a failed build with a failed trash left a deck in My Drive | The move comes first; the error says whether the deck was trashed |
| `_fit` measured escaped JSON, so a Greek slide lost its whole body; 4,000 small shapes took 3 s | Characters measured as sent, cuts found by binary search, other shapes dropped in one step |
| `cap_items` counted a Greek letter as six characters | It measures with `ensure_ascii=False`, for every server |
| The deck's title and layout names were outside the budget | Title cut at 300 characters, 30 layouts of 40 characters, and the rest of the budget goes to slides |
| The combined server named Docs' tool by toolset | Renamed the Slides tool and count names over declared tools |

## Migration Plan

Additive: a new entry point and new tools. Clients add `gslides-mcp` or use `gworkspace-mcp`.
