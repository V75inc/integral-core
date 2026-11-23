#!/bin/bash
# Apple Silicon/macOS developer fixture. Exact approval for temporary hosts and
# login-keychain trust is required before use. No passwords or warnings bypassed.
set -euo pipefail
umask 077

host=integral-webchat.test
wrong=integral-webchat-wrong.test
port=58173
pg_port=55523
window=900
root="$(cd "$(dirname "$0")/../.." && pwd)"
runner="$root/backend/scripts/run_disposable_web_chat_tls.sh"
py="${PREVIEW_PYTHON:-$root/backend/.venv/bin/python}"
[[ "$(uname -s)" == Darwin && -x "$py" && -f "$runner" ]] || exit 2

check_no_host() {
  /usr/bin/awk -v host="$host" '
    { sub(/#.*/, ""); for (i=2;i<=NF;i++) if (tolower($i)==host) found=1 }
    END {exit found ? 1 : 0}
  ' /etc/hosts
}
check_no_host || { echo "Refusing to replace an existing hosts entry" >&2; exit 2; }
if /usr/bin/dscacheutil -q host -a name "$host" | /usr/bin/grep -q ip_address:; then
  echo "Refusing to replace existing name resolution" >&2
  exit 2
fi
if /usr/bin/security find-certificate -c "$host" >/dev/null 2>&1; then
  echo "Refusing to alter existing certificate/trust for the fixture hostname" >&2
  exit 2
fi
keychain="$(/usr/bin/security default-keychain -d user | /usr/bin/sed -E 's/^[[:space:]]*"([^"]+)"[[:space:]]*$/\1/')"
[[ "$keychain" == "$HOME/Library/Keychains/login.keychain-db" && -f "$keychain" ]] || { echo "Login keychain not identifiable" >&2; exit 2; }
if /usr/bin/security find-certificate -c "$host" "$keychain" >/dev/null 2>&1; then
  echo "Refusing to replace an existing login-keychain certificate" >&2
  exit 2
fi
if /usr/sbin/lsof -nP -iTCP:"$port" -iTCP:"$pg_port" -sTCP:LISTEN >/dev/null; then
  echo "A fixture port is already in use" >&2
  exit 2
fi
if ! /usr/bin/sudo -n -v; then
  echo "Cached macOS admin authorization unavailable. Ask the owner to authorize the hosts operation interactively, then rerun." >&2
  exit 2
fi

tmp="$(mktemp -d /tmp/integral-safari-preview.XXXXXX)"
cert="$tmp/cert.pem"
key="$tmp/key.pem"
trust_attempted=0
mapping_pid=
pipe_open=0
tag=
fp=
cleanup() {
  local original=$? failed=0
  trap - EXIT
  trap '' INT TERM HUP
  # Closing our sole pipe writer triggers privileged targeted cleanup even
  # if the app stopped early or the cached sudo authorization expired.
  if [[ "$pipe_open" == 1 ]]; then exec 8>&-; fi
  if [[ -n "$mapping_pid" ]] && ! wait "$mapping_pid"; then failed=1; fi
  check_no_host || failed=1
  if [[ "$trust_attempted" == 1 ]]; then
    # Only the exact fresh fixture certificate (no search by display name).
    if /usr/bin/security find-certificate -a -c "$host" -Z "$keychain" \
        | /usr/bin/grep -qi "$fp"; then
      /usr/bin/security delete-certificate -Z "$fp" -t "$keychain" || failed=1
    fi
    if /usr/bin/security verify-cert -L -p ssl -n "$host" -c "$cert" >/dev/null 2>&1; then
      failed=1
    fi
    if /usr/bin/security find-certificate -a -c "$host" -Z "$keychain" \
        | /usr/bin/grep -qi "$fp"; then
      failed=1
    fi
  fi
  if /usr/sbin/lsof -nP -iTCP:"$port" -iTCP:"$pg_port" -sTCP:LISTEN >/dev/null; then failed=1; fi
  if [[ "$failed" == 0 ]]; then
    rm -rf -- "$tmp"
    printf 'Fixture listeners stopped; exact hosts line and temporary certificate trust removed.\n'
    exit "$original"
  fi
  # Never retain a fixture private key. Preserve only public recovery evidence.
  rm -f -- "$key"
  printf 'CHECK CLEANUP: fixture host %s, tag %s, certificate SHA-256 %s, public cert %s\n' "$host" "$tag" "$fp" "$cert" >&2
  exit 1
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 1 \
  -subj "/CN=$host" -addext "subjectAltName=DNS:$host" \
  -addext 'basicConstraints=critical,CA:FALSE' \
  -addext 'keyUsage=critical,digitalSignature,keyEncipherment' \
  -addext 'extendedKeyUsage=serverAuth' \
  -keyout "$key" -out "$cert" >/dev/null 2>&1
fp="$(openssl x509 -in "$cert" -noout -sha256 -fingerprint | /usr/bin/awk -F= '{gsub(/:/,"",$2); print $2}')"
[[ "$fp" =~ ^[[:xdigit:]]{64}$ ]] || exit 1
if /usr/bin/security verify-cert -L -p ssl -n "$host" -c "$cert" >/dev/null 2>&1; then
  echo "Fresh fixture leaf unexpectedly trusted before setup" >&2
  exit 1
fi
trust_attempted=1
/usr/bin/security add-trusted-cert -r trustRoot -p ssl -s "$host" -k "$keychain" "$cert"
/usr/bin/security verify-cert -L -p ssl -n "$host" -c "$cert" >/dev/null
if /usr/bin/security verify-cert -L -p ssl -n "$wrong" -c "$cert" >/dev/null 2>&1; then
  echo "Unexpected trust for another hostname; refusing to launch" >&2
  exit 1
fi

tag="$(openssl rand -hex 8)"
/usr/bin/mkfifo "$tmp/hosts-lifetime"
exec 8<>"$tmp/hosts-lifetime"
pipe_open=1
# The sole elevated code path touches /etc/hosts only. Open a separate read end
# and close the inherited writer. The timeout also bounds orphaned supervisors.
/usr/bin/sudo -n /usr/bin/env -i /bin/bash --noprofile --norc -c '
set -euo pipefail
tag=$1
[[ "$tag" =~ ^[0-9a-f]{16}$ ]] || exit 2
line="127.0.0.1 integral-webchat.test # integral-webchat-demo-$tag"
line_re="^127[.]0[.]0[.]1 integral-webchat[.]test # integral-webchat-demo-$tag$"
if ! /usr/bin/awk '"'"'
  { sub(/#.*/, ""); for (i=2;i<=NF;i++) if (tolower($i)=="integral-webchat.test") found=1 }
  END {exit found ? 1 : 0}
'"'"' /etc/hosts; then exit 2; fi
undo() {
  local result=$?
  trap - EXIT
  trap "" INT TERM HUP
  if /usr/bin/grep -Fxq "$line" /etc/hosts; then
    /usr/bin/sed -i "" "/$line_re/d" /etc/hosts || exit 1
  fi
  if /usr/bin/grep -Fxq "$line" /etc/hosts; then exit 1; fi
  exit "$result"
}
trap undo EXIT
trap "exit 130" INT TERM HUP
# Refuse a hand-edited file without a final newline rather than leave a
# separator behind on rollback. No other lines are intentionally modified.
[[ -s /etc/hosts && "$(/usr/bin/tail -c 1 /etc/hosts | /usr/bin/od -An -tx1 | /usr/bin/tr -d "[:space:]")" == 0a ]] || exit 2
printf "%s\n" "$line" >> /etc/hosts
IFS= read -r -t 900 _ || :
' integral-webchat-hosts "$tag" <"$tmp/hosts-lifetime" 8>&- &
mapping_pid=$!
mapping_start=$SECONDS
for ((attempt=0; attempt<25; attempt++)); do
  if /usr/bin/grep -Fxq "127.0.0.1 $host # integral-webchat-demo-$tag" /etc/hosts; then break; fi
  sleep 0.2
done
/usr/bin/grep -Fxq "127.0.0.1 $host # integral-webchat-demo-$tag" /etc/hosts
resolved="$(/usr/bin/dscacheutil -q host -a name "$host" | /usr/bin/awk '/ip_address:/{print $2}')"
[[ "$resolved" == 127.0.0.1 ]] || { echo "Fixture hostname does not resolve only to loopback" >&2; exit 1; }
# Reserve time for artifact build/setup and teardown inside the mapping window.
duration=$((window - (SECONDS - mapping_start) - 45))
(( duration >= 60 )) || exit 1
# Bound the runner including build and fixture setup. Signal just its new
# process group, never any existing Integral process, and allow its PG trap
# to finish. If it cannot clean up, the parent reports the open ports.
watchdog=$((window - (SECONDS - mapping_start) - 10))
printf 'Run the printed fixture URL in Safari only; stop on any browser warning.\n'
PREVIEW_HOST="$host" PREVIEW_PORT="$port" PREVIEW_PG_PORT="$pg_port" \
  PREVIEW_SECONDS="$duration" PREVIEW_PYTHON="$py" \
  PREVIEW_CERT="$cert" PREVIEW_KEY="$key" "$py" - "$runner" "$watchdog" 8>&- <<'PY_RUNNER'
import os
import signal
import subprocess
import sys

child = subprocess.Popen(["/bin/bash", sys.argv[1]], start_new_session=True)

def interrupted(_signum, _frame):
    raise KeyboardInterrupt

for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(sig, interrupted)
try:
    sys.exit(child.wait(timeout=int(sys.argv[2])))
except (KeyboardInterrupt, subprocess.TimeoutExpired):
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    try:
        os.killpg(child.pid, signal.SIGTERM)
        child.wait(timeout=8)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        print("Runner did not stop after its bounded grace period", file=sys.stderr)
    sys.exit(1)
PY_RUNNER
