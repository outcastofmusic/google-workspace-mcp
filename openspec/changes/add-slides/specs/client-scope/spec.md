## MODIFIED Requirements

### Requirement: Drive folders in scope
With `drive_folders` set, a Drive, Docs, Sheets or Slides call SHALL fail with an error starting
`Outside scope:` that names the file when any file or folder it names (`file_id`, `file_ids`,
`folder_id`, `parent_id`, `new_parent_id`, `document_id`, `spreadsheet_id`, `presentation_id`, also as keys of the
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

### Requirement: Creating inside the scope
With `drive_folders` set, a call that creates a file SHALL name a parent inside the scope
(`parent_id` for `upload_file`, `create_folder`, `copy_file` and each item of
`batch_create_folders`, `folder_id` for
`create_document`, `create_document_from_markdown`, `create_spreadsheet`, `create_presentation` and
`create_presentation_from_outline`), and SHALL fail
with an error naming that parameter when it names none. `create_document` and
`create_spreadsheet` SHALL accept an optional `folder_id` with or without a scope.

#### Scenario: New Sheet in the project folder
- **WHEN** the scope holds folder `DDB` and the model calls `create_spreadsheet` with `folder_id` set to `DDB`
- **THEN** the spreadsheet is created inside `DDB`

#### Scenario: New Doc with no folder
- **WHEN** the scope holds folder `DDB` and the model calls `create_document` with no `folder_id`
- **THEN** the call fails with an error saying to pass `folder_id` inside the scope, and nothing is created

#### Scenario: Deck outside the project
- **WHEN** the scope holds folder `DDB` and the model calls `read_presentation` on a deck in `doValue/`
- **THEN** the call fails with `Outside scope:` naming the deck
