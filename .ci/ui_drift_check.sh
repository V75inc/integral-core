#!/usr/bin/env bash
# UI drift guard — blocks raw typography/surface utilities in feature code.
#
# Phase 3-A of the FE templating refactor (see
# .planning/ui-templating/adr-frontend-layering.md) ships typed primitives
# (`<Text>`, `<Surface>`, …) and forbids the underlying literals everywhere
# outside `frontend/src/ui/`. This script enforces that in pre-commit.
#
# Currently enforces:
#   1. No `text-[var(--text*)]` outside `frontend/src/ui/` —
#      use `<Text tone="…">`.
#   2. No `bg-[var(--panel)]` / `bg-[var(--panel-2)]` outside
#      `frontend/src/ui/` — use `<Surface tone="…">`.
#
# Future rules (added incrementally as the corresponding primitives ship):
#   - `width="max-w-[…]"` on `<Modal>` → use `max-w-dialog-{confirm,form,wide}`
#
# Bypass with `git commit --no-verify` only for documented exceptions
# (e.g. a primitive's own implementation file inside `frontend/src/ui/`,
# which is allowed). Anywhere else, fix the literal — the lint exists
# to keep one source of truth.
#
# Exit code: 0 = clean, 1 = violations found.

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

FAILED=0
RULES_RUN=0

# Staged frontend source files (tsx/ts). The guard scans ONLY these, so a
# backend-only commit isn't blocked by pre-existing drift in files this
# commit never touched. --diff-filter=ACM keeps added/copied/modified paths
# (present in the working tree); deletions are dropped. Read-loop instead of
# `mapfile` for bash 3.2 (macOS default) compatibility.
STAGED_FE_FILES=()
while IFS= read -r f; do
  [ -n "$f" ] && STAGED_FE_FILES+=("$f")
done < <(git diff --cached --name-only --diff-filter=ACM 2>/dev/null \
           | grep -E '^frontend/src/.*\.(tsx|ts)$' || true)

