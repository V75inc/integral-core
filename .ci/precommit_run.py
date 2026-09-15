"""Launch a .ci/*.sh hook with Git Bash on Windows (WSL-safe).

pre-commit ``language: system`` hooks that call ``bash .ci/foo.sh`` fail on
Windows when ``bash`` resolves to WSL: the hook cwd is a Windows path WSL
does not see, so relative ``.ci/`` lookups miss. This launcher always runs
the script from the repo root using Git Bash when available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _find_bash() -> str:
    if sys.platform == "win32":
        for cand in (
            os.environ.get("PRE_COMMIT_GIT_BASH"),
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ):
            if cand and Path(cand).is_file():
                return cand
    found = shutil.which("bash")
    if not found:
        sys.exit(
            "bash not found for pre-commit hook — install Git for Windows "
            "or set PRE_COMMIT_GIT_BASH to bash.exe"
        )
    return found


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit("usage: precommit_run.py <script-name-under-.ci>")
    script = REPO_ROOT / ".ci" / sys.argv[1]
    if not script.is_file():
        sys.exit(f"missing hook script: {script}")
    bash = _find_bash()
    return subprocess.call([bash, str(script)], cwd=REPO_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
