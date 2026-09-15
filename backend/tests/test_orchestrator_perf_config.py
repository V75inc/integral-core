"""Orchestrator performance configuration baseline (CUCS guardrails).

These read `agent/agents/integral/integral_agent/agent.yaml`, which configures
the resident's runtime behaviour and is applied at bootstrap — a bad value
ships without touching a line of Python, so the descriptor needs the same gate
the code has.

Marked `smoke` deliberately. They were unmarked until 2026-08-10, which meant
CI's `-m "smoke and not domain_app and not slow"` selection collected nothing
from this file: the only tests that read agent.yaml had never run on a PR. That
compounded with `agent/**` missing from ci.yml's paths filter, so a
descriptor-only change produced no CI run at all. Both are fixed together —
either alone still leaves the file ungated.

They are pure YAML reads with no fixtures or I/O, so they cost the smoke gate
essentially nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.smoke


def _load_agent_yaml() -> dict:
    agent_yaml = (
        Path(__file__).resolve().parents[2]
        / "agent"
        / "agents"
        / "integral"
        / "integral_agent"
        / "agent.yaml"
    )
    return yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}


def _actions_with_context() -> list[tuple[str, dict]]:
    """Every declared action that sets a context block, in file order."""
    out = []
    for action in _load_agent_yaml().get("actions") or []:
        ctx = dict(action.get("context") or {})
        if ctx:
            out.append((str(action.get("action")), ctx))
    return out


def _resolve_action_model(label: str):
    """Import the action class jvagent will apply this context to.

    The module layout is not one convention — orchestrator and intro sit at
    ``action/<pkg>/<module>``, while the language models live under
    ``action/model/language/<vendor>/<vendor>`` with a ``<Vendor>Language``
    prefix. Rather than guess, the mapping is explicit: a wrong guess would
    resolve nothing and SKIP, which is the failure mode this whole test exists
    to avoid.

    Returns None when the class is genuinely unresolvable — the caller skips,
    because "we could not check" and "we checked and it is fine" must never
    look the same.

    ``integral/*`` actions live in the ``agent/`` tree, which is not importable
    from the backend test path, so they resolve against jvagent's BASE
    ``Action`` instead: every Integral action subclasses it, so a key that is
    not on the base and not on the subclass would still be caught, and one that
    IS on the base (``enabled``, which is what our embedded action sets) is
    correctly accepted. Narrower than the real subclass, but sound in the
    direction that matters — it cannot pass a key jvagent would drop for being
    unknown to the whole hierarchy.
    """
    import importlib
    import importlib.util

    explicit = {
        "jvagent/orchestrator": (
            "jvagent.action.orchestrator.orchestrator_interact_action",
            "OrchestratorInteractAction",
        ),
        "jvagent/intro_interact_action": (
            "jvagent.action.intro.intro_interact_action",
            "IntroInteractAction",
        ),
        # LiteLLM is the only model backend the harness declares. The four
        # per-provider entries that used to live here went with the actions
        # themselves; provider selection is now the `provider/model` prefix
        # on a model id, not a separate action class.
        "jvagent/litellm_lm": (
            "jvagent.action.model.language.litellm.litellm_lm",
            "LiteLLMLanguageModelAction",
        ),
    }
    candidates = []
    if label in explicit:
        candidates.append(explicit[label])
    elif not label.startswith("jvagent/"):
        # Integral-owned action. Resolve the real class where one exists --
        # these live under agent/agents/<app>/<agent>/actions/<ns>/<name>/.
        # Checking them against the base Action was safe while no Integral
        # action declared fields of its own, but it turns a genuine field
        # (personal_context_attention's model / model_action_type) into a
        # false "not a field" failure, and the fix for that failure would be
        # to DELETE live config.
        ns, name = label.split("/", 1)
        repo_root = Path(__file__).resolve().parents[2]
        agent_root = repo_root / "agent" / "agents" / "integral" / "integral_agent"
        mod_dir = agent_root / "actions" / ns / name
        for stem in (f"{name}_interact_action", f"{name}_action", name):
            path = mod_dir / f"{stem}.py"
            if not path.is_file():
                continue
            spec = importlib.util.spec_from_file_location(stem, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception:  # noqa: BLE001 — fall through to base Action
                break
            for obj in vars(module).values():
                if (
                    isinstance(obj, type)
                    and hasattr(obj, "model_fields")
                    and obj.__module__ == stem
                ):
                    return obj
            break
        # No Integral class found — fall back to the base contract.
        candidates.append(("jvagent.action", "Action"))
    elif label.startswith("jvagent/"):
        name = label.split("/", 1)[1]
        candidates += [
            (f"jvagent.action.{name}.{name}_action", _camel(name) + "Action"),
            (f"jvagent.action.{name}.{name}", _camel(name) + "Action"),
        ]

    for module_path, class_name in candidates:
        try:
            module = importlib.import_module(module_path)
        except Exception:  # noqa: BLE001 — a miss falls through to the next
            continue
        cls = getattr(module, class_name, None)
        if cls is not None and hasattr(cls, "model_fields"):
            return cls
    return None


def _camel(snake: str) -> str:
    return "".join(part.title() for part in snake.split("_"))


def _load_orchestrator_context() -> dict:
    agent_yaml = (
        Path(__file__).resolve().parents[2]
        / "agent"
        / "agents"
        / "integral"
        / "integral_agent"
        / "agent.yaml"
    )
    data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}
    for action in data.get("actions") or []:
        if action.get("action") == "jvagent/orchestrator":
            return dict(action.get("context") or {})
    return {}


def test_orchestrator_activation_budget_within_recommended_ceiling():
    ctx = _load_orchestrator_context()
    budget = int(ctx.get("activation_budget", 0))
    assert 20 <= budget <= 40, f"activation_budget={budget} outside 20–40 band"


def test_orchestrator_context_keys_are_real_fields():
    """Every agent.yaml context key must exist on the action model.

    jvagent applies context by name: a key that is not a field on
    ``OrchestratorInteractAction`` is logged once at boot ("the override will
    be silently dropped") and then ignored forever. Nothing else fails.

    This replaces ``test_orchestrator_escalation_not_premature``, which
    asserted ``escalate_after_tool_calls >= 2`` by reading THIS FILE — so it
    passed while the value never reached the action, and the ADR-0016
    escalation floor it claimed to guard was not in effect. Found by reading
    the boot log of a local server rather than by any test.

    Validating the whole key set rather than one knob is the point: the same
    silent-drop applies to every tuning value here (budgets, history limit,
    model overrides), and a rename upstream would take them out the same way.
    """
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    ctx = _load_orchestrator_context()
    assert ctx, "no orchestrator context found in agent.yaml"
    fields = set(OrchestratorInteractAction.model_fields)
    unknown = sorted(k for k in ctx if k not in fields)
    assert not unknown, (
        f"agent.yaml sets orchestrator context keys that are not fields on "
        f"OrchestratorInteractAction: {unknown}. jvagent drops them silently "
        "— the boot log warns once and the value never takes effect. Either "
        "the key was renamed upstream (find the new name) or the feature is "
        "gone (remove the key and whatever documents it)."
    )


@pytest.mark.parametrize(
    ("label", "ctx"),
    _actions_with_context(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_every_action_context_key_is_a_real_field(label: str, ctx: dict):
    """The orchestrator gate, generalised to every action in agent.yaml.

    The silent-drop is a property of how jvagent applies context BY NAME, not
    of the orchestrator: a dead key on ``reply``, ``vision``, ``intro`` or any
    ``*_lm`` action is discarded exactly as quietly, with one boot-log line
    nobody reads. `escalate_after_tool_calls` (removed in #84) proved the class
    is real; this closes the rest of the surface rather than the one instance
    that happened to be found.

    Actions whose class cannot be resolved SKIP — "could not check" must not
    read as "checked and fine". Integral-owned actions are skipped by design:
    they are not jvagent models.
    """
    model = _resolve_action_model(label)
    if model is None:
        pytest.skip(f"{label}: not a resolvable jvagent action model")

    fields = set(model.model_fields)
    unknown = sorted(k for k in ctx if k not in fields)
    assert not unknown, (
        f"agent.yaml sets context keys on {label} that are not fields on "
        f"{model.__name__}: {unknown}. jvagent drops them silently — the boot "
        "log warns once and the value never takes effect. Either the key was "
        "renamed upstream (find the new name) or the feature is gone (remove "
        "the key and whatever documents it)."
    )


def test_orchestrator_history_limit_bounded():
    ctx = _load_orchestrator_context()
    assert int(ctx.get("history_limit", 10)) <= 8


def test_orchestrator_observation_budget_fits_a_real_listing():
    """The observation budget must hold a per-app track listing.

    jvagent elides a tool result over `observation_max_chars` and tells the
    model to re-run the tool. A Track serialized by `export_node` measures
    ~617 chars, so jvagent's 4000 default holds about six of them — and the
    orchestrator answered "how many tracks are in the Finance app" with 7 tool
    calls, 123.2k tokens and 4 of the 5 names, because it kept re-querying to
    see past the elision. At 12000 the same question took 2 calls, 28.3k
    tokens and got all five.

    The floor is 8000 rather than the configured 12000 so ordinary retuning
    does not trip it; what it catches is a drop back toward the default, which
    costs correctness and (counter-intuitively) more tokens, not fewer.
    """
    ctx = _load_orchestrator_context()
    budget = int(ctx.get("observation_max_chars", 0))
    assert budget >= 8000, (
        f"observation_max_chars={budget} is too small for integral's listing "
        "tools (~617 chars/track); see the note in agent.yaml"
    )


def test_agent_interaction_limit_prunes_memory():
    agent_yaml = (
        Path(__file__).resolve().parents[2]
        / "agent"
        / "agents"
        / "integral"
        / "integral_agent"
        / "agent.yaml"
    )
    data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}
    ctx = data.get("context") or {}
    assert int(ctx.get("interaction_limit", 0)) > 0
