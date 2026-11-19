#!/bin/bash
# Start GraySpace in its container: refresh the default deployment's working tree on /data from the
# image's code, then run nginx and the control plane, which runs one backend per deployment
# (docs/deployments.md).
#
# /data/work is the GraySpace tree that task, Terraform and Ansible run in for the deployment the
# site's environment file describes; the control plane adopts it as the default deployment. Other
# deployments, made through the API, live in /data/deployments/<id>/, each with its own work tree.
# The image's code is copied over every work tree on start, while everything the copy excludes
# (state, keys, generated files, the backend's own files) stays on the volume.
set -euo pipefail

SRC=/opt/grayspace
WORK=/data/work
ENV_FILE=/config/environment

mkdir -p "$WORK" /data/root-ssh /data/terraform-plugins /data/deployments
"$SRC/scripts/sync-worktree.sh" "$SRC" "$WORK"

# task runs .venv/bin/python and sources .environment, both from the tree's root.
ln -sfn /opt/venv-ops "$WORK/.venv"
if [ ! -f "$ENV_FILE" ]; then
    echo "grayspace: mount the site's environment file at $ENV_FILE (see scripts/environment.template)" >&2
    exit 1
fi
# Terraform also writes the default deployment's key to /root/.ssh; keep it on the volume with the rest.
if [ ! -L /root/.ssh ]; then rm -rf /root/.ssh; ln -s /data/root-ssh /root/.ssh; fi
export TF_PLUGIN_CACHE_DIR=/data/terraform-plugins

# The environment file is shell (export X=...), as task uses it.
set -a +u
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a -u

if [ -n "${TF_VAR_proxy:-}" ]; then
    export http_proxy="$TF_VAR_proxy" https_proxy="$TF_VAR_proxy" no_proxy="${TF_VAR_no_proxy:-}"
fi
# The tree's environment file is the site's, plus the Proxmox cluster's lock, which every
# deployment's jobs on the cluster take (control/registry.lock_file), task in this tree included.
host="${TF_VAR_proxmox_host_addr:-proxmox}"
mkdir -p /data/deployments/locks
lock="${GRAYSPACE_LOCK_FILE:-/data/deployments/locks/${host//[^A-Za-z0-9._-]/_}.lock}"
rm -f "$WORK/.environment"
(umask 077 && printf '# Written by the container entrypoint on every start (docs/deployments.md)\nsource %q\nexport GRAYSPACE_LOCK_FILE=%q\n' \
    "$ENV_FILE" "$lock" > "$WORK/.environment")
export GRAYSPACE_LOCK_FILE="$lock"

# A command (e.g. "task parse") runs in the prepared tree instead of the services.
if [ "$#" -gt 0 ]; then
    cd "$WORK" && exec "$@"
fi

# The token and allowed browser origins come from the environment file too (GRAYSPACE_API_TOKEN,
# GRAYSPACE_CORS_ORIGINS). Without a token the API refuses every protected route.
if [ -z "${GRAYSPACE_API_TOKEN:-}" ]; then
    echo "grayspace: GRAYSPACE_API_TOKEN is not set in $ENV_FILE; the API will refuse protected routes (503)" >&2
fi
# The control plane derives each deployment's backend settings from its environment file
# (control/worker_env.py); the site's file is the one every deployment's sources.
export GRAYSPACE_SOURCE_PATH="$SRC" GRAYSPACE_DEPLOYMENTS_PATH=/data/deployments GRAYSPACE_SITE_ENVIRONMENT="$ENV_FILE" \
    GRAYSPACE_ADOPT_PATH="$WORK" GRAYSPACE_OPS_VENV=/opt/venv-ops

nginx -g 'daemon off;' &
cd "$WORK/grayspace-web/backend"
/opt/venv-web/bin/python -m uvicorn control.main:app --host 127.0.0.1 --port 8000 &
# Whichever stops first stops the container, so the restart policy brings both back.
wait -n
exit 1
