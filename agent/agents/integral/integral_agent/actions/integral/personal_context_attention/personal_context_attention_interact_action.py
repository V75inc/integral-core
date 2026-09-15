"""Post-turn attention for the Personal Context App.

After a turn has been answered and the reply sent, this action reads what
just happened and writes down what it revealed about the person. It is the
chat-side door of the App; the ``entry.create`` / ``entry.update`` hook is
the other one.

Three properties are load-bearing.

**It never holds the turn open.** ``run_in_background=True`` defers it past
the response (``interact_walker`` line ~658 queues it; embed mode runs the
queue inline after ``build_interact_response``). Weight 250 alone would not
be enough — the walker would still execute it inside the turn.

**It states facts; it never steers.** The thin-harness rule. Nothing written
here is injected into the next turn, no directive is issued, no reply is
composed, and the walker's state is not touched. A person's context is a
record, not a prompt hook.

**It extracts; it does not interpret.** The light gear's only job is to pick
out candidate observations and quote them verbatim. Interpretation happens at
promotion (``context_promote``), where a person sees a card and can disagree.
So this action does not run a tool loop: the model returns candidates, and
the action writes them through the same dispatch seam every other agent write
uses. Nothing the model returns can choose a track or a tool.

Where the writes land
---------------------

Observations go to the ``stream`` track of the App in the acting principal's
OWN personal workspace — regardless of which workspace the conversation
happened in. That workspace id is passed as the dispatch scope explicitly,
which is what lets a turn in an organization workspace still record into the
person's own context under their own principal. ``stream`` and ``attention``
are declared unstaged (ADR-006 / I-PC-01), so nothing here mints a card.

The procedure itself lives in the App bundle's ``context_attend/SKILL.md``,
resolved through the graph rather than by path, so the SOP has exactly one
home and a bundle rename does not break this action.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

#: The channel Integral's human chat uses (``INTEGRAL_CHANNEL`` in
#: ``app/agentive/connectors/jvagent_connector.py``). Anything else is a
#: machine-driven turn.
_HUMAN_CHANNEL = "integral"

#: Placeholder the agent-workstream path substitutes when a routine turn
#: carries no prompt (``app/api/ai_chat.py``: ``prompt or "[agent workstream]"``).
_WORKSTREAM_PLACEHOLDER = "[agent workstream]"

#: Cap on candidates accepted from one turn. A model that returns forty
#: "observations" about one message has not observed forty things.
_MAX_CANDIDATES = 6

_EXTRACTION_CONTRACT = """
You are running the procedure above over ONE finished exchange.

Return STRICT JSON and nothing else — no prose, no code fence:

{"observations": [
  {"title": "<one-line gist, reporting voice>",
   "excerpt": "<the source text, VERBATIM — copy it, never paraphrase>",
   "salience": <0.0-1.0>,
   "repeat_of": "<title of an existing fact this restates, or empty>"}
]}

Rules that override anything you infer from the procedure:
- Return [] when the exchange says nothing about the PERSON. Task content,
  code, build output and your own reasoning are not observations.
- `excerpt` must appear verbatim in the exchange below. Do not summarize it.
- One entry per distinct thing observed. Never merge two facts into one.
- At most %d entries.
- Salience measures how much this says about the person, not how important
  the work is.
