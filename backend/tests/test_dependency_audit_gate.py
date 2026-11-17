"""Failure-envelope regression tests for the dependency audit gate."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT = REPO_ROOT / ".ci" / "dependency_audit.sh"
pytestmark = pytest.mark.unit

NPM_FIXTURE_HEADER = """#!/bin/sh
case "$*" in
  'exec --yes --package=npm@11.6.2 -- npm --version')
    printf '%s\\n' "${AUDIT_NPM_VERSION-11.6.2}"
    exit "${AUDIT_VERSION_EXIT:-0}"
    ;;
  'exec --yes --package=npm@11.6.2 -- npm audit --json') ;;
  *) echo 'unexpected auditor invocation' >&2; exit 64 ;;
esac
"""


@pytest.fixture
def frontend_audit(tmp_path):
    """Run the real gate in an isolated repository with a fixture install tree."""
    root = tmp_path / "audit-repository"
    (root / ".ci").mkdir(parents=True)
    (root / "frontend" / "node_modules").mkdir(parents=True)
    script = root / ".ci" / "dependency_audit.sh"
    shutil.copy2(AUDIT_SCRIPT, script)
    shutil.copy2(
        REPO_ROOT / ".ci/frontend_dependency_policy.py",
        root / ".ci/frontend_dependency_policy.py",
    )
    return script, root, tmp_path / "npm-calls.log"


@pytest.mark.parametrize(
    "document",
    [
        '{"error":{"code":"E401","summary":"registry unavailable"}}',
        "{malformed-json",
        "",
    ],
)
def test_frontend_audit_error_or_malformed_output_fails_closed(
    tmp_path, document, frontend_audit
):
    script, root, calls = frontend_audit
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm = fake_bin / "npm"
    npm.write_text(
        NPM_FIXTURE_HEADER + 'printf "%s\\n" called >> "$AUDIT_CALL_LOG"\n'
        "printf '%s' \"$AUDIT_FIXTURE\"\n"
        'exit "${AUDIT_EXIT:-1}"\n'
    )
    npm.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "AUDIT_FIXTURE": document,
        "AUDIT_EXIT": "1",
        "AUDIT_CALL_LOG": str(calls),
    }
    result = subprocess.run(
        ["bash", str(script), "frontend"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "FAILED" in result.stderr
    assert calls.read_text() == "called\n"


def test_successful_frontend_audit_document_is_accepted(tmp_path, frontend_audit):
    script, root, calls = frontend_audit
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm = fake_bin / "npm"
    npm.write_text(
        NPM_FIXTURE_HEADER + 'printf "%s\\n" called >> "$AUDIT_CALL_LOG"\n'
        "printf '%s' '{\"metadata\":{},\"vulnerabilities\":{}}'\n"
        "exit 0\n"
    )
    npm.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "AUDIT_CALL_LOG": str(calls),
    }
    result = subprocess.run(
        ["bash", str(script), "frontend"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "passed (no unreviewed" in result.stdout
    assert calls.read_text() == "called\n"


@pytest.mark.parametrize(
    ("version", "version_exit"),
    [("10.8.2", "0"), ("11.6.3", "0"), ("", "0"), ("11.6.2", "7")],
)
def test_frontend_auditor_selection_fails_without_ambient_fallback(
    tmp_path, frontend_audit, version, version_exit
):
    script, root, calls = frontend_audit
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm = fake_bin / "npm"
    npm.write_text(
        NPM_FIXTURE_HEADER
        + 'printf "%s\\n" called >> "$AUDIT_CALL_LOG"\n'
        + "printf '%s' '{\"metadata\":{},\"vulnerabilities\":{}}'\n"
    )
    npm.chmod(0o755)
    result = subprocess.run(
        ["bash", str(script), "frontend"],
        cwd=root,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "AUDIT_NPM_VERSION": version,
            "AUDIT_VERSION_EXIT": version_exit,
            "AUDIT_CALL_LOG": str(calls),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "required npm 11.6.2 auditor" in result.stderr
    assert not calls.exists()


@pytest.mark.parametrize("audit_exit", [0, 7])
def test_backend_audit_uses_pinned_lock_without_bootstrapping_pip(tmp_path, audit_exit):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uv = fake_bin / "uv"
    uv.write_text("#!/bin/sh\nprintf '%s\\n' 'example==1.2.3'\n")
    uv.chmod(0o755)
    pip_audit = fake_bin / "pip-audit"
    pip_audit.write_text(
        "#!/bin/sh\n"
        "case \" $* \" in *' --no-deps '* ) ;; *) exit 20 ;; esac\n"
        "case \" $* \" in *' --disable-pip '* ) ;; *) exit 21 ;; esac\n"
        "case \" $* \" in *' --ignore-vuln '* ) exit 22 ;; esac\n"
        'exit "${AUDIT_EXIT:-0}"\n'
    )
    pip_audit.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "AUDIT_EXIT": str(audit_exit),
    }
    result = subprocess.run(
        ["bash", str(AUDIT_SCRIPT), "backend"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if audit_exit == 0:
        assert result.returncode == 0, result.stderr
        assert "clean." in result.stdout
    else:
        assert result.returncode != 0
        assert "FAILED" in result.stderr


@pytest.mark.parametrize("known_direct_patch", [False, True])
def test_unreviewed_high_or_new_direct_patch_is_not_implicitly_accepted(
    tmp_path, frontend_audit, known_direct_patch
):
    import json

    script, root, _ = frontend_audit
    package = "braces" if known_direct_patch else "unreviewed"
    advisory = (
        "GHSA-vfj7-8cjw-p6xm" if known_direct_patch else "GHSA-synthetic-unreviewed"
    )
    fix = (
        {"name": "braces", "version": "3.0.4", "isSemVerMajor": False}
        if known_direct_patch
        else False
    )
    document = {
        "vulnerabilities": {
            package: {
                "severity": "high",
                "fixAvailable": fix,
                "nodes": [f"node_modules/{package}"],
                "via": [
                    {
                        "name": package,
                        "url": f"https://github.com/advisories/{advisory}",
                        "severity": "high",
                        "range": "<=3.0.3",
                    }
                ],
            }
        }
    }
    (root / "frontend/package-lock.json").write_text(
        json.dumps(
            {"packages": {f"node_modules/{package}": {"version": "3.0.3", "dev": True}}}
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm = fake_bin / "npm"
    npm.write_text(NPM_FIXTURE_HEADER + 'printf "%s" "$AUDIT_FIXTURE"\nexit 1\n')
    npm.chmod(0o755)
    result = subprocess.run(
        ["bash", str(script), "frontend"],
        cwd=root,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "AUDIT_FIXTURE": json.dumps(document),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0, result.stdout
    assert "ACTIONABLE" in result.stderr


@pytest.mark.parametrize(
    "state", ["approved", "unapproved", "changed-input", "expired"]
)
def test_candidate_review_is_enforced_by_real_audit_wrapper(
    tmp_path, frontend_audit, state
):
    """A synthetic approved build passes; missing or stale evidence blocks."""
    import importlib.util
    import json
    from datetime import datetime, timedelta, timezone

    script, root, _ = frontend_audit
    spec = importlib.util.spec_from_file_location(
        "wrapper_candidate_policy", REPO_ROOT / ".ci/frontend_dependency_policy.py"
    )
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    review = json.loads((REPO_ROOT / ".ci/frontend_dependency_review.json").read_text())
    document = {"vulnerabilities": review["graph"]}
    today = datetime.now(timezone.utc).date()
    review["reviewed_on"] = today.isoformat()
    review["expires"] = (today + timedelta(days=7)).isoformat()
    review["approval"] = {
        "decision": "approved",
        "owner": "Test fixture only",
        "approved_on": today.isoformat(),
        "evidence": "Synthetic isolated build; never a production approval",
    }
    for relative in (
        "frontend/src",
        "frontend/plugins",
        "frontend/public",
        "frontend/scripts",
        "sdk/react/src",
        "sdk/react/bin",
        ".github/workflows",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
    for relative in (
        ".ci/bundle_web_assets.sh",
        ".ci/reproducible_build_env.sh",
        ".github/workflows/ci.yml",
        ".github/workflows/publish-testpypi.yml",
        ".github/workflows/publish-pypi.yml",
    ):
        (root / relative).write_text("fixture build recipe\n")
    shutil.copy2(
        REPO_ROOT / "frontend/package-lock.json", root / "frontend/package-lock.json"
    )
    review["build_inputs_sha256"] = policy.build_inputs_digest(root)
    if state == "unapproved":
        review["approval"] = None
    elif state == "expired":
        review["expires"] = today.isoformat()
    elif state == "changed-input":
        (root / "frontend/src/unreviewed.ts").write_text("unreviewed input\n")
    (root / ".ci/frontend_dependency_review.json").write_text(json.dumps(review))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm = fake_bin / "npm"
    npm.write_text(NPM_FIXTURE_HEADER + 'printf "%s" "$AUDIT_FIXTURE"\nexit 1\n')
    npm.chmod(0o755)
    result = subprocess.run(
        ["bash", str(script), "frontend"],
        cwd=root,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "AUDIT_FIXTURE": json.dumps(document),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    if state == "approved":
        assert result.returncode == 0, result.stderr
        assert "accepted: high picomatch" in result.stdout
    else:
        assert result.returncode != 0, result.stdout
        assert "ACTIONABLE: high picomatch" in result.stderr
