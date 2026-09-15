"""jvspatial-convention compliance gates (in-process complement to .ci/jvspatial_drift_check.sh).

These tests assert the hard-forbidden patterns from root ``CLAUDE.md`` §
jvspatial Object-Spatial Contract → Forbidden Patterns are not present in
``backend/app/`` (outside ``main.py``). CI runs both the pre-commit hook
``jvspatial-drift-guard`` and this pytest module; either catches drift
independently.

Invariants enforced:

- I-CONV-01: every HTTP route registered via ``@endpoint`` from
  ``jvspatial.api`` — ``@router.<method>`` and ``APIRouter`` are hard-forbidden
  outside ``main.py``.
- I-CONV-02: every error raised via ``JVSpatialAPIException`` subclass from
  ``app.api.errors`` — ``raise HTTPException(...)`` hard-forbidden.
- I-CONV-03: request/response Pydantic ``BaseModel`` declarations live under
  ``backend/app/schemas/`` — inline declarations in ``api/*.py`` or
  ``agentive/api/*.py`` hard-forbidden.

The WebSocket routes in ``api/events_ws.py`` and ``agentive/api/agent_events.py``
use FastAPI's ``APIRouter().websocket(...)`` because jvspatial's ``@endpoint``
does not support WebSocket. These are documented inline with ``# deviation:``
comments per CLAUDE.md § Forbidden Patterns → Pragmatism Clause.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

# Repo root — backend/tests/test_jvspatial_convention_compliance.py is 2 levels
# under repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = REPO_ROOT / "backend" / "app"


def _scan(pattern: str, paths: list[Path]) -> list[str]:
    """Run ``grep -rn -E <pattern> <paths>``, return matching lines (filtered)."""
    matches: list[str] = []
    if not paths:
        return matches
    cmd = [
        "grep",
        "-rn",
        "--include=*.py",
        "-E",
        pattern,
        *[str(p) for p in paths],
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        # Fallback to pure-python scan if grep is unavailable.
        return _python_scan(pattern, paths)
    if result.returncode not in (0, 1):  # 1 = no matches, 0 = matches
        return []
    # Filter out lines with inline '# deviation:' annotation (matches the
    # pre-commit guard's contract).
    return [
        line
        for line in result.stdout.splitlines()
        if line.strip() and "# deviation:" not in line
    ]


def _python_scan(pattern: str, paths: list[Path]) -> list[str]:
    """Pure-python fallback when grep is unavailable."""
    rgx = re.compile(pattern)
    matches: list[str] = []
    for root in paths:
        if root.is_file():
            files = [root]
        else:
            files = list(root.rglob("*.py"))
        for f in files:
            try:
                content = f.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(content.splitlines(), start=1):
                if rgx.search(line) and "# deviation:" not in line:
                    matches.append(f"{f}:{lineno}:{line}")
    return matches


def _exclude_main(matches: list[str]) -> list[str]:
    """Drop matches inside ``backend/app/main.py`` (legitimate Server bootstrap)."""
    return [m for m in matches if "/backend/app/main.py" not in m]


# ---------------------------------------------------------------------------
# I-CONV-01 — routes via @endpoint
# ---------------------------------------------------------------------------


def test_no_apirouter_import_in_backend_app() -> None:
    """No ``from fastapi import APIRouter`` outside ``main.py``."""
    matches = _exclude_main(_scan(r"from fastapi import APIRouter", [BACKEND_APP]))
    assert not matches, (
        "Forbidden APIRouter import detected. Use @endpoint from jvspatial.api:\n"
        + "\n".join(matches)
    )


def test_no_router_method_decorators_in_backend_app() -> None:
    """No ``@router.<method>`` for HTTP verbs outside ``main.py``.

    Excludes ``@router.websocket`` (jvspatial's @endpoint does not support
    WebSocket — see ``api/events_ws.py`` and ``agentive/api/agent_events.py``).
    """
    matches = _exclude_main(
        _scan(r"@router\.(post|get|put|delete|patch)", [BACKEND_APP])
    )
    assert (
        not matches
    ), "Forbidden @router.<method> decorator detected. Use @endpoint:\n" + "\n".join(
        matches
    )


def test_no_app_method_decorators_in_backend_app() -> None:
    """No ``@app.<method>`` for HTTP verbs outside ``main.py``."""
    matches = _exclude_main(_scan(r"@app\.(post|get|put|delete|patch)", [BACKEND_APP]))
    assert (
        not matches
    ), "Forbidden @app.<method> decorator detected. Use @endpoint:\n" + "\n".join(
        matches
    )


# ---------------------------------------------------------------------------
# I-CONV-02 — errors via JVSpatialAPIException
# ---------------------------------------------------------------------------


def test_no_raise_httpexception_in_backend_app() -> None:
    """No ``raise HTTPException(...)`` outside ``main.py``.

    Use the matching ``JVSpatialAPIException`` subclass from
    ``app.api.errors`` instead.
    """
    matches = _exclude_main(_scan(r"raise HTTPException", [BACKEND_APP]))
    assert not matches, (
        "Forbidden raise HTTPException detected. Use JVSpatialAPIException "
        "subclass from app.api.errors:\n" + "\n".join(matches)
    )


# ---------------------------------------------------------------------------
# I-CONV-03 — schemas in backend/app/schemas/
# ---------------------------------------------------------------------------


def test_no_inline_basemodel_in_api_modules() -> None:
    """No inline ``class .*BaseModel`` in ``backend/app/api/`` or
    ``backend/app/agentive/api/``.

    Pydantic request/response models live under ``backend/app/schemas/``
    (subpackages ``schemas/api/`` and ``schemas/agentive/``).
    """
    api_dir = BACKEND_APP / "api"
    agentive_api_dir = BACKEND_APP / "agentive" / "api"
    matches: list[str] = []
    # Scan only top-level api/ and agentive/api/ — NOT models/, NOT schemas/.
    for d in (api_dir, agentive_api_dir):
        if d.exists():
            matches.extend(_scan(r"^class .*BaseModel", [d]))
    matches = _exclude_main(matches)
    assert not matches, (
        "Forbidden inline BaseModel detected in api/ modules. Extract to "
        "backend/app/schemas/ (subpackages api/ and agentive/):\n" + "\n".join(matches)
    )
