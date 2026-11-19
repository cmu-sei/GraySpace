#!/bin/bash
# Refresh a GraySpace work tree from a source tree: sync-worktree.sh <source> <work>
#
# A work tree is the GraySpace tree that task, Terraform and Ansible run in for one deployment (the
# container's /data/work, or a control-plane deployment's <id>/work). The source's code is copied
# over it, so new code arrives, while everything below that the copy excludes (state, keys,
# generated files, the backend's own files) stays. Development-only parts of a checkout (git,
# dependencies, the GHOSTS clone, other deployments) aren't copied.
set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "usage: $0 <source> <work>" >&2
    exit 2
fi
SRC=${1%/}
WORK=${2%/}

mkdir -p "$WORK"
rsync -a --delete \
    --exclude '/.environment*' \
    --exclude '/environment' \
    --exclude '/.venv' \
    --exclude '/.ssh/' \
    --exclude '/terraform/terraform.tfstate*' \
    --exclude '/terraform/.terraform/' \
    --exclude '/terraform/.terraform.lock.hcl' \
    --exclude '/terraform/terraform.tfvars.json' \
    --exclude '/terraform/scenarios/' \
    --exclude '/terraform/.grayspace.lock' \
    --exclude '/terraform/grayspace-data/' \
    --exclude '/terraform/slice/instances/' \
    --exclude '/terraform/slice/terraform.tfstate.d/' \
    --exclude '/terraform/slice/.terraform/' \
    --exclude '/terraform/slice/.terraform.lock.hcl' \
    --exclude '/terraform/images/terraform.tfstate*' \
    --exclude '/terraform/images/.terraform/' \
    --exclude '/terraform/images/.terraform.lock.hcl' \
    --exclude '/terraform/player/terraform.tfstate*' \
    --exclude '/terraform/player/.terraform/' \
    --exclude '/terraform/player/.terraform.lock.hcl' \
    --exclude '/terraform/player/player.tfvars.json' \
    --exclude '/terraform/images/images.tfvars.json' \
    --exclude '/ansible/slice-inventory/' \
    --exclude '/ansible/inventory/*_inventory.ini' \
    --exclude '/ansible/ansible.log' \
    --exclude '/ansible/.cache/' \
    --exclude '/.git/' \
    --exclude '/.claude/' \
    --exclude '/.devcontainer/' \
    --exclude '/.github/' \
    --exclude '/.vscode/' \
    --exclude '/.task/' \
    --exclude '/.ansible/' \
    --exclude '/.ruff_cache/' \
    --exclude '/GHOSTS/' \
    --exclude '/deployments/' \
    --exclude '/aspire/' \
    --exclude '/packer/' \
    --exclude '/site/' \
    --exclude '/*.tar.gz' \
    --exclude '/terraform.tfstate*' \
    --exclude 'node_modules/' \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    "$SRC/" "$WORK/"
