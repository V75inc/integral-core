# Commercial host integration

Core supplies workspace entitlement enforcement and optional host hooks. The
host owns subscriptions, pricing, payment providers, trials, credit balances,
and billing UI. This guide describes Core's integration surface for operators
and trusted host developers. Integral Business documents its own settings in
its [Billing guide](https://github.com/V75inc/integral-business/blob/main/README.md#billing).

## Configuration and startup

`INTEGRAL_HOST_EXTENSION_MODULE` defaults to empty. Set it to the importable
Python module supplied by your host distribution, install that module in the
API environment, and restart the API. Core imports the module after its own API
modules; import-time registration can supply routes and hooks. A configured
module import failure propagates and prevents API boot. Setting a module name
does not install a package or add the host's frontend.

For example, an Integral Business deployment uses:

```dotenv
INTEGRAL_HOST_EXTENSION_MODULE=billing.api
INTEGRAL_SUBSCRIPTION_REQUIRED=1
```

The first setting belongs to Core; the second belongs to Business.
`INTEGRAL_SUBSCRIPTION_REQUIRED` is **not a Core paywall switch**. Setting it to
`0`, or leaving the extension module empty, does not disable Core's commercial
App entitlement checks. Standalone Core has no subscription plans, checkout
routes, or Billing settings UI. The host distribution supplies those frontend
extensions through Core's host registry.

Host modules execute trusted server code. Configure them through operator-owned
deployment settings; never accept a module path or hook implementation from a
model response or App manifest. These process-wide hooks are separate from the
App manifest hook catalog in the [extension contract](extension-contract-v1.md).
Register them consistently in every API/worker process that executes Core.

## Commercial App access

A package classified as `commercial_app` requires access in its target workspace.
The entitlement key is `package.entitlement_key` when supplied, otherwise its
resolved package slug. Core checks the installed App's active definition and
preserves its installed package identity when resolving the key.

The admission order is:

1. Ask a registered host access resolver for an explicit boolean decision.
2. If it returns `True`, admit commercial access without creating a durable grant.
3. Otherwise, require a matching durable workspace entitlement whose status is
   `active` and whose optional expiry has not passed.
4. Refuse missing, revoked, or expired access with HTTP 403 and error code
   `entitlement.required`.

A non-boolean resolver result raises `TypeError`; resolver exceptions propagate.
Neither grants access. `False` means use the durable entitlement check, not an
explicit deny of an otherwise active entitlement. A host using temporary access
must define its scope and expiry itself; Core does not turn that decision into
an `Entitlement`, ownership edge, or collaborator grant.

Commercial access does not replace authentication, workspace admission, resource
permissions, App lifecycle checks, or operation approval. It is checked during
install, settings finalization, resume, startup hook registration, App operation
and query dispatch, and active-App resource validation. An old installation or
cached UI state does not establish current commercial access.

Installed-App enforcement applies to catalog installations carrying
`installed_from_library_id`. Generic Operational Model authoring/library merge
flows are separate; a host must assess their commercial policy independently.
See the [host entitlement access reference](../backend/HOST_ENTITLEMENT_ACCESS.md)
for invariant mapping, startup dependencies, and read-only BYOK readiness.

## Durable entitlement API

These authenticated endpoints use an explicit workspace identifier. Core allows
a current platform administrator or the target workspace's admin/owner to
administer that workspace's entitlements. A registered host authorizer can
further restrict grant/revoke operations, for example to platform administrators
on a subscription-enforced deployment.

| Endpoint | Input | Result |
| --- | --- | --- |
| `GET /api/entitlements` | `workspace_id` query parameter | Workspace entitlement rows |
| `POST /api/entitlements/grant` | JSON `workspace_id`, `entitlement_key`; optional `package_slug`, `expires_at` | Created/reactivated manual entitlement |
| `POST /api/entitlements/revoke` | JSON `workspace_id`, `entitlement_key` | Revocation status, paused App IDs, continuity policy |

Example request body for an authorized operator-managed trial:

```json
{
  "workspace_id": "<target workspace>",
  "entitlement_key": "<commercial package key>",
  "package_slug": "<package slug>",
  "expires_at": "2026-11-01T00:00:00+00:00"
}
```

Use normalized UTC timestamps consistent with Core's stored ISO format. Omitted
expiry creates an open-ended manual grant. Core's current expiry check compares
stored ISO timestamp strings; do not supply local-time or mixed-format expiry
values. These API calls grant access, not evidence of payment.

Entitlements expose `status`, `source`, expiry/revocation timestamps, and the
continuity fields `on_loss`, `data_access`, and `retention`. Manual API grants
use `source=manual`. Host provider projection can use `grant_entitlement` with
`respect_manual=True` and `revoke_provider_entitlement` to preserve manual
operator overrides. The ordinary revoke API can revoke manual access; the
provider-specific service deliberately leaves manual rows unchanged.

## Loss of access and recovery

Revocation marks the durable entitlement revoked and attempts to pause matching
installed Apps. The default continuity values are `on_loss=pause`,
`data_access=core_generic_read`, and `retention=retain_until_uninstall`. Generic
Core read/export paths retain their own authorization checks. Revocation does
not itself uninstall an App or delete its records.

Expiry is evaluated at admission; it does not by itself rewrite the row status
or synchronously pause every App. The host owns scheduled reconciliation and
subscription state projection. A temporary resolver returning `True` can still
admit access after a durable row is revoked; the host must withdraw that
exception when its policy requires access to stop.

Restore access through the host's supported billing or administrator flow, then
resume paused Apps through the normal lifecycle API. Granting an entitlement
alone does not automatically resume an already paused App. Inspect revocation
results and logs: an individual pause failure is logged and is not proof that
all matching Apps transitioned successfully.

When troubleshooting:

- For `entitlement.required`, check the target workspace, resolved package key,
  row status/expiry, and any temporary host decision. Fix commercial access;
  do not broaden user resource permissions to bypass the gate.
- For a denied grant/revoke, check both Core's current administrator authority
  and the host mutation policy.
- For startup failure, check that the configured extension can import and its
  required middleware can mount. A middleware factory registered as required
  aborts startup on an exception; an optional one logs and continues. A factory
  returning `None` explicitly skips its registration.

## Host hooks and defaults

Registrations live in `backend/app/services/host_hooks.py`. They are trusted
host integration APIs, not model tools or permission grants.

| Registration | Host responsibility | Core behavior when absent |
| --- | --- | --- |
| `register_entitlement_access_resolver` | Temporary workspace/key/package access decision | Require durable entitlement |
| `register_entitlement_mutation_authorizer` | Additional grant/revoke policy | Core admin/owner checks still apply |
| `register_platform_quota_assert` | Pre-operation platform-funded AI allowance check; raise to refuse | No additional host quota gate |
| `register_usage_event_recorder` | Post-operation usage recording and host ledger reconciliation | No host usage recording |
| `register_model_price_resolver` | Qualified model price evidence for bounded work | Bounded-work price admission refuses missing evidence |
| `register_model_bounds_resolver` | Trusted payload limits for approved bounded-work dispatch | Missing required bounds prevent that dispatch |
| `register_workspace_export_enricher` | Opaque host fields on workspace exports | Original Core payload |
| `register_middleware_factory` | Host request middleware; required by default | No host middleware |
| `register_background_task` | Host reconciliation/task coroutine | No host task |

AI allowance checks and App entitlements are separate. The deployment-funded
model route invokes the platform quota gate; user/workspace credentials follow
the model credential resolver. The host decides how funding source affects
charging. Core's `INTEGRAL_NATIVE_TURN_TOKEN_LIMIT`, context limits and timeouts
are execution bounds, not a subscription balance or plan price. See
[configuration](../ops/CONFIGURATION.md) and the
[bounded-work contract](../backend/work-mandate-contract.md).

The native usage bridge includes workspace/user, input/output tokens, model,
run/thread identity, funding source, and an idempotency key when recording a
model observation. A host should deduplicate observations and persist its own
ledger. Usage recorder failure is logged, counted, and available through
`get_last_usage_record_failure()` / `get_usage_record_failure_count()`; it
returns a `usage_record_failed` result without failing the completed turn.
Those diagnostic counters/snapshots are process-local, not a durable retry
queue. A successful model answer does not prove its billing record settled.

## Qualification

A host release should test missing/expired/revoked entitlements, temporary access
withdrawal, resolver errors, ordinary-user denial, restricted manual mutations,
install/resume/execution rechecks, retained generic data access, quota rejection,
and idempotent metering recovery. Check restart and multiple processes as well
as in-process tests. Confirm that a billing UI reflects backend access and never
treats a checkout return URL as payment proof.

Core regression examples include `backend/tests/test_host_entitlement_access.py`,
`backend/tests/test_host_hooks.py`, `backend/tests/test_host_boundary.py`,
`backend/tests/test_entitlements_provider.py`, and
`backend/tests/contract/test_f3_entitlement_kill_switch.py`. Their source coverage
is not payment-provider or production billing qualification.
