## ADDED Requirements

### Requirement: Short event records in list tools
`list_events`, `search_events`, `get_recurring_event_instances` and `list_events_all_accounts`
SHALL return each event as a record of `id`, `summary`, `start` and `end`. A record SHALL hold
`status` (when not `confirmed`), `location`, `description`, `attendees`, `organizer`, `video`,
`recurring_event_id` and `event_type` (when not `default`) only when the event has them, and no
other key.

#### Scenario: A week of meetings
- **WHEN** `list_events` returns the 15 events of 9 November 2026 on the user's work calendar, whose raw resources gave a 21,423-character result (21,412 in the test fixture, with ids, emails, links and dates replaced)
- **THEN** each record holds only the keys above, and the tool's text result holds at most 6,000 characters

### Requirement: Record times
`start` and `end` SHALL be the event's `dateTime` string, or its `date` string for an all-day
event.

#### Scenario: An all-day event
- **WHEN** an event's start is `{"date": "2026-11-12"}`
- **THEN** its record's `start` is `"2026-11-12"`

### Requirement: Description text
`description` SHALL be the description as plain text, with HTML tags removed, entities decoded and
runs of white space made one space, cut at 200 characters with `…` after a cut.

#### Scenario: A long description
- **WHEN** an event's description holds 2,000 characters of plain text
- **THEN** its record's `description` holds its first 200 characters followed by `…`

#### Scenario: An HTML description
- **WHEN** a description is `<p>Room <b>4</b></p><p>Bring&nbsp;slides</p>`
- **THEN** its record's `description` is `Room 4 Bring slides`

### Requirement: People and links
`attendees` SHALL be `{email, response}` pairs, with `name` when Google gives a display name.
`organizer` SHALL be an email, shown only when the organizer is not the calendar's owner. `video`
SHALL be the Meet link, or else the first video entry point of the event's conference.

#### Scenario: A Zoom meeting
- **WHEN** an event has no Meet link and its conference has a video entry point `https://zoom.us/j/1`
- **THEN** its record's `video` is `https://zoom.us/j/1`

### Requirement: Paging and order kept
The list tools SHALL keep `next_page_token`, the `ok` envelope and their sort order.
`list_events_all_accounts` SHALL keep the `account` key on each record.

#### Scenario: Two accounts
- **WHEN** `list_events_all_accounts` reads two accounts
- **THEN** the records are sorted by start, and each carries its `account`

### Requirement: Whole events from get_event
`get_event` SHALL return Google's full event resource. Each list tool's description SHALL say that
it returns short records and that `get_event` returns the whole event.

#### Scenario: The fields a record drops
- **WHEN** an agent needs an event's `htmlLink` after `list_events`
- **THEN** `get_event` with the record's `id` returns it
