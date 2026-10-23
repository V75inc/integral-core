# Workspace invitation after App access removal

An HTTP regression reproduces the complete sequence with a separate registered
recipient: accept an App invitation as commenter, receive guest workspace access,
remove the App collaborator, issue a new workspace member invitation, then list
and accept that new invitation. With a verified mailbox, the new invitation is
independent of the accepted App invitation and grants member access successfully.
The removed App grant stays absent.

The same sequence with an unverified account reproduces the reported unavailable
message: email-token acceptance can grant the initial App access, while the
authenticated pending-invitation list requires verified current mailbox proof.
The recipient page now explains this requirement and offers verification instead
of treating an empty protected list as a handled invitation. Successful
verification returns to the exact invitation and still requires explicit Accept.
No invitation state, mailbox-proof or grant-authority checks were relaxed.

Both HTTP scenarios pass against both the JSON store and an isolated PostgreSQL
test database. Seven component tests cover the complete verification
return flow, exact new invitation selection, genuinely unavailable invitations
and rejection of unrelated/external return destinations.
