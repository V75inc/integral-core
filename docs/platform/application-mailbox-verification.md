# Application mailbox verification (Core proof)

Recruitment (and future Apps) must not send Core a browser `verified: true`
flag or rely on App-owned timestamps such as `email_verified_at` for
credential delivery. Core owns the OTP, stores only a hash, and retains a
server-side **proof** that hire provisioning consumes atomically.

## Records

`ApplicationMailboxVerification` (I-GRAPH-02 `Object`) binds:

- `candidate_entry_id`, `workspace_id`, normalized `email`
- `purpose` (e.g. `recruitment_application`)
- Pending OTP: `otp_hash`, `otp_expires_at`, `otp_attempts`
- After confirm: `verified_at`, `proof_expires_at` (30-day hire window)
- After hire consume: `consumed_at`, `consumed_for_user_id`

The opaque `proof_id` is the Object id (`o.ApplicationMailboxVerification.*`).
Recruitment may store that id on the candidate for server-side hire calls only;
it is not a bearer secret for browsers.

## Public apply (Recruitment host)

1. **Start** — invalidate prior pending proofs for the entry, persist OTP hash,
   send email (Recruitment host or App tool calls Core service).
2. **Confirm** — validate OTP; set `verified_at` and a 30-day
   `proof_expires_at`; clear OTP material; return `proof_id`.
3. Recruitment may set `email_verified_at` for UI/readiness; Core ignores it
   for delivery authorization.

## Hire provisioning

`POST /workspaces/{id}/members/provision` accepts optional:

- `personal_email` — stored on `User.personal_email`; not the login mailbox
- `application_mailbox_proof_id` + `candidate_entry_id`

When a valid, unconsumed proof matches workspace, candidate, and
`personal_email`, Core:

1. Creates the account with **work** `email` as primary (AuthUser).
2. Sets `User.personal_email`.
3. Consumes the proof in the provisioning transaction.
4. Sends a **single-use password setup link** to `personal_email` (no second
   OTP).

Without a valid proof, alternate delivery follows the existing
`credentials_email` OTP path. Responses distinguish activation sent, pending
OTP, and retryable delivery failures.

## Activation retry (application proof)

When initial setup-link delivery fails or is suppressed, workspace **admins**
may call:

- `GET /workspaces/{id}/members/{member_user_id}/activation-delivery` — safe
  state only (`sent`, `pending_retry`, `suppressed`, `expired`,
  `already_released`, `not_pending`, `sending`).
- `POST .../activation-delivery/retry` — resends the **same** encrypted setup
  token after provider acceptance; never mints a new token or changes recipient.

Recruitment exposes this to authorized staff via
`recruitment_hire_wizard_retry_activation` (App UI only).

## Invalidation

- New OTP start for the same entry invalidates pending proofs.
- OTP resends for the same candidate are limited to one per 60 seconds.
- Verified proofs expire 30 days after confirmation.
- Email change on the candidate invalidates proofs whose `email` no longer
  matches (Recruitment should call `invalidate_application_mailbox_proofs`).

## Security

Follow the same patterns as `password_reset.py` and `email_verification.py`:
hashed OTP, rate limits, expiry, constant-time compare, no codes in logs.
