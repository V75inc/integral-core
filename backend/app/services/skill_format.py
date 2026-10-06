"""Provider-neutral parsing of the Agent Skills SKILL.md format."""

from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

FRONTMATTER_FIELDS = frozenset(
    {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
)


def parse_skill_document(path: Path) -> Tuple[Dict[str, Any], str]:
    """Read YAML frontmatter and Markdown without vendor inheritance."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path}: missing YAML frontmatter")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError(f"{path}: unterminated YAML frontmatter")
    try:
        meta = yaml.safe_load("".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid YAML frontmatter") from exc
    if not isinstance(meta, dict):
        raise ValueError(f"{path}: frontmatter must be a mapping")
    return meta, "".join(lines[end + 1 :]).strip()


def allowed_tool_names(value: Any) -> List[str]:
    """Parse the standard space-separated tools field."""
    return value.split() if isinstance(value, str) else []
