"""Small built-in identity adapters; product registration stays in the host."""

from dataclasses import dataclass

from app.runtime.composition import runtime


@dataclass(frozen=True)
class AccountIdentity:
    async def bootstrap(self) -> None:
        from app.bootstrap_admin import bootstrap_admin_if_needed

        await bootstrap_admin_if_needed()

    async def controller_session(self, request) -> dict:
        from app.api.errors import ResourceNotFoundError

        raise ResourceNotFoundError(message="Not found")

    async def preserve_workspace(self, workspace_id: str) -> None:
        """Hosted workspace lifecycle follows ordinary authorization."""

    async def audit_ready(self) -> None:
        """Hosted startup adds no installation-owner bootstrap facts."""


@dataclass(frozen=True)
class InstallationIdentity:
    async def bootstrap(self) -> None:
        from app.runtime.local_identity import bootstrap_owner

        await bootstrap_owner()

    async def controller_session(self, request) -> dict:
        import secrets

        from app.api.errors import MissingAuthenticationError
        from app.config import settings
        from app.runtime.local_identity import issue_session

        expected = settings.INTEGRAL_DESKTOP_CONTROL_TOKEN
        supplied = request.headers.get("authorization", "")
        if (
            not expected
            or request.headers.get("origin") is not None
            or request.headers.get("sec-fetch-site") is not None
            or not secrets.compare_digest(supplied, "Bearer " + expected)
        ):
            raise MissingAuthenticationError(
                message="Native controller authentication required"
            )
        return await issue_session()

    async def preserve_workspace(self, workspace_id: str) -> None:
        """Keep the installation's recovery workspace addressable."""
        from app.runtime.local_identity import preserve_owner_workspace

        await preserve_owner_workspace(workspace_id)

    async def audit_ready(self) -> None:
        """Deliver atomic owner-bootstrap facts after audit storage is ready."""
        from app.services.app_operations.event_outbox import (
            deliver_pending_operation_events,
        )

        await deliver_pending_operation_events()


def identity_adapter() -> AccountIdentity | InstallationIdentity:
    """Select the adapter from the supported trusted composition."""
    return (
        InstallationIdentity()
        if runtime().identity == "installation_owner"
        else AccountIdentity()
    )
