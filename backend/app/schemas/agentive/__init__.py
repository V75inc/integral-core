"""Pydantic request/response schemas for the agentive layer.

Schemas are imported by handlers in ``backend/app/agentive/api/`` per the
jvspatial Object-Spatial Contract (CLAUDE.md § Forbidden Patterns —
inline ``BaseModel`` declarations inside ``api/*.py`` are hard-forbidden).

Mirrors the layout convention of top-level ``backend/app/schemas/``.
"""
