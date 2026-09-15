"""Pydantic request/response schemas for core ``backend/app/api/`` handlers.

Schemas are imported by handlers in ``backend/app/api/`` per the
jvspatial Object-Spatial Contract (CLAUDE.md § Forbidden Patterns —
inline ``BaseModel`` declarations inside ``api/*.py`` are hard-forbidden).

This subpackage complements top-level ``backend/app/schemas/`` for the
plan-06-05 migration set; existing top-level entity schemas (``track.py``,
``entry.py``, ``apps.py``, …) remain where they are.
"""
