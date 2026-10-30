# Proposal

## Why

Drive's query language quotes strings in single quotes and escapes `'` and `\` with a backslash.
`DriveAPI.search_files` escaped only `'` in `name` and `full_text`, and put `mime_type`,
`parent_id` and `folder_id` between quotes unescaped. On 2026-10-30 a review of mini-webui's
folder picker found that a search for `abc\` sends `name contains 'abc\'`, which Google refuses.
A quote in a `mime_type` or folder id ends its string early in the same way.

## What Changes

- One helper, `_quote`, escapes `\` and then `'`, and every string a Drive query embeds goes
  through it: `name`, `full_text`, `mime_type`, `parent_id` in `search_files`, `folder_id` in
  `list_files`, and each folder of `list_folder_tree`.
- No tool, argument or answer changes, so the change has no spec delta.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None.

## Impact

`google_workspace_mcp/drive/drive_api.py` and `tests/test_drive_server.py`.
