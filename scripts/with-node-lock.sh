#!/bin/bash
# Run a command while holding the Proxmox node lock.
#
# task apply, task destroy, task configure and every slice job take the same flock, so only one
# of them changes the node at a time. SDN applies are cluster-wide: an apply from any Terraform
# state also applies the other states' pending SDN changes.
#
# With --guard apply|destroy, scripts/check_slice_attachments.py runs first, inside the lock, and
# the command runs only if no live slice instance would break.
#
# Usage: with-node-lock.sh [--guard apply|destroy] COMMAND [ARGS...]
set -euo pipefail

guard=""
if [ "${1:-}" = "--guard" ]; then
    guard="${2:?--guard needs apply or destroy}"
    shift 2
fi

lock="${GRAYSPACE_LOCK_FILE:-$(cd "$(dirname "$0")/.." && pwd)/terraform/.grayspace.lock}"
exec 9>"$lock"
if ! flock -n 9; then
    echo "=== Waiting for the node lock ($lock): another apply, destroy, configure or slice job holds it ==="
    flock 9
fi
if [ -n "$guard" ]; then
    python3 "$(dirname "$0")/check_slice_attachments.py" "$guard"
fi
"$@"
