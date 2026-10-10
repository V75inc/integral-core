#!/usr/bin/env bash
# Install Core and its dependencies from the ordinary PyPI registry.
set -euo pipefail
if [ "$#" -ne 2 ]; then
  echo "usage: install_core_wheel.sh <python> <wheel>" >&2
  exit 2
fi
uv pip install --python "$1" --index-url https://pypi.org/simple "$2"
