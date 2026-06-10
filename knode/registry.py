# -*- coding: utf-8 -*-
"""
knode Global Registry

Maintains ~/.knode/registry.json — a map of
  project_name -> { "path": "<abs_path>", "db": "<abs_db_path>", "indexed_at": "..." }

This lets `knode mcp` start without a --project flag and serve
ALL previously-indexed projects, just like GitNexus.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Global registry file location
_REGISTRY_DIR = Path.home() / ".knode"
_REGISTRY_FILE = _REGISTRY_DIR / "registry.json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register(project_path: str, db_path: str) -> str:
    """Register (or refresh) a project in the global registry.

    Returns the project name (directory basename).
    """
    project_path = str(Path(project_path).resolve())
    db_path = str(Path(db_path).resolve())
    name = Path(project_path).name

    registry = _load()
    registry[name] = {
        "path": project_path,
        "db": db_path,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
    }
    _save(registry)
    return name


def unregister(project_path: str) -> bool:
    """Remove a project from the registry. Returns True if it was found."""
    project_path = str(Path(project_path).resolve())
    registry = _load()
    to_remove = [k for k, v in registry.items() if v["path"] == project_path]
    if not to_remove:
        return False
    for k in to_remove:
        del registry[k]
    _save(registry)
    return True


def list_projects() -> dict[str, dict]:
    """Return all registered projects as {name: {path, db, indexed_at}}."""
    return _load()


def get_db_for_project(name_or_path: str) -> Optional[str]:
    """Resolve a project name or path to its DB file path.

    Lookup order:
      1. Exact name key match in registry
      2. Path match (resolved absolute path)
      3. Nearest .knode/graph.db found by walking up from cwd
    """
    registry = _load()

    # 1. Name match
    if name_or_path in registry:
        return registry[name_or_path]["db"]

    # 2. Path match
    resolved = str(Path(name_or_path).resolve())
    for entry in registry.values():
        if entry["path"] == resolved:
            return entry["db"]

    # 3. Walk up from cwd
    return _find_nearest_db()


def get_nearest_db() -> Optional[str]:
    """Walk up from cwd looking for .knode/graph.db.
    Returns the DB path string, or None."""
    return _find_nearest_db()


def get_all_db_paths() -> list[str]:
    """Return DB paths for all registered projects that still exist on disk."""
    return [
        entry["db"]
        for entry in _load().values()
        if Path(entry["db"]).exists()
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load() -> dict:
    if not _REGISTRY_FILE.exists():
        return {}
    try:
        return json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(registry: dict) -> None:
    _REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    _REGISTRY_FILE.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _find_nearest_db() -> Optional[str]:
    """Walk up directory tree from cwd looking for .knode/graph.db."""
    current = Path(os.getcwd()).resolve()
    for candidate in [current, *current.parents]:
        db = candidate / ".knode" / "graph.db"
        if db.exists():
            return str(db)
    return None
