"""Post-turn attention: deferred, gated, and unable to observe itself.

The action records what a finished turn revealed about the person. Three
properties decide whether it is safe to leave running on every turn, and each
is defended here:

- **deferral** — it must run AFTER the reply is sent. ``weight`` alone does not
  do that; the walker executes weighted actions inside the turn and only
  defers on ``run_in_background``. Both are asserted, and so is the walker
  branch that honours the flag, because the contract lives upstream.
- **gating** — a turn with no Integral user behind it is not observed. Facets
  only narrow: a system-facet turn has no person to have context about.
- **the loop guard** — this App runs scheduled turns of its own. Observing
  those would produce observations about the App's own promotions, which the
  next promotion would read, forever.

Extraction is pinned separately: a malformed model response must produce no
observations rather than a guess at what was meant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import app.agentive.staging_executors  # noqa: F401

# Imported at module scope on purpose. These pull app.config, which loads
# .env into os.environ as a side effect. Left to the lazy imports inside the
# action, that mutation happens INSIDE a test body and trips conftest's
# env-leak guard at teardown — a real guard catching an artefact of import
# timing rather than of this code.
import app.api.entries  # noqa: F401
from app.agentive.tooling import dispatch_tool  # noqa: F401

_REPO = Path(__file__).resolve().parents[2]
_ACTION_DIR = (
    _REPO
    / "agent"
    / "agents"
    / "integral"
    / "integral_agent"
    / "actions"
    / "integral"
    / "personal_context_attention"
)


def _agent_yaml() -> dict:
    path = _REPO / "agent" / "agents" / "integral" / "integral_agent" / "agent.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _action_source() -> str:
    return (_ACTION_DIR / "personal_context_attention_interact_action.py").read_text(
        encoding="utf-8"
    )


class _Interaction:
    """Minimal stand-in for a jvagent Interaction."""

    def __init__(self, *, utterance: str = "", channel: str = "integral", response=""):
        self.id = "n.Interaction.1"
        self.utterance = utterance
        self.channel = channel
        self.response = response


def _action() -> Any:
    """Import the action without booting jvagent's action loader."""
    import importlib.util
    import sys

    name = "personal_context_attention_interact_action"
    if name in sys.modules:
        return sys.modules[name].PersonalContextAttentionInteractAction
    spec = importlib.util.spec_from_file_location(
        name, _ACTION_DIR / "personal_context_attention_interact_action.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.PersonalContextAttentionInteractAction


# ---------------------------------------------------------------------------
# Deferral
# ---------------------------------------------------------------------------


def test_the_action_declares_itself_background():
    cls = _action()
    # Read the declared default off the attribute descriptor rather than
    # instantiating — instantiating needs a graph.
    src = _action_source()
    assert "run_in_background: bool = attribute(" in src
    assert "default=True" in src.split("run_in_background: bool = attribute(")[1][:200]
    assert cls.__name__ == "PersonalContextAttentionInteractAction"


def test_weight_alone_would_not_defer_it():
    # The pairing is the point: the walker runs weighted actions inside the
    # turn and checks run_in_background BEFORE executing. If a later refactor
    # drops the flag and keeps the weight, attention starts adding latency to
    # every reply — silently.
    walker = (
        _REPO.parent
        / "jv"
        / "jvagent"
        / "jvagent"
        / "action"
        / "interact"
        / "interact_walker.py"
    )
    if not walker.is_file():
        pytest.skip("jvagent source not available in this checkout")
    src = walker.read_text(encoding="utf-8")
    assert 'getattr(here, "run_in_background", False)' in src
    assert "self.background_actions.append(here)" in src


def test_it_is_registered_on_the_resident():
    names = [a.get("action") for a in _agent_yaml().get("actions") or []]
    assert "integral/personal_context_attention" in names


def test_the_light_gear_is_pinned_and_light():
    # A light gear now exists, so this pins it rather than pinning its
    # absence. The earlier version of this test asserted no model was wired
    # and told whoever wired one to swap the assertion for exactly this.
    #
    # What matters is that attention does NOT run on the heavy reasoning
    # model: it fires on every turn, and extraction is the cheapest thing
    # the harness does.
    declared = None
    orchestrator = None
    for action in _agent_yaml().get("actions") or []:
        if action.get("action") == "integral/personal_context_attention":
            declared = dict(action.get("context") or {})
        if action.get("action") == "jvagent/orchestrator":
            orchestrator = dict(action.get("context") or {})
    assert declared is not None and orchestrator is not None

    assert declared.get("model") == orchestrator.get("light_model"), (
        "attention should run on the SAME light gear the orchestrator uses, "
        "not a separately-drifting model id"
    )
    assert declared.get("model") != orchestrator.get("model"), (
        "attention is pinned to the heavy reasoning model — it runs on every "
        "turn and only extracts"
    )
    assert declared.get("model_action_type") == "LiteLLMLanguageModelAction"


def test_the_light_gear_fallback_is_still_loud():
    # The fallback path stays reachable for a deployment that unsets the
    # model. Unset must mean "fall back and say so once", never "quietly run
    # extraction on the heavy model".
    src = _action_source()
    assert "_warned_no_light_gear" in src
    assert "no light gear configured" in src


# ---------------------------------------------------------------------------
# Gating + loop guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "interaction,expected,why",
    [
        (_Interaction(utterance="Sarah needs the deck"), False, "a person spoke"),
        (_Interaction(utterance=""), True, "nobody said anything"),
        (_Interaction(utterance="   "), True, "whitespace is not an utterance"),
        (
            _Interaction(utterance="[agent workstream]"),
            True,
            "the workstream placeholder ai_chat substitutes for an empty prompt",
        ),
        (
            _Interaction(utterance="run the nightly promote", channel="routine"),
            True,
            "a non-human channel",
        ),
    ],
)
def test_machine_turns_are_not_observed(interaction, expected, why):
    cls = _action()
    assert cls._is_machine_turn(cls, interaction) is expected, why


