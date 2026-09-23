# Author your first Integral App

This guide builds a small **Studio Equipment Desk**: a place to register
cameras and lights, see whether they are available, and check one out to a
team member.

The point is not the equipment. It is to show how a business idea becomes an
Integral App without changing Integral Core. Replace cameras with rental cars,
client cases, inspection items, clinic rooms, or whatever your operation needs.

By the end, you will have a package that gives a workspace:

- an **Equipment** track with an **Equipment item** record type;
- an inventory table and an availability board;
- a safe **Check out equipment** action; and
- a named skill the resident harness can use in the right workspace context.

## What you are making

An Integral App is a folder that describes a domain model and, where useful,
adds small pieces of domain behaviour. Core owns the graph, permissions,
validation, audit, views, and agent perimeter. Your App owns the language and
rules of the operation.

| If you need… | Put it in… | Example |
| --- | --- | --- |
| A record somebody will work with | A track and entry type | An equipment item with a serial number |
| A field or relationship | The entry type schema | Availability, category, current holder |
| A way to see the work | A Core view declaration | Inventory table or availability board |
| A business action | A typed operation and handler | Check out an available camera |
| Guidance for the resident | A declarative skill | “Find equipment available for a shoot” |
| A specialised visual panel | An optional extension view | A compact equipment-detail panel |

Start declaratively. Many useful Apps need only a manifest: tracks, fields,
views, and skills. Add Python only when the operation has a rule that cannot be
expressed as ordinary data entry, such as “only equipment in the available
state can be checked out.”

## Before you begin

Run Core locally from the [repository README](../../README.md). For App
authoring, Core must be restarted with your package directory enabled.

```bash
# From the Integral Core checkout
./scripts/bootstrap_env.sh .env .env.example
docker compose up -d db

cd backend
uv sync --frozen --extra dev --extra test
```

Use a directory outside Core for your own packages. This keeps the extension
boundary visible from the first day.

If the `integral` command is on your `PATH` (the `0.1.1rc4` wheel and later),
generate the distro instead of copying by hand:

```bash
integral init ../my-integral --slug studio-equipment --name "Studio Equipment Desk"
```

That writes `.env`, a README, and `integral-apps/studio-equipment/` with
`operational-model.yaml`, `tools/`, `skills/`, and `views/`. Source `.env`
before `python -m app.main`. The installed package does not read that file
on its own.

From a checkout, the same shape written by hand starts here:

```bash
mkdir -p ../integral-apps
cp -R examples/reference-hello-app ../integral-apps/studio-equipment
```

Your starting point should look like this:

```text
integral-apps/
└── studio-equipment/
    ├── operational-model.yaml             # schema, views, tools, operations, skills
    ├── tools/
    │   ├── __init__.py
    │   └── equipment.py         # only needed for custom behaviour
    └── skills/
        └── find_available/
            └── SKILL.md
```

Delete the copied `views/` directory if you do not need a package-owned panel.
Core’s table, board, feed, calendar, and gallery views need no frontend code.

## 1. Give the App a clear name and one useful track

Open `../integral-apps/studio-equipment/operational-model.yaml` and replace its contents
with the following minimal manifest. The names shown here become the words
people see in Integral, so write them as your team speaks.

```yaml
integral_operational_model_version: 3
scope: app

package:
  name: Studio Equipment Desk
  slug: studio-equipment
  class: community_app
  version: 0.1.0
  trust_tier: trusted
  publisher: Your organization
  description: Keep studio equipment, availability, and handovers in one place.

app:
  description: A small equipment register for a production team.
  defaults:
    provision_prescribed_tracks: true
    default_track: equipment
    default_view: equipment_table
  tracks:
  - key: equipment
    name: Equipment
    description: One record for each camera, lens, light, or accessory.
    provision_on_create: true
    entry_types:
    - key: equipment_item
      name: Equipment item
      icon: camera
      fields:
      - key: asset_tag
        name: Asset tag
        type: text
        required: true
        index: true
      - key: category
        name: Category
        type: select
        enum: [camera, lens, light, audio, grip, other]
        required: true
      - key: availability
        name: Availability
        type: select
        enum: [available, checked_out, maintenance]
        required: true
        index: true
      - key: serial_number
        name: Serial number
        type: text
      - key: current_holder
        name: Current holder
        type: text
      base_fields:
        title:
          label: Equipment name
          order: 0
        body:
          label: Notes
          order: 1000
    views:
    - key: equipment_table
      name: Inventory
      view_type: table
      entry_type_keys: [equipment_item]
      default_entry_type: equipment_item
      is_default: true
    - key: availability_board
      name: Availability
      view_type: kanban
      entry_type_keys: [equipment_item]
      default_entry_type: equipment_item
      group_by: custom_fields.availability
      kanban_columns:
      - key: available
        label: Available
      - key: checked_out
        label: Checked out
      - key: maintenance
        label: Maintenance
```

