"""
File scanner — walks an Android project and returns Java/Kotlin source files,
skipping generated, build, and hidden directories.
"""
from pathlib import Path
from typing import Generator, Tuple

SKIP_DIRS = {
    "build", ".gradle", ".idea", ".git", ".knode",
    "generated", "intermediates", "outputs", "__pycache__",
    "node_modules", ".cxx",
}

EXTENSIONS = {".java": "java", ".kt": "kotlin"}


def scan_sources(project_path: str) -> Generator[Tuple[Path, str], None, None]:
    """
    Recursively yield (file_path, language) tuples for all Java/Kotlin
    source files found under *project_path*, excluding build artifacts.
    """
    root = Path(project_path)
    for path in root.rglob("*"):
        # Skip unwanted directories
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        lang = EXTENSIONS.get(path.suffix)
        if lang:
            yield path, lang


def count_sources(project_path: str) -> int:
    return sum(1 for _ in scan_sources(project_path))
