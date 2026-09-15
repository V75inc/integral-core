"""Integral-specific API exceptions extending jvspatial's HTTP error model."""

from http import HTTPStatus

from jvspatial.api.exceptions import JVSpatialAPIException


class BadRequestError(JVSpatialAPIException):
    """Client error with HTTP 400 (e.g. invalid parameters)."""

    status_code = HTTPStatus.BAD_REQUEST
    error_code = "bad_request"
    default_message = "Bad request"


class PasswordResetError(BadRequestError):
    """HTTP 400 envelope for password-reset domain errors.

    Overrides ``to_dict()`` to include a ``"detail"`` key (singular) that
    maps to ``{"error_code": <domain_code>}`` — matching the test contract
    and the frontend's expected response shape for reset-password failures.

    The standard ``"details"`` key (plural) is also preserved so any
    existing callers that read the generic envelope still work.
    """

    def __init__(
        self,
        *,
        message: str,
        reset_error_code: str,
    ) -> None:
        self._reset_error_code = reset_error_code
        super().__init__(
            message=message,
            details={"error_code": reset_error_code},
        )

    async def to_dict(self):
        """Extend base dict with a 'detail' alias for the reset error code."""
        result = await super().to_dict()
        result["detail"] = {"error_code": self._reset_error_code}
        return result


class PayloadTooLargeError(JVSpatialAPIException):
    """Phase 7 Plan 07-04 — payload exceeds APPROVAL_PAYLOAD_MAX_BYTES cap.

    Raised by ``policy_engine.evaluate`` at the requires_human_approval
    intercept when the agent-supplied payload (serialized JSON) exceeds the
    configured cap (default 1MB). The Approval is NOT persisted on overflow
    — the agent must retry with a smaller payload.
    """

    status_code = HTTPStatus.REQUEST_ENTITY_TOO_LARGE  # 413
    error_code = "payload_too_large"
    default_message = "Payload exceeds approval cap"


class ContentProfileValidationError(BadRequestError):
    """422-class envelope for ContentProfile manifest validation failures.

    Phase 10 / Plan 10-03 (MANIFEST-V2-01). Raised by
    ``content_profile_runtime.compile_canonical_manifest()`` when manifest
    shape is well-formed JSON/dict but fails v2 validation — e.g. an unknown
    skill kind, an agent referencing an undeclared skill key, or a public
    catalog submission that declares a ``kind: custom`` skill (which is
    forbidden in the public catalog per Architectural Decision 6).

    Subclass of ``BadRequestError`` so existing callers that catch the
    parent class still observe the error. The dedicated subclass lets the
    install / merge surface (Plan 10-05) and the public-catalog submission
    surface (Phase 13) distinguish validation failures from generic 400s.

    Defined in ``app.exceptions`` (not ``app.api.errors``) to avoid the
    ``app.api`` package-init side effects when the content_profile_runtime
    service-layer module imports the class.
    """

    status_code = HTTPStatus.UNPROCESSABLE_ENTITY  # 422
    error_code = "content_profile_validation_error"
    default_message = "ContentProfile manifest failed validation"


class ContentProfileV1RejectedError(ContentProfileValidationError):
    """422 envelope for the specific "manifest v1 no longer supported" case.

    Phase 10 / Plan 10-03 (MANIFEST-V2-01). Raised by
    ``compile_canonical_manifest()`` when the submitted manifest declares
    ``content_profile_schema_version != 2``. Carries the canonical
    upgrade-path message pointing at ``docs/app_bundles_v1.md §13.1`` and
    the migration script.

    Subclass distinct from generic validation so the install flow (Plan
    10-05) can format a tailored UX prompt that recommends running
    `migration script (no longer shipped)`.
    """

    error_code = "content_profile_v1_rejected"

    def __init__(
        self,
        *,
        submitted_version: object = None,
        message: str | None = None,
        details: dict | None = None,
    ):
        if message is None:
            message = (
                "manifest v1 no longer supported — set "
                "content_profile_schema_version to 2 and convert to the v2 "
                "shape per docs/app_bundles_v1.md §13.1. Run "
                "the migration script to migrate stored "
                "ContentProfiles."
            )
        merged_details = {"submitted_version": submitted_version}
        if details:
            merged_details.update(details)
        super().__init__(message=message, details=merged_details)


class SkillRegistrationError(BadRequestError):
    """Phase 10 / Plan 10-04 — generic envelope for App-bundled skill registration failures.

    Raised by ``app.agentive.services.skill_registry.register_skill`` when the
    skill spec fails validation that isn't covered by the more specific
    subclasses (``InvalidToolReferenceError``,
    ``CustomSkillPublicCatalogRejectedError``). Examples: missing required
    fields, kind/handler mismatch, invalid handler_ref shape.

    Defined here (alongside the ContentProfile validation classes) so the
    service layer can raise without importing ``app.api.errors`` and risking
    package-init side effects (per the 10-03 placement decision).
    """

    error_code = "skill_registration_error"
    default_message = "App-bundled skill registration failed"


