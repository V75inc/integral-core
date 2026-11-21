"""Native Harness profile policy through supported Pydantic AI hooks.

Integral supplies authenticated connection grants and scoped persistence; the
framework owns tool preparation and execution. No persona or model input is
used to select a profile or elevate a principal.
"""

import hashlib
from dataclasses import dataclass, field

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.pydantic_ai_compat import AbstractCapability
from app.agentive.services.connection_profiles import (
    ConnectionProfileExecutor,
    ProfileDenied,
    ProfileSession,
)


@dataclass(frozen=True)
class NativeConnectionProfile:
    executor: ConnectionProfileExecutor
    connection_id: str
    principal_id: str
    session: ProfileSession
    workspace_id: str
    audience: str
    declarations: dict = field(default_factory=dict)
    turn_scoped: bool = False

    @classmethod
    async def open(
        cls,
        executor,
        connection_id,
        principal_id,
        *,
        declarations=None,
        turn_scoped=False,
    ) -> "NativeConnectionProfile":
        session = await executor.open(connection_id, principal_id)
        grant = await executor.current_grant(connection_id, principal_id, session)
        return cls(
            executor,
            connection_id,
            principal_id,
            session,
            grant.workspace_id,
            grant.audience,
            declarations or {},
            turn_scoped,
        )

    def bind_scope(self, scope: HarnessExecutionScope) -> HarnessExecutionScope:
        if (
            scope.principal_id != self.principal_id
            or scope.workspace_id != self.workspace_id
        ):
            raise ProfileDenied("Native principal does not match connection")
        scope_key = self.session.scope_key
        if self.turn_scoped:
            # Installed reads may contain data whose resource access changes
            # independently of App permission. Never resume them across turns.
            scope_key = hashlib.sha256(
                f"{scope_key}:{scope.run_id}".encode()
            ).hexdigest()
        return HarnessExecutionScope.model_validate(
            {
                **scope.model_dump(),
                "connection_scope_key": scope_key,
                "connection_audience": self.audience,
            }
        )

    def assert_scope(self, scope):
        if self.bind_scope(scope) != scope:
            raise ProfileDenied("Native scope does not match connection")

    async def current(self):
        grant = await self.executor.current_grant(
            self.connection_id, self.principal_id, self.session
        )
        if grant.workspace_id != self.workspace_id or grant.audience != self.audience:
            raise ProfileDenied("Native connection unavailable")

    async def authorize(self, tool):
        await self.current()
        await self.executor.authorize_operation(
            self.connection_id, self.principal_id, self.session, tool
        )


class NativeProfilePolicy(AbstractCapability):
    """Filter every step and recheck immediately before every tool execution."""

    def __init__(self, profile: NativeConnectionProfile):
        self.profile = profile

    async def before_run(self, ctx):
        await self.profile.current()

    async def before_model_request(self, ctx, request_context):
        await self.profile.current()
        from app.agentive.harness.pydantic_ai_compat import ToolReturnPart

        for message in request_context.messages:
            for part in message.parts:
                if isinstance(part, ToolReturnPart):
                    await self.profile.authorize(part.tool_name)
        return request_context

    async def prepare_tools(self, ctx, tool_defs):
        await self.profile.current()
        allowed = []
        for tool in tool_defs:
            try:
                await self.profile.authorize(tool.name)
            except ProfileDenied:
                continue
            allowed.append(tool)
        return allowed

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        await self.profile.authorize(tool_def.name)
        return await handler(args)


def build_connection_profile_tools(profile, declarations):
    """Adapt only server-registered narrow handlers, never the staff broker."""
    from app.agentive.harness.pydantic_ai_compat import build_integral_json_schema_tool

    def handler(name):
        async def invoke(ctx, **arguments):
            return await profile.executor.execute(
                profile.connection_id,
                profile.principal_id,
                profile.session,
                name,
                arguments,
            )

        return invoke

    tools = []
    for name, declaration in declarations.items():
        tool = build_integral_json_schema_tool(
            function=handler(name),
            name=name,
            description=declaration["description"],
            json_schema=declaration["input_schema"],
            prepare=None,
            defer_loading=False,
        )
        tool.metadata = {"integral_connection_scope": profile.session.scope_key}
        tools.append(tool)
    return tools