def test_the_loop_guard_is_documented_as_structural():
    # The routine path tags its turn origin="routine_task", but that tag stops
    # at ChatTurnContext.extra_data and never reaches the walker — so the
    # guard is channel + empty-utterance rather than an origin match. If
    # somebody threads origin through later, this comment is the pointer.
    src = _action_source()
    assert "origin=" in src and "does not reach the walker" in src


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_a_malformed_response_yields_nothing():
    cls = _action()
    for raw in ["", "I could not find anything.", "{", "{}", '{"observations": "no"}']:
        assert cls._parse_candidates(raw) == [], raw


def test_candidates_are_parsed_out_of_a_fenced_block():
    cls = _action()
    rows = cls._parse_candidates(
        '```json\n{"observations": [{"title": "Owes Sarah the deck", '
        '"excerpt": "get the deck to Sarah before Friday", "salience": 0.8}]}\n```'
    )
    assert len(rows) == 1
    assert rows[0]["title"] == "Owes Sarah the deck"
    assert rows[0]["excerpt"] == "get the deck to Sarah before Friday"
    assert rows[0]["salience"] == 0.8


def test_rows_without_a_verbatim_excerpt_are_dropped():
    # The excerpt IS the evidence. A candidate with only a gist is an
    # interpretation, and interpretation belongs at promotion.
    cls = _action()
    rows = cls._parse_candidates(
        '{"observations": [{"title": "Something", "excerpt": ""}, '
        '{"title": "", "excerpt": "words"}]}'
    )
    assert rows == []


def test_a_flood_of_candidates_is_capped():
    cls = _action()
    many = ", ".join(
        f'{{"title": "t{i}", "excerpt": "e{i}", "salience": 0.5}}' for i in range(40)
    )
    rows = cls._parse_candidates('{"observations": [%s]}' % many)
    assert len(rows) <= 6


def test_salience_is_clamped():
    cls = _action()
    rows = cls._parse_candidates(
        '{"observations": [{"title": "a", "excerpt": "b", "salience": 9.5}, '
        '{"title": "c", "excerpt": "d", "salience": -3}, '
        '{"title": "e", "excerpt": "f", "salience": "high"}]}'
    )
    assert [r["salience"] for r in rows] == [1.0, 0.0, 0.5]


# ---------------------------------------------------------------------------
# Thin harness
# ---------------------------------------------------------------------------


def test_attention_states_facts_and_never_steers():
    # The thin-harness rule: what the App learns is a record, not a prompt
    # hook. Nothing here may compose a reply, issue a directive, or write back
    # onto the walker.
    src = _action_source()
    for forbidden in (
        "visitor.interaction.response =",
        "add_directive",
        "set_directive",
        "visitor.directives",
        "ReplyAction",
    ):
        assert forbidden not in src, f"attention steers the next turn: {forbidden}"


