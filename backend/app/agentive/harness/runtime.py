"""Factories for the native Pydantic AI Harness binding.

The harness composes model and tool behavior. Integral supplies the model,
brokered tools, tenant-scoped checkpoint store, and the permission-filtered
Agent Skills directories for each invocation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Collection, Sequence

from app.agentive.harness.capability_search import pydantic_tool_search_strategy
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.jvspatial_store import JvSpatialStepStore
from app.agentive.harness.pydantic_ai_compat import (
    Agent,
    ConversationSearch,
    Instrumentation,
    IntegralToolDisclosure,
    Skills,
    SnapshotHistorySource,
    StepPersistence,
    StepStore,
    ToolSearch,
    build_integral_context_compaction,
    build_integral_history_compaction,
    build_integral_run_instructions,
)
from app.agentive.harness.scoped_store import ScopedStepStore
from app.schemas.agentive.work import WorkExecutionContext


def build_native_agent(
    *,
    model: Any,
    instructions: str,
    tools: Sequence[Any],
    step_store_backend: StepStore | None = None,
    scope: HarnessExecutionScope,
    agent_name: str,
    skill_directories: Sequence[Path] = (),
    allowed_skill_names: Collection[str] = (),
    work_execution_context: WorkExecutionContext | None = None,
) -> Agent:
    """Build a fresh Harness composition for one trusted Integral invocation.

    ``tools`` must already be authorized Integral broker adapters. By default,
    the Core-owned encrypted jvspatial store is used; tests may inject a
    protocol-compatible store. Either backend is wrapped here with the
    immutable tenant/principal/thread/session scope. There is no unscoped or
    file/SQLite fallback. Skill files are supplied only alongside an explicit
    allowlist computed by Core. Construct this function per run so mutable
    Harness capability state does not leak between conversations.
    """
    agent, _ = build_native_runtime(
        model=model,
        instructions=instructions,
        tools=tools,
        step_store_backend=step_store_backend,
        scope=scope,
        agent_name=agent_name,
        skill_directories=skill_directories,
        allowed_skill_names=allowed_skill_names,
        work_execution_context=work_execution_context,
    )
    return agent


def build_native_runtime(
    *,
    model: Any,
    instructions: str,
    tools: Sequence[Any],
    step_store_backend: StepStore | None = None,
    scope: HarnessExecutionScope,
    agent_name: str,
    skill_directories: Sequence[Path] = (),
    allowed_skill_names: Collection[str] = (),
    work_execution_context: WorkExecutionContext | None = None,
) -> tuple[Agent, ScopedStepStore]:
    """Build an Agent and expose its scoped store for conversation resume.

    The returned store remains bound to this execution scope. Callers may use
    it to load the previous session checkpoint, but must not retain or share it
    across tenant, principal, thread, or session boundaries.
    """
    if step_store_backend is None:
        step_store_backend = JvSpatialStepStore(scope=scope)
    if work_execution_context is not None and (
        work_execution_context.principal_id != scope.principal_id
        or work_execution_context.workspace_id != scope.workspace_id
        or work_execution_context.run_id != scope.run_id
    ):
        raise ValueError("WorkItem authority does not match Harness execution scope")
    scoped_store = ScopedStepStore(
        store=step_store_backend,
        scope=scope,
        work_execution_context=work_execution_context,
    )

    selected_skills = frozenset(
        name.strip() for name in allowed_skill_names if name.strip()
    )
    if selected_skills and not skill_directories:
        raise ValueError("approved Agent Skill names require a Core skill source")

    capabilities: list[Any] = [
        Instrumentation(),
        # Pydantic AI owns deferred tool discovery; its supported callable
        # strategy uses Integral's tenant-authorized semantic catalog rather
        # than the built-in keyword-only fallback. The matching score affects
        # discovery order only; the broker remains the authorization boundary.
        (
            IntegralToolDisclosure(
                strategy=pydantic_tool_search_strategy,
                max_results=8,
                require_initial_search=not any(
                    tool.name == "integral_resolve_pending_write"
                    or (
                        tool.name == "integral_build_approved_design"
                        and not tool.defer_loading
                    )
                    for tool in tools
                ),
                continuation_tools=frozenset(
                    tool.name
                    for tool in tools
                    if tool.name == "integral_build_approved_design"
                    and not tool.defer_loading
                ),
                pending_decision_tool=next(
                    (
                        tool.name
                        for tool in tools
                        if tool.name == "integral_resolve_pending_write"
                    ),
                    None,
                ),
            )
            if any(tool.name == "search_capabilities" for tool in tools)
            else ToolSearch(strategy=pydantic_tool_search_strategy, max_results=8)
        ),
        StepPersistence(
            store=scoped_store,
            agent_name=agent_name,
            run_id=scope.framework_run_id,
        ),
        # Adapter-owned policy bounds repeated tool-call arguments and results
        # without discarding the newest outcome or active skill state.
        build_integral_context_compaction(),
        build_integral_history_compaction(),
        # Search only this server-assigned Pydantic conversation. The source
        # is the exact tenant/principal/thread/session-scoped StepStore used by
        # StepPersistence; never use the library's store-wide search scope.
        ConversationSearch(
            SnapshotHistorySource(scoped_store),
            scope="conversation",
            # Integral supplies task-specific guidance. The generic hint can
            # invite archive searches during unrelated current-state work.
            add_instructions=False,
        ),
    ]
    if selected_skills:
        capabilities.append(
            Skills(
                directories=list(skill_directories),
                include=selected_skills,
            )
        )

    return (
        Agent(
            model,
            instructions=build_integral_run_instructions(instructions),
            tools=list(tools),
            capabilities=capabilities,
        ),
        scoped_store,
    )
