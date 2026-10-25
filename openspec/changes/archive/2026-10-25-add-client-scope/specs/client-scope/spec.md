# Spec Delta

## Purpose
Lets a client limit one tool call to some Google accounts and some Drive folders, so a project in
a chat app reaches only its own accounts and files whatever arguments the model writes.

## ADDED Requirements

### Requirement: Scope in the request
A client MAY attach a scope to a tool call in the request's `_meta` under the key
`io.github.outcastofmusic.workspace/scope`, as an object with an optional `accounts` list of
emails or aliases and an optional `drive_folders` list of `{"account", "id"}` objects. The
server SHALL apply the scope to that call only. A scope that is not an object of that shape SHALL
fail the call with an error naming the problem. A call with no scope SHALL behave as without
this capability, unless `GOOGLE_MCP_REQUIRE_SCOPE` is set to `1`, `true`, `yes` or `on`, in which
case the server SHALL refuse it.

#### Scenario: No scope
- **WHEN** a client calls `read_file_text` with no `_meta` scope
- **THEN** the call behaves as it did before this change

#### Scenario: Scope required
- **WHEN** `GOOGLE_MCP_REQUIRE_SCOPE=1` and a client calls any tool with no scope
- **THEN** the call fails with an error saying a scope is required

### Requirement: Accounts in scope
With `accounts` set, a call that resolves to an account outside the list SHALL fail with an
error starting `Outside scope:` that names the account, before any Google request. Tools that
work across accounts SHALL use only the listed accounts, and `list_accounts` SHALL list only
them.

#### Scenario: Personal account from a work project
- **WHEN** the scope lists `work` and the model calls `list_messages` with account `personal`
- **THEN** the call fails with `Outside scope: account me@personal.com`

#### Scenario: Search across accounts
- **WHEN** the scope lists `work` and the client calls `search_messages_all_accounts`
- **THEN** only the work account is searched and the result's `accounts` lists only it

### Requirement: Drive folders in scope
With `drive_folders` set, a Drive, Docs or Sheets call SHALL fail with an error starting
`Outside scope:` that names the file when any file or folder it names (`file_id`, `file_ids`,
`folder_id`, `parent_id`, `new_parent_id`, `document_id`, `spreadsheet_id`, also as keys of the
`items` of the batch tools) is neither one of the scope's folders for the call's account nor below
one of them through its parent folders. A
`drive_id` argument, `list_drives` and `list_changes` SHALL be refused, since they reach the whole
Drive. A file that has no parent in the
account's view, such as one only shared with the user, is outside the scope.

#### Scenario: File in a subfolder
- **WHEN** the scope holds folder `DDB` and the model reads a document in `DDB/specs`
- **THEN** the call runs

#### Scenario: File elsewhere in the same Drive
- **WHEN** the scope holds folder `DDB` and the model reads a document in `doValue/`
- **THEN** the call fails with `Outside scope:` naming the document, and no content is returned

#### Scenario: Moving a file out
- **WHEN** the scope holds folder `DDB` and the model calls `move_file` on a file in `DDB` with `new_parent_id` set to a folder outside it
- **THEN** the call fails and the file stays where it was

### Requirement: Search and listing inside the scope
With `drive_folders` set, `search_files`, `list_files`, `search_files_all_accounts` and
`list_folder_tree` SHALL return only files inside the scope. `list_files` with no folder and
`search_files` with no parent SHALL search the whole account and drop results outside the
scope, so a page MAY hold fewer than `page_size` files while `next_page_token` still continues.

#### Scenario: Search finds files in and out of scope
- **WHEN** the scope holds folder `DDB` and `search_files` for `budget` matches one file in `DDB` and one in `Personal`
- **THEN** the result holds only the file in `DDB`

### Requirement: Creating inside the scope
With `drive_folders` set, a call that creates a file SHALL name a parent inside the scope
(`parent_id` for `upload_file`, `create_folder`, `copy_file` and each item of
`batch_create_folders`, `folder_id` for
`create_document`, `create_document_from_markdown` and `create_spreadsheet`), and SHALL fail
with an error naming that parameter when it names none. `create_document` and
`create_spreadsheet` SHALL accept an optional `folder_id` with or without a scope.

#### Scenario: New Sheet in the project folder
- **WHEN** the scope holds folder `DDB` and the model calls `create_spreadsheet` with `folder_id` set to `DDB`
- **THEN** the spreadsheet is created inside `DDB`

#### Scenario: New Doc with no folder
- **WHEN** the scope holds folder `DDB` and the model calls `create_document` with no `folder_id`
- **THEN** the call fails with an error saying to pass `folder_id` inside the scope, and nothing is created

### Requirement: Scope in the audit log
When `GOOGLE_MCP_AUDIT_LOG` is set, each mutating call's entry SHALL include the scope it ran
under, or `null` when it had none.

#### Scenario: Audited scoped write
- **WHEN** a scoped client creates a Doc and the audit log is on
- **THEN** the entry for `create_document_from_markdown` holds the scope's accounts and folders
