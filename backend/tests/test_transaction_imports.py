"""Infrastructure imports must not eagerly load the HTTP operation facade."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "app.services.membership_write_scope",
        "app.services.app_operations.transaction_scope",
    ],
)
def test_transaction_helper_import_in_fresh_process(module):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import importlib; importlib.import_module({module!r})",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "TESTING": "1", "DEBUG": "false"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
