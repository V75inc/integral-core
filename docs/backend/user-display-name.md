# Local display name and chat identity

The saved `User.display_name` is the source for Settings, local records and the
resident chat's self-identity context. A host account's name is separate account
metadata. It must not replace the saved display name in the frontend `/auth/me`
result, including when a new installation still has the bootstrap name `Owner`.

Previously the host integration substituted the account name in the UI for
`Owner` or an empty local name. Settings then treated that substituted name as
already saved and disabled Save, while chat correctly read the unchanged local
profile. The UI now retains the API's local display name. Enter the chosen name
in Settings → Profile and save it; `/auth/update-profile` persists the change,
and subsequent chat turns read it through `user_identity_context`.

Core without a host already used the saved name. Hosts such as Integral One
must rebuild their composed UI from the corrected Core source; an existing
installed bundle does not change merely because the upstream source is fixed.
No central-account synchronization, installation-ID change or profile migration
is required. As an immediate workaround in an older bundle, edit the displayed
name to make Save available, save, then enter and save the intended local name.

Regression coverage:

- Host account name cannot mask `Owner`, blank or a chosen local display name.
- Saving and refreshing retains the chosen local profile name.
- Standalone Core continues to use its saved profile.
- Actual profile API writes/readback match the next chat identity context after
  repeated renames, through both graph and authentication principal IDs.
