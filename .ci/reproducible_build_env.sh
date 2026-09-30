#!/usr/bin/env bash
# Use a stable source timestamp for wheel and sdist archives.
# Call after ROOT is set and before invoking a package build.
integral_set_source_date_epoch() {
  local repo_root="${1:?repository root is required}"

  if [[ -z "${SOURCE_DATE_EPOCH:-}" ]]; then
    SOURCE_DATE_EPOCH="$(git -C "$repo_root" show -s --format=%ct HEAD)"
  fi

  if [[ ! "$SOURCE_DATE_EPOCH" =~ ^[0-9]+$ ]]; then
    echo "invalid SOURCE_DATE_EPOCH: expected a Unix timestamp" >&2
    return 1
  fi

  export SOURCE_DATE_EPOCH
}
