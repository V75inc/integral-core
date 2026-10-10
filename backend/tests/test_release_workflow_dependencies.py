"""Keep publication and release tags bound to declared, verified source jobs."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


@pytest.mark.parametrize("filename", ["publish-pypi.yml", "publish-testpypi.yml"])
def test_release_job_context_references_have_direct_dependencies(filename: str) -> None:
    """Implicit dependencies do not populate the GitHub Actions needs context."""
    root = Path(__file__).resolve().parents[2]
    jobs = yaml.safe_load((root / ".github/workflows" / filename).read_text())["jobs"]
    missing = []
    for job_name, job in jobs.items():
        dependencies = job.get("needs", [])
        if isinstance(dependencies, str):
            dependencies = [dependencies]
        referenced = set(re.findall(r"\bneeds\.([\w-]+)\.", json.dumps(job)))
        missing.extend(
            f"{job_name} references undeclared dependency {name}"
            for name in sorted(referenced - set(dependencies))
        )
    assert not missing, "\n".join(missing)