class AgentRegistrationError(BadRequestError):
    """Phase 10 / Plan 10-04 — generic envelope for App-bundled agent registration failures.

    Raised by ``app.agentive.services.uplink_registry.register_app_agent`` when
    the agent spec fails validation — e.g. referenced skill key not previously
    registered under the same App, missing persona, invalid scope, unsupported
    staging mode.
    """

    error_code = "agent_registration_error"
    default_message = "App-bundled agent registration failed"


class InvalidToolReferenceError(SkillRegistrationError):
    """Phase 10 / Plan 10-04 — skill.tools_required references an unknown MCP tool.

    Raised by ``skill_registry.register_skill`` when any entry in
    ``tools_required`` is absent from the live MCP tool catalogue
    (``mcp_adapter.build_tool_catalogue()``) — validated at registration time.
    """

    error_code = "invalid_tool_reference"
    default_message = "One or more declared tools are not in the live MCP catalogue"


class CustomSkillPublicCatalogRejectedError(ContentProfileValidationError):
    """Phase 10 / Plan 10-04 — kind:custom skill in a public-catalog merge.

    Raised by ``content_profile_merge.merge_library_manifest_into_content_profile``
    when the library ContentProfile's ``manifest.package.publisher_tier`` is
    ``"public_catalog"`` AND any declared skill has ``kind == "custom"``.
    Mirrors the compile-time gate at
    ``content_profile_runtime.compile_canonical_manifest(is_public_catalog=True)``
    (Architectural Decision 6 — belt-and-suspenders enforcement at both compile
    AND merge time).

    Subclass of ``ContentProfileValidationError`` so existing callers that
    catch the parent class still observe the error; the dedicated subclass lets
    the install flow (Plan 10-05) and public-catalog submission surface
    (Phase 13) format a tailored rejection message.
    """

    error_code = "custom_skill_public_catalog_rejected"
    default_message = "Skills with kind: custom are not permitted in the public catalog"


class AppInstallError(BadRequestError):
    """Phase 10 / Plan 10-05 — generic envelope for App install failures.

    Raised by ``app_lifecycle.install_app`` when the install transaction
    fails for reasons not covered by the more specific subclasses
    (``AppInstallTokenInvalidError`` / ``AppInstallTokenExpiredError`` /
    ``AppLifecycleStateError`` / ``AppDependencyError`` /
    ``AppUninstallBlockedError``). Examples: cross-App resolution ambiguity,
    settings_schema validation failure post-resume, manifest compile
    failures upstream of the more specific errors.

    Defined here (alongside the ContentProfile + skill registration error
    classes) so the service layer can raise without importing
    ``app.api.errors`` and risking package-init side effects (per the
    10-03 placement decision).
    """

    error_code = "app_install_error"
    default_message = "App install failed"


class AppInstallTokenInvalidError(BadRequestError):
    """Phase 10 / Plan 10-05 — install_token signature / shape rejected.

    Raised by ``app_install_token.verify_install_token`` when the token
    payload fails to parse, the HMAC signature is wrong, or the embedded
    ``app_id`` does not match the expected App. Treats malformed and
    tampered tokens identically (no oracle for the attacker).

    HTTP 400 — the client supplied an invalid token; not 401 (the user IS
    authenticated, the token is the per-install single-use credential).
    """

    error_code = "app_install_token_invalid"
    default_message = "App install token is invalid"


class AppInstallTokenExpiredError(BadRequestError):
    """Phase 10 / Plan 10-05 — install_token past its ``expires_at``.

    Raised by ``app_install_token.verify_install_token`` when the token
    parses and signature-verifies cleanly but the embedded ``expires_at``
    is in the past. Distinct from the generic invalid case so the UI can
    prompt the user with "your install session expired — please restart
    the install" rather than a generic "invalid token".

    The background reaper (``app_install_reaper``) typically force-
    uninstalls the abandoned App row before the user notices the expiry,
    but a race is possible (user submits settings just past TTL, before
    the reaper sweep). This error covers that race.
    """

    error_code = "app_install_token_expired"
    default_message = "App install token has expired"


class AppLifecycleStateError(BadRequestError):
    """Phase 10 / Plan 10-05 — App is in a state that disallows the operation.

    Raised when a lifecycle transition is attempted from a state that does
    not allow it. Examples:
      - resume-with-settings called on an App that is already ``active``;
      - pause called on a ``paused`` App;
      - install-token consumed twice (the second call observes
        ``lifecycle_state != "awaiting_settings"`` and rejects).

    Carries ``details.current_state`` so the client can surface a precise
    message. Mirrors the precedent set by ``ApprovalStateError`` in
    Phase 7 Plan 07-04.
    """

    error_code = "app_lifecycle_state_error"
    default_message = "App is in a state that disallows this operation"


