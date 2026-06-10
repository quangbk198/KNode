# -*- coding: utf-8 -*-
"""
knode Git Hooks

Installs/uninstalls lightweight git hooks that trigger incremental re-indexing
automatically after: commit, checkout (branch switch), and merge (pull).

Hooks are installed into <project>/.git/hooks/ and are guarded by
  # knode:start / # knode:end
marker comments so they can coexist with existing user-defined hooks.

Git hooks are always POSIX sh scripts (Git for Windows uses sh.exe),
so we always emit sh-compatible code regardless of the host OS.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Marker strings used to identify the knode-managed block inside hook files
# ---------------------------------------------------------------------------
_KNODE_START = "# knode:start"
_KNODE_END   = "# knode:end"

# Git hooks that knode installs into
_HOOK_NAMES = ("post-commit", "post-checkout", "post-merge")


# ---------------------------------------------------------------------------
# Hook script templates
# ---------------------------------------------------------------------------

def _make_hook_block(project_path: str, python_exe: str) -> str:
    """Return the knode-managed block for a POSIX sh hook.

    Git hooks are always executed via sh (even on Windows via Git for Windows),
    so we always emit a POSIX sh snippet regardless of the host OS.
    On Windows we convert the Python executable path to a Unix-friendly form.
    """
    # Convert Windows backslashes to forward slashes for sh compatibility
    py   = python_exe.replace("\\", "/")
    proj = project_path.replace("\\", "/")

    return (
        f"{_KNODE_START} — knode auto re-index (do not edit this block)\n"
        f'( "{py}" -m knode index "{proj}" --quiet & ) >/dev/null 2>&1\n'
        f"{_KNODE_END}\n"
    )


def _posix_hook_header() -> str:
    return "#!/bin/sh\n"


def _get_python_exe() -> str:
    """Return the absolute path to the current Python interpreter."""
    return sys.executable


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_git_dir(project_path: str) -> Optional[Path]:
    """Walk up from project_path looking for a .git directory."""
    current = Path(project_path).resolve()
    for candidate in [current, *current.parents]:
        git_dir = candidate / ".git"
        if git_dir.is_dir():
            return git_dir
    return None


def _inject_block(existing: str, block: str) -> str:
    """Insert or replace the knode block inside an existing hook file.

    If a knode block is already present, replace it in-place.
    Otherwise append it at the end.
    """
    if _KNODE_START in existing and _KNODE_END in existing:
        before = existing[: existing.index(_KNODE_START)]
        after  = existing[existing.index(_KNODE_END) + len(_KNODE_END):]
        after  = after.lstrip("\n")
        return before + block + ("\n" if after else "") + after
    else:
        separator = "\n" if existing.endswith("\n") else "\n\n"
        return existing + separator + block


def _remove_block(existing: str) -> str:
    """Remove the knode block from a hook file, returning the remainder."""
    if _KNODE_START not in existing or _KNODE_END not in existing:
        return existing

    before = existing[: existing.index(_KNODE_START)]
    after  = existing[existing.index(_KNODE_END) + len(_KNODE_END):]
    before = before.rstrip("\n")
    return before + after


def _write_hook(hook_path: Path, content: str):
    """Write hook file and ensure it is executable."""
    hook_path.write_text(content, encoding="utf-8")
    current_mode = hook_path.stat().st_mode
    hook_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _has_knode_block(hook_path: Path) -> bool:
    """Return True if the hook file contains a knode-managed block."""
    if not hook_path.exists():
        return False
    content = hook_path.read_text(encoding="utf-8", errors="replace")
    return _KNODE_START in content


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def install_hooks(project_path: str) -> dict[str, str]:
    """Install knode git hooks for the project.

    Git hooks are always POSIX sh scripts (Git for Windows uses sh.exe),
    so we always emit sh-compatible code regardless of the host OS.

    Returns a dict mapping hook_name -> result ("installed" | "updated" | "error:<msg>").
    """
    git_dir = _find_git_dir(project_path)
    if not git_dir:
        raise FileNotFoundError(
            f"No .git directory found at or above '{project_path}'. "
            "Is this a git repository?"
        )

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)

    python_exe = _get_python_exe()
    block  = _make_hook_block(project_path, python_exe)
    header = _posix_hook_header()

    results: dict[str, str] = {}

    for hook_name in _HOOK_NAMES:
        hook_path = hooks_dir / hook_name
        try:
            if hook_path.exists():
                existing = hook_path.read_text(encoding="utf-8", errors="replace")
                action   = "updated" if _has_knode_block(hook_path) else "installed"
                new_content = _inject_block(existing, block)
            else:
                action = "installed"
                new_content = header + block

            _write_hook(hook_path, new_content)
            results[hook_name] = action
        except Exception as exc:  # noqa: BLE001
            results[hook_name] = f"error:{exc}"

    return results


def uninstall_hooks(project_path: str) -> dict[str, str]:
    """Remove knode-managed blocks from git hooks.

    If a hook file becomes empty after removal, it is left in place so
    user-defined logic is never silently deleted.

    Returns a dict mapping hook_name -> result ("removed" | "not_found" | "error:<msg>").
    """
    git_dir = _find_git_dir(project_path)
    if not git_dir:
        raise FileNotFoundError(
            f"No .git directory found at or above '{project_path}'."
        )

    hooks_dir = git_dir / "hooks"
    results: dict[str, str] = {}

    for hook_name in _HOOK_NAMES:
        hook_path = hooks_dir / hook_name
        try:
            if not hook_path.exists():
                results[hook_name] = "not_found"
                continue

            existing = hook_path.read_text(encoding="utf-8", errors="replace")
            if not _has_knode_block(hook_path):
                results[hook_name] = "not_found"
                continue

            new_content = _remove_block(existing)
            _write_hook(hook_path, new_content)
            results[hook_name] = "removed"
        except Exception as exc:  # noqa: BLE001
            results[hook_name] = f"error:{exc}"

    return results


def get_hooks_status(project_path: str) -> dict[str, str]:
    """Return the installation status of each hook.

    Returns a dict mapping hook_name -> "installed" | "not_installed" | "no_git".
    """
    git_dir = _find_git_dir(project_path)
    if not git_dir:
        return {h: "no_git" for h in _HOOK_NAMES}

    hooks_dir = git_dir / "hooks"

    return {
        hook_name: (
            "installed"
            if _has_knode_block(hooks_dir / hook_name)
            else "not_installed"
        )
        for hook_name in _HOOK_NAMES
    }
