"""Reviewed, expiring exceptions for Core's build-time dependency audit.

A matching advisory ID alone never grants an exception. Remediation is checked
against the reviewed dependency graph; boolean flags have no version identity,
so an unchanged flag still needs review at expiry. This is not a reachability
proof for arbitrary changes to the application's source or build inputs.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REVIEW_EXPIRES = date(2026, 11, 15)
SEVERITIES = {"info", "low", "moderate", "high", "critical"}
# Only these transitive rows in the separately approved report may vary
# between the two literal boolean remediation indicators.
ALTERNATE_BOOLEAN_REMEDIATION = {"anymatch", "chokidar", "fast-glob", "readdirp"}
ROOTS = {
    "GHSA-vfj7-8cjw-p6xm": ("braces", "high", "<=3.0.3"),
    "GHSA-rj75-hqrm-r3gf": ("postcss-selector-parser", "moderate", "<7.1.6"),
}
# Versions and remediation shapes reviewed on 2026-10-15. Tailwind 4 is a
# separately qualified build migration. A direct same-package patch is not
# covered by that decision, nor is a changed affected version or severity.
PACKAGES = {
    "braces": ("3.0.3", "high", "tailwind-major"),
    "chokidar": ("3.6.0", "high", "tailwind-major"),
    "fast-glob": ("3.3.3", "high", "boolean"),
    "micromatch": ("4.0.8", "high", "tailwind-major"),
    "postcss-nested": ("6.2.0", "moderate", "boolean"),
    "postcss-selector-parser": ("6.1.4", "moderate", "tailwind-major"),
    "tailwindcss": ("3.4.19", "high", "tailwind-major"),
    # Reviewed 2026-10-18: same build-only Tailwind peer chain, not a runtime
    # import. Keep exact node/dev/version/advisory/fix/expiry enforcement.
    "tailwindcss-animate": ("1.0.7", "high", "tailwind-major"),
}


def canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_inputs_digest(root: Path) -> str:
    """Bind the exception to reviewed application and executable build inputs."""

    def require_local_path(path: Path) -> None:
        current = root
        for component in path.relative_to(root).parts:
            current /= component
            if current.is_symlink():
                raise ValueError("symlink in reviewed build input ancestry")

    files: set[Path] = set()
    for relative in (
        "frontend/src",
        "frontend/plugins",
        "frontend/public",
        "frontend/scripts",
        "sdk/react/src",
        "sdk/react/bin",
    ):
        directory = root / relative
        require_local_path(directory)
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError("missing or linked reviewed build input directory")
        entries = list(directory.rglob("*"))
        if any(path.is_symlink() for path in entries):
            raise ValueError("symlink in reviewed build inputs")
        files.update(path for path in entries if path.is_file())
    for relative in ("frontend", "sdk/react"):
        directory = root / relative
        if directory.is_symlink():
            raise ValueError("linked build root")
        files.update(
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix in {".json", ".ts", ".js", ".mjs", ".cjs", ".html", ".css"}
        )
    files.update(
        root / relative
        for relative in (
            ".ci/bundle_web_assets.sh",
            ".ci/reproducible_build_env.sh",
            ".github/workflows/ci.yml",
            ".github/workflows/publish-testpypi.yml",
            ".github/workflows/publish-pypi.yml",
        )
    )
    for path in files:
        require_local_path(path)
    return canonical_digest(
        {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(files)
        }
    )


def same_reviewed_remediation(name: str, actual: object, expected: object) -> bool:
    """Keep exact fix identities apart from four proven npm reporting aliases."""
    if type(actual) is type(expected) and canonical_digest(actual) == canonical_digest(
        expected
    ):
        return True
    # npm 11.6.2 / Arborist 9.1.6 propagates fixAvailable through an
    # asynchronously populated via Set. Identical audits alternated these
    # four transitive rows between True and this same known major migration.
    # The exact dependency/advisory graph is checked separately; never apply
    # this equivalence to picomatch itself or any other/direct remediation.
    if name not in {"anymatch", "chokidar", "fast-glob", "readdirp"}:
        return False
    migration = {"name": "tailwindcss", "version": "4.3.5", "isSemVerMajor": True}

    def is_migration(value: object) -> bool:
        return (
            isinstance(value, dict)
            and value == migration
            and value.get("isSemVerMajor") is True
        )

    return (actual is True and is_migration(expected)) or (
        expected is True and is_migration(actual)
    )


def reviewed_picomatch_packages(
    document: dict,
    lock: dict,
    *,
    review: dict | None,
    build_digest: str | None,
    today: date,
    exact_remediation: bool = False,
) -> set[str]:
    """A separately approved, exact candidate review; never renew October's waiver."""
    if not review or not review.get("approval") or not build_digest:
        return set()
    try:
        approval = review["approval"]
        if not isinstance(approval, dict) or set(approval) != {
            "decision",
            "owner",
            "approved_on",
            "evidence",
        }:
            return set()
        if approval["decision"] != "approved":
            return set()
        if not all(
            isinstance(approval[key], str) and approval[key].strip()
            for key in ("owner", "approved_on", "evidence")
        ):
            return set()
        if approval["approved_on"] != review["reviewed_on"]:
            return set()
        approved_on = date.fromisoformat(review["reviewed_on"])
        expires = date.fromisoformat(review["expires"])
        if (
            not approved_on <= today < expires
            or not 0 < (expires - approved_on).days <= 7
        ):
            return set()
        if review["advisory"] != "GHSA-fc5q-39xc-vjpf":
            return set()
        if canonical_digest(lock) != review["lock_sha256"]:
            return set()
        if build_digest != review["build_inputs_sha256"]:
            return set()
        expected = review["graph"]
        actual = document["vulnerabilities"]
        high = set(review["high_packages"])
        if not high or high != {
            name for name, row in expected.items() if row["severity"] == "high"
        }:
            return set()
        checked: set[str] = set()

        def visit(name: str, seen: frozenset[str] = frozenset()) -> bool:
            if name in seen or len(seen) >= 32:
                raise ValueError("cyclic reviewed advisory chain")
            if name in checked:
                return True
            if name not in expected or name not in actual:
                return False
            row = actual[name]
            if any(
                row[key] != expected[name][key] for key in ("severity", "via", "nodes")
            ):
                return False
            fix = row["fixAvailable"]
            expected_fix = expected[name]["fixAvailable"]
            if exact_remediation:
                boolean_variation = (
                    name in ALTERNATE_BOOLEAN_REMEDIATION
                    and type(fix) is bool
                    and type(expected_fix) is bool
                    and all(isinstance(source, str) for source in row["via"])
                )
                if not boolean_variation and canonical_digest(fix) != canonical_digest(
                    expected_fix
                ):
                    return False
            elif not same_reviewed_remediation(name, fix, expected_fix):
                return False
            if not row["via"] or not row["nodes"]:
                return False
            for node in row["nodes"]:
                if lock["packages"][node].get("dev") is not True:
                    return False
            for source in row["via"]:
                if isinstance(source, str):
                    if not visit(source, seen | {name}):
                        return False
                elif not isinstance(source, dict):
                    return False
                elif source.get("severity") in {"high", "critical"} and (
                    name != "picomatch"
                    or source.get("name") != "picomatch"
                    or source.get("severity") != "high"
                    or source.get("range") != "<=4.0.7"
                    or source.get("url")
                    != ("https://github.com/advisories/GHSA-fc5q-39xc-vjpf")
                ):
                    return False
            checked.add(name)
            return True

        if not all(visit(name) for name in high) or checked != set(expected):
            return set()
        return high
    except (KeyError, TypeError, AttributeError):
        return set()


