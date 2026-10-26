# Tasks

- [x] 1.1 Bump the version to 0.8.0 and point `.mcp.json` and the README at the git tag. Verify: no PyPI install line or 0.7.0 pin is left.
- [x] 1.2 Replace the PyPI publish job with a GitHub release of the built files. Verify: the workflow parses and keeps the tag-version check.
- [ ] 1.3 Merge, tag `v0.8.0` on `main`, and confirm the release job attaches the wheel and that `uvx --from git+…@v0.8.0 gworkspace-mcp --help` starts.
