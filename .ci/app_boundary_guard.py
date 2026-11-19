#!/usr/bin/env python3
"""Supplement the substrate check with a narrow runtime boundary regression gate."""

import ast
from collections import Counter
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
OWNER = r"(?:hr_host|business_host|app\.(?:packages|profiles|plugins))(?:\.|$)"
SIGNALS = {
    "app-id": re.compile(r"(?i)(?<![\w-])(?:hr_app|guyana-payroll-hr|payroll_filings|recruitment-app)(?![\w-])"),
    "named-vault-tool": re.compile(r"^e_sign_vault_(?:save|list|delete)$"),
    "named-entry-type": re.compile(r"^(?:Employee|Recruitment)$"),
    "hr-route": re.compile(r"/(?:me/onboarding-form|auth/complete-onboarding-form)(?![\w-])"),
    "implicit-field": re.compile(r"^(?:seed_from|onboarding_form|employee_onboarding|employee_id|employee_ids|contract_template_id|contract_|contract_status|pending_onboarding_form)$"),
    "recruitment-copy": re.compile(r"(?i)public job applications are not configured"),
    "hr-copy": re.compile(r"(?i)\b(?:personal details are filled in from HR records|employee onboarding forms?|employment contract|guyana payroll \(hr-linked\))\b"),
}
# Approximate JS/TS lexical scan. Quoted strings are captured ahead of comments
# so that URLs and comment-like literals are not silently dropped.
JS_TOKEN = re.compile(r"""(?P<string>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|(?P<comment>//[^\n]*|/\*[\s\S]*?\*/)""")


def collect(root):
    found = Counter()
    for area, suffixes in (
        ("backend/app", {".py"}),
        ("frontend/src", {".tsx", ".ts", ".js", ".jsx"}),
        ("sdk/python/integral_sdk", {".py"}),
        ("sdk/react/src", {".tsx", ".ts", ".js", ".jsx"}),
        ("sdk/ui/src", {".tsx", ".ts", ".js", ".jsx"}),
    ):
        for path in sorted((root / area).rglob("*")):
            if path.suffix not in suffixes or any(
                part in {"__tests__", "tests", "fixtures", "packages", "profiles"}
                for part in path.relative_to(root / area).parts[:-1]
            ) or re.search(r"\.(?:test|spec)\.", path.name):
                continue
            source = path.read_text(encoding="utf-8")
            rel = path.relative_to(root).as_posix()
            if path.suffix == ".py":
                tree = ast.parse(source, filename=rel)
                docs = {
                    id(node.body[0].value)
                    for node in ast.walk(tree)
                    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                }
                for n in ast.walk(tree):
                    names = (
                        ([n.module or ""] + [f"{n.module}.{a.name}" for a in n.names] if n.module == "app" else [n.module or ""]) if isinstance(n, ast.ImportFrom) else
                        [a.name for a in n.names] if isinstance(n, ast.Import) else []
                    )
                    for name in names:
                        if re.match(OWNER, name):
                            found[(rel, "app-import", name)] += 1
                values = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                          and isinstance(n.value, str) and id(n) not in docs]
            else:
                values = [m["string"][1:-1] for m in JS_TOKEN.finditer(source) if m["string"]]
                executable = JS_TOKEN.sub(" ", source)
                for match in re.finditer(r"\b(?:contract_status|pending_onboarding_form)\b", executable):
                    found[(rel, "implicit-field", match.group())] += 1
                opening_pattern = r"""\bsearchParams\.get\(\s*['"]opening['"]\s*\)"""
                for _ in re.finditer(opening_pattern, source):
                    found[(rel, "recruitment-query", "searchparams.get(opening)")] += 1
                # JSX visible text is not a quoted string.
                values.extend(m.group(1).strip() for m in re.finditer(r">([^<>{}\n]+)<", source))
            for value in values:
                if (re.match(OWNER, value) and value != "app.packages.") or (
                    path.suffix != ".py" and re.search(
                        r"(?:^|/)(?:hr_host|business_host|recruitment-app|payroll-guyana)(?:/|$)", value
                    )
                ):
                    found[(rel, "app-module-or-path", value)] += 1
                for rule, pattern in SIGNALS.items():
                    for match in pattern.finditer(value):
                        found[(rel, rule, match.group().casefold())] += 1
    return found


def main():
    actual = collect(ROOT)
    for key, count in sorted(actual.items()):
        print(f"named-App dependency: {' | '.join(key)} ({count})")
    return int(bool(actual))


if __name__ == "__main__":
    sys.exit(main())
