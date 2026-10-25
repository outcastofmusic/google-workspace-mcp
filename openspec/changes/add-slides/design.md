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
- [Slides API quota] → one batch per call.

## Migration Plan

Additive: a new entry point and new tools. Clients add `gslides-mcp` or use `gworkspace-mcp`.