def analyze(
    document: dict,
    lock: dict,
    *,
    today: date | None = None,
    review: dict | None = None,
    build_digest: str | None = None,
) -> list[str]:
    """Return complete inventory lines; unreviewed high/critical are actionable."""
    today = today or datetime.now(timezone.utc).date()
    if not isinstance(document, dict) or not isinstance(
        document.get("vulnerabilities"), dict
    ):
        raise ValueError("missing vulnerabilities object")
    if document.get("error"):
        raise ValueError("audit error envelope")
    vulns = document["vulnerabilities"]
    for name, item in vulns.items():
        if (
            not isinstance(name, str)
            or not isinstance(item, dict)
            or item.get("severity") not in SEVERITIES
            or not isinstance(item.get("via"), list)
            or "fixAvailable" not in item
        ):
            raise ValueError("invalid vulnerability record")

    def reviewed_package(name: str) -> bool:
        item = vulns[name]
        if name not in PACKAGES or today >= REVIEW_EXPIRES:
            return False
        version, severity, remediation = PACKAGES[name]
        if item["severity"] != severity:
            return False
        nodes = item.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            return False
        for node in nodes:
            if not isinstance(node, str) or not (
                node == f"node_modules/{name}" or node.endswith(f"/node_modules/{name}")
            ):
                return False
            row = lock.get("packages", {}).get(node, {})
            if row.get("version") != version or row.get("dev") is not True:
                return False
        fix = item["fixAvailable"]
        if remediation == "boolean":
            return fix is True
        # CI npm can report no automatic fix for this exact reviewed graph,
        # while local npm proposes the reviewed Tailwind 4
        # migration. Neither flag changes its vulnerability or build-only scope.
        # Advisory/lock/dev/expiry checks still apply to every dependency branch.
        if fix is False:
            return True
        return bool(
            isinstance(fix, dict)
            and fix.get("name") == "tailwindcss"
            and fix.get("isSemVerMajor") is True
            and isinstance(fix.get("version"), str)
            and re.fullmatch(r"4\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", fix["version"])
        )

    def reviewed_chain(name: str, seen: frozenset[str] = frozenset()) -> bool:
        if name in seen or name not in vulns or len(seen) >= 32:
            raise ValueError("cyclic or missing transitive advisory")
        if not reviewed_package(name):
            return False
        via = vulns[name]["via"]
        if not via:
            return False
        for source in via:
            if isinstance(source, str):
                if not reviewed_chain(source, seen | {name}):
                    return False
            elif isinstance(source, dict):
                url = source.get("url", "")
                if not isinstance(url, str) or not url.startswith(
                    "https://github.com/advisories/"
                ):
                    return False
                root = ROOTS.get(url.removeprefix("https://github.com/advisories/"))
                if root != (name, source.get("severity"), source.get("range")):
                    return False
                if source.get("name") != name:
                    return False
            else:
                raise ValueError("invalid transitive advisory")
        return True

    reviewed = set()
    matched_review = None
    if isinstance(review, dict):
        # Evaluate complete reports independently. A row from one report must
        # never grant an exception to a row in a different report.
        for profile, exact in ((review, False), (review.get("alternate_report"), True)):
            if not isinstance(profile, dict):
                continue
            reviewed = reviewed_picomatch_packages(
                document,
                lock,
                review=profile,
                build_digest=build_digest,
                today=today,
                exact_remediation=exact,
            )
            if reviewed:
                matched_review = profile
                break
    lines = []
    for name, item in sorted(vulns.items()):
        severity = item["severity"]
        if severity not in {"high", "critical"}:
            lines.append(f"NOTICE {severity} {name} (outside blocking severity policy)")
        elif matched_review is not None and name in reviewed:
            lines.append(
                f"ACCEPTED {severity} {name} (approved exact build inputs; "
                f"GHSA-fc5q-39xc-vjpf; expires {matched_review['expires']})"
            )
        elif reviewed_chain(name):
            lines.append(
                f"ACCEPTED {severity} {name} (reviewed build graph; expires {REVIEW_EXPIRES})"
            )
        else:
            lines.append(f"ACTIONABLE {severity} {name} (outside reviewed conditions)")
    return lines