""" % _MAX_CANDIDATES


class PersonalContextAttentionInteractAction(InteractAction):
    """Records what a finished turn revealed about the person."""

    weight: int = attribute(
        default=250,
        description="Runs after the reply. Deferred anyway; kept for ordering.",
    )
    always_execute: bool = attribute(
        default=True,
        description="Every turn is a candidate for observation.",
    )
    run_in_background: bool = attribute(
        default=True,
        description=(
            "Deferred past the response. Attention must never add latency to a "
            "reply, and must never surface as a typing indicator."
        ),
    )
    model_action_type: str = attribute(
        default="",
        description=(
            "LIGHT gear for extraction. Empty means 'no light gear configured' "
            "— the action falls back to whatever model action the agent has and "
            "logs a one-line warning, rather than silently running extraction "
            "on the heavy reasoning model."
        ),
    )
    model: str = attribute(
        default="",
        description="Light model identifier. Empty falls back with a warning.",
    )
    model_temperature: float = attribute(
        default=0.0,
        description="Extraction is not a creative task.",
    )

    # Warn once per process, not once per turn.
    _warned_no_light_gear: bool = False

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------

    def _is_machine_turn(self, interaction: Any) -> bool:
        """True when nobody said anything — a routine, a workstream, a probe.

        The loop this guards: this App runs scheduled turns of its own
        (nightly promote). Observing those would produce observations about
        the App's own promotions, which the next promotion would then read.

        NOTE (reported at gate B): the routine path tags its turn
        ``origin="routine_task"``, but that tag does not reach the walker —
        it stops at ``ChatTurnContext.extra_data``. So the guard is
        structural: a turn on a non-human channel, or with no human
        utterance, is not observed. Threading ``origin`` through to the
        Interaction would let this be exact rather than structural.
        """
        channel = str(getattr(interaction, "channel", "") or "").strip().lower()
        if channel and channel != _HUMAN_CHANNEL:
            return True
        utterance = str(getattr(interaction, "utterance", "") or "").strip()
        if not utterance:
            return True
        if utterance == _WORKSTREAM_PLACEHOLDER:
            return True
        return False

    # ------------------------------------------------------------------
    # Graph resolution
    # ------------------------------------------------------------------

    async def _resolve_target(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Resolve the person's App, its tracks and its settings.

        Delegates the graph walk to ``services.personal_context`` — this
        action asks the substrate where to write and then writes through the
        agent dispatch seam. It does not traverse the graph itself and holds
        no model imports.

        Returns ``None`` — never raises — when there is no Integral user
        behind the turn (a system-facet turn, or a principal that is not a
        person: facets only narrow), when the person has no Personal Context
        App, or when attention is switched off.
        """
        from app.services.permissions import get_user_node
        from app.services.personal_context import resolve_personal_context

        user = await get_user_node(user_id)
        if user is None:
            return None

        target = await resolve_personal_context(user_id=user.id)
        if target is None:
            return None
        tracks = target.get("tracks") or {}
        if "stream" not in tracks:
            return None
        return {
            "user": user,
            "app": target["app"],
            "workspace_id": target["workspace_id"],
            "settings": target["settings"],
            "bundle_dir": target.get("bundle_dir") or "",
            "stream": tracks["stream"],
            "attention": tracks.get("attention"),
        }

    @staticmethod
    def _load_sop(bundle_dir: str) -> str:
        """Read the ``context_attend`` SOP from the bundle it was installed from.

        The directory comes from the library row the substrate resolved, so
        the SOP has exactly one home and this action holds no path to a
        bundle it does not own — a rename does not break it.
        """
        from pathlib import Path

        if not bundle_dir:
            return ""
        try:
            sop = Path(bundle_dir) / "skills" / "context_attend" / "SKILL.md"
            if sop.is_file():
                return sop.read_text(encoding="utf-8")
        except OSError:
            logger.exception("personal-context attention: SOP unreadable")
        return ""

    # ------------------------------------------------------------------
    # Light gear
    # ------------------------------------------------------------------

    async def _resolve_model_action(self) -> Optional[Any]:
        if not (self.model_action_type or "").strip():
            if not PersonalContextAttentionInteractAction._warned_no_light_gear:
                PersonalContextAttentionInteractAction._warned_no_light_gear = True
                logger.warning(
                    "personal-context attention: no light gear configured "
                    "(light_model unset in agent.yaml); falling back to the "
                    "agent's configured model for observation extraction"
                )
        try:
            return await self.get_model_action(required=False)
        except Exception:  # noqa: BLE001
            logger.exception("personal-context attention: no model action available")
            return None

    @staticmethod
    def _parse_candidates(raw: str) -> List[Dict[str, Any]]:
        """Pull the observation list out of a model response.

        Tolerant of a fenced block, strict about everything else: a malformed
        response yields no observations rather than a guess at what was meant.
        """
        text = (raw or "").strip()
        if not text:
            return []
        fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return []
        try:
            parsed = json.loads(text[start : end + 1])
        except (ValueError, TypeError):
            return []
        rows = parsed.get("observations") if isinstance(parsed, dict) else None
        if not isinstance(rows, list):
            return []
        out: List[Dict[str, Any]] = []
        for row in rows[:_MAX_CANDIDATES]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            excerpt = str(row.get("excerpt") or "").strip()
            if not title or not excerpt:
                continue
            try:
                salience = float(row.get("salience", 0.5))
            except (TypeError, ValueError):
                salience = 0.5
            out.append(
                {
                    "title": title[:200],
                    "excerpt": excerpt,
                    "salience": max(0.0, min(1.0, salience)),
                    "repeat_of": str(row.get("repeat_of") or "").strip(),
                }
            )
        return out

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def _write(
        self,
        *,
        user_id: str,
        scope: str,
        track_id: str,
        title: str,
        body: str,
        fields: Dict[str, Any],
    ) -> Optional[str]:
        """One row, through the shared dispatch seam.

        Returns the new entry id on success, ``""`` when the write succeeded
        but the executor surfaced no id, and ``None`` when it failed.

        ``scope`` is the person's personal workspace, not the conversation's
        workspace — that is what lets a turn in an organization workspace
        record into the person's own context. The seam's unstaged gate
        (I-PC-01) recognises the target and skips the card.
        """
        from app.agentive.tooling import dispatch_tool

        result = await dispatch_tool(
            "integral_create_entry",
            {
                "track_id": track_id,
                "title": title,
                "body": body,
                "fields": fields,
            },
            principal_id=user_id,
            scope=scope,
        )
        if result.is_error:
            logger.warning(
                "personal-context attention: write failed (%s): %s",
                result.error_code,
                result.message,
            )
            return None
        data = result.data or {}
        if data.get("staged") is not False:
            # The unstaged gate did not recognise the target. Writing an
            # approval card for a note the App took is exactly what ADR-006
            # exists to prevent, so say so loudly rather than shipping cards.
            logger.error(
                "personal-context attention: observation STAGED instead of "
                "landing (track=%s) — the unstaged declaration is not "
                "reaching the attached manifest",
                track_id,
            )
        # The id if the executor surfaced one, otherwise the empty string —
        # NOT None. The caller distinguishes "written" from "failed", and an
        # id-less success is still a success. Returning None here once meant
        # a written observation looked like a failure, and the Attention Log
        # entry that should have followed it was never created.
        written = data.get("result")
        if isinstance(written, dict):
            for key in ("id", "entry_id"):
                found = written.get(key)
                if found:
                    return str(found)
            entry = written.get("entry")
            if isinstance(entry, dict) and entry.get("id"):
                return str(entry["id"])
        return ""

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def execute(self, visitor: "InteractWalker") -> None:
        """Observe the turn that just closed. Never raises into the walker."""
        try:
            await self._attend(visitor)
        except Exception:  # noqa: BLE001
            logger.exception("personal-context attention failed; turn unaffected")

    async def _attend(self, visitor: "InteractWalker") -> None:
        interaction = getattr(visitor, "interaction", None)
        if interaction is None:
            return
        if self._is_machine_turn(interaction):
            return

        user_id = str(getattr(visitor, "user_id", "") or "")
        if not user_id:
            return

        target = await self._resolve_target(user_id)
        if target is None:
            return

        utterance = str(getattr(interaction, "utterance", "") or "").strip()
        response = str(getattr(interaction, "response", "") or "").strip()
        exchange = f"PERSON SAID:\n{utterance}\n\nAGENT REPLIED:\n{response}".strip()

        sop = self._load_sop(target["bundle_dir"])
        if not sop:
            return

        model_action = await self._resolve_model_action()
        if model_action is None:
            return

        prompt = (
            f"{sop}\n\n---\n{_EXTRACTION_CONTRACT}\n\n---\nTHE EXCHANGE:\n{exchange}"
        )
        try:
            raw = await model_action.generate(
                prompt=prompt,
                temperature=self.model_temperature,
            )
        except TypeError:
            # Older LanguageModelAction signatures take the prompt positionally
            # and carry temperature on the action rather than the call.
            raw = await model_action.generate(prompt)
        except Exception:  # noqa: BLE001
            logger.exception("personal-context attention: extraction call failed")
            return

        candidates = self._parse_candidates(
            raw if isinstance(raw, str) else str(getattr(raw, "content", "") or raw)
        )
        if not candidates:
            return

        scope = target["workspace_id"]
        observed_workspace = str(
            getattr(visitor, "workspace_id", "") or ""
        ) or self._scope_from_context()
        interaction_ref = str(getattr(interaction, "id", "") or "")

        written: List[str] = []
        for candidate in candidates:
            title = candidate["title"]
            if candidate["repeat_of"]:
                # A restatement of something already believed. Recorded as its
                # own observation so promotion can raise last_confirmed —
                # attention never edits a fact itself.
                title = f"Again: {title}"
            entry_id = await self._write(
                user_id=user_id,
                scope=scope,
                track_id=target["stream"].id,
                title=title,
                body=candidate["excerpt"],
                fields={
                    "surface": "chat",
                    "excerpt": candidate["excerpt"],
                    "salience": candidate["salience"],
                    "handled": "pending",
                    "interaction_ref": interaction_ref,
                    "workspace_ref": observed_workspace,
                },
            )
            if entry_id is not None:
                written.append(entry_id)

        if not written or target["attention"] is None:
            return

        # One event per run, never one per observation.
        await self._write(
            user_id=user_id,
            scope=scope,
            track_id=target["attention"].id,
            title=f"Noticed {len(written)} thing{'s' if len(written) != 1 else ''}",
            body=f"From the exchange at interaction `{interaction_ref}`.",
            fields={
                "kind": "observed",
                "summary": f"{len(written)} observation(s) recorded",
                "refs": {
                    "interaction_id": interaction_ref,
                    "observation_ids": written,
                },
            },
        )

    @staticmethod
    def _scope_from_context() -> str:
        """The workspace the conversation was in, for provenance only."""
        try:
            from app.services.agent_scope import current_scope_workspace_id

            return str(current_scope_workspace_id.get() or "")
        except Exception:  # noqa: BLE001
            return ""
