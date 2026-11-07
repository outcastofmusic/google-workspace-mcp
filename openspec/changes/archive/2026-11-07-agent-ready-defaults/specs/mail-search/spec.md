## ADDED Requirements

### Requirement: Short message records from search
`search_messages` and `search_messages_all_accounts` SHALL return each message as a record with
the keys `id`, `threadId`, `labelIds`, `subject`, `from`, `to`, `date` and `snippet`, plus `cc`
when the message has one, under the same names and values `get_message` returns. They SHALL NOT
return `payload`, `sizeEstimate`, `historyId` or `internalDate`, and SHALL take no `format`
parameter.

#### Scenario: Ten messages fit
- **WHEN** a search returns the 10 messages recorded on 2026-11-07, each with 34 headers
- **THEN** the text result holds at most 8,000 characters

#### Scenario: Same fields as get_message
- **WHEN** a message has the headers `Subject: Hi`, `From: A <a@x.org>`, `To: b@x.org` and `Date: Fri, 06 Nov 2026 07:39:51 -0800`
- **THEN** its record holds those four values under `subject`, `from`, `to` and `date`, and holds no `cc`

### Requirement: Order, paging and accounts kept
The search tools SHALL keep `next_page_token`, the envelope, the newest-first order of the
all-accounts tool, and the `account` key on each all-accounts record.

#### Scenario: All accounts
- **WHEN** two accounts each return a message
- **THEN** both records carry their `account`, newest first
