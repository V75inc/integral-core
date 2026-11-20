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
capability to that exact Entry ID. Core's generic public entry read, comment,
reaction, attachment, and update routes enforce the contract for Entries marked with
`PUBLIC_SUBMISSION_CAPABILITY_REQUIRED_FIELD`. The marker is an opt-in guard,
not the secret or the ownership check. The Core routes still apply the share
link's normal admission and permission checks.

Public lists, search totals and relation-option collections omit protected
submissions, even when a capability header is supplied. Owners read their own
submission through the capability-gated detail endpoint. The apply-context
snapshot also requires the entry's capability when the entry is protected.
Binding uses a conditional database claim: concurrent workers cannot bind the
same bearer to different Entries, and revocation cannot restore a stale binding.

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

## Public create-form presentation

Anonymous create forms group the published schema fields under their declared
labels, with a neutral Details group for ungrouped fields. Questions remain
visible above their controls. This does not change assigned-form review,
prefilled fields, public permissions, upload restrictions or the existing
sensitive-field exclusions. Those exclusions remain pending a separately
qualified host-policy migration.

An EntryType may declare optional `confirmation` metadata with plain-text
`title` (maximum 160 characters), `message` (maximum 2000 characters), and boolean
`allow_another_response`. Omitted values retain the existing confirmation copy
and repeat-response button. Only `false` hides the anonymous repeat button;
it is presentation, not duplicate prevention. Assigned response editing remains
available under its existing rules. Metadata persists through compile, merge,
authoring and graph manifest round trips.


### Fixed entry type on a create-only link

Public share settings accept optional `public_entry_type_id` for a single
persisted EntryType on the shared Track. A bound link requires explicitly
`read_entries: false`, `create_entries: true` and all other permissions false.
Public config exposes only that schema, the form omits the type switcher, and
server create/relation requests enforce the binding. Missing, foreign, template
or authenticated-only types are refused rather than exposing alternatives.

An active token's binding is immutable. Omitted settings preserve its binding;
changing, adding or clearing it requires disabling the link and minting a new
one. Legacy shares with no binding otherwise retain their existing behavior.
Disabling a link remains the normal recoverable way to stop intake. Binding is
not approval of any application, duplicate detection, or a `read_entries` grant.
Existing relation fields may still expose their configured option labels through
the relation-options endpoint. A zero-record-disclosure intake must contain no
relation fields; otherwise its author must verify the options are public-safe.
