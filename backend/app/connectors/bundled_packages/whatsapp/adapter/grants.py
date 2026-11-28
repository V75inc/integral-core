"""Read WhatsApp grants. This module does not write or rekey them."""

GRANT_EDGE = "USES_WHATSAPP_LINE"


def package():
    """Expose each durable workspace grant as its managed installation."""
    from pydantic import ValidationError

    from app.agentive.services.whatsapp_channel_binding import (
        disconnect_workspace_whatsapp_line,
        register_saved_whatsapp_channel,
        save_whatsapp_channel_credentials,
        set_workspace_line_enabled,
        workspace_line_epoch,
        workspace_line_grant,
    )
    from app.api.errors import BadRequestError, ResourceConflictError
    from app.connectors.channel_packages import (
        ChannelDefinition,
        ChannelInstallation,
        ChannelLifecycle,
        ChannelPackage,
        ChannelProfileSetup,
        ChannelServing,
        ChannelServingStatus,
    )
    from app.schemas.agentive.whatsapp_channel import SaveWhatsAppChannelBindingRequest

    async def observe(scope):
        binding = await workspace_binding(scope.workspace_id)
        grant = (
            await workspace_line_grant(scope.workspace_id, binding.id)
            if binding
            else None
        )
        if not grant:
            return None
        return ChannelInstallation(
            scope.workspace_id,
            "whatsapp",
            grant.id,
            workspace_line_epoch(binding, grant),
            grant.enabled and binding.enabled,
        )

    async def install(_scope):
        raise ResourceConflictError(
            "Configure a JV Connect line before enabling WhatsApp"
        )

    async def set_enabled(scope, current, enabled):
        if enabled:
            binding = await workspace_binding(scope.workspace_id)
            if binding and not binding.enabled and await observe(scope) == current:
                binding = await register_saved_whatsapp_channel(
                    binding,
                    workspace_id=scope.workspace_id,
                    request_base_url="",
                    expected_grant=(current.provision_id, current.route_epoch),
                )
                latest = await observe(scope)
                if (
                    not binding.enabled
                    or not latest
                    or latest.provision_id != current.provision_id
                ):
                    raise ResourceConflictError(
                        "WhatsApp registration did not complete"
                    )
                current = latest
        await set_workspace_line_enabled(
            scope.actor_id,
            scope.workspace_id,
            current.provision_id,
            current.route_epoch,
            enabled,
        )

    async def read_credentials(scope):
        binding = await workspace_binding(scope.workspace_id)
        return {
            "binding": (
                {
                    "id": binding.id,
                    "jvconnect_base_url": binding.jvconnect_base_url,
                    "api_key_last4": binding.api_key_last4,
                }
                if binding
                else None
            )
        }

    async def configure(scope, values, request_base_url):
        try:
            submitted = SaveWhatsAppChannelBindingRequest.model_validate(values)
        except ValidationError:
            raise BadRequestError("Invalid JV Connect line credentials") from None
        if "expected_binding_id" not in submitted.model_fields_set:
            raise BadRequestError("The current WhatsApp binding must be specified")
        await save_whatsapp_channel_credentials(
            user_id=scope.actor_id,
            workspace_id=scope.workspace_id,
            jvconnect_base_url=submitted.jvconnect_base_url,
            api_key=submitted.api_key,
            request_base_url=request_base_url,
            expected_binding_id=submitted.expected_binding_id,
            check_expected_binding=True,
            activate=False,
        )

    async def remove(scope, current):
        binding = await workspace_binding(scope.workspace_id)
        if binding is None:
            raise ResourceConflictError("WhatsApp grant changed")
        await disconnect_workspace_whatsapp_line(
            scope.actor_id,
            scope.workspace_id,
            binding.id,
            expected_grant=(current.provision_id, current.route_epoch),
        )

    async def probe(scope, installation):
        binding = await workspace_binding(scope.workspace_id)
        state = "unavailable"
        if (
            installation
            and binding
            and await observe(scope) == installation
            and (
                binding.api_key_ciphertext
                and binding.phone_number_id
                and (not installation.enabled or binding.webhook_secret_ciphertext)
            )
        ):
            state = "accepting" if installation.enabled else "ready"
        return ChannelServingStatus(
            scope.workspace_id,
            "whatsapp",
            installation.provision_id if installation else None,
            installation.route_epoch if installation else None,
            state,
        )

    async def receive(_message):
        raise ResourceConflictError(
            "WhatsApp messages require the signed JV Connect ingress"
        )

    async def describe_profile(scope, current):
        from app.agentive.services.whatsapp_public_profile import (
            describe_line_profile,
        )

        if await observe(scope) != current:
            raise ResourceConflictError("WhatsApp grant changed")
        return await describe_line_profile(scope.workspace_id, current)

    async def assign_profile(scope, current, expected_profile_revision, profile_key):
        from app.agentive.services.whatsapp_public_profile import assign_line_profile

        if await observe(scope) != current or current.enabled:
            raise ResourceConflictError("WhatsApp grant changed or is enabled")
        await assign_line_profile(
            scope.actor_id,
            scope.workspace_id,
            current,
            expected_profile_revision,
            profile_key,
        )

    return ChannelPackage(
        definition=ChannelDefinition(
            "whatsapp",
            "JV Connect",
            "WhatsApp",
            "Manage this workspace’s grant on a JV Connect line.",
            "managed-text-v1",
        ),
        lifecycle=ChannelLifecycle(observe, install, set_enabled, remove),
        serving=ChannelServing(probe, receive),
        profile_setup=ChannelProfileSetup(describe_profile, assign_profile),
        read_credentials=read_credentials,
        configure_credentials=configure,
    )


def existing_grant(binding) -> dict[str, str]:
    """Return the stored grant identity. Ciphertext is copied, not decrypted."""
    return {
        "id": binding.id,
        "edge": GRANT_EDGE,
        "api_key_ciphertext": binding.api_key_ciphertext,
        "webhook_secret_ciphertext": binding.webhook_secret_ciphertext,
    }


async def workspace_binding(workspace_id: str):
    """Return this package's workspace binding. Other packages do not use it."""
    from app.agentive.services.whatsapp_channel_binding import binding_for_workspace

    return await binding_for_workspace(workspace_id)


def grant_status(binding) -> dict:
    """Settings read. The binding stays the grant record. No secret is returned."""
    if binding is None:
        return {"edge": GRANT_EDGE, "present": False}
    return {
        "edge": GRANT_EDGE,
        "present": True,
        "id": getattr(binding, "id", ""),
        "enabled": bool(getattr(binding, "enabled", False)),
    }
