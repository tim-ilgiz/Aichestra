#!/usr/bin/env bash
# Thin macOS bootstrap wrapper — portable logic lives in Python.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m aichestra bootstrap "$@"
