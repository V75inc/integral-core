# AGENTS.md

Instructions for AI coding agents working in this repository (Codex, Cursor,
Copilot, Claude Code, and any other agent that reads `AGENTS.md`).

## Source of truth

**[`CLAUDE.md`](CLAUDE.md) is the authoritative agent guide. Read it in full
before doing any work.** It covers the project overview, the jvspatial
object-spatial contract (MUST-USE patterns + forbidden patterns), the access
model, content-profile substrate, the singular resident harness direction, and
the git/commit discipline. This file is a short pointer so agents that key off
`AGENTS.md` land in the right place; `CLAUDE.md` is what you follow.

## Non-negotiable rules (also in CLAUDE.md — repeated here so you can't miss them)

### Git & commits
1. **NEVER `git push` (or open/update a PR) without explicit user consent** in
   the current session. Committing is fine at any time; pushing is not. Do not
   push to "back up" work, because a task feels finished, or to be helpful —
   ask first.
2. **Gate every commit on a clean build.** Before committing: run the
   pre-commit hooks and fix **every** lint/format error (`black`, `isort`,
   `flake8`, `tsc --noEmit`, the substrate drift guards); run the relevant
   tests (`pytest` for touched backend areas — the full suite for
   substrate-touching changes — and the frontend suite for touched frontend
   areas) and fix **every** failure. Never commit failing lint or failing
   tests. Do not `--no-verify` past a real failure. If a fix is out of scope,
   stop and surface it rather than commit a broken state.

### Substrate
- Follow the jvspatial object-spatial contract in `CLAUDE.md` (use `@endpoint`,
  `JVSpatialAPIException`, schemas in `app/schemas/`, wire structural edges at
  `Node.create` per I-GRAPH-01, walkers for multi-hop, etc.). The
  hard-forbidden patterns there have no exceptions.
- Consult `docs/INVARIANTS.md` for substrate invariants before touching
  substrate code.

## One-time setup

```bash
git config core.hooksPath .githooks   # wires the pre-commit substrate guards
```

See `CLAUDE.md` → "Quick Start Commands" for backend/frontend setup, testing,
and code-quality commands.
