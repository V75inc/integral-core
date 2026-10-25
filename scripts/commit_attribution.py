#!/usr/bin/env python3
"""Check new commit messages or audit existing attribution without rewriting Git."""

import argparse
import json
import re
import subprocess
from pathlib import Path

AGENT_EMAIL = (
    rb"(?:cursoragent@cursor\.com|noreply@anthropic\.com|"
    rb"(?:[0-9]+\+)?(?:cursoragent|claude)@users\.noreply\.github\.com)"
)
AGENT_IDENTITY = re.compile(AGENT_EMAIL, re.IGNORECASE)
AGENT_TRAILER = re.compile(
    rb"^co-authored-by:[ \t]*[^\r\n]*<" + AGENT_EMAIL + rb">[ \t]*\r?$",
    re.IGNORECASE | re.MULTILINE,
)


def check(refs, event=None):
    """Reject attribution in complete reachable history and proposed PR text."""
    if (
        subprocess.check_output(
            ["git", "rev-parse", "--is-shallow-repository"], text=True
        ).strip()
        != "false"
    ):
        raise ValueError("Complete history is required; fetch with fetch-depth: 0.")
    resolved = [
        subprocess.check_output(
            ["git", "rev-parse", "--verify", "--end-of-options", ref + "^{commit}"],
            text=True,
        ).strip()
        for ref in refs
    ]
    fields = subprocess.check_output(
        ["git", "log", "--format=%H%x00%ae%x00%ce%x00%B%x00", *resolved, "--"]
    ).split(b"\0")
    violations = []
    for i in range(0, len(fields) - 1, 4):
        sha = fields[i].strip().decode("ascii")
        for role, email in zip(("author", "committer"), fields[i + 1 : i + 3]):
            if AGENT_IDENTITY.fullmatch(email):
                violations.append({"commit": sha, "field": role})
        if AGENT_TRAILER.search(fields[i + 3]):
            violations.append({"commit": sha, "field": "coauthor"})
    if event is not None and "pull_request" in event:
        pr = event["pull_request"]
        for field in ("title", "body"):
            if AGENT_TRAILER.search((pr.get(field) or "").encode("utf-8")):
                violations.append({"field": "pull_request." + field})
    return {
        "heads": resolved,
        "commits": (len(fields) - 1) // 4,
        "violations": violations,
    }


def audit(ref):
    """Count direct matches and descendants whose IDs a message rewrite changes."""
    resolved = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", ref + "^{commit}"],
        text=True,
    ).strip()
    fields = subprocess.check_output(
        [
            "git",
            "log",
            "--reverse",
            "--topo-order",
            "--format=%H%x00%P%x00%B%x00",
            resolved,
        ]
    ).split(b"\0")
    changed = set()
    affected = []
    trailers = 0
    count = 0
    for i in range(0, len(fields) - 1, 3):
        sha = fields[i].strip().decode("ascii")
        parents = fields[i + 1].decode("ascii").split()
        matches = AGENT_TRAILER.findall(fields[i + 2])
        if matches:
            affected.append(sha)
            trailers += len(matches)
        if matches or any(parent in changed for parent in parents):
            changed.add(sha)
        count += 1
    return {
        "ref": ref,
        "head": resolved,
        "commits": count,
        "affected_messages": len(affected),
        "matching_trailers": trailers,
        "rewrite_commit_count": len(changed),
        "affected_commits": affected,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    message = commands.add_parser(
        "message", help="Reject targeted AI coauthor trailers"
    )
    message.add_argument("path", type=Path)
    history = commands.add_parser("audit", help="Read-only history impact report")
    history.add_argument(
        "refs", nargs="+", help="Local commit refs, e.g. origin/main HEAD"
    )
    gate = commands.add_parser("check", help="Fail on reachable agent attribution")
    gate.add_argument("refs", nargs="+", help="Commit refs to check with all ancestors")
    gate.add_argument("--event", type=Path, help="GitHub event JSON for PR title/body")
    args = parser.parse_args()
    if args.command == "message":
        if AGENT_TRAILER.search(args.path.read_bytes()):
            parser.exit(
                1, "Remove Cursor/Claude coauthor trailers from this commit message.\n"
            )
    elif args.command == "audit":
        print(json.dumps([audit(ref) for ref in args.refs], indent=2))
    else:
        try:
            result = check(
                args.refs, json.loads(args.event.read_text()) if args.event else None
            )
        except (ValueError, OSError, subprocess.CalledProcessError) as error:
            parser.exit(2, f"Attribution check could not complete: {error}\n")
        print(json.dumps(result, indent=2))
        if result["violations"]:
            parser.exit(
                1, "Remove agent attribution before merging; preserve human credit.\n"
            )


if __name__ == "__main__":
    main()
