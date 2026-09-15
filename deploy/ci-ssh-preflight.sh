#!/usr/bin/env bash
# Validate key.pem and SSH auth (same flags as gitlab-ci deploy).
# Usage: ci-ssh-preflight.sh <ssh_user> <server_ip> [key.pem]
set -euo pipefail

ssh_user="${1:?ssh_user required}"
server_ip="${2:?server_ip required}"
key_path="${3:-key.pem}"

# shellcheck source=deploy/ci-ssh-common.sh
source "$(dirname "$0")/ci-ssh-common.sh"

if [ ! -s "$key_path" ]; then
  echo "ci-ssh-preflight: missing key file: $key_path" >&2
  exit 1
fi

tr -d '\r' < "$key_path" > "${key_path}.tmp"
mv "${key_path}.tmp" "$key_path"
chmod 400 "$key_path"

echo "Validating private key..."
ssh-keygen -yf "$key_path" >/tmp/ci-derived.pub
echo "Fingerprint (must be in ~${ssh_user}/.ssh/authorized_keys on ${server_ip}):"
ssh-keygen -lf /tmp/ci-derived.pub
cat /tmp/ci-derived.pub

target="${ssh_user}@${server_ip}"
echo "SSH preflight: ssh -o StrictHostKeyChecking=no -o IdentitiesOnly=yes -i ${key_path} ${target}"

if ssh "${SSH_OPTS[@]}" -i "$key_path" "$target" "echo ci-ssh-preflight-ok"; then
  echo "SSH preflight succeeded."
  exit 0
fi

echo "ci-ssh-preflight: auth failed — verify public key on server for user ${ssh_user}" >&2
exit 1
