# calendar-time-zone Specification

## Purpose
The time zone a Calendar tool assumes when the caller gives none: the account's own.

## Requirements

### Requirement: The calendar's own time zone by default
`find_available_slots`, `create_event`, `create_out_of_office` and `create_focus_time` SHALL use
the account's Calendar time zone setting when the caller gives no `timezone`, and UTC when that
setting cannot be read. A `timezone` the caller gives SHALL win.

#### Scenario: Slots in local working hours
- **WHEN** the account's time zone is Europe/Athens and `find_available_slots` runs for a free Monday with no `timezone`
- **THEN** the first slot starts at 09:00+02:00

#### Scenario: A naive start time
- **WHEN** `create_event` gets `start_time` `2026-11-10T10:00:00` and no `timezone`, and the account's time zone is Europe/Athens
- **THEN** the event is created with time zone Europe/Athens

#### Scenario: Setting unreadable
- **WHEN** the Calendar settings cannot be read
- **THEN** the tool uses UTC

#### Scenario: Explicit zone
- **WHEN** the caller passes `timezone` America/New_York
- **THEN** the tool uses America/New_York and does not read the setting
