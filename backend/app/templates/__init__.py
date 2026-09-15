"""Package marker for backend/app/templates.

Sub-packages (e.g. ``notifications/``) contain Jinja2 ``.j2`` template
files consumed by the corresponding service layer. The Jinja2
``FileSystemLoader`` in those services resolves the directory path
relative to this package marker, so this file must exist even though it
holds no module-level code.
"""
