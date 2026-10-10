# Build your first Integral App

Start with the external Reference Hello App. It is small enough to inspect and proves the public package boundary without adding domain code to Core.

## 1. Prepare Core

Follow the [source setup](../../README.md#run-from-source). Keep your dependency lock and use a development database. Set up private environment keys with the root bootstrap utility. Do not expose the demo configuration as production.

The reference package lives at `examples/reference-hello-app/`. It contains an authoring manifest, a Python tool, a skill, and a sandboxed iframe view. Its directory name matches `package.slug`.

## 2. Create an external package root

From the repository root:

```bash
mkdir -p integral-apps
cp -R examples/reference-hello-app integral-apps/
app_root="$PWD/integral-apps"
cd backend
INTEGRAL_PACKAGE_PATHS="$app_root" INTEGRAL_CORE_ONLY=0 .venv/bin/python -m app.main
```

`INTEGRAL_PACKAGE_PATHS` points to a parent of package directories. Core-only filtering must be off to admit this community App. Use trusted Python packages only in an appropriate development/trust configuration; production may require a signature.

Open the UI, select a workspace, inspect the library entry, and install the App through the library installation flow. A catalog entry alone is not an installed App.

## 3. Understand the manifest

Current disk authoring begins with:

```yaml
integral_operational_model_version: 3
scope: app
package:
  name: Reference Hello
  slug: reference-hello-app
  class: community_app
  version: 1.1.0
  trust_tier: trusted
```

This is an excerpt, not a complete package. The checked-in reference is the runnable example. The loader compiles YAML to canonical runtime schema version 2.

`app.tracks` defines the Notes Track and its Note EntryType. `app.tools` declares the echo handler and input schema. `app.hooks` binds an entry-create hook. `app.operations` exposes a read operation with `policy_action: app.read`. `app.skills` points to its on-disk SKILL.md. `view_types` defines a composite and `app.extension_views` declares the iframe asset.

Do not add a new Core conditional for your slug. If a domain behavior needs an operation, tool, or hook, declare it in the package and implement it through the public facade.

## 4. Use the scoped facade

Prefer `get`, `query`, and `invoke` over raw graph entities:

```python
async def inspect_record(ctx, object_ref: dict):
    record = await ctx.get(object_ref)
    return {"record": record}
```

This is a handler fragment; use the actual reference tool's signature and declaration as your implementation pattern. Core supplies the authenticated principal and workspace. Never accept a payload's user or workspace as authority.

OperationContext additionally exposes the installed App, operation key, idempotency/correlation identity, validated create/update helpers, conditional update, audit, and deduplicated notifications. `expected_record_revision` supports stale-update detection. Check the returned result rather than assuming a write occurred.

## 5. Give the App useful behavior

Declare queries with bounded inputs and explicit outputs. A Home widget expecting rows and totals needs both. Declare operations with policy and staging requirements. Keep read operations free of hidden writes.

Use a `member` field for an account reference, a `relation` for an operational entity, and `file`/`files` for attachments. Sensitive data needs a separate resource boundary rather than a hidden field.

Write skills in the standard Agent Skills format. Required frontmatter is name and description; disk names use lowercase hyphens. Skills instruct the resident and do not grant permission. Runtime overlay names are scoped and namespaced independently of disk names.

## 6. Validate and install

Run the existing reference contract tests from the backend environment:

```bash
.venv/bin/pytest tests/contract/test_reference_hello_app.py
```

When changing registered tools, core skills, bindings, or example Apps, regenerate the capability map with `make capability-map` from the repository root. Use `make verify` for the broad gate before committing a finished change.

Verify in the browser: install, open Notes, create and reload a Note, render its views, discover the operation, inspect scoped results, and confirm pause/removal withdraws the applicable capabilities. Include a principal without access. Test signatures separately for production packages.

## 7. Evolve the package

Change package identity only when creating a different App; its directory and slug must agree. Increment versions deliberately. Review installed-definition changes, schema impact, migrations, dependencies, and customizations before upgrade. A new library version does not automatically rewrite every installed instance.

Use [Operational Models](../operational-models/README.md), [bundle architecture](../backend/app-bundles-v1.md), [extension contracts](../platform/extension-contract-v1.md), and [qualification](../ops/QUALIFICATION.md) as the next references.