def main() -> int:
    try:
        document = json.loads(Path(sys.argv[1]).read_text())
        status = int(sys.argv[2])
        if status not in (0, 1):
            raise ValueError(f"npm audit exited {status}")
        if not isinstance(document, dict) or document.get("message") and status != 0:
            raise ValueError("audit error envelope")
        findings = document.get("vulnerabilities")
        if status == 1 and not findings:
            raise ValueError("nonzero audit status without findings")
        lock = json.loads(Path(sys.argv[3]).read_text()) if findings else {}
        today = datetime.now(timezone.utc).date()
        review_path = Path(__file__).with_name("frontend_dependency_review.json")
        review = json.loads(review_path.read_text()) if review_path.is_file() else None
        build_digest = (
            build_inputs_digest(Path(__file__).resolve().parents[1])
            if review and review.get("approval") and findings
            else None
        )
        lines = analyze(
            document, lock, today=today, review=review, build_digest=build_digest
        )
        for line in lines:
            print(line)
        if any(line.startswith("ACTIONABLE") for line in lines):
            # Public audit facts only. Never copy registry URLs or the rest of
            # a lock record, which may contain authenticated source locations.
            packages = {}
            for name, item in findings.items():
                nodes = item.get("nodes")
                packages[name] = {
                    "severity": item["severity"],
                    "via": item["via"],
                    "fixAvailable": item["fixAvailable"],
                    "nodes": [
                        {
                            "path": node,
                            "version": lock.get("packages", {})
                            .get(node, {})
                            .get("version"),
                            "dev": lock.get("packages", {}).get(node, {}).get("dev"),
                        }
                        for node in (nodes if isinstance(nodes, list) else [])
                        if isinstance(node, str)
                    ],
                }
            print(
                "REVIEW_CONTEXT "
                + json.dumps(
                    {
                        "policy_date": str(today),
                        "review_expires": str(REVIEW_EXPIRES),
                        "packages": packages,
                    },
                    sort_keys=True,
                )
            )
        return 0
    except (ValueError, TypeError, AttributeError, KeyError, OSError) as exc:
        print(f"INVALID_AUDIT_DOCUMENT: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
