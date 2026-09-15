"""Package marker for notification Jinja2 templates (Phase 9 Plan 09-03a).

Each ``<kind>.{txt,html}.j2`` file in this directory is rendered by the
``EmailChannel`` in ``app.services.notification_channels.email_channel``
when dispatching a notification of that kind. The Jinja2 environment is
built with ``autoescape=select_autoescape(['html','j2'])`` so HTML
templates escape user-supplied variables (T-09-03a-T01 — XSS gate);
plain-text templates are not HTML-rendered and carry no XSS surface.

Kinds present in 09-03a (3 kinds × 2 formats = 6 templates):

- ``mention``             — someone @-mentioned the user in an entry/comment.
- ``share``               — someone shared a resource (space/track/entry) with the user.
- ``agent_pending_write`` — an agent has a write pending human approval.

``invitation`` and ``system`` templates are deferred to a follow-up; the
default preferences already gate ``system`` off-by-email so no Jinja
template is required to ship. ``whatsapp_welcome`` is intentionally
absent here — WhatsApp Business Cloud API requires a pre-approved Meta
template, not a server-side Jinja render; that template lives in
Plan 09-03b's WhatsApp adapter.
"""
