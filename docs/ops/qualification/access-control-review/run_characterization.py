"""Run isolated characterization evidence using Integral's existing test fixtures.

Passing means the observed baseline behaviors were reproduced, including defects.
This is not a security requirement suite and must not become a release gate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path


def main() -> int:
    evidence_dir = Path(__file__).resolve().parent
    repo = evidence_dir.parents[3]
    python = repo / "backend/.venv/bin/python"
    if not python.is_file():
        raise SystemExit("Use the repository's documented uv sync setup first.")
    target = repo / "backend/tests" / f"test_access_review_{uuid.uuid4().hex}.py"
    env = os.environ.copy()
    # Never attach audit fixtures to a live server or production PostgreSQL.
    env.pop("BASE_URL", None)
    env["INTEGRAL_TEST_DB"] = "json"
    env["TESTING"] = "1"
    env["INTEGRAL_CREDENTIAL_ENC_KEY"] = subprocess.check_output(
        [
            str(python),
            "-c",
            "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())",
        ],
        text=True,
    ).strip()
    print(
        "Characterization: passing reproduces observations, including unsafe behavior.",
        flush=True,
    )
    try:
        shutil.copyfile(evidence_dir / "characterization_probes.py", target)
        result = subprocess.run(
            [
                str(python),
                "-m",
                "pytest",
                str(target),
                "-o",
                "addopts=",
                "-q",
                "--tb=short",
            ],
            cwd=repo,
            env=env,
            check=False,
        )
        return result.returncode
    finally:
        target.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
