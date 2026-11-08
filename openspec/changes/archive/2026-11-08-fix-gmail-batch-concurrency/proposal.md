# Proposal

## Why

On 2026-11-08 `search_messages_all_accounts` with `max_results_per_account` 50 returned 17 of 50
records holding only `id` and `account`. Upstream `get_messages_batch` sends all 50 `messages.get`
calls in one batch, and Gmail refused 17 with 429 "Too many concurrent requests for user". The
batch kept each refusal as an `error` key, and `project_message_record` dropped it, so the agent
saw empty records with no reason.

Google publishes no number for the per-user concurrent limit. Its handle-errors guide says not to
send batches larger than 50 and to retry at least one second after the error. Batches of 20
returned 0 refusals for the same 50 messages.

## What Changes

- `GmailChangesAPI.get_messages_batch` sends the gets in batches of 20, retries the 429-refused
  ones once after 1 to 2 seconds, and returns a message that still fails as `{"id", "error"}`.
- `project_message_record` returns such a message as `id`, `error` and `account`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `mail-search`: a record the fetch could not get carries `error`.

## Impact

`google_workspace_mcp/gmail/changes_api.py`, the two search tools' descriptions and
`tests/test_change_feed.py`. Release v0.12.1.
