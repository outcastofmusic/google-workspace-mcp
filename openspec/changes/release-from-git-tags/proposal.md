# Proposal

## Why

The README and `.mcp.json` install `google-workspace-suite-mcp` from PyPI, and the release
workflow publishes to PyPI on a tag. That name belongs to the upstream project: a client
following the README runs upstream's 0.7.0, without this fork's safety policy, client scope,
busy sync, Drive document text or Slides, and the fork's publish job cannot succeed. On
2026-10-26 the user chose to release from git tags.

## What Changes

- Version 0.8.0.
- `.mcp.json` and the README install from `git+https://github.com/outcastofmusic/google-workspace-mcp@v0.8.0`
  (`uvx --from`, `pip`, `pipx`), and say why.
- `release.yml` keeps the build and the tag-version check, and replaces the PyPI publish with a
  GitHub release that carries the sdist and wheel.
- CLAUDE.md's release steps match.
- No tool, dependency or behaviour changes, so the change has no spec delta.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None.

## Impact

`pyproject.toml`, `.mcp.json`, `README.md`, `CLAUDE.md`, `.github/workflows/release.yml`.
