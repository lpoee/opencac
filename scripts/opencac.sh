#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m opencac.cli "$@"
