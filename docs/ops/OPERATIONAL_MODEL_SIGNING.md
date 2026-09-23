# Operational Model Bundle Signing — Operator Runbook

## When to use

Bundles under `backend/app/packages/` that ship `skills/*/scripts/*.py`
run **in-process** with the integral backend. To prevent unauthorized
code from being loaded in production, set `INTEGRAL_OPERATIONAL_MODEL_PUBKEY` and
sign each bundle that ships python.

## Generate a keypair (one-time)

```bash
python -c "
from nacl.signing import SigningKey
from nacl.encoding import Base64Encoder
sk = SigningKey.generate()
print('private (KEEP SECRET):', sk.encode(Base64Encoder).decode())
print('public:               ', sk.verify_key.encode(Base64Encoder).decode())
"
```

Store the private key in your secrets manager. Set the public key on
all integral instances:

```bash
export INTEGRAL_OPERATIONAL_MODEL_PUBKEY=<base64-public-key>
```

## Sign a bundle

```python
# tools/sign_bundle.py
import sys
from pathlib import Path
from nacl.signing import SigningKey
from nacl.encoding import Base64Encoder
from app.services.operational_model_signature import compute_bundle_payload

bundle = Path(sys.argv[1])
sk_b64 = open(sys.argv[2]).read().strip()
sk = SigningKey(sk_b64.encode(), encoder=Base64Encoder)
payload = compute_bundle_payload(bundle)
(bundle / "signature.bin").write_bytes(sk.sign(payload).signature)
print("Signed:", bundle)
```

Run: `python tools/sign_bundle.py backend/app/packages/my-bundle /path/to/sk.b64`

## Verify

`POST /api/admin/packages` — every loaded bundle's row shows
`signature_verified: true` and `signature_reason: "valid"`.

A bundle that ships python and fails verification is skipped entirely
(loader logs `"signature verification failed (verify_failed) and ships
python — skipping"`).

## Rollout

1. Land code with `INTEGRAL_OPERATIONAL_MODEL_PUBKEY` unset (dev mode — all bundles load).
2. Sign every bundle in `backend/app/packages/` that ships python.
3. Set `INTEGRAL_OPERATIONAL_MODEL_PUBKEY` env var; restart the process.
4. Verify via `GET /api/admin/packages` that every bundle is `signature_verified: true`.

## Rotation

To rotate keys: generate a new keypair, re-sign every bundle, then
update `INTEGRAL_OPERATIONAL_MODEL_PUBKEY` and restart. Process restart is
required (env var is not runtime-mutable; see §11 of design spec).
