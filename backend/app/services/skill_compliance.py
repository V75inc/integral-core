"""Skill SKILL.md compliance checks for Integral core and app bundles."""

from __future__ import annotations

import glob
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _profiles_root() -> Path:
    from app.services.package_paths import default_profiles_root

    return default_profiles_root()


_PROFILES_ROOT = _REPO_ROOT / "backend" / "app" / "profiles"
_CORE_SKILLS_GLOB = (
    _REPO_ROOT
    / "agent"
    / "agents"
    / "integral"
    / "integral_agent"
    / "actions"
    / "integral"
    / "embedded_integral_action"
    / "skills"
    / "integral_*"
    / "SKILL.md"
)

CORE_INTEGRAL_SKILL_NAMES: Tuple[str, ...] = (
    "integral_artifacts",
    "integral_attachments",
    "integral_dashboards",
    "integral_entries",
    "integral_filing",
    "integral_identity",
    "integral_insights",
    "integral_model",
    "integral_navigation",
    "integral_onboard",
    "integral_organize",
    "integral_profiles",
    "integral_review",
    "integral_scaffold",
    "integral_scheduling",
    "integral_workspace",
)

SkillTier = Literal["core", "bundle_public", "bundle_private"]

SECTION_ALIASES: Dict[str, Tuple[str, ...]] = {
    "when_to_use": ("when to use", "purpose", "purpose / when to use"),
    "when_not": (
        "when not to use",
        "when not to use — delegate",
        "when not to use - delegate",
        "delegate",
    ),
    "grounding": ("grounding", "grounding (read before write)", "read before write"),
    "procedure": ("procedure", "workflow", "steps"),
    "staging": ("staging discipline", "staging", "approval"),
    "forbidden": ("forbidden patterns", "forbidden", "anti-patterns", "constraints"),
    "example": ("example", "examples", "walkthrough"),
}

MERGE_GATE_SECTIONS = ("when_not", "grounding", "staging")

_FORBIDDEN_FRONTMATTER_KEYS = frozenset({"plan-steps", "plan_steps", "version"})

# Anthropic / jvagent discovery voice — reject second-person coaching in description.
_SECOND_PERSON_DESCRIPTION_RE = re.compile(
    r"\b(help the user|you should|your job|when you)\b",
    re.IGNORECASE,
)

VALID_SKILL_SPECS = frozenset({"jv", "claude"})


@dataclass
class SkillComplianceIssue:
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"


@dataclass
class SkillComplianceReport:
    path: str
    tier: SkillTier
    bundle_slug: str = ""
    skill_key: str = ""
    private: bool = False
    line_count: int = 0
    issues: List[SkillComplianceIssue] = field(default_factory=list)
    sections_present: Dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def score(self) -> int:
        """0–7 section score for audit reporting."""
        return sum(1 for v in self.sections_present.values() if v)


def _parse_frontmatter(skill_path: Path) -> Tuple[Dict[str, Any], str]:
    raw = skill_path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        return {}, raw.strip()
    parts = raw.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"Invalid frontmatter in {skill_path}")
    meta = yaml.safe_load(parts[1]) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"Frontmatter must be a mapping in {skill_path}")
    return meta, parts[2].strip()


