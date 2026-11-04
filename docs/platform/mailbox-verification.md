# Mailbox verification proofs

Core provides a reusable OTP and proof lifecycle for Apps and platform flows
that need to verify access to an email address. Core owns the challenge, stores
only its hash, and issues a server-side proof. A caller must provide the
purpose; Core never assumes which App or workflow owns the subject record.

## Proof contract

`MailboxVerificationProof` (I-GRAPH-02 `Object`) binds:

- `subject_entry_id`, `workspace_id`, and normalized email
- caller-supplied `purpose`
- pending OTP hash, expiry, and attempt count
- verification and proof expiry timestamps
- invalidation and one-time consumption state

New records use `subject_entry_id`. The persisted Object entity identity stays
stable for already-issued proof records; Core reads the former subject field
when loading those records and migrates it when the proof is next used.

## Service lifecycle

1. `start_mailbox_verification` requires a subject, workspace, email, and
   non-empty purpose. It invalidates the prior proof for that scoped purpose,
   persists a hashed OTP, and returns the plaintext code for immediate email
   delivery only.
2. `confirm_mailbox_verification` checks the same subject, workspace, purpose,
   and proof ID. It enforces expiry and attempt limits, then clears OTP material
   and issues a proof that expires after 30 days.
3. `rebind_mailbox_verification_proof` lets a trusted server move the proof
   from a temporary submission record to its final record while preserving the
   workspace, email, purpose, and verified state.
4. `consume_mailbox_verification_proof` checks every bound value and consumes
   the proof once for the newly created account.

Caller-selected purposes can distinguish independent flows using the same
subject. A proof issued for one purpose cannot confirm or authorize another.
The service applies a per-process 60-second resend cooldown for the same
subject, workspace and purpose. Public hosts running multiple workers must
also enforce shared endpoint rate limits before invoking the service.

OTP attempt reservations, verification, rebinding and proof consumption use
conditional database transitions. Concurrent workers cannot reuse a proof or
overwrite a later invalidation/consumption with a stale record.

## Account activation delivery

`POST /workspaces/{id}/members/provision` accepts optional:

- `personal_email` — the alternate destination stored on `User.personal_email`
- `mailbox_verification_proof_id`
- `mailbox_subject_entry_id`
- `mailbox_verification_purpose`

When all proof fields match a valid, unused proof, Core creates the account
with its primary email as the login address, consumes the proof, and sends a
single-use activation link to the verified alternate address. Without a valid
proof, alternate delivery uses the existing credential verification flow.

Workspace admins can inspect activation delivery state with:

- `GET /workspaces/{id}/members/{member_user_id}/activation-delivery`
- `POST /workspaces/{id}/members/{member_user_id}/activation-delivery/retry`

Retry reuses the same encrypted, single-use setup token. It does not create
another account or change the recipient. Provider suppressions remain visible
for an administrator to resolve before retrying.
Status and retry responses are scoped to the workspace that provisioned the
account. Membership in another workspace does not disclose the alternate inbox,
delivery intent or provider identifier. Historical release records without a
workspace binding are not disclosed through workspace-scoped status endpoints.

## Security

Never accept browser-supplied `verified: true` values or App-owned timestamps
as proof. Use Core-issued proof IDs only in trusted server-side calls. Proofs
are bound to a workspace, subject, email, and purpose; they expire, can be
invalidated, and can be consumed once. OTPs use constant-time hash comparison,
rate limits, and are never logged.
