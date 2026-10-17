#!/usr/bin/env python3
"""Check new commit messages or audit existing attribution without rewriting Git."""

import argparse
import json
import re
import subprocess
from pathlib import Path

AGENT_TRAILER = re.compile(
    rb"^co-authored-by:[ \t]*[^\r\n]*"
    rb"<(?:cursoragent@cursor\.com|noreply@anthropic\.com)>[ \t]*\r?$",
    re.IGNORECASE | re.MULTILINE,
)


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
    args = parser.parse_args()
    if args.command == "message":
        if AGENT_TRAILER.search(args.path.read_bytes()):
            parser.exit(
                1, "Remove Cursor/Claude coauthor trailers from this commit message.\n"
            )
    else:
        print(json.dumps([audit(ref) for ref in args.refs], indent=2))


if __name__ == "__main__":
    main()
