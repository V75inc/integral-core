"""Tool-manifest loader + validator (M2a Task 1).

``backend/app/agentive/tool_manifest.yaml`` is the single canonical definition
of the Integral agent tool surface. This module parses it into typed
:class:`ToolSpec` records and validates the registry against the substrate's
two ground-truth vocabularies:

* :data:`app.schemas.policy.PolicyAction` — the canonical ``policy_action``
  Literal (plus the manifest's two ``genuinely_absent`` add-on gates).
* :func:`app.agentive.staging_executors.supports` — the registered
  ``StagedChange`` executor kinds backing ``op_class == "propose"`` tools.

The manifest groups tools under ``domains.<domain_key>.tools[]``. Each tool
entry carries ``name``, ``status`` (existing/alias/gap), ``op_class``
(read/propose/execute), ``priority``, ``summary``, ``http`` (the backing route
or ``(service ...)`` form), ``policy_action`` (the gate, or ``null``),
optionally ``staging_kind``, plus ``params`` / ``returns`` / ``privacy``.

Validation is route-agnostic: it does NOT check that an ``http`` route exists
(that lands in a later task). It only enforces the op-class literal set, the
policy-action vocabulary, and the staging-kind contract for ``propose`` tools.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, get_args

import yaml
from pydantic import BaseModel

from app.agentive import staging_executors
from app.schemas.policy import PolicyAction

# --------------------------------------------------------------------------- #
# Manifest location
# --------------------------------------------------------------------------- #
# manifest.py lives at backend/app/agentive/tooling/manifest.py; the manifest
# lives one level up, inside the agentive package, at
# backend/app/agentive/tool_manifest.yaml. parents[1] is that package dir
# (tooling -> agentive). Keeping it inside ``app/`` means it ships with the
# Docker image (which COPYs ``app``, not ``docs``) and resolves identically in
# dev and in the container (/app/app/agentive/tool_manifest.yaml).
DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "tool_manifest.yaml"

# Env-var override for flexibility (CI, alternate checkouts, tests).
_ENV_OVERRIDE = "INTEGRAL_TOOL_MANIFEST"

OpClass = Literal["read", "propose", "execute"]

# HTTP verbs the manifest uses in its "METHOD /path" http strings.
_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}

# Policy-action gates the manifest legitimately wants that the PolicyAction
# Literal does not (yet) contain — mirrors the manifest's
# ``policy_actions.genuinely_absent`` block. Both are optional add-on gates.
_GENUINELY_ABSENT_POLICY_ACTIONS = {"tag.assign", "workspace.read"}

# ``propose`` tools that stage through a non-registry path (conversation
# context, or a staging seam not yet wired to a dedicated executor kind) and
# therefore legitimately carry a null ``staging_kind`` in the manifest. This
# mirrors the ``genuinely_absent`` allowlist idiom for policy actions: the
# manifest declares these with ``staging_kind: null`` on purpose, so nulling
# them is not a violation. A ``propose`` tool NOT on this list (e.g.
# ``integral_create_entry``) MUST declare a known staging kind.
#
# Membership means "will never mint a StagedChange", NOT "not built yet" —
# ``gap`` entries are exempt from rule (c) by status and need no entry here.
# Two gaps below (``integral_workspace_setup``, ``integral_onboard_user``) are
# listed anyway because they are exempt on the merits: even once implemented,
# neither is a single stageable mutation.
_STAGING_EXEMPT_PROPOSE_TOOLS = {
    # integral_set_focus (T5c): EPHEMERAL conversation/session state — sets the
    # focused track/space on a ConversationContext, NOT a substrate write. It is
    # ``propose`` in the op-class taxonomy but mints NO StagedChange (there is
    # nothing to bless; PC-8 stage-substrate-mutations-for-bless does not apply
    # to ephemeral context ops). It dispatches via the DIRECT-EXECUTE seam
    # (ToolBinding.direct_ref → conversation_context.set_focus_for_dispatch),
    # running immediately. So it legitimately carries ``staging_kind: null`` and
    # stays exempt. RECONCILIATION NOTE: its op_class should be reconsidered — it
    # behaves like a (read-adjacent) context op, not a propose/stage-a-mutation.
    # Recommend a dedicated ``context`` op_class (or reclassifying to ``read``
    # with a side effect) in a future manifest pass; tracked for T6.
    "integral_set_focus",
    # Batch-control tools (Phase 0 batch staging): EPHEMERAL workflow-control ops
    # that open / commit / cancel a staging batch for the acting (user, session).
    # begin/cancel mint nothing; commit mints a single ``kind="batch"`` token
    # INTERNALLY via ``staging.commit_batch`` (not the per-tool stager→mint path),
    # so all three legitimately carry ``staging_kind: null`` and are intercepted by
    # name in ``_dispatch_propose`` before the stager check. The ``batch`` executor
    # kind itself is registered in ``staging_executors`` and exercised on bless.
    "integral_begin_batch",
    "integral_build_approved_design",
    "integral_commit_batch",
    "integral_cancel_batch",
    # integral_ask_user: same shape as propose_design. ``propose`` in the
    # op-class taxonomy, but it mints NO StagedChange — there is nothing to
    # bless, only a question to answer. Intercepted by name in
    # ``_dispatch_propose`` because it needs the live session_id to key the
    # thread marker, and calls ``chat_threads.record_pending_question``
    # directly. Legitimately carries ``staging_kind: null``.
    "integral_ask_user",
    # Design markers and session artifacts are conversation-scoped working
    # state. They never mint a StagedChange, and dispatch through named
    # intercepts because they need the live session id. Keep the allowlist
    # explicit so a new immediate propose tool cannot silently bypass staging.
    "integral_propose_design",
    "integral_upsert_artifact",
    "integral_get_artifact",
    "integral_list_artifacts",
    # integral_add_comment RECONCILED (M2a Task 5b): ``add_comment`` executor
    # wired (create_comment handler) — manifest declares staging_kind, no longer
    # exempt.
    # integral_draft_new_model RECONCILED (M2a Task 5b): ``draft_new_profile``
    # executor wired (create_empty_library_draft service) — no longer exempt.
    # integral_author_model RECONCILED (M2a Task 5a): the ``author_operational_model``
    # executor already exists in staging_executors, so the manifest now declares
    # ``staging_kind: author_operational_model`` and the tool is wired with a stager —
    # no longer exempt.
    # integral_create_app RECONCILED (M2a Task 5b): ``create_app`` executor wired
    # (create_app handler) — no longer exempt.
    # integral_share RECONCILED (T5c): the ``share`` executor (collaborator-add
    # path, email→user resolution at bless) is wired; the manifest now declares
    # ``staging_kind: share`` and the tool carries a stager — no longer exempt.
    # integral_workspace_setup (T5c): DEFERRED — there is NO legacy execute_tool
    # branch and no orchestration service; the legacy code carries only a persona
    # metadata description, never a callable mutation. Nothing to stage. Stays
    # exempt (``staging_kind: null``) and unwired (ToolBinding.stager is None →
    # propose dispatch returns ``not_implemented``) until a real
    # workspace-template orchestration service exists to back it.
    "integral_workspace_setup",
    # integral_onboard_user (T5c): DEFERRED — the legacy ``integral_onboard_user``
    # is a genuine MULTI-TURN state machine (step + context → next_step/prompt;
    # multiple steps each perform writes: create apps/tracks, set prefs,
    # finalize). It is NOT a single stageable mutation; a StagedChange would be a
    # meaningless envelope (which step?). It is resident-orchestration-only — the
    # LLM advances the flow turn-by-turn — and should NOT be an external-MCP
    # stage-and-bless tool. Stays exempt + unwired pending a non-staging design.
    "integral_onboard_user",
}


class ManifestError(Exception):
    """Raised when the parsed manifest violates a validation rule.

    The message enumerates ALL offending entries (violations are collected,
    then raised once) so a single run surfaces every problem. Messages embed
    the substring ``policy_action`` for policy-action violations and
    ``staging_kind`` for staging-kind violations.
    """


class HttpSpec(BaseModel):
    """Parsed ``http`` field of a manifest tool entry.

    A ``METHOD /path`` string (e.g. ``"GET /api/tracks"``) parses into
    ``method`` + ``path``. Service-backed entries whose ``http`` is NOT a
    ``METHOD /path`` form (e.g. ``"(agentive service: smart_file)"`` or
    ``"(operational_model draft)"``) parse with ``method == "SERVICE"`` and the
    raw string as ``path`` — these dispatch via a service binding later, not a
    route.
    """

    method: str
    path: str

    @classmethod
    def parse(cls, raw: str) -> "HttpSpec":
        """Parse a manifest ``http`` string into method + path."""
        text = (raw or "").strip()
        # A route form is "<VERB> <path...>" where the first token is an HTTP
        # verb and the remainder starts with "/". Anything else (parenthetical
        # service descriptors, multi-route convenience strings) is SERVICE.
        parts = text.split(None, 1)
        if (
            len(parts) == 2
            and parts[0].upper() in _HTTP_METHODS
            and parts[1].lstrip().startswith("/")
        ):
            return cls(method=parts[0].upper(), path=parts[1].strip())
        return cls(method="SERVICE", path=text)


class ToolSpec(BaseModel):
    """A single tool entry from the manifest, typed and ready to bind.

    Field names mirror the manifest. ``params`` / ``returns`` / ``privacy``
    are carried verbatim for downstream binding; this module does not validate
    their internal shape.
    """

    name: str
    status: str
    op_class: OpClass
    priority: str
    summary: str
    http: HttpSpec
    policy_action: Optional[str] = None
    staging_kind: Optional[str] = None
    params: Dict[str, Any] = {}
    returns: Optional[Dict[str, Any]] = None
    privacy: Optional[str] = None

    @classmethod
    def from_entry(cls, entry: Dict[str, Any]) -> "ToolSpec":
        """Build a ToolSpec from a raw manifest tool dict."""
        return cls(
            name=entry["name"],
            status=entry["status"],
            op_class=entry["op_class"],
            priority=entry["priority"],
            summary=entry["summary"],
            http=HttpSpec.parse(entry.get("http", "")),
            policy_action=entry.get("policy_action"),
            staging_kind=entry.get("staging_kind"),
            params=entry.get("params") or {},
            returns=entry.get("returns"),
            privacy=entry.get("privacy"),
        )


def _resolve_manifest_path(path: Optional[Path | str]) -> Path:
    """Resolve the manifest path: explicit arg > env override > default."""
    if path is not None:
        return Path(path)
    env = os.environ.get(_ENV_OVERRIDE)
    if env:
        return Path(env)
    return DEFAULT_MANIFEST_PATH


def load_manifest(path: Optional[Path | str] = None) -> Dict[str, ToolSpec]:
    """Load the tool manifest into a ``{name: ToolSpec}`` registry.

    Returns ALL tools regardless of ``status`` (callers filter as needed).
    Walks ``domains.<domain_key>.tools[]``.
    """
    manifest_path = _resolve_manifest_path(path)
    if not manifest_path.exists():
        raise ManifestError(f"tool manifest not found at {manifest_path}")

    with manifest_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    domains = data.get("domains") or {}
    registry: Dict[str, ToolSpec] = {}
    for domain_value in domains.values():
        if not isinstance(domain_value, dict):
            continue
        for entry in domain_value.get("tools") or []:
            spec = ToolSpec.from_entry(entry)
            registry[spec.name] = spec
    return registry


def _known_staging_kind(kind: str) -> bool:
    """True if ``kind`` is a registered StagedChange executor kind.

    ``staging_executors.supports`` covers the static ``_EXECUTORS`` keys plus
    the dynamically-registered ``modify_operational_model.<sub>`` family. The manifest's
    glob form ``modify_operational_model.*`` denotes that whole family, so it is treated
    as known.
    """
    if kind == "modify_operational_model.*":
        return True
    return staging_executors.supports(kind)


def validate_manifest(registry: Dict[str, ToolSpec]) -> None:
    """Validate a tool registry; raise :class:`ManifestError` on any violation.

    Rules, per the M2a spec:

    (a) ``op_class`` is a member of the read/propose/execute literal set.
    (b) ``policy_action`` is ``None``, OR a member of the ``PolicyAction``
        Literal, OR one of the genuinely-absent add-on gates
        (``tag.assign`` / ``workspace.read``). Resource-prefixed sharing gates
        (``{resource}.*``) are expanded to the concrete app/space/track/entry
        members at the boundary and recognized here.
    (c) For ``status == "existing"``, ``op_class == "propose"`` tools that are
        NOT staging-exempt, the ``staging_kind`` must be non-null AND a known
        executor kind.

    Rule (c) is scoped to ``status == "existing"`` because a ``gap`` entry is a
    declared-but-unbuilt intent: there is no executor for it yet, so demanding
    it name a registered one is a category error. Gaps are never advertised
    (``build_tool_catalogue`` filters on status), so an unbacked gap cannot
    reach a model regardless. Without this scoping the natural call —
    ``validate_manifest(load_manifest())`` — fails on entries that are correct
    as written, which made the validator unusable as a gate: the only passing
    caller was a test that pre-filtered by status, so the invariant lived in
    the test instead of the function.

    This is route-agnostic — it performs no ``http`` route-existence check.
    """
    valid_op_classes = set(get_args(OpClass))
    valid_policy_actions = (
        set(get_args(PolicyAction)) | _GENUINELY_ABSENT_POLICY_ACTIONS
    )

    violations: List[str] = []

    for name, spec in registry.items():
        # (a) op_class — already constrained by the Literal at parse time, but
        # validate defensively in case a spec was constructed bypassing parse.
        if spec.op_class not in valid_op_classes:
            violations.append(
                f"{name}: op_class {spec.op_class!r} is not one of "
                f"{sorted(valid_op_classes)}"
            )

        # (b) policy_action
        action = spec.policy_action
        if action is not None and action not in valid_policy_actions:
            if not _is_resource_prefixed_action(action, valid_policy_actions):
                violations.append(
                    f"{name}: policy_action {action!r} is not a member of the "
                    f"PolicyAction vocabulary (and is not a genuinely-absent "
                    f"add-on gate)"
                )

        # (c) staging_kind for implemented propose tools. Gaps are exempt by
        # status — see the docstring; they have no executor to name yet.
        if (
            spec.status == "existing"
            and spec.op_class == "propose"
            and name not in _STAGING_EXEMPT_PROPOSE_TOOLS
        ):
            if not spec.staging_kind:
                violations.append(
                    f"{name}: op_class 'propose' requires a non-null staging_kind"
                )
            elif not _known_staging_kind(spec.staging_kind):
                violations.append(
                    f"{name}: staging_kind {spec.staging_kind!r} is not a "
                    f"registered StagedChange executor kind"
                )

    if violations:
        raise ManifestError(
            "tool manifest validation failed:\n  - " + "\n  - ".join(violations)
        )


def _is_resource_prefixed_action(action: str, valid_actions: set) -> bool:
    """True if ``action`` is a resource-templated sharing gate.

    The manifest declares sharing gates with a ``{resource_type}`` /
    ``{target_type}`` placeholder (e.g. ``"{resource_type}.collaborator_add"``)
    resolved to a concrete resource at the boundary. Treat such a placeholder
    as valid when its concrete forms (``app.*``, ``space.*``, ``track.*``,
    ``entry.*``) are members of the vocabulary.
    """
    if "{" not in action or "}" not in action:
        return False
    # Strip the "<{placeholder}>." prefix, keep the verb suffix.
    suffix = action.split("}.", 1)[-1] if "}." in action else action
    for resource in ("app", "space", "track", "entry", "workspace"):
        if f"{resource}.{suffix}" in valid_actions:
            return True
    return False
