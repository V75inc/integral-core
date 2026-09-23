"""Re-exports + Integral-specific subclasses for jvspatial-aligned API errors.

The base subclasses (`BadRequestError`, `MissingAuthenticationError`, …) are
re-exported from jvspatial's exception module so handlers under
`backend/app/api/` and `backend/app/agentive/api/` can import them from a
single canonical module per AGENTS.md § jvspatial Object-Spatial Contract.

The Integral-specific subclasses below cover agentive-layer error codes that
needed an inline definition before the 06-05 migration:

- ``ServiceUnavailableError`` — 503, raised when the agentive harness is
  disabled mid-deploy or when a chat connector is registered but currently
  offline. Replaces inline ``HTTPException(503, …)`` calls.
- ``NotImplementedAPIError`` — 501, raised by vendor-neutral connector
  dispatch when the requested agent type has no registered chat connector.
"""

from http import HTTPStatus

from jvspatial.api.exceptions import (
    InsufficientPermissionsError,
    JVSpatialAPIException,
    MissingAuthenticationError,
    ResourceConflictError,
    ResourceNotFoundError,
)

from app.exceptions import (
    AgentRegistrationError,
    AmbiguousCrossAppTargetError,
    AppDependencyError,
    AppInstallError,
    AppInstallTokenExpiredError,
    AppInstallTokenInvalidError,
    ApplicationDefinitionUpgradeConflictError,
    AppLifecycleStateError,
    AppUninstallBlockedError,
    BadRequestError,
    CrossAppPermissionDenied,
    CrossAppTargetNotFoundError,
    CrossWorkspaceTargetRejectedError,
    CustomSkillPublicCatalogRejectedError,
    InvalidToolReferenceError,
    MigrationInProgressError,
    OperationalModelV1RejectedError,
    OperationalModelValidationError,
    PasswordResetError,
    SkillRegistrationError,
)


class ConnectorAuthError(JVSpatialAPIException):
    """401 envelope for connector OAuth / refresh / authorization failures.

    Raised by ``quickbooks_oauth`` helpers when
    Intuit's token endpoint rejects the request (invalid_grant on refresh,
    invalid_code on exchange, state mismatch on callback). Carries an
    optional ``reauth_required`` marker in ``details`` so callers /
    front-ends can branch to the re-authorize flow vs. surface a generic
    error.
    """

    status_code = HTTPStatus.UNAUTHORIZED  # 401
    error_code = "connector.auth_failed"
    default_message = (
        "Connector authentication failed (re-authorization may be required)"
    )


class ServiceUnavailableError(JVSpatialAPIException):
    """503 envelope for transient unavailability (harness disabled / offline).

    Used by:

    - agentive ``/chat/message`` when ``AGENTIVE_ENABLED`` is False at request
      time (defense-in-depth for mid-deploy flag flips).
    - agentive ``/chat/message`` when no deployment agent is connected
      (``uplink_registry.get_system_agent`` returns ``None``).
    - core ``/api/chat/threads/{id}/messages`` when the registered provider
      reports ``is_available() is False``.

    Distinct from ``InsufficientPermissionsError`` (403) — the caller IS
    permitted, but the underlying service is unavailable.
    """

    status_code = HTTPStatus.SERVICE_UNAVAILABLE  # 503
    error_code = "service_unavailable"
    default_message = "Service is temporarily unavailable"


class QueryUnavailableError(ServiceUnavailableError):
    """503: an exact query could not read its authorized source data."""

    error_code = "query_unavailable"
    default_message = "Query could not be completed"


class OperationIdempotencyConflictError(BadRequestError):
    """400: an operation key was reused with a different request body."""

    error_code = "idempotency_conflict"
    default_message = "Idempotency key reused with different payload"


class OperationReceiptRecoveryError(ServiceUnavailableError):
    """503: a prior command claim exists but has no safely replayable result."""

    error_code = "operation_receipt_incomplete"
    default_message = "Operation outcome is being recovered; retry with the same key"


