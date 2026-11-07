# Design

## Decisions

| Date | Decision | Alternative rejected |
|---|---|---|
| 2026-11-07 | Message records reuse `get_message`'s key names (`threadId`, `labelIds`, `from`) | snake_case names: the same message would read differently from two tools |
| 2026-11-07 | Project after the fetch, in `changes_api.project_message_record` | Gmail's `metadataHeaders` filter: `gmail_cli.search_with_details` does not pass it, and the fetch is one batch either way |
| 2026-11-07 | Drop the search tools' `format` parameter | Keep it with records for every format: `full` would still fetch bodies the record discards |
| 2026-11-07 | Read the zone from `get_user_settings()["timezone"]` once per account and cache it beside the client | Ask the model to pass a zone: the agent instructions already say "the person's time zone" and the model still sent naive times |
| 2026-11-07 | `find_available_slots` reports each slot in the zone it used | UTC slots as the library returns them: a model had to convert `07:00+00:00` to 09:00 itself |
| 2026-11-07 | Default page 40,000 characters, and `read_files`' 400,000 total budget drops to it | Keep `read_files` at 100,000 per file and 400,000 in all: no client with a 50,000 cap can take it | 45,000: the JSON envelope, escapes and a `hint` add up to a few thousand characters, and the cap is in characters of the whole result |

## Risks

- A client that read `payload` from a search result breaks. Eurycleia's agents read `subject`,
  `from` and `snippet`, and `get_message` still returns the whole message.
- The settings read adds one Calendar call to the first zone-less call per account per process.
