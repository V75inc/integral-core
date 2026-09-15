# Backend Views Subsystem

This folder is the canonical backend home for view-related support code used by
content profiles.

## Purpose

- Keep backend view contracts and registries in one human-identifiable place.
- Provide stable view type keys (`feed`, `kanban`, etc.) consumed by profile manifests.
- Keep the contract platform-neutral so web/mobile clients can implement the same keys.

## Files

- `view_contract_catalog.py`
  - Loads canonical contract rows from `backend/app/views/contracts/*.json`.
  - Can sync frontend artifact via `sync_frontend_contract_catalog()`.
  - Applies safe fallback contracts if canonical files are missing/invalid.
- `contracts/*.json`
  - Canonical one-file-per-view contract definitions.
  - Human-reviewable and easy to extend with drop-in JSON files.
- `content_profile_view_types.py`
  - Registers builtin + composable view types for runtime/profile validation.
  - Exposes registry helpers (`register_view_type`, `resolve`, `allowed_keys`, etc.).

## How To Extend

1. Add/update contract metadata as JSON files in `backend/app/views/contracts/`.
2. Sync frontend artifact with `cd backend && venv/bin/python scripts/sync_view_contracts.py`.
3. Add/update backend spec/config schema in `content_profile_view_types.py`.
4. Implement/adjust frontend widget behavior for each platform client.
5. Add/adjust tests in `backend/tests/test_content_profile_registries.py` and related suites.

## Compatibility Note

Legacy import paths under `app/services/*view*` are compatibility shims only.
Use `app.views.*` for all new code.

