"""I-SUBSTRATE-01 (DR-30-03) — substrate-domain drift CI gate."""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / ".ci" / "substrate_domain_drift_check.sh"


def test_gate_script_exists_and_executable():
    assert GATE.exists(), f"missing: {GATE}"
    assert os.access(GATE, os.X_OK), f"not executable: {GATE}"


def test_gate_passes_on_current_repo():
    """Repo MUST pass the substrate-domain drift gate (I-SUBSTRATE-01).

    Was xfail through Phase 30 Waves A–D while the eight leak files
    (pricing.py, proposal_convert.py, etc.) still carried V75-era
    tokens. The full V75 de-branding pass closed those leaks and the
    surrounding allowlist entries, so this is a permanent green test
    now — regressions block commits at the .githooks gate AND here.
    """
    result = subprocess.run([str(GATE)], cwd=REPO, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"substrate drift gate failed:\n"
        f"--- stdout (truncated) ---\n{result.stdout[:2000]}\n"
        f"--- stderr ---\n{result.stderr[:500]}"
    )


def test_gate_flags_planted_v75_string():
    """Plant a forbidden token in a temp substrate file + run gate.

    Anchored on sentinel filename in output — confirms the gate
    actually flagged the planted line (not just incidentally surfaced
    other leak files containing the same token name).
    """
    pid = os.getpid()
    sentinel = REPO / "backend" / "app" / "services" / f"_drift_probe_{pid}.py"
    try:
        # `V75` is the canonical V75-era static token — 3 chars, enforced
        # regardless of length thanks to the static-pass code path.
        sentinel.write_text(
            '"""sentinel."""\n# forbidden: V75 inside services/ — gate must flag this\n'
        )
        result = subprocess.run([str(GATE)], cwd=REPO, capture_output=True, text=True)
        assert result.returncode != 0, "gate FAILED to flag planted V75 token"
        combined = result.stdout + result.stderr
        # Anchor on the sentinel filename — proves the gate flagged the
        # planted line (not some unrelated leak in the repo).
        assert f"_drift_probe_{pid}" in combined, (
            f"gate output did not surface the planted sentinel file "
            f"_drift_probe_{pid}.py:\n{combined[:2000]}"
        )
    finally:
        if sentinel.exists():
            sentinel.unlink()


def test_allowlist_lines_carry_reason_comments():
    """Every active (non-comment, non-blank) line in the allowlist MUST
    carry a `# reason:` comment."""
    allow = REPO / ".ci" / "substrate_drift_allowlist.txt"
    if not allow.exists():
        return  # empty allowlist file is OK; gate handles missing file
    for ln, line in enumerate(allow.read_text().splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        assert (
            "# reason:" in line
        ), f"allowlist line {ln} missing `# reason:` justification: {line!r}"
