"""Schemas for agentive/api/agent_events.py.

The agent_events WebSocket route carries no Pydantic request/response bodies
(it uses raw text frames + JSON-encoded pings). This module exists as a
namespace marker so future agent_events HTTP additions land in the canonical
schemas location.
"""
