## ADDED Requirements

### Requirement: Tagged tools stay unregistered under the flag
When `GOOGLE_MCP_NO_SEND_DELETE` is set at import to any value other than empty, `0`, `false`, `no` or `off` (case and surrounding spaces ignored), a server SHALL register no tool tagged `sends_or_deletes`. Every untagged tool SHALL register as it does without the flag. The flag fails closed, so a mistyped value hides the tools.

#### Scenario: A mistyped value
- **WHEN** `gmail-mcp` starts with `GOOGLE_MCP_NO_SEND_DELETE=2`
- **THEN** `tools/list` holds no tagged Gmail tool

#### Scenario: Gmail with the flag
- **WHEN** `gmail-mcp` starts with `GOOGLE_MCP_NO_SEND_DELETE=1`
- **THEN** `tools/list` holds no tagged Gmail tool, and holds `modify_labels`, `batch_modify_labels`, `archive_message` and `create_label`

#### Scenario: Drive with the flag
- **WHEN** `gdrive-mcp` starts with `GOOGLE_MCP_NO_SEND_DELETE=1`
- **THEN** `tools/list` holds no tagged Drive tool, and holds `move_file`, `create_folder` and `upload_file`

#### Scenario: Without the flag
- **WHEN** a server starts with `GOOGLE_MCP_NO_SEND_DELETE` unset
- **THEN** `tools/list` holds every tagged tool it held before this change

### Requirement: The tagged Gmail tools
The Gmail server SHALL tag exactly these 21 tools: `send_message`, `reply_to_message`, `forward_message`, `draft_reply`, `create_draft`, `update_draft`, `send_draft`, `delete_draft`, `trash_message`, `untrash_message`, `delete_message`, `batch_trash_messages`, `batch_untrash_messages`, `batch_delete_messages`, `mark_as_spam`, `unmark_spam`, `update_label`, `delete_label`, `create_filter`, `delete_filter`, `block_sender`.

#### Scenario: The Gmail set
- **WHEN** a test reads the Gmail tools tagged `sends_or_deletes`
- **THEN** it finds these 21 names and no other

### Requirement: The tagged Drive tools
The Drive server SHALL tag exactly these 8 tools: `share_file`, `unshare_file`, `trash_file`, `untrash_file`, `delete_file`, `batch_trash_files`, `batch_delete_files`, `delete_comment`.

#### Scenario: The Drive set
- **WHEN** a test reads the Drive tools tagged `sends_or_deletes`
- **THEN** it finds these 8 names and no other

### Requirement: Label tools cannot trash or mark spam under the flag
While `GOOGLE_MCP_NO_SEND_DELETE` is on, `modify_labels` and `batch_modify_labels` SHALL refuse a call that adds or removes the `TRASH` or `SPAM` label, in any letter case, before reaching Gmail. Adding `TRASH` trashes a message, so without this the label tools would bypass the gate.

#### Scenario: Trash through a label
- **WHEN** `modify_labels` is called with `add_label_ids` holding `TRASH` while the flag is on
- **THEN** the call fails with an error naming the flag, and Gmail receives no request