This alone creates a usable App. A member can install it, add “Sony FX3”, set
its tag to `CAM-014`, choose `available`, and then use the Inventory or
Availability view. Core validates required fields and persists the records in
the workspace graph.

### Choose stable keys

The `key` values are the App’s durable vocabulary. Users can rename “Equipment”
to “Kit” later, but changing `equipment`, `equipment_item`, or `availability`
after data exists is a schema migration. Use short, specific, lowercase keys
and treat them as API names.

## 2. Run Core with your App visible

Start the API from the `backend` directory with your package root enabled.
These variables must be present when the process starts, so stop and restart an
already-running local API first.

```bash
export INTEGRAL_PACKAGE_PATHS="$PWD/../../integral-apps"
export INTEGRAL_CORE_ONLY=0
.venv/bin/python -m app.main
```

The exact path depends on where you made `integral-apps`. From the repository
root it would be:

```bash
export INTEGRAL_PACKAGE_PATHS="$PWD/../integral-apps"
```

Then open [http://localhost:9006](http://localhost:9006), sign in, select the
workspace where you want the App, and use **Apps** to install **Studio
Equipment Desk**. Installation materializes its Equipment track, schema, and
views in that workspace. If it does not appear in the library, check the
terminal that started Core: package discovery reports invalid manifests there.

For an API-driven install, first get the library Operational Model id from the
library response. The current compatibility query field remains
`library_operational_model_id`:

```http
POST /api/workspaces/{workspace_id}/apps/install?library_operational_model_id={library_operational_model_id}
```

Core returns the installed `app_id`. Keep it: typed App operations address the
installed App instance, not its package slug.

## 3. Add a real business action

Ordinary entry creation is enough for most work. Add an operation when you
need a rule to hold even if the action comes from the UI, the resident harness,
or an MCP client.

For Studio Equipment Desk, “check out” should only work when the item is
currently available. Add this to `app:` in `operational-model.yaml`:

```yaml
  tools:
  - key: check_out_equipment
    name: Check out equipment
    description: Mark available equipment as checked out to a named holder.
    handler_ref: tools.equipment:check_out_equipment
    parameters_schema:
      type: object
      properties:
        equipment_id:
          type: string
        holder:
          type: string
      required: [equipment_id, holder]

  operations:
  - key: check_out_equipment
    kind: execute
    name: Check out equipment
    description: Move available equipment into a checked-out state.
    policy_action: entry.create
    staging_level: none
    idempotency_key: supported
    tool: check_out_equipment
    input_schema:
      type: object
      properties:
        equipment_id:
          type: string
        holder:
          type: string
      required: [equipment_id, holder]
```

Create `tools/equipment.py` with the handler:

```python
from typing import Any

from integral_sdk import OperationContext


async def check_out_equipment(
    input: dict[str, Any], ctx: OperationContext
) -> dict[str, Any]:
    equipment_id = str(input["equipment_id"])
    holder = str(input["holder"]).strip()

    if not holder:
        return {"ok": False, "error_code": "invalid_input", "message": "holder is required"}

    changed, reason = await ctx.conditional_update_entry_fields(
        equipment_id,
        state_field="availability",
        expected_state="available",
        updates={"availability": "checked_out", "current_holder": holder},
    )
    if not changed:
        return {
            "ok": False,
            "error_code": reason or "state_conflict",
            "message": "Equipment is no longer available",
        }

    await ctx.emit_audit(
        "studio_equipment.checked_out",
        {"equipment_id": equipment_id, "holder": holder},
    )
    return {"ok": True, "equipment_id": equipment_id, "holder": holder}
```

The handler imports only `integral_sdk`. It cannot reach around Core’s
permission, schema, graph-wiring, audit, or workspace-scope rules. The
conditional update also protects against two people trying to check out the
same camera at once.

Restart the API so the changed package is discovered. Then invoke the action
against the **installed** App:

```http
POST /api/extensions/{app_id}/operations/check_out_equipment
Idempotency-Key: studio-checkout-001
Content-Type: application/json

{
  "input": {
    "equipment_id": "{entry_id}",
    "holder": "Maya Singh"
  }
}
```

A successful response returns `ok: true`. Repeat the same request with a new
idempotency key: it should return a state conflict because the item is already
checked out. This is a useful first proof that your operational rule, rather
than only the screen, is doing the work.

## 4. Help the resident use the App well

A declarative skill gives the resident contextual guidance without shipping
executable code. Add this to `app:`:

```yaml
  skills:
  - key: find_available
    name: Find available equipment
    kind: declarative
    description: Help a producer find suitable equipment that is ready to use.
    prompt_template: skills/find_available/SKILL.md
```

Then create `skills/find_available/SKILL.md`:

```markdown
---
name: find_available
description: Find studio equipment that is currently available.
spec: jv
requires-actions: [EmbeddedIntegralAction]
extends: action:integral/embedded_integral_action
allowed-tools: []
---

# Find available equipment

When someone asks for studio equipment, first clarify the category and date.
Search the Equipment track for items whose availability is `available`.
Present the asset tag, name, and notes. Do not promise a checkout: offer to
prepare one for the user to review.
```

The resident sees this guidance only in the App’s workspace context. Its
writes still pass through Integral’s normal staging and policy controls.

## 5. Test the experience a person will actually have

Use this short acceptance run after every meaningful change:

1. Install the App into a fresh workspace.
2. Create an equipment item in the Inventory view: “Sony FX3”, `CAM-014`,
   category `camera`, availability `available`.
3. Open the Availability board and confirm the card appears in **Available**.
4. Call **Check out equipment** with that item and a holder.
5. Refresh the board and confirm it has moved to **Checked out** and shows the
   holder on the record.
6. Try the same action again. Confirm it is refused instead of creating a
   second checkout.
7. Ask the resident, “What cameras are available?” Confirm it uses the
   Equipment vocabulary and does not claim a checkout has happened without a
   proposed or authorised action.

Platform checks are still valuable, but they prove the contract rather than
your particular workflow:

```bash
make verify-contract
make verify-core-only
make verify-independent-artifacts
```

Use `make build-asset-register` and
`examples/asset-register/` as the reference when you are ready to package a
larger App with multiple tracks, relations, read operations, and durable
lifecycle behaviour.

## When to add more

| Need | Add next |
| --- | --- |
| One kind of record and a few views | Stay manifest-only |
| A cross-record rule or state transition | A typed operation using `OperationContext` |
| A repeated resident workflow | A declarative skill |
| A highly tailored detail panel | An `extension_view` with a sandboxed package asset |
| Several related lists | More tracks and relation fields |
| A distributable package | Build, checksum, and sign the archive as described in [bundle signing](../ops/OPERATIONAL_MODEL_SIGNING.md) |

Avoid creating a custom view or Python handler merely because a conventional
screen or endpoint exists elsewhere. First use the graph schema and Core view
palette. This keeps the App smaller, makes it work for people and agents, and
preserves the public extension boundary.

## Common mistakes

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| The App is absent from the library | Package path was not set when the API started | Restart with `INTEGRAL_PACKAGE_PATHS` and `INTEGRAL_CORE_ONLY=0` |
| The package is rejected | YAML indentation, duplicate keys, or an invalid manifest reference | Read the API startup log and compare with `examples/reference-hello-app/operational-model.yaml` |
| An operation cannot find a record | It used an id from another workspace or App | Resolve records through the injected context and keep the operation App-scoped |
| A handler needs `app.models` or `app.services` | The public facade is missing a needed capability | Do not import Core internals; document the missing capability and propose an extension-contract addition |
| A change seems ignored | The API process is still running the previous package contents | Restart the API during local package development |

## Where to go next

- [Extension Contract v1](../platform/extension-contract-v1.md) defines the
  public compatibility boundary.
- [App bundles v1](../backend/app-bundles-v1.md) is the full manifest and
  lifecycle reference.
- [Asset Register](../../examples/asset-register/operational-model.yaml) is the working
  multi-track example.
- [Independent developer trial log](quickstart-trial-log.md) records a clean
  external-package proof.
- [Core README](../../README.md) covers deployment and repository verification.
