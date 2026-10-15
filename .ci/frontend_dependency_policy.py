"""Reviewed, expiring exceptions for Core's build-time dependency audit.

A matching advisory ID alone never grants an exception. Remediation is checked
against the reviewed dependency graph; boolean flags have no version identity,
so an unchanged flag still needs review at expiry. This is not a reachability
proof for arbitrary changes to the application's source or build inputs.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REVIEW_EXPIRES = date(2026, 11, 15)
SEVERITIES = {"info", "low", "moderate", "high", "critical"}
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
}


def analyze(document: dict, lock: dict, *, today: date | None = None) -> list[str]:
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

    lines = []
    for name, item in sorted(vulns.items()):
        severity = item["severity"]
        if severity not in {"high", "critical"}:
            lines.append(f"NOTICE {severity} {name} (outside blocking severity policy)")
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
        lines = analyze(document, lock, today=today)
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
