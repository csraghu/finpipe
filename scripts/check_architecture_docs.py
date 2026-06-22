#!/usr/bin/env python3
"""Pre-commit hook: require docs/architecture.md when src/ package changes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import PurePosixPath

ARCHITECTURE_DOC = "docs/architecture.md"
SOURCE_PREFIX = "src/"


def staged_files() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def normalize(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).as_posix()


def is_source_change(path: str) -> bool:
    return normalize(path).startswith(SOURCE_PREFIX)


def main() -> int:
    try:
        files = staged_files()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return 0

    if not files:
        return 0

    source_changed = [path for path in files if is_source_change(path)]
    if not source_changed:
        return 0

    if any(normalize(path) == ARCHITECTURE_DOC for path in files):
        return 0

    print(
        "Architecture doc sync required.\n"
        f"  Staged changes under {SOURCE_PREFIX}:\n"
        + "".join(f"    - {path}\n" for path in source_changed)
        + f"\n  Update and stage {ARCHITECTURE_DOC} before committing.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
