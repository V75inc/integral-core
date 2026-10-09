"""Check maintained Markdown links and heading anchors without network access."""

import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

root = Path.cwd()
files = set(Path("docs").rglob("*.md"))
files |= {
    Path(s)
    for s in subprocess.check_output(
        ["git", "ls-files", "*README.md", "*AGENTS.md"], text=True
    ).splitlines()
    if Path(s).exists()
}
issues = []


def anchors(p: Path) -> set[str]:
    """Collect GitHub-compatible heading and explicit anchor identities."""
    text = p.read_text()
    out = set()
    seen = {}
    for line in re.sub(r"```.*?```", "", text, flags=re.S).splitlines():
        m = re.match(r"^#{1,6}\s+(.*)", line)
        if not m:
            continue
        s = m[1].strip().lower()
        s = re.sub(r"[^\w\-\s]", "", s)
        s = s.replace(" ", "-")
        n = seen.get(s, 0)
        seen[s] = n + 1
        out.add(s if not n else f"{s}-{n}")
    out |= set(re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)', text))
    return out


for p in sorted(files):
    txt = re.sub(r"```.*?```", "", p.read_text(), flags=re.S)
    for m in re.finditer(r"!?\[[^\]]*\]\(([^)]+)\)", txt):
        raw = m[1].strip().split(' "')[0].strip("<>")
        u = urlsplit(raw)
        if u.scheme or raw.startswith("//"):
            continue
        dst = (
            (root / u.path.lstrip("/"))
            if u.path.startswith("/")
            else p.parent / unquote(u.path)
        )
        if not dst.exists():
            issues.append({"file": str(p), "target": raw, "issue": "missing path"})
        elif (
            u.fragment
            and dst.suffix == ".md"
            and unquote(u.fragment) not in anchors(dst)
        ):
            issues.append({"file": str(p), "target": raw, "issue": "missing anchor"})
print(json.dumps(issues, indent=2))
print("checked", len(files), "files;", len(issues), "issues")
sys.exit(bool(issues))
