#!/usr/bin/env python3
"""Ensure every bundle skill has a full manifest entry synced from SKILL.md.

Canonical shape (no bare-string skill keys in profile.yaml):

```yaml
  - key: my_skill
    name: My Skill
    kind: declarative
    description: ...
    prompt_template: skills/my_skill/SKILL.md
    tools_required: [...]  # must match SKILL.md allowed-tools
```

Run: python3 backend/scripts/sync_bundle_skill_manifests.py [--write]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

_REPO = Path(__file__).resolve().parents[2]
_PROFILES = _REPO / "backend" / "app" / "profiles"


def _skills_tier_key(raw: Dict[str, Any]) -> str:
    return "app" if str(raw.get("scope") or "track") == "app" else "track"


def _parse_skill(skill_md: Path) -> Dict[str, Any]:
    raw = skill_md.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise ValueError(f"{skill_md}: missing frontmatter")
    fm = yaml.safe_load(raw.split("---", 2)[1]) or {}
    key = skill_md.parent.name
    tools = fm.get("allowed-tools") or []
    if isinstance(tools, str):
        tools = [tools]
    return {
        "key": key,
        "name": str(fm.get("name") or key).replace("_", " ").title(),
        "kind": "declarative",
        "description": str(fm.get("description") or "").strip(),
        "prompt_template": f"skills/{key}/SKILL.md",
        "tools_required": [str(t) for t in tools],
    }


def _skill_dicts_for_bundle(bundle_dir: Path) -> List[Dict[str, Any]]:
    skills_dir = bundle_dir / "skills"
    if not skills_dir.is_dir():
        return []
    return [_parse_skill(p) for p in sorted(skills_dir.glob("*/SKILL.md"))]


def _validate_declared(
    bundle_name: str,
    declared: List[Any],
    expected_by_key: Dict[str, Dict[str, Any]],
) -> List[str]:
    issues: List[str] = []
    declared_keys: List[str] = []
    for entry in declared:
        if isinstance(entry, str):
            issues.append(f"{bundle_name}: bare string skill {entry!r} — use full dict")
            declared_keys.append(entry.strip())
            continue
        if not isinstance(entry, dict):
            continue
        k = str(entry.get("key") or "").strip()
        if not k:
            continue
        declared_keys.append(k)
        exp = expected_by_key.get(k)
        if exp is None:
            continue
        if str(entry.get("prompt_template") or "") != exp["prompt_template"]:
            issues.append(
                f"{bundle_name}/{k}: prompt_template must be {exp['prompt_template']!r}"
            )
        manifest_tools = list(entry.get("tools_required") or [])
        if manifest_tools != exp["tools_required"]:
            issues.append(
                f"{bundle_name}/{k}: tools_required mismatch "
                f"manifest={manifest_tools} skill={exp['tools_required']}"
            )
        manifest_desc = str(entry.get("description") or "").strip()
        if manifest_desc != exp.get("description", ""):
            issues.append(
                f"{bundle_name}/{k}: description mismatch "
                f"manifest={manifest_desc!r} skill={exp.get('description', '')!r}"
            )
    for key in expected_by_key:
        if key not in declared_keys:
            issues.append(f"{bundle_name}: missing manifest entry for skill {key!r}")
    return issues


def sync_profile(bundle_dir: Path, *, write: bool = False) -> Tuple[List[str], bool]:
    profile_path = bundle_dir / "profile.yaml"
    if not profile_path.is_file():
        return [], False
    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    tier_key = _skills_tier_key(raw)
    tier = raw.get(tier_key) or {}
    if not isinstance(tier, dict):
        return [], False
    expected = _skill_dicts_for_bundle(bundle_dir)
    if not expected:
        return [], False
    expected_by_key = {s["key"]: s for s in expected}
    declared = tier.get("skills") or []
    issues = _validate_declared(bundle_dir.name, declared, expected_by_key)
    changed = bool(issues)
    if write and issues:
        tier["skills"] = expected
        raw[tier_key] = tier
        profile_path.write_text(
            yaml.dump(
                raw, default_flow_style=False, sort_keys=False, allow_unicode=True
            ),
            encoding="utf-8",
        )
    return issues, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write", action="store_true", help="Rewrite profile.yaml skills"
    )
    args = parser.parse_args()
    all_issues: List[str] = []
    for bundle_dir in sorted(_PROFILES.iterdir()):
        if not bundle_dir.is_dir() or not (bundle_dir / "skills").is_dir():
            continue
        issues, _ = sync_profile(bundle_dir, write=args.write)
        all_issues.extend(issues)
    if args.write:
        # Re-validate after write
        all_issues = []
        for bundle_dir in sorted(_PROFILES.iterdir()):
            if not bundle_dir.is_dir() or not (bundle_dir / "skills").is_dir():
                continue
            issues, _ = sync_profile(bundle_dir, write=False)
            all_issues.extend(issues)
    if all_issues:
        print("\n".join(all_issues))
        return 1
    n = sum(1 for d in _PROFILES.iterdir() if (d / "skills").is_dir())
    print(f"OK: {n} bundle skill manifests in sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
