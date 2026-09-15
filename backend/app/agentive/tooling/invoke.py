"""In-process route invocation under a resolved principal + bound scope.

Mirrors the established embedded-action pattern (the working reference lives in
the jvagent app-dir at
``agent/agents/integral/integral_agent/actions/integral/embedded_integral_action/``
— ``_stub_request`` / ``_resolve_user`` / ``_call_endpoint``). We reimplement
it here in the backend so the manifest-driven tool surface does NOT depend on
the agent app-dir.

The shape of the contract: synthesize a minimal request exposing
``request.state.user`` (read by :func:`app.api.utils.resolve_principal_id`)
plus an ``X-Integral-Scope`` header (parsed by
:func:`app.services.scope_header.parse_scope_header` →
:func:`app.services.request_scope.resolve_workspace_id_from_request`), then
call the ``@endpoint``-decorated route handler directly as a regular async
function. The route's own auth/policy/scope/audit run unchanged — the
forwarder is thin (PC-1..PC-8 inherited).

Error-enveloping of :class:`JVSpatialAPIException` is added in ``dispatch.py``
in a later task; ``invoke`` stays thin here (the only envelope it emits is the
``user_not_found`` 401 when the principal cannot be resolved, matching the
working seed's ``_call_endpoint``).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from starlette.datastructures import Headers

logger = logging.getLogger(__name__)


class _StubRequest:
    """Minimal Starlette-Request-shaped object for in-process route calls.

    Integral's endpoint handlers only ever read ``request.state.user`` (via
    :func:`app.api.utils.resolve_principal_id`) plus ``X-Integral-Scope`` when
    the route is workspace-scoped. We satisfy both contracts without real
    Starlette/ASGI machinery.

    ``headers`` is a genuine case-insensitive :class:`starlette.datastructures.
    Headers` (not a plain dict): the working seed uses a lowercase-keyed dict,
    which happens to match every current backend read (``.get("x-integral-
    scope")``), but a real ``Headers`` is strictly more robust against route
    code that does a mixed-case ``.get("X-Integral-Scope")`` lookup.

    POST-body reads (``integral_query`` over ``/api/retrieve``) read their
    request body via ``await request.json()``; the async :meth:`json` method
    returns the body dict supplied at construction (default ``{}``).
    Query-param reads (``integral_get_related`` over
    ``/api/entries/{id}/related?relation=...``) read
    ``request.query_params.get(...)``; ``query_params`` is populated from the
    supplied dict (default ``{}``).
    """

    def __init__(
        self,
        user: Any,
        scope_workspace_id: Optional[str],
        *,
        json_body: Optional[dict] = None,
        query: Optional[dict] = None,
    ) -> None:
        # ``state.workspace_resolution`` is read+written by
        # ``resolve_workspace_id_from_request`` for per-request memoization;
        # giving it a mutable namespace keeps that fast-path intact.
        from types import SimpleNamespace

        self.state = SimpleNamespace(user=user, workspace_resolution={})
        header_pairs: dict[str, str] = {}
        if scope_workspace_id:
            # Canonical scope-header form parsed by parse_scope_header.
            header_pairs["x-integral-scope"] = f"ws:{scope_workspace_id}"
        self.headers = Headers(header_pairs)
        self.method = "GET"
        self.url = SimpleNamespace(path="/agent-internal")
        # query_params is a plain dict here — route code only ever calls
        # ``.get(name)`` on it (e.g. entry_relations reads ``?relation=``),
        # which a dict satisfies without the full Starlette QueryParams type.
        self.query_params: dict[str, str] = dict(query or {})
        self._json_body: dict = dict(json_body or {})
        self.client = None

    async def json(self) -> Any:
        """Return the stored request body (default ``{}``).

        Mirrors Starlette's ``Request.json`` for handlers (e.g. ``retrieve``)
        that parse their body via ``await request.json()``.
        """
        return self._json_body

    async def is_disconnected(self) -> bool:
        """Always false — in-process calls have no client disconnect path.

        Required by ``generate_chat_turn_sse`` (agent-turn / chat streaming)
        which polls disconnect between provider events.
        """
        return False


def stub_request(
    user: Any,
    scope_workspace_id: Optional[str],
    *,
    json_body: Optional[dict] = None,
    query: Optional[dict] = None,
) -> Any:
    """Build a stub request carrying ``user``, scope header, body + query."""
    return _StubRequest(user, scope_workspace_id, json_body=json_body, query=query)


async def resolve_user(user_id: str) -> Optional[Any]:
    """Load the jvspatial ``AuthUser`` by id.

    Integral's endpoint handlers read ``request.state.user.id`` (via
    :func:`app.api.utils.resolve_principal_id`) and forward that id to the
    service layer, which is keyed on the AuthUser id (``o.User.*``). So the
    stub request needs the AuthUser instance.

    Returns ``None`` when the id is empty or no AuthUser matches — callers
    convert that into a structured error envelope so the agent sees a clean
    refusal rather than an exception.
    """
    if not user_id:
        return None
    from jvspatial.api.auth.models import User as AuthUser

    return await AuthUser.get(user_id)


async def invoke_route_in_process(
    handler,
    *,
    principal_id: str,
    scope: Optional[str] = None,
    json_body: Optional[dict] = None,
    query: Optional[dict] = None,
    **kwargs: Any,
) -> Any:
    """Invoke a route handler in-process under a resolved principal.

    Args:
        handler: An ``@endpoint``-decorated async route handler, called as
            ``await handler(request, **kwargs)``.
        principal_id: The acting user's AuthUser id (``o.User.*``).
        scope: Optional workspace id to bind as ``X-Integral-Scope``.
        json_body: Optional dict returned by ``await request.json()`` — for
            POST-body reads whose handler parses the body itself (e.g.
            ``retrieve`` validating ``RetrieveRequest``). Data only; never an
            identity/scope carrier.
        query: Optional dict exposed as ``request.query_params`` — for reads
            whose handler pulls a param off the query string (e.g.
            ``list_entry_relations`` reading ``?relation=``). Data only.
        **kwargs: Path/handler-kwarg params forwarded verbatim to ``handler``.

    Returns:
        Whatever the handler returns, or a structured ``user_not_found``
        error envelope when the principal cannot be resolved.
    """
    user = await resolve_user(principal_id)
    if not user:
        return {
            "error": True,
            "status_code": 401,
            "error_code": "user_not_found",
            "message": f"No user {principal_id!r}",
        }
    # NOTE: do NOT pre-seed ``request.state.workspace_resolution`` here.
    #
    # ``scope`` arrives from the caller -- ``execute_tool_endpoint`` accepts it
    # from the request body ("explicit override") or from the X-Integral-Scope
    # header, and neither input is validated before it reaches this function.
    # Seeding the memo used to save a membership round-trip, but
    # ``resolve_workspace_id_from_request`` returns a memo hit *before* its
    # ``_user_member_of`` check, so the gate never ran for any tool call: an
    # authenticated caller could name any workspace id and have handlers treat
    # it as their validated scope.
    #
    # That was reachable, not merely theoretical. Handlers that scope by the
    # resolved workspace rather than re-checking each row read straight through
    # it -- the unfiltered ``GET /api/tags`` branch resolves the workspace and
    # then lists the Apps/Tracks inside it, and ``integral_list_tags`` is a
    # manifest tool. See tests/test_agentive_scope_enforcement.py, which fails
    # with the victim's tag in hand when this seed is restored.
    #
    # ``stub_request`` already carries ``scope`` as the X-Integral-Scope header,
    # so the resolver validates it against membership and falls back to the
    # caller's personal workspace when it does not hold. The extra round-trip is
    # the cost of the check actually happening; the resolver memoizes its own
    # validated result for the rest of the request.
    request = stub_request(user, scope, json_body=json_body, query=query)
    return await handler(request, **kwargs)
