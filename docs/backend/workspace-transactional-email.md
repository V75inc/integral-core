# Workspace transactional email

Org workspaces may send outbound mail through a workspace-configured **Resend** or **SendGrid** account. Configuration is owned by the **Email Log** commercial app; routing lives in Core.

## Choke point

All bundle sends go through:

```python
await ctx.send_workspace_transactional_email(
    to="user@example.com",
    subject="…",
    text="…",
    html="…",
    source_kind="recruitment_hire",
    source_id=entry_id,
)
```

Core services use `send_email(EmailMessage(...))` with the same `workspace_id` and `source_kind` fields.

## Rules

| Rule | Detail |
|------|--------|
| Single choke point | No provider HTTP from bundle code. |
| Attribute sends | Always pass `source_kind` (stable string per flow) and bind `workspace_id` via ToolContext. |
| System mail | `password_reset` and `email_verification` always use platform credentials. |
| Fallback | No config, disabled delivery, or personal workspace → platform sender; user flows must not fail. |
| Audit | When Email Log is installed, `email.sent` → outbound email entries (optional). |

## Email Log admin facades

- `get_workspace_email_delivery_redacted()`
- `upsert_workspace_email_delivery(payload)`
- `validate_workspace_email_delivery(payload)`
- `send_workspace_test_email(to=None)` — `source_kind=email_log_test`
