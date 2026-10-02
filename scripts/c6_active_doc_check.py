"""Check active Core docs for missing relative targets and one executable command.

Called by the C6 A15 qualification, not by the application runtime.
Run from anywhere; the script locates the repository root.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_ROOTS = (
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "product",
    ROOT / "docs" / "ops",
    ROOT / "docs" / "backend",
    ROOT / "docs" / "operational-models",
)
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def markdown_files() -> list[Path]:
    files: list[Path] = []
    for root in DOC_ROOTS:
        if root.is_file():
            files.append(root)
        else:
            files.extend(sorted(root.rglob("*.md")))
    return files


def main() -> int:
    missing: list[str] = []
    checked = 0
    for path in markdown_files():
        text = path.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            target = match.group(1).strip()
            if not target or target.startswith(
                ("#", "http://", "https://", "mailto:", "/")
            ):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            checked += 1
            if not resolved.exists():
                missing.append(f"{path.relative_to(ROOT)} -> {target}")
    command = subprocess.run(
        ["make", "-n", "verify-ci"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    result = {
        "files": len(markdown_files()),
        "relative_targets": checked,
        "missing": missing,
        "executable_command": "make -n verify-ci",
        "executable_exit": command.returncode,
    }
    print(json.dumps(result, indent=2))
    return 1 if missing or command.returncode != 0 else 0


if __name__ == "__main__":
    sys.exit(main())
