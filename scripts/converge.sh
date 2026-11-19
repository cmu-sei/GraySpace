#!/bin/bash
# Apply the Terraform root module in the current directory until it converges.
#
# Converged means `terraform plan -detailed-exitcode` exits 0 (no changes), not that the apply
# printed "No changes.". Several applies may be needed because of Proxmox SDN dependencies and
# container startup timing.
#
# Usage: converge.sh MAX_ATTEMPTS [ARGS...]   ARGS are passed to the plan,
#        e.g. -var-file=instances/mb.tfvars.json
#
# Each attempt plans to a file and applies that file, so the state is refreshed once per attempt (by
# the plan), not by the apply and again by the check. A saved plan carries its variables, so the
# apply gets no ARGS. The file holds sensitive values in clear: mktemp makes it 0600, and it is
# removed on exit.
#
# The plan, which only reads, runs with -parallelism=$GRAYSPACE_PLAN_PARALLELISM (default 30). The
# apply runs with -parallelism=$GRAYSPACE_APPLY_PARALLELISM (default 10): creates contend for the
# cluster's storage lock, and too many at once time out. A deployment's environment file exports
# both from its Proxmox connection (Admin > Platform).
set -uo pipefail

max="${1:?Usage: converge.sh MAX_ATTEMPTS [ARGS...]}"
shift
apply_parallelism="${GRAYSPACE_APPLY_PARALLELISM:-10}"
plan_parallelism="${GRAYSPACE_PLAN_PARALLELISM:-30}"
planfile=$(mktemp -t grayspace-converge.XXXXXX) || exit 1
trap 'rm -f "$planfile"' EXIT

# One plan per attempt and one after the last apply
for i in $(seq 1 $((max + 1))); do
    rc=0
    terraform plan -detailed-exitcode -input=false "$@" -parallelism="$plan_parallelism" -out="$planfile" || rc=$?
    case "$rc" in
        0) echo "=== Converged after $((i - 1)) applies ==="; exit 0 ;;
        2) ;;
        *) echo "=== Plan before attempt $i failed (exit $rc) ===" ;;
    esac
    [ "$i" -gt "$max" ] && break
    if [ "$rc" -eq 2 ]; then
        echo "=== Apply attempt $i of $max ==="
        terraform apply -auto-approve -input=false -parallelism="$apply_parallelism" "$planfile" || echo "=== Apply attempt $i failed ==="
    fi
done
echo "=== Failed to converge after $max attempts ==="
exit 1
