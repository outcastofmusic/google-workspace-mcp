# Tasks

- [x] 1.1 Add `_quote` and use it for every string a Drive query embeds. Verify: a test that a name ending in `\` and holding `'`, a mime type and a parent id holding `'`, and a `list_files` folder id holding `'` are escaped; it fails on the code before.
  - Done: `test_query_strings_are_quoted`, which failed before; ruff and 698 tests pass.