def test_it_writes_through_the_shared_dispatch_seam():
    # Not a private write path: the same dispatch_tool every agent write uses,
    # so permissions, change events and the unstaged gate all apply.
    src = _action_source()
    assert "from app.agentive.tooling import dispatch_tool" in src
    assert "integral_create_entry" in src
    # No direct persistence and no endpoint calls: the action asks
    # services.personal_context WHERE to write and writes through the seam.
    for forbidden in (
        "Entry.create(",
        ".save()",
        ".destroy()",
        "from app.models",
        "from app.api",
    ):
        assert forbidden not in src, f"attention bypasses the seam: {forbidden}"


def test_a_staged_observation_is_reported_as_a_defect():
    # If the unstaged declaration stops reaching the attached manifest, the
    # App silently starts minting approval cards for its own note-taking —
    # exactly what ADR-006 exists to prevent. That must be loud.
    src = _action_source()
    assert 'data.get("staged") is not False' in src
    assert "logger.error(" in src


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


@pytest.mark.library
@pytest.mark.asyncio
async def test_a_turn_becomes_an_unstaged_observation(test_user):
    """The whole path, with the model stubbed: turn in, observation out.

    Everything above this asserts structure. This one asserts the effect —
    that an observation lands in the person's stream, unstaged, carrying the
    verbatim excerpt and a thread back to the interaction it came from.
    """
    from unittest.mock import patch

    from app.agentive.staging import get_pending_for_user
    from app.models.edges import CONTAINS
    from app.services.personal_context import provision_personal_context_app

    app_node = await provision_personal_context_app(user_id=test_user.id)
    assert app_node is not None
    tracks = {
        str(getattr(t, "template_id", "") or ""): t
        for t in await app_node.nodes(edge=[CONTAINS], node=["Track"], direction="out")
    }
    principal = str(getattr(test_user, "user_id", "") or test_user.id)
    before = len(await get_pending_for_user(principal) or [])

    cls = _action()
    action = cls()

    class _Model:
        async def generate(self, prompt: str, **_: object) -> str:
            assert "context_attend" in prompt, "the SOP was not loaded into the prompt"
            assert "get the deck to Sarah" in prompt, "the exchange was not included"
            return (
                '{"observations": [{"title": "Owes Sarah the MMG deck by Friday", '
                '"excerpt": "get the deck to Sarah before Friday", '
                '"salience": 0.8, "repeat_of": ""}]}'
            )

    class _Visitor:
        user_id = principal
        interaction = _Interaction(
            utterance="Can you get the deck to Sarah before Friday?",
            response="Staged it for your review.",
        )

    with patch.object(cls, "_resolve_model_action", return_value=_Model()):
        await action.execute(_Visitor())

    from app.models.nodes import Entry

    rows = [
        e for e in (await Entry.find({"context.track_id": tracks["stream"].id})) or []
    ]
    assert rows, "no observation was written"
    titles = [getattr(r, "title", "") for r in rows]
    assert "Owes Sarah the MMG deck by Friday" in titles

    observation = next(
        r for r in rows if r.title == "Owes Sarah the MMG deck by Friday"
    )
    fields = dict(getattr(observation, "custom_fields", None) or {})
    assert fields.get("surface") == "chat"
    assert fields.get("handled") == "pending"
    assert fields.get("excerpt") == "get the deck to Sarah before Friday"
    assert fields.get("interaction_ref") == "n.Interaction.1"

    # An attention event, one per run.
    if "attention" in tracks:
        events = [
            e
            for e in (await Entry.find({"context.track_id": tracks["attention"].id}))
            or []
        ]
        assert events, "nothing was written to the Attention Log"

    # And no approval card for any of it.
    assert len(await get_pending_for_user(principal) or []) == before


