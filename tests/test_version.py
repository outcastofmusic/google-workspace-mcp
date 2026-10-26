"""The package's version string matches the one a release tag is checked against."""

import re
from pathlib import Path

import google_workspace_mcp


def test_version_matches_pyproject():
    text = (Path(__file__).parent.parent / "pyproject.toml").read_text()
    declared = re.search(r'^version = "([^"]+)"', text, re.M).group(1)
    assert google_workspace_mcp.__version__ == declared
