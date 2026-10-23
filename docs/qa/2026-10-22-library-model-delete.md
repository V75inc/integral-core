# Delete user-created App and Track library models

Manage Apps exposes **Delete model** directly beside workspace library packages, in addition to the existing detail action. Manage Tracks exposes the same action for workspace library models. Platform packages and models explicitly marked non-deletable have no delete control.

Both actions require confirmation. Deleting a Track model clears it from the starting-point selection and removes the library row; cancellation or server failure preserves both. Results and failures use toasts. Deletion uses the existing backend endpoint, which rechecks permissions and rejects models attached to a live App or Track by reference. Apps/Tracks created from copies remain unchanged; this is separate from uninstalling an App or deleting a Track.

Component coverage checks confirmation, cancellation, successful removal, selection reset, denied/attached-model failures, platform controls, and App detail/list actions. The existing backend delete-control tests cover permission rejection, reference-attachment protection and permission metadata.

Browser readback in the isolated preview verified the delete action for an uploaded App model and an uploaded Track model, opened both confirmations, then cancelled. Both models and the previously created Tracks remained. No original user data was deleted.

Validation passed: 349 frontend files / 2,112 tests; 37 focused dialog tests; four backend deletion tests (including the HTTP catalog lifecycle); TypeScript, production build, pre-commit framework and all 16 guards; `make verify-ci` with no backend `.env`. Targeted ESLint reported zero errors and ten effect-pattern warnings.
