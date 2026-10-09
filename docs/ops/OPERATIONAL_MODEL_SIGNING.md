# Sign and verify App bundles

Python-shipping packages introduce executable behavior. Their trust tier and production signature policy must be reviewed before catalog admission.

The loader computes a deterministic fingerprint over sorted bundle files, excluding `signature.bin` and Python cache artifacts. Verification uses `INTEGRAL_OPERATIONAL_MODEL_PUBKEY` when configured. A failed verification excludes the package rather than quietly treating it as trusted.

Keep the private signing key outside the runtime distribution. Sign the final package contents after building, inspect the resulting fingerprint and signature, and verify with the public key in a clean environment. Changing a file after signing changes the artifact being authorized.

The implementation authority is `backend/app/services/operational_model_signature.py`; the loader reports `signature_verified`, `signature_reason`, and bundle identity. Use the available signing utility's `--help` rather than inventing arguments or copying an old host-specific command.

Test valid, modified, missing-signature, and wrong-key cases. Confirm catalog diagnostics identify rejection without printing secrets. Signatures establish artifact provenance under the chosen key; they do not replace permission checks, code review, or App qualification.