@pytest.mark.library
@pytest.mark.asyncio
async def test_a_routine_turn_writes_nothing(test_user):
    """The loop guard, at the level that matters: no write, no model call.

    Asserting ``_is_machine_turn`` in isolation proves the predicate, not
    that ``_attend`` consults it. Deleting the guard from the entry point
    left every predicate test passing — this is the one that fails.
    """
    from unittest.mock import patch

    from app.models.edges import CONTAINS
    from app.models.nodes import Entry
    from app.services.personal_context import provision_personal_context_app

    app_node = await provision_personal_context_app(user_id=test_user.id)
    assert app_node is not None
    stream = next(
        t
        for t in await app_node.nodes(edge=[CONTAINS], node=["Track"], direction="out")
        if str(getattr(t, "template_id", "") or "") == "stream"
    )
    before = len((await Entry.find({"context.track_id": stream.id})) or [])

    cls = _action()
    action = cls()
    calls: list[str] = []

    class _Model:
        async def generate(self, prompt: str, **_: object) -> str:
            calls.append(prompt)
            return '{"observations": [{"title": "x", "excerpt": "y"}]}'

    class _RoutineVisitor:
        user_id = str(getattr(test_user, "user_id", "") or test_user.id)
        # What the nightly promote turn looks like: this App's own scheduled
        # run. Observing it would produce observations about the App's own
        # promotions, which the next promotion would read.
        interaction = _Interaction(utterance="", channel="routine")

    with patch.object(cls, "_resolve_model_action", return_value=_Model()):
        await action.execute(_RoutineVisitor())

    assert calls == [], "the light gear was called on a machine turn"
    after = len((await Entry.find({"context.track_id": stream.id})) or [])
    assert after == before, "a routine turn was observed"


@pytest.mark.library
@pytest.mark.asyncio
async def test_a_turn_with_no_person_behind_it_writes_nothing(test_user):
    """Facet gating, structurally.

    Facets only narrow. A turn with no Integral user behind it — the system
    facet, or any principal that is not a person — has nobody to have
    context about, so nothing is observed and the light gear is not called.

    NOTE (reported at gate B): there is no per-turn facet discriminator
    reachable from the walker today. Chat resolves the SYSTEM AgentConfig
    for every turn (``uplink_registry.get_system_agent()``) and acts under
    the user's principal, so a facet check against the connection would
    reject every turn. The gate that IS available and enforces the same
    intent is this one: the acting principal must resolve to a User who owns
    a personal workspace with the App installed.
    """
    from unittest.mock import patch

    from app.models.edges import CONTAINS
    from app.models.nodes import Entry
    from app.services.personal_context import provision_personal_context_app

    app_node = await provision_personal_context_app(user_id=test_user.id)
    assert app_node is not None
    stream = next(
        t
        for t in await app_node.nodes(edge=[CONTAINS], node=["Track"], direction="out")
        if str(getattr(t, "template_id", "") or "") == "stream"
    )
    before = len((await Entry.find({"context.track_id": stream.id})) or [])

    cls = _action()
    action = cls()
    calls: list[str] = []

    class _Model:
        async def generate(self, prompt: str, **_: object) -> str:
            calls.append(prompt)
            return '{"observations": [{"title": "x", "excerpt": "y"}]}'

    class _SystemVisitor:
        user_id = "n.User.does-not-exist"
        interaction = _Interaction(
            utterance="Summarize the deployment health check",
            response="All green.",
        )

    with patch.object(cls, "_resolve_model_action", return_value=_Model()):
        await action.execute(_SystemVisitor())

    assert calls == [], "the light gear ran for a turn with no person behind it"
    assert len((await Entry.find({"context.track_id": stream.id})) or []) == before


@pytest.mark.library
@pytest.mark.asyncio
async def test_attention_can_be_switched_off(test_user):
    """``attention_enabled: false`` stops it at the source.

    Not "records but hides" — nothing is written and no model is called.
    """
    from unittest.mock import patch

    from app.models.edges import CONTAINS
    from app.models.nodes import Entry
    from app.services.personal_context import provision_personal_context_app

    app_node = await provision_personal_context_app(user_id=test_user.id)
    assert app_node is not None
    stream = next(
        t
        for t in await app_node.nodes(edge=[CONTAINS], node=["Track"], direction="out")
        if str(getattr(t, "template_id", "") or "") == "stream"
    )
    before = len((await Entry.find({"context.track_id": stream.id})) or [])

    settings = dict(getattr(app_node, "settings", None) or {})
    settings["attention_enabled"] = False
    app_node.settings = settings
    await app_node.save()

    cls = _action()
    action = cls()
    calls: list[str] = []

    class _Model:
        async def generate(self, prompt: str, **_: object) -> str:
            calls.append(prompt)
            return '{"observations": [{"title": "x", "excerpt": "y"}]}'

    class _Visitor:
        user_id = str(getattr(test_user, "user_id", "") or test_user.id)
        interaction = _Interaction(
            utterance="Sarah needs the deck by Friday", response="Noted."
        )

    with patch.object(cls, "_resolve_model_action", return_value=_Model()):
        await action.execute(_Visitor())

    assert calls == [], "attention ran while switched off"
    assert len((await Entry.find({"context.track_id": stream.id})) or []) == before
