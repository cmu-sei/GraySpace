#!/bin/bash
# Create or update grayspace-web/backend/.venv with the backend's requirements.
#
# pydantic==2.9.0 has no build for Python 3.14, so this picks the newest Python from 3.13 down to
# 3.10 (override with PYTHON=...). It reinstalls whenever requirements*.txt change, and does nothing
# otherwise, so task backend:venv and the Aspire AppHost can run it on every start.
set -euo pipefail

backend="$(cd "$(dirname "$0")/../grayspace-web/backend" && pwd)"
venv="$backend/.venv"
stamp="$venv/.requirements.sha256"

python="${PYTHON:-}"
if [ -z "$python" ]; then
    for candidate in python3.13 python3.12 python3.11 python3.10; do
        if command -v "$candidate" > /dev/null; then
            python="$(command -v "$candidate")"
            break
        fi
    done
fi
if [ -z "$python" ]; then
    echo "No Python 3.10-3.13 found; set PYTHON=/path/to/python3.x" >&2
    exit 1
fi

if [ ! -x "$venv/bin/python" ]; then
    echo "Creating $venv with $python"
    if ! "$python" -m venv "$venv" 2> /dev/null; then
        # Debian's python3.x without python3.x-venv has no ensurepip
        rm -rf "$venv"
        "$python" -m venv --without-pip "$venv"
        curl -sSf https://bootstrap.pypa.io/get-pip.py | "$venv/bin/python" - -q
    fi
fi

wanted="$(cat "$backend"/requirements*.txt | sha256sum | cut -d' ' -f1)"
if [ "$(cat "$stamp" 2> /dev/null)" != "$wanted" ]; then
    echo "Installing $backend/requirements-dev.txt"
    "$venv/bin/pip" install -q -r "$backend/requirements-dev.txt"
    echo "$wanted" > "$stamp"
fi
echo "Backend venv ready: $("$venv/bin/python" --version) at $venv"
