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
from app.agentive.harness.disclosure import (
    DISCLOSURE_POLICY,
    instruction_output_validator,
)
from app.agentive.harness.jvspatial_store import JvSpatialStepStore
from app.agentive.harness.pydantic_ai_compat import (
    Agent,
    ConversationSearch,
    InstructionDisclosureGuard,
    Instrumentation,
    IntegralLimitWarnings,
    IntegralToolDisclosure,
    PublicConversationHistorySource,
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
from app.agentive.harness.turn_progress import TurnProgress
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
    turn_progress: TurnProgress | None = None,
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
        turn_progress=turn_progress,
    )
    return agent


def build_native_runtime(
    *,
    model: Any,
    protected_instruction_text: str | None = None,
    instructions: str,
    tools: Sequence[Any],
    step_store_backend: StepStore | None = None,
    scope: HarnessExecutionScope,
    agent_name: str,
    skill_directories: Sequence[Path] = (),
    allowed_skill_names: Collection[str] = (),
    work_execution_context: WorkExecutionContext | None = None,
    turn_progress: TurnProgress | None = None,
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

    protected = (
        (
            instructions
            if protected_instruction_text is None
            else protected_instruction_text
        )
        + "\n"
        + DISCLOSURE_POLICY
    )

    capabilities: list[Any] = [
        Instrumentation(),
        IntegralLimitWarnings(),
        # Pydantic AI owns deferred tool discovery; its supported callable
        # strategy uses Integral's tenant-authorized semantic catalog rather
        # than the built-in keyword-only fallback. The matching score affects
        # discovery order only; the broker remains the authorization boundary.
        (
            IntegralToolDisclosure(
                turn_progress=turn_progress,
                strategy=pydantic_tool_search_strategy,
                max_results=8,
                workflow_skill_ids=selected_skills,
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
        # Bound repeated read payloads even within a long active turn. Only
        # tools explicitly marked read-only in the Core catalogue may be
        # compacted; capability loading, skills, staging, and writes stay exact.
        build_integral_context_compaction(
            clearable_tools=frozenset(
                tool.name
                for tool in tools
                if (getattr(tool, "metadata", None) or {}).get("integral_op_class")
                == "read"
            )
        ),
        build_integral_history_compaction(),
        # Search only this server-assigned Pydantic conversation. The source
        # is the exact tenant/principal/thread/session-scoped StepStore used by
        # StepPersistence; never use the library's store-wide search scope.
        ConversationSearch(
            PublicConversationHistorySource(
                SnapshotHistorySource(scoped_store), protected_instructions=protected
            ),
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

    capabilities.append(InstructionDisclosureGuard(protected_instructions=protected))

    agent = Agent(
        model,
        instructions=build_integral_run_instructions(
            instructions + "\n\n" + DISCLOSURE_POLICY, turn_progress
        ),
        tools=list(tools),
        retries={"output": 3},
        capabilities=capabilities,
    )
    agent.output_validator(instruction_output_validator(protected))
    return agent, scoped_store
