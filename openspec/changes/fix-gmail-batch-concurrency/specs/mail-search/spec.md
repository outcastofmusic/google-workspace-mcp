## ADDED Requirements

### Requirement: Fetch errors kept
The search tools SHALL fetch messages in batches of at most 20, SHALL retry a message Gmail
refuses with 429 once after at least one second, and SHALL return a message that still fails as
a record with `id`, `error` (the HTTP status and reason) and, from the all-accounts tool,
`account`.

#### Scenario: Refused once
- **WHEN** Gmail refuses 2 of 10 messages with 429 and then returns them
- **THEN** all 10 records carry their fields and none carries `error`

#### Scenario: Still failing
- **WHEN** Gmail refuses a message with 429 twice
- **THEN** its record holds its `id` and an `error` naming 429
