"""Local file-path guard shared by every tool that reads or writes local files.

Agents feed these tools paths that may come from untrusted content (an email,
a shared doc), so a path is resolved (``~`` and symlinks) and refused when it
points into the Google token store, or outside ``GOOGLE_MCP_FILE_ROOT`` when
that env var is set. Without that env var, hidden paths (any ``.``-prefixed
component, e.g. ``~/.ssh``, ``~/.aws``, ``.env``) and system directories are
refused too, so an injected instruction cannot attach a key file to an email or
write into a dotfile; ``GOOGLE_MCP_ALLOW_HIDDEN_PATHS=1`` lifts that.
Writes refuse to replace an existing file unless asked.
"""

from __future__ import annotations

import os
from pathlib import Path

import google_auth_core as core
from google_auth_core import store


def _within(path: str, root: str) -> bool:
    # Case-folded because macOS volumes are case-insensitive by default:
    # ~/.Google is the same directory as ~/.google.
    path, root = path.casefold(), root.casefold()
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _forbidden_roots() -> list[str]:
    # Both the configured store and the default ~/.google, read at call time
    # so a relocated store (tests, custom config) is still covered.
    return [os.path.realpath(store.GOOGLE_CONFIG_DIR), os.path.realpath(Path.home() / ".google")]


_SYSTEM_ROOTS = ("/etc", "/private/etc", "/proc", "/sys", "/dev", "/boot")


def _hidden_allowed() -> bool:
    flag = os.getenv("GOOGLE_MCP_ALLOW_HIDDEN_PATHS", "")
    return flag.strip().lower() in ("1", "true", "yes", "on")


def _sensitive(real: str) -> bool:
    if any(_within(real, root) for root in _SYSTEM_ROOTS):
        return True
    return any(part.startswith(".") and part not in (".", "..") for part in Path(real).parts)


def _resolve(path: str) -> str:
    if not path:
        raise core.InvalidArgumentError("a local path is required")
    real = os.path.realpath(os.path.expanduser(path))
    if any(_within(real, root) for root in _forbidden_roots()):
        raise core.InvalidArgumentError(f"refusing to access the Google token store: {path}")
    root = os.getenv("GOOGLE_MCP_FILE_ROOT", "").strip()
    if root:
        real_root = os.path.realpath(os.path.expanduser(root))
        if not _within(real, real_root):
            raise core.InvalidArgumentError(
                f"{path} is outside GOOGLE_MCP_FILE_ROOT ({real_root})"
            )
    elif _sensitive(real) and not _hidden_allowed():
        raise core.InvalidArgumentError(
            f"refusing hidden or system path {path}; set GOOGLE_MCP_FILE_ROOT to a folder "
            "you choose, or GOOGLE_MCP_ALLOW_HIDDEN_PATHS=1"
        )
    return real


def safe_path(path: str, *, write: bool = False, overwrite: bool = False) -> str:
    """Return the absolute path, or raise InvalidArgumentError.

    Checks run on the symlink-resolved path, but the caller gets the path as
    named so attachment and upload names don't change to the link target's.
    Reads must name an existing file; writes refuse an existing file unless
    ``overwrite`` is True.
    """
    real = _resolve(path)
    if write:
        if os.path.isdir(real):
            raise core.InvalidArgumentError(f"{path} is a directory; give a file path")
        if os.path.exists(real) and not overwrite:
            raise core.InvalidArgumentError(
                f"{path} already exists; pass overwrite=True to replace it"
            )
    elif not os.path.isfile(real):
        raise core.InvalidArgumentError(f"no such file: {path}")
    return os.path.abspath(os.path.expanduser(path))


def free_path(path: str) -> str:
    """``path``, or ``name (n).ext`` beside it when taken; for default temp-dir
    downloads, where a clash should pick a new name rather than fail."""
    stem, ext = os.path.splitext(path)
    n = 1
    while os.path.lexists(path):
        path = f"{stem} ({n}){ext}"
        n += 1
    return path


def safe_dir(path: str) -> str:
    """Resolve a directory to write into, applying the same refusals as safe_path."""
    real = _resolve(path)
    if os.path.exists(real) and not os.path.isdir(real):
        raise core.InvalidArgumentError(f"{path} is a file; give a directory")
    return os.path.abspath(os.path.expanduser(path))


def safe_paths(paths: list[str] | None) -> list[str] | None:
    """safe_path over a list of files to read (e.g. mail attachments)."""
    if paths is None:
        return None
    if isinstance(paths, str):
        paths = [paths]
    return [safe_path(p) for p in paths]
