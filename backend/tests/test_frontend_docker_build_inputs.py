"""The frontend image must run the real guide gate in its isolated build tree."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.smoke
@pytest.mark.skipif(not shutil.which("npm"), reason="Node/npm required for guide gate")
def test_frontend_docker_preserves_guide_inputs_and_sibling_sdk(tmp_path):
    # Materialize the guide inputs via the Dockerfile's own COPY destinations,
    # rather than testing a second hand-written image layout. Dependencies are
    # unnecessary for this check; never copy the host's node_modules into it.
    inputs = [
        "frontend/package.json",
        "APP_UI_GUIDE.md",
        "AGENTS.md",
        "CLAUDE.md",
        ".cursor/rules/app-ui.mdc",
        "sdk/react/package.json",
        "sdk/react/APP_UI_GUIDE.md",
        "sdk/react/bin/app-ui-guides.mjs",
        "sdk/react/bin/app-ui-guides.test.mjs",
    ]
    text = (ROOT / "frontend/Dockerfile").read_text()
    builder = text.split("FROM nginx:")[0]
    workdir = Path("/")
    for line in builder.splitlines():
        if line.startswith("WORKDIR "):
            workdir = Path(line.split(maxsplit=1)[1])
        if not line.startswith("COPY "):
            continue
        *sources, dest = shlex.split(line)[1:]
        destination = Path(dest) if dest.startswith("/") else workdir / dest
        for source in sources:
            source_dir = source.endswith("/")
            for name in inputs:
                if name != source and not (source_dir and name.startswith(source)):
                    continue
                suffix = (
                    Path(name).relative_to(source) if source_dir else Path(name).name
                )
                target = tmp_path / destination.relative_to("/")
                if source_dir or dest.endswith("/"):
                    target /= suffix
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, target)
    cwd = tmp_path / workdir.relative_to("/")
    assert (cwd.parent / "sdk/react/bin/app-ui-guides.mjs").is_file()
    assert "RUN npm run build" in builder
    assert f"COPY --from=builder {workdir}/dist /usr/share/nginx/html" in text
    result = subprocess.run(
        ["npm", "run", "check:app-ui-guides"], cwd=cwd, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # Keep the gate meaningful: stale checked-in guides must still fail.
    guide = cwd.parent / "APP_UI_GUIDE.md"
    guide.write_text(guide.read_text() + "\nSTALE\n")
    result = subprocess.run(
        ["npm", "run", "check:app-ui-guides"], cwd=cwd, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "STALE" in result.stderr