class OperationTransactionUnavailableError(ServiceUnavailableError):
    """503: a durable command was requested on a non-transactional store."""

    error_code = "operation_transaction_unavailable"
    default_message = "Mutating App operations require transactional storage"


class NotImplementedAPIError(JVSpatialAPIException):
    """501 envelope for "registered but unimplemented" connector dispatch.

    Raised by agentive ``/chat/message`` when ``get_chat_connector(agent_type)``
    fails because the agent's declared ``agent_type`` has no chat connector
    registered. Per AGT-03 D-11 vendor-neutral dispatch.
    """

    status_code = HTTPStatus.NOT_IMPLEMENTED  # 501
    error_code = "not_implemented"
    default_message = "Operation not implemented"


class InternalServerError(JVSpatialAPIException):
    """500 envelope for unrecoverable server faults raised at the boundary.

    Used by core ``/api/chat/threads/{id}`` hard-delete when the underlying
    thread.delete() raises after the user is authenticated and authorized.
    Distinct from FastAPI's automatic 500 for unhandled exceptions —
    raising this explicitly produces a canonical 5-key envelope.
    """

    status_code = HTTPStatus.INTERNAL_SERVER_ERROR  # 500
    error_code = "internal_server_error"
    default_message = "Internal server error"


class UnprocessableEntityError(JVSpatialAPIException):
    """422 envelope for semantically invalid requests.

    Distinct from ``BadRequestError`` (400, malformed input) — the request
    is well-formed but cannot be fulfilled because the server's current
    state makes it impossible. Used by:

    - core ``/api/chat/threads`` POST when no agent can be resolved (no
      body ``agent_id``, no per-workspace preference, empty catalog) for a
      multi-agent provider. Per agent-switcher plan Task 5.

    Mirrors ``BadRequestError``'s shape so handlers can swap based on
    400-vs-422 semantics without changing the envelope contract.
    """

    status_code = HTTPStatus.UNPROCESSABLE_ENTITY  # 422
    error_code = "unprocessable_entity"
    default_message = "Request cannot be processed"


class RateLimitedError(JVSpatialAPIException):
    """429 envelope for per-principal limits enforced inside a service.

    ``RateLimitMiddleware`` answers 429 per IP before routing; this covers
    limits keyed on the authenticated principal — e.g. minting voice-input
    sessions, each of which spends the workspace's provider quota.
    """

    status_code = HTTPStatus.TOO_MANY_REQUESTS  # 429
    error_code = "rate_limited"
    default_message = "Too many requests — try again shortly"


# OperationalModelValidationError + OperationalModelV1RejectedError are defined
# in ``app.exceptions`` (Phase 10 Plan 10-03) to avoid the ``app.api`` package
# init side-effects when the operational_model_runtime service-layer module
# imports them. Re-exported here so existing callers can still pull them from
# the canonical errors module.


__all__ = [
    "AgentRegistrationError",
    "AmbiguousCrossAppTargetError",
    "AppDependencyError",
    "ApplicationDefinitionUpgradeConflictError",
    "AppInstallError",
    "AppInstallTokenExpiredError",
    "AppInstallTokenInvalidError",
    "AppLifecycleStateError",
    "AppUninstallBlockedError",
    "BadRequestError",
    "OperationalModelV1RejectedError",
    "OperationalModelValidationError",
    "CrossAppPermissionDenied",
    "CrossAppTargetNotFoundError",
    "CrossWorkspaceTargetRejectedError",
    "CustomSkillPublicCatalogRejectedError",
    "InsufficientPermissionsError",
    "InternalServerError",
    "InvalidToolReferenceError",
    "MigrationInProgressError",
    "JVSpatialAPIException",
    "PasswordResetError",
    "MissingAuthenticationError",
    "NotImplementedAPIError",
    "OperationIdempotencyConflictError",
    "OperationReceiptRecoveryError",
    "OperationTransactionUnavailableError",
    "ResourceConflictError",
    "ResourceNotFoundError",
    "ServiceUnavailableError",
    "SkillRegistrationError",
    "UnprocessableEntityError",
]
