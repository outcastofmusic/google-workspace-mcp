# Design

## Context

The list tools pass `page["items"]` from `CalendarChangesAPI` straight to `ok()`, and the MCP
library serialises the result with `indent=2`. On the user's work calendar, 15 events of one day
took 21,423 characters. Per field, over those 15: `description` 4,508, `htmlLink` 1,643, `start`
and `end` 1,140 each, `conferenceData` 943, `creator` and `organizer` 810 each, `iCalUID` 602.
The main clients run a local model with a 200k context, and Eurycleia cuts a result at 50,000
characters. A probe of the projection on the 70 recorded events of 7 to 15 November (audit,
2026-11-07) measured the result text as the client sees it, with the library's `indent=2`.

## Goals / Non-Goals

**Goals:** a week of events in one call of a few KB; every field still reachable through
`get_event`; no change to paging, sorting or the envelope.

**Non-Goals:** compact JSON serialisation (the library's `indent=2` applies to every tool of
every server and is its own change); changing `get_event` or the write tools; a `detail` switch.

## Decisions

| Date | Decision | Alternative rejected |
|---|---|---|
| 2026-11-07 | Short records always, `get_event` for the whole event | A `detail="full"` parameter: one more schema field in every turn's context for a need `get_event` already meets |
| 2026-11-07 | Project in this package, in `calendar/server.py` | A per-tool output filter in Eurycleia: every other client (mini-web-ui chats) would keep paying for the raw resource, and Eurycleia would have to know Google's event schema |
| 2026-11-07 | `start` and `end` as one string each | Keeping `{dateTime, timeZone}`: the offset in `dateTime` already fixes the instant, and an all-day event's `date` string says it is all-day |
| 2026-11-07 | Descriptions as plain text (stdlib `html.parser`), cut at 200 characters with `…` (audit) | Raw HTML cut at 300: 63 of the 70 recorded events are Morgen busy blocks with a 316-character HTML description, which kept the 15-event day at 7,692 characters; as text they are 151 characters and the day is 5,502. Also rejected: dropping descriptions (3,094 characters), which loses the first lines an agenda question needs, such as a room or a dial-in |
| 2026-11-07 | `video` from `hangoutLink`, else the conference's first `video` entry point (audit) | `meet` from `hangoutLink` alone: a Zoom or Teams link lives only in `conferenceData`, which the record drops |
| 2026-11-07 | Attendees keep `name` when Google gives one (audit) | Email only: "who is in my 3 o'clock" reads names |
| 2026-11-07 | Keep `recurring_event_id` and a non-default `event_type` | Dropping them: `get_recurring_event_instances` needs the series id, and `outOfOffice` or `focusTime` changes what an event means for the agenda |

## Untrusted content

Event titles, descriptions and locations come from other people's invitations and may carry
injected instructions. The projection copies them as data, cut shorter for the description, and
adds nothing a model would read as an instruction. It neither widens nor narrows what a tool
exposes beyond dropping fields; the full resource stays behind `get_event`, as today.

## Risks

- A client that read `htmlLink` or `conferenceData` from a list result breaks. → The proposal
  marks it **BREAKING**; the README names `get_event`. Neither known client reads them.