def _normalize_allowed_tools(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _headings(body: str) -> Set[str]:
    found: Set[str] = set()
    for line in body.splitlines():
        m = re.match(r"^#{1,3}\s+(.+)$", line.strip())
        if m:
            found.add(m.group(1).strip().lower())
    return found


def detect_sections(body: str) -> Dict[str, bool]:
    """Return which canonical SOP sections are present in a SKILL.md body."""
    headings = _headings(body)
    body_lower = body.lower()

    def hmatch(*phrases: str) -> bool:
        for heading in headings:
            for phrase in phrases:
                if phrase in heading:
                    return True
        return False

    return {
        "when_to_use": hmatch("when to use", "purpose"),
        "when_not": hmatch("when not", "delegate", "vs delegate"),
        "grounding": hmatch("grounding") or "read before write" in body_lower,
        "procedure": hmatch(
            "procedure", "workflow", "creating a routine", "executing a scheduled"
        ),
        "staging": hmatch("staging discipline", "staging")
        or "staging discipline" in body_lower
        or "### staging" in body_lower,
        "forbidden": hmatch("forbidden", "anti-injection", "constraints", "honesty"),
        "example": hmatch("example", "walkthrough"),
    }


def _load_manifest_skill_meta(bundle_dir: Path) -> Dict[str, Dict[str, Any]]:
    profile_path = bundle_dir / "profile.yaml"
    if not profile_path.is_file():
        return {}
    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    manifest: Dict[str, Any] = {}
    if isinstance(raw, dict):
        from app.services.content_profile_loader import _assemble_manifest

        manifest = _assemble_manifest(raw)
    out: Dict[str, Dict[str, Any]] = {}
    for scope_key in ("track", "app"):
        tier = manifest.get(scope_key) or {}
        if not isinstance(tier, dict):
            continue
        for entry in tier.get("skills") or []:
            if isinstance(entry, str) and entry.strip():
                out[entry.strip()] = {"private": False, "tools_required": []}
            elif isinstance(entry, dict):
                key = str(entry.get("key") or "").strip()
                if key:
                    tools = entry.get("tools_required") or []
                    out[key] = {
                        "private": bool(entry.get("private", False)),
                        "tools_required": (
                            list(tools) if isinstance(tools, list) else []
                        ),
                        "description": str(entry.get("description") or "").strip(),
                    }
    return out


def _integral_backtick_refs(body: str) -> List[str]:
    return re.findall(r"`(integral_[a-z0-9_]+)`", body)


def check_skill_body(
    body: str,
    *,
    tier: SkillTier = "bundle_public",
    known_tool_names: Optional[Set[str]] = None,
) -> List[SkillComplianceIssue]:
    """Body-only compliance for workspace-authored skills (warn at save)."""
    issues: List[SkillComplianceIssue] = []
    sections = detect_sections(body)
    enforce = tier in ("core", "bundle_public")
    if enforce:
        for sec in MERGE_GATE_SECTIONS:
            if not sections.get(sec):
                severity: Literal["error", "warning"] = (
                    "error" if tier == "core" else "warning"
                )
                issues.append(
                    SkillComplianceIssue(
                        f"missing_section_{sec}",
                        f"Missing required section: {sec}",
                        severity=severity,
                    )
                )
        for sec in ("when_to_use", "procedure", "forbidden", "example"):
            if not sections.get(sec):
                severity = "error" if tier == "core" else "warning"
                issues.append(
                    SkillComplianceIssue(
                        f"missing_section_{sec}",
                        f"Missing section: {sec}",
                        severity=severity,
                    )
                )
    if known_tool_names is not None:
        core_skills = set(CORE_INTEGRAL_SKILL_NAMES)
        for ref in _integral_backtick_refs(body):
            if ref in core_skills:
                if "delegate" not in body.lower() and "skill" not in body.lower():
                    issues.append(
                        SkillComplianceIssue(
                            "ambiguous_skill_ref",
                            f"`{ref}` may be a skill name — use explicit skill delegation wording",
                            severity="warning",
                        )
                    )
            elif ref not in known_tool_names and not ref.endswith("_setup"):
                issues.append(
                    SkillComplianceIssue(
                        "unknown_tool_ref",
                        f"Backtick reference `{ref}` is not in tool catalogue",
                        severity="warning",
                    )
                )
    return issues


def check_skill_file(
    skill_path: Path,
    *,
    tier: SkillTier,
    manifest_meta: Optional[Dict[str, Any]] = None,
    known_tool_names: Optional[Set[str]] = None,
) -> SkillComplianceReport:
    """Audit one SKILL.md file and return a structured compliance report."""
    skill_key = skill_path.parent.name
    bundle_slug = ""
    if tier.startswith("bundle"):
        bundle_slug = skill_path.parents[2].name

    private = bool((manifest_meta or {}).get("private", False))
    manifest_tools = set((manifest_meta or {}).get("tools_required") or [])

    report = SkillComplianceReport(
        path=str(skill_path),
        tier=tier,
        bundle_slug=bundle_slug,
        skill_key=skill_key,
        private=private,
    )

    try:
        meta, body = _parse_frontmatter(skill_path)
    except ValueError as exc:
        report.issues.append(SkillComplianceIssue("invalid_frontmatter", str(exc)))
        return report

    report.line_count = len(body.splitlines()) + len(
        yaml.dump(meta).splitlines() if meta else []
    )

    if not meta and tier != "core":
        report.issues.append(
            SkillComplianceIssue(
                "missing_frontmatter",
                "SKILL.md must start with YAML frontmatter",
            )
        )
        return report

    for bad_key in _FORBIDDEN_FRONTMATTER_KEYS:
        if bad_key in meta:
            report.issues.append(
                SkillComplianceIssue(
                    "forbidden_frontmatter",
                    f"Remove deprecated frontmatter key '{bad_key}'",
                )
            )

    name = str(meta.get("name") or "").strip()
    if name and name != skill_key:
        report.issues.append(
            SkillComplianceIssue(
                "name_mismatch",
                f"frontmatter name={name!r} != directory {skill_key!r}",
            )
        )
    if not str(meta.get("description") or "").strip():
        report.issues.append(
            SkillComplianceIssue("missing_description", "description is required")
        )
    else:
        skill_desc = str(meta.get("description") or "").strip()
        if _SECOND_PERSON_DESCRIPTION_RE.search(skill_desc):
            report.issues.append(
                SkillComplianceIssue(
                    "description_second_person",
                    "description must be third-person discovery prose (jvagent / Anthropic)",
                )
            )

    spec_raw = str(meta.get("spec") or "").strip().lower()
    if tier in ("core", "bundle_public"):
        if not spec_raw:
            report.issues.append(
                SkillComplianceIssue(
                    "missing_spec",
                    "frontmatter must declare spec: jv (or spec: claude)",
                )
            )
        elif spec_raw not in VALID_SKILL_SPECS:
            report.issues.append(
                SkillComplianceIssue(
                    "invalid_spec",
                    f"spec must be jv or claude, got {spec_raw!r}",
                )
            )

    if tier == "core":
        req_actions = meta.get("requires-actions") or []
        if "EmbeddedIntegralAction" not in req_actions:
            report.issues.append(
                SkillComplianceIssue(
                    "missing_requires_actions",
                    "requires-actions must include EmbeddedIntegralAction",
                )
            )
        if not meta.get("extends"):
            report.issues.append(
                SkillComplianceIssue(
                    "missing_extends",
                    "core integral_* skills must extend embedded_integral_action base SOP",
                )
            )

    allowed_tools = set(_normalize_allowed_tools(meta.get("allowed-tools")))
    has_integral_tools = bool(
        allowed_tools or any(t.startswith("integral_") for t in manifest_tools)
    )

    if tier.startswith("bundle") and has_integral_tools:
        if not meta.get("extends"):
            report.issues.append(
                SkillComplianceIssue(
                    "missing_extends",
                    "Bundle skills using integral_* tools must declare extends",
                )
            )
        req_actions = meta.get("requires-actions") or []
        if "EmbeddedIntegralAction" not in req_actions:
            report.issues.append(
                SkillComplianceIssue(
                    "missing_requires_actions",
                    "requires-actions must include EmbeddedIntegralAction",
                )
            )

    if allowed_tools and manifest_tools and not allowed_tools.issubset(manifest_tools):
        extra = sorted(allowed_tools - manifest_tools)
        report.issues.append(
            SkillComplianceIssue(
                "allowed_tools_not_in_manifest",
                f"allowed-tools not in manifest tools_required: {extra}",
            )
        )

    if tier == "core" and known_tool_names is not None and allowed_tools:
        body_tool_refs = {
            ref for ref in _integral_backtick_refs(body) if ref in known_tool_names
        }
        missing_from_allowed = sorted(body_tool_refs - allowed_tools)
        if missing_from_allowed:
            report.issues.append(
                SkillComplianceIssue(
                    "body_tool_not_in_allowed_tools",
                    f"Body references tools not in allowed-tools: {missing_from_allowed}",
                )
            )

    if tier.startswith("bundle") and allowed_tools and not manifest_tools:
        report.issues.append(
            SkillComplianceIssue(
                "empty_manifest_tools",
                "manifest tools_required must match SKILL.md allowed-tools",
            )
        )

    manifest_desc = str((manifest_meta or {}).get("description") or "").strip()
    skill_desc = str(meta.get("description") or "").strip()
    if tier.startswith("bundle") and manifest_desc and skill_desc:
        if manifest_desc != skill_desc:
            report.issues.append(
                SkillComplianceIssue(
                    "manifest_description_mismatch",
                    "profile.yaml description must match SKILL.md frontmatter",
                )
            )

    report.sections_present = detect_sections(body)
    enforce_sections = tier == "core" or (tier.startswith("bundle") and not private)
    if enforce_sections:
        for sec in MERGE_GATE_SECTIONS:
            if not report.sections_present.get(sec):
                report.issues.append(
                    SkillComplianceIssue(
                        f"missing_section_{sec}",
                        f"Missing required section: {sec}",
                    )
                )
        for sec in ("when_to_use", "procedure", "forbidden", "example"):
            if not report.sections_present.get(sec):
                severity: Literal["error", "warning"] = (
                    "error" if tier in ("core", "bundle_public") else "warning"
                )
                report.issues.append(
                    SkillComplianceIssue(
                        f"missing_section_{sec}",
                        f"Missing section: {sec}",
                        severity=severity,
                    )
                )

    if known_tool_names is not None:
        core_skills = set(CORE_INTEGRAL_SKILL_NAMES)
        for ref in _integral_backtick_refs(body):
            if ref in core_skills:
                if "delegate" not in body.lower() and "skill" not in body.lower():
                    report.issues.append(
                        SkillComplianceIssue(
                            "ambiguous_skill_ref",
                            f"`{ref}` may be a skill name — use explicit skill delegation wording",
                            severity="warning",
                        )
                    )
            elif ref not in known_tool_names and not ref.endswith("_setup"):
                report.issues.append(
                    SkillComplianceIssue(
                        "unknown_tool_ref",
                        f"Backtick reference `{ref}` is not in tool catalogue",
                        severity="warning",
                    )
                )

    return report


def iter_core_skill_paths() -> List[Path]:
    """Return sorted paths to integral_* core agent SKILL.md files."""
    return sorted(Path(p) for p in glob.glob(str(_CORE_SKILLS_GLOB)))


def iter_bundle_skill_paths() -> List[Path]:
    """Return sorted paths to app-bundle SKILL.md files under package roots."""
    from app.services.package_paths import resolve_package_paths

    paths: List[Path] = []
    for root in resolve_package_paths():
        paths.extend(sorted(root.glob("*/skills/*/SKILL.md")))
    return sorted(set(paths))


def audit_all_skills(
    *,
    known_tool_names: Optional[Set[str]] = None,
) -> List[SkillComplianceReport]:
    """Run compliance checks on every core and bundle SKILL.md."""
    reports: List[SkillComplianceReport] = []
    for path in iter_core_skill_paths():
        reports.append(
            check_skill_file(path, tier="core", known_tool_names=known_tool_names)
        )
    for path in iter_bundle_skill_paths():
        bundle_dir = path.parents[2]
        manifest = _load_manifest_skill_meta(bundle_dir)
        meta = manifest.get(path.parent.name, {})
        tier: SkillTier = "bundle_private" if meta.get("private") else "bundle_public"
        reports.append(
            check_skill_file(
                path,
                tier=tier,
                manifest_meta=meta,
                known_tool_names=known_tool_names,
            )
        )
    return reports


def format_audit_markdown(reports: List[SkillComplianceReport]) -> str:
    """Generate skill-bundle-audit.md body."""
    from datetime import date

    bundles: Dict[str, List[SkillComplianceReport]] = {}
    core: List[SkillComplianceReport] = []
    for r in reports:
        if r.tier == "core":
            core.append(r)
        else:
            bundles.setdefault(r.bundle_slug, []).append(r)

    lines = [
        "# Skill Bundle Audit — machine-generated compliance report",
        "",
        f"**Date:** {date.today().isoformat()}.",
        "**Generator:** `backend/scripts/audit_skills.py` / "
        "[`skill_compliance.py`](../backend/app/services/skill_compliance.py).",
        "**Contract:** [skill-format-standard.md](./skill-format-standard.md).",
        "",
        "## Executive summary",
        "",
    ]

    total = len(reports)
    errors = sum(1 for r in reports if not r.ok)
    lines.append(
        f"- **{total}** skills audited ({len(core)} core, "
        f"{total - len(core)} bundle)."
    )
    lines.append(f"- **{errors}** skills with compliance errors.")
    lines.append("")

    lines.append("## Core integral_* skills")
    lines.append("")
    lines.append("| Skill | Sections | Lines | Status |")
    lines.append("|-------|----------|-------|--------|")
    for r in sorted(core, key=lambda x: x.skill_key):
        status = "PASS" if r.ok else "FAIL"
        lines.append(f"| `{r.skill_key}` | {r.score}/7 | {r.line_count} | {status} |")
    lines.append("")

    for slug in sorted(bundles):
        lines.append(f"## Bundle: `{slug}`")
        lines.append("")
        lines.append("| Skill | Private | Sections | Lines | Status | Issues |")
        lines.append("|-------|---------|----------|-------|--------|--------|")
        for r in sorted(bundles[slug], key=lambda x: x.skill_key):
            status = "PASS" if r.ok else "FAIL"
            issue_codes = ", ".join(i.code for i in r.issues if i.severity == "error")
            lines.append(
                f"| `{r.skill_key}` | {r.private} | {r.score}/7 | "
                f"{r.line_count} | {status} | {issue_codes or '—'} |"
            )
        lines.append("")

    lines.append("## Error detail")
    lines.append("")
    for r in reports:
        errs = [i for i in r.issues if i.severity == "error"]
        if not errs:
            continue
        lines.append(f"### `{r.skill_key}` ({r.path})")
        for e in errs:
            lines.append(f"- **{e.code}:** {e.message}")
        lines.append("")

    return "\n".join(lines)
