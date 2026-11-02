# Public submission capabilities

Public share links admit a submission to a Track; they do not prove ownership
of an individual applicant's submission. Host applications that need anonymous
follow-up actions must use Core's `app.services.public_submission_capability`
contract.

The host issues a capability on the server after validating the public form.
Core stores only a hash of the random bearer secret and binds it to the
ShareLink, workspace, Track, form `EntryType`, and stable submission ID. The
secret is returned once to the submitting browser and sent on later requests as
`X-Entry-Capability`; it must not be placed in a URL or logs. The default and
maximum lifetime is 24 hours.

After the OTP succeeds and the host creates the Entry, it binds the same
capability to that exact Entry ID. Core's generic public attachment and update
routes enforce the contract for Entries marked with
`PUBLIC_SUBMISSION_CAPABILITY_REQUIRED_FIELD`. The marker is an opt-in guard,
not the secret or the ownership check. The Core routes still apply the share
link's normal admission and permission checks.

Host-side flow:

1. Call `issue_public_submission_capability(...)` with the validated workspace,
   Track, form ID, ShareLink ID, and pending submission ID. Return the plaintext
   `capability` once and store only `capability_id` on the pending host record.
2. Before verify or resend, call
   `validate_public_submission_capability(...)` with the same scope and
   submission ID. Validate before touching OTP state or queuing mail.
3. After OTP confirmation and Entry creation, call
   `bind_public_submission_capability(...)`, then set
   `PUBLIC_SUBMISSION_CAPABILITY_REQUIRED_FIELD = True` on the Entry.
4. For host-specific attachment routes, validate again with the same scope and
   the Entry ID. Call `revoke_public_submission_capability(...)` when the
   pending submission is deleted or abandoned. Expiration is enforced by Core
   even if a cleanup task is delayed.

`validate_public_entry_capability(...)` is the bound-Entry variant for generic
Core routes that know the Entry and its current Track/form but do not own the
host's pending-record ID. All validation failures should be reported as a
generic not-found response so a shared form token cannot enumerate protected
submissions.