class AppDependencyError(BadRequestError):
    """Phase 10 / Plan 10-05 — install / uninstall blocked by ``requires_apps``.

    Install path: the manifest declares a hard dependency that is not
    present in the target Workspace. ``details.missing_deps`` lists the
    unsatisfied dependency keys.

    Uninstall path: another App in the Workspace declares this App as a
    hard dependency. ``details.blocking_dependents`` lists the dependent
    App ids. The 409 envelope hints at ``?force=true`` as the override.

    HTTP 409 — Conflict (state-dependent rejection); not 400 (the request
    is well-formed, the surrounding state is incompatible).
    """

    status_code = HTTPStatus.CONFLICT  # 409
    error_code = "app_dependency_error"
    default_message = "App dependency check failed"


class AppUninstallBlockedError(BadRequestError):
    """Phase 10 / Plan 10-05 — uninstall blocked by dependents or references.

    Distinct subclass of ``BadRequestError`` (not ``AppDependencyError``)
    so the install + uninstall paths can be differentiated by error_code
    on the client side. Carries
    ``details.blocking_dependents`` and ``details.blocking_references``.

    HTTP 409 — same precedent as ``AppDependencyError``. Hints at
    ``?force=true`` as the escape hatch.
    """

    status_code = HTTPStatus.CONFLICT  # 409
    error_code = "app_uninstall_blocked"
    default_message = "App uninstall blocked by dependents or cross-App references"


class AmbiguousCrossAppTargetError(BadRequestError):
    """Phase 10 / Plan 10-06 — ``resolution: workspace`` matched multiple Apps.

    Raised by ``relation_runtime.resolve_target_app`` when a cross-App relation
    declares ``resolution: workspace`` and the workspace has more than one
    installed App matching the declared ``target_app`` key. The fix is for the
    manifest author to pin the install via ``resolution: instance:<app_id>``.

    HTTP 409 — the request is well-formed; the surrounding workspace state is
    incompatible (multi-install ambiguity, T-10-06-08 mitigation).
    """

    status_code = HTTPStatus.CONFLICT  # 409
    error_code = "ambiguous_cross_app_target"
    default_message = "Multiple App installations match target_app"


class CrossAppTargetNotFoundError(BadRequestError):
    """Phase 10 / Plan 10-06 — declared ``target_app`` is not installed.

    Raised by ``relation_runtime.resolve_target_app`` when no App with the
    declared ``target_app`` key exists in the workspace. Typically surfaces at
    relation-write time when the source App was installed but a soft-dep target
    is absent.
    """

    error_code = "cross_app_target_not_found"
    default_message = "target_app declared by the relation is not installed"


class CrossAppPermissionDenied(BadRequestError):
    """Phase 10 / Plan 10-06 — viewer lacks ``entry.read`` on the cross-App target.

    Raised internally by ``relation_runtime.read_cross_app_label`` — the public
    contract is that the function returns a restricted-stub dict on permission
    denial; callers that want explicit error semantics receive this exception.
    Defined so audit-log surface can distinguish denial from other failures.
    """

    status_code = HTTPStatus.FORBIDDEN  # 403
    error_code = "cross_app_permission_denied"
    default_message = "Viewer is not permitted to read the cross-App target"


class CrossWorkspaceTargetRejectedError(BadRequestError):
    """Phase 10 / Plan 10-06 — I-APP-05 — cross-Workspace target rejected.

    Raised by ``relation_runtime.resolve_target_app`` when the resolved target
    App lives in a different ``workspace_id`` than the source App. v1 hard-
    enforces the same-Workspace invariant (I-APP-05). Cross-workspace federation
    is explicitly deferred to a future ROADMAP phase.
    """

    error_code = "cross_workspace_target_rejected"
    default_message = "Cross-Workspace App targets are not supported in v1 (I-APP-05)"


class PendingApprovalError(JVSpatialAPIException):
    """Phase 7 Plan 07-04 — agent write deferred for human approval.

    Raised by mutation handlers when ``policy_engine.evaluate`` returns
    ``Decision(allowed=False, reason='requires_human_approval', approval_id=...)``.
    Carries the persisted Approval id + expires_at in the error envelope so
    the agent caller can poll the approval surface.

    HTTP 202 (Accepted) — the call wasn't denied, it was deferred. Agents
    that recognize this envelope SHOULD stop retrying; naive agents will
    retry and the engine will return a NEW Approval each call (idempotency
    is the human's responsibility via approve/reject, not the agent's).
    """

    status_code = HTTPStatus.ACCEPTED  # 202
    error_code = "pending_approval"
    default_message = "Write deferred for human approval"