# Emit `path:line:content` matches for a grep pattern, restricted to the
# staged frontend files. No output when nothing relevant is staged. `-H`
# forces the path prefix even when only one file is scanned (grep drops it
# for a lone file, which would break the `path:linenum:content` parsing).
staged_grep() {
  [ ${#STAGED_FE_FILES[@]} -eq 0 ] && return 0
  grep -Hn "$@" "${STAGED_FE_FILES[@]}" 2>/dev/null || true
}

# ── Rule 1 — `text-[var(--text*)]` outside `frontend/src/ui/` ──────────
#
# `<Text variant="…" tone="…">` is the typed alternative. New code must
# use it. Pre-existing drift is tracked in
# `.planning/ui-templating/MIGRATION_LOG.md` and exempted via the
# allowlist below.

ALLOWLIST_FILE=".ci/ui_drift_allowlist.txt"

# Files currently allowed to contain the literal (Phase 3-A long tail).
# Each entry should be retired as the file is swept; do NOT add new
# entries casually — instead, write `<Text>` instead of the literal.
declare -a allowlist=()
if [ -f "$ALLOWLIST_FILE" ]; then
  while IFS= read -r line; do
    # Skip comments + blank lines.
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// /}" ]] && continue
    allowlist+=("$line")
  done < "$ALLOWLIST_FILE"
fi

# Frozen baseline — update only when removing entries (never increase).
# Matches service_layer_drift_check.sh ratchet pattern.
MAX_TEXT_ALLOWLIST_ENTRIES=164
if [ "${#allowlist[@]}" -gt "$MAX_TEXT_ALLOWLIST_ENTRIES" ]; then
  echo "FAIL: ui_drift_allowlist grew (${#allowlist[@]} > $MAX_TEXT_ALLOWLIST_ENTRIES)."
  echo "  Drain debt by migrating files to <Text>, then remove allowlist paths —"
  echo "  do NOT expand MAX_TEXT_ALLOWLIST_ENTRIES without a FE templating plan."
  exit 1
fi

is_allowed() {
  local path="$1"
  for entry in "${allowlist[@]}"; do
    if [[ "$path" == "$entry" ]]; then
      return 0
    fi
  done
  return 1
}

echo "ui-drift: rule 1 — text-[var(--text*)] outside frontend/src/ui/..."
RULES_RUN=$((RULES_RUN + 1))

# Find raw literals in the staged frontend files only (see STAGED_FE_FILES).
# Scanning the whole tree would block an unrelated commit on pre-existing
# drift in files it never touched.
violations=()
while IFS= read -r match; do
  # `match` format: `path:linenum:content`
  path="${match%%:*}"
  rest="${match#*:}"
  linenum="${rest%%:*}"
  content="${rest#*:}"

  # Exempt substrate directories (primitives, patterns, templates) and codemods.
  case "$path" in
    frontend/src/ui/*) continue ;;
    frontend/src/patterns/*) continue ;;
    frontend/src/templates/*) continue ;;
    .planning/ui-templating/codemods/*) continue ;;
  esac

  if is_allowed "$path"; then
    continue
  fi

  violations+=("$path:$linenum: $content")
done < <(staged_grep 'text-\[var(--text')

if [ ${#violations[@]} -gt 0 ]; then
  FAILED=1
  echo ""
  echo "ui-drift: rule 1 FAILED — found ${#violations[@]} raw typography literal(s):"
  for v in "${violations[@]}"; do
    echo "  $v"
  done
  echo ""
  echo "  Use <Text variant=\"...\" tone=\"...\"> from 'frontend/src/ui'."
  echo "  See .planning/ui-templating/TOKENS.md § Ink + frontend/src/ui/Text.tsx."
  echo "  Exempt a file (last resort) by adding its path to $ALLOWLIST_FILE."
  echo ""
fi

# ── Rule 2 — `bg-[var(--panel*)]` outside `frontend/src/ui/` ───────────
#
# `<Surface tone="panel">` / `<Surface tone="panel-2">` is the typed
# alternative. Same allowlist convention as rule 1 — file appears in
# `ui_drift_surface_allowlist.txt` only while it still contains drift.

SURFACE_ALLOWLIST_FILE=".ci/ui_drift_surface_allowlist.txt"
declare -a surface_allowlist=()
if [ -f "$SURFACE_ALLOWLIST_FILE" ]; then
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// /}" ]] && continue
    surface_allowlist+=("$line")
  done < "$SURFACE_ALLOWLIST_FILE"
fi

# Frozen baseline — update only when removing entries (never increase).
MAX_SURFACE_ALLOWLIST_ENTRIES=132
if [ "${#surface_allowlist[@]}" -gt "$MAX_SURFACE_ALLOWLIST_ENTRIES" ]; then
  echo "FAIL: ui_drift_surface_allowlist grew (${#surface_allowlist[@]} > $MAX_SURFACE_ALLOWLIST_ENTRIES)."
  echo "  Drain debt by migrating files to <Surface>, then remove allowlist paths —"
  echo "  do NOT expand MAX_SURFACE_ALLOWLIST_ENTRIES without a FE templating plan."
  exit 1
fi

is_surface_allowed() {
  local path="$1"
  for entry in "${surface_allowlist[@]}"; do
    if [[ "$path" == "$entry" ]]; then
      return 0
    fi
  done
  return 1
}

echo "ui-drift: rule 2 — bg-[var(--panel*)] outside frontend/src/ui/..."
RULES_RUN=$((RULES_RUN + 1))

surface_violations=()
while IFS= read -r match; do
  path="${match%%:*}"
  rest="${match#*:}"
  linenum="${rest%%:*}"
  content="${rest#*:}"

  case "$path" in
    frontend/src/ui/*) continue ;;
    .planning/ui-templating/codemods/*) continue ;;
  esac

  if is_surface_allowed "$path"; then
    continue
  fi

  surface_violations+=("$path:$linenum: $content")
done < <(staged_grep -E 'bg-\[var\(--panel(-2)?\)\]')

if [ ${#surface_violations[@]} -gt 0 ]; then
  FAILED=1
  echo ""
  echo "ui-drift: rule 2 FAILED — found ${#surface_violations[@]} raw surface literal(s):"
  for v in "${surface_violations[@]}"; do
    echo "  $v"
  done
  echo ""
  echo "  Use <Surface tone=\"panel\" | \"panel-2\" border=\"...\" radius=\"...\"> from 'frontend/src/ui'."
  echo "  See .planning/ui-templating/TOKENS.md § Surface + frontend/src/ui/Surface.tsx."
  echo "  Exempt a file (last resort) by adding its path to $SURFACE_ALLOWLIST_FILE."
  echo ""
fi

# ── Summary ─────────────────────────────────────────────────────────────
if [ "$FAILED" -ne 0 ]; then
  echo "ui-drift: $RULES_RUN rule(s) ran, at least one FAILED."
  exit 1
fi

echo "ui-drift: $RULES_RUN rule(s) clean."
exit 0
