"""Create typecheck/finpipe -> src junction/symlink for static import resolution."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _remove_link(link: Path) -> None:
    if not link.exists() and not link.is_symlink():
        return
    if link.is_dir() and not link.is_symlink():
        subprocess.run(["cmd", "/c", "rmdir", str(link)], check=False)
        return
    link.unlink()


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    typecheck_root = repo_root / "typecheck"
    link = typecheck_root / "finpipe"
    target = repo_root / "src"
    legacy_roots = (
        repo_root / "finpipe",
        repo_root / ".typecheck",
    )

    typecheck_root.mkdir(exist_ok=True)

    for legacy in legacy_roots:
        legacy_link = legacy / "finpipe" if legacy.name == ".typecheck" else legacy
        if legacy_link.exists() or legacy_link.is_symlink():
            _remove_link(legacy_link)
        if legacy.is_dir() and legacy.name == ".typecheck" and not any(legacy.iterdir()):
            legacy.rmdir()

    if link.exists() or link.is_symlink():
        return

    if sys.platform == "win32":
        subprocess.check_call(["cmd", "/c", "mklink", "/J", str(link), str(target)])
        return

    link.symlink_to(target, target_is_directory=True)


if __name__ == "__main__":
    main()
