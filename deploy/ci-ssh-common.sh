#!/usr/bin/env bash
# OpenSSH options matching gitlab-ci deploy (key.pem + StrictHostKeyChecking=no).
SSH_OPTS=(
  -o StrictHostKeyChecking=no
  -o IdentitiesOnly=yes
)
