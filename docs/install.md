# GraySpace Installation

If you are installing GraySpace on your own Proxmox VE node/cluster, or if you are interested in contributing to the project, follow the documentation on this page.

## Development Container

This repo includes code to set up a development environment with a [Development Container](https://containers.dev/). This is the quickest way to get started. See the [Manual Installation](#manual-installation) section if you are not using the devcontainer.

To use this development environment, you'll need to run Docker on your machine or connect to a remote Docker host. [Docker Desktop](https://www.docker.com/) is a great way to get started if you're not confident administering Docker from the command line.

The devcontainer installs required packages and initializes Terraform.

Minimum requirements on local machine for the devcontainer:

- Docker
- Docker Compose version 2.24.0

Many tools and services [support the devcontainer specification](https://containers.dev/supporting). Our suggestion is to use Visual Studio Code. When opening this repo in VSCode, you will see an option to "Reopen in container". Clicking this will build the containers and run the initial configuration. Please wait until the container is finished building before using it. The last step you will see is the initialization of Terraform.

Once the dev container finishes its setup, move to the [Environment Setup](#environment-setup) section.

### Optional configuration

#### Proxy

If your docker host is behind a proxy, copy `.devcontainer/proxy_template.env` to `.devcontainer/proxy.env` and fill in the relevant values. Empty values will be ignored.

#### Claude Code

The development container integrates Claude Code via AWS Bedrock to support development. Expand this section to learn how to set up Claude Code to work with this project.

<details><summary>Claude Code</summary>
1. <strong>Generate AWS Long-Term Access Key</strong><br><br>

Access to Amazon Bedrock is required for Claude Code to work inside the dev container, and models are available for either commercial or CUI workloads (through AWS GovCloud).<br><br>

<a href="https://docs.aws.amazon.com/sdkref/latest/guide/access-iam-users.html">Generating an AWS Access Key</a><br><br>

2. <strong>Setup Dev Container with AWS Credentials</strong><br><br>

Run the script <code>scripts/setup_claude.sh</code> from the root of the repo.<br><br>

The script will prompt you to:<br>

- Select your AWS region (Commercial or GovCloud)<br>
- Enter your AWS Access Key ID<br>
- Enter your AWS Secret Access Key<br><br>

3. <strong>Build and Open the Dev Container</strong><br><br>

In VS Code:<br>

- Open the command palette (F1 or Cmd/Ctrl+Shift+P)<br>
- Select: Dev Containers: Reopen in Container(or Rebuild Container if you've already created it)<br>
- Wait for the container to build and start (this may take a few minutes on first run)<br><br>

4. <strong>Start Using Claude Code</strong><br><br>

Once the container is running, start Claude Code from the terminal:<br><br>
<code>claude</code>

</details>

## Manual installation

If proceeding without the devcontainer, you will need to install and configure several prerequisites.

### Prerequisites

- Install required tools:
    - Python: <https://www.python.org/downloads/>
    - Terraform: <https://developer.hashicorp.com/terraform/install>
    - Ansible: <https://docs.ansible.com/ansible/latest/installation_guide/intro_installation.html>
    - sshpass: <https://linux.die.net/man/1/sshpass>

- Copy environment template:

    ```bash
    cp scripts/environment.template environment
    ```

- Fill in values in `environment` select either PROXMOX_VE_USERNAME and PROXMOX_VE_PASSWORD or PROXMOX_VE_API_TOKEN, then load them:

    ```bash
    source environment
    ```

- Create and activate a Python virtual environment, then install dependencies:
    ```bash
    python3 -m venv .env && source .env/bin/activate && pip install -r requirements.txt
    ```
    Only needs to be run once or whenever `requirements.txt` changes.

## Quickstart with Taskfile

GraySpace uses [Taskfile](https://taskfile.dev/) to wrap the common operations (parsing the graph, running Terraform, and configuring with Ansible) into a handful of commands. Taskfile is preinstalled in the devcontainer. This is the recommended way to deploy — the individual sections below explain what each task does under the hood.

Run `task --list` to see all available tasks. A typical first deployment is:

```bash
task env      # Interactively create .environment from the template (first run only)
task parse    # Generate terraform.tfvars.json from the topology CSVs
task plan     # Preview the infrastructure changes Terraform will make
task deploy   # Apply the infrastructure, then configure it with Ansible
```

Useful options and follow-up tasks:

```bash
task parse SCOPE=tier1   # Parse only Tier 1 ISPs (default is full)
task apply               # Apply Terraform only (auto-retries until converged)
task configure           # Re-run Ansible configuration only
task destroy             # Tear down all Terraform-managed resources
task tunnel              # Start an sshuttle tunnel to the Proxmox network
task lint                # Lint Ansible, Python, Terraform, YAML, and shell scripts
```

!!! note
    `task apply` automatically retries (up to `MAX_RETRIES`, default 6) because multiple Terraform runs may be needed due to Proxmox SDN dependencies and container startup timing. Override with `task apply MAX_RETRIES=3`.

The tasks source your `.environment` file automatically, so you do not need to `source` it manually when using Taskfile.

## Environment setup

You will need to do several things depending on your infrastructure and which parts of the model you want to deploy.

### Prepare Proxmox

- Routers, core services and slice edges are made from the GraySpace Alpine image (`packer/alpine-lxc/`), which has everything Ansible needs, and Prometheus from the GraySpace Ubuntu LXC image (`packer/ubuntu-lxc/`; `TF_VAR_ubuntu_lxc_image_url` overrides its URL), fetched the same way. The node downloads it from the `grayspace-alpine` URL in `images/images.csv`, a GitHub release, through the cluster's proxy (`TF_VAR_alpine_image_transfer=download`, the default), so that proxy (`datacenter.cfg` `http_proxy`) must allow `github.com` and `release-assets.githubusercontent.com`, where release downloads redirect. With `upload`, Terraform fetches it on its own machine, directly (the Proxmox provider ignores proxy settings), and uploads it to the node over the API: for a server only that machine reaches. Switching modes fetches each image once more and replaces no guest. To serve it from elsewhere, publish a build (`task image:build`) and set `TF_VAR_alpine_image_url`.
- Enclaves' desktops are made from the GraySpace desktop image (`packer/desktop-lxc/`, `task image:build:desktop`), the `grayspace-desktop` row of `images/images.csv`, their Ubuntu container hosts from the GraySpace Ubuntu LXC image (`packer/ubuntu-lxc/`, `task image:build:ubuntu-lxc`), the `grayspace-ubuntu-lxc` row, and their VM hosts (the `ad-dc` role's) from the GraySpace Ubuntu VM image (`packer/ubuntu-vm/`, `task image:build:ubuntu-vm`), the `grayspace-ubuntu-vm` row. GraySpace's own images follow `TF_VAR_alpine_image_transfer` too: with `upload`, Terraform fetches them and uploads them to the node; other images are always downloaded by the node.
- Enclave VMs get their cloud-init vendor data as Proxmox [snippets](https://pve.proxmox.com/pve-docs/chapter-pvesm.html#_common_storage_properties), which the provider uploads over SSH as a user who can write the snippets directory (usually root).

### Define deployment scope

Terraform and Ansible depend on variables generated from a base version's topology CSVs under `bases/` (see the [graph data model](graph_model.md)). This allows for multiple deployment scopes, depending on which part of the topology is included.

Supported scopes:

- `full` — Deploys the entire internet model (Tier1 + Tier2 ISPs)
- `tier1` — Deploys only Tier1 ISP routers and links between Tier1 ISPs

Generate `terraform.tfvars.json`:

```bash
./scripts/generate_tfvars.py --scope=[scope]
```

### Define environment variables

Make a copy of the environment template

`cp scripts/environment.template .environment`

Edit the file and fill in the values for your environment. Then run:

`source .environment`

Two settings concern the web application and GHOSTS:

- `GRAYSPACE_API_TOKEN`: the bearer token of the Terrain API and the web application's changes.
  `task env` turns the value `generate` into a random token. Give it to GraySpace's clients:
  GHOSTS takes it as `TerrainProvider__Token` (`TERRAIN_TOKEN` under Docker Compose), next to
  the deployment's address in `TerrainProvider__BaseUrl` (`TERRAIN_BASE_URL`): GraySpace's address
  and `/deployments/<id>`, without `/api/v1` (see [Deployments](deployments.md) and GHOSTS's
  `docs/core/api/terrain.md`). People don't need it: they sign in to the web application with their own accounts.
- `GRAYSPACE_BOOTSTRAP_ADMIN`: the first account of the web application (an admin), made on first
  start. Its one-time password is in the server's log (`docker compose logs grayspace` in the
  container); change it at first sign-in. People sign in with their own accounts, which an admin
  creates (see [Deployments](deployments.md#sign-in)).

The base has no GHOSTS VM: GHOSTS runs outside the simulated internet, and enclave hosts run its
clients (the `ghosts-client` role), which report to it over the management network.

`TF_VAR_environment_id` must be at most 4 characters: slice vnet IDs append 4 to it, and Proxmox
allows 8.

### Deploy resources

Create LXCs with Terraform:

`cd terraform`

`terraform init` (if you're using the devcontainer, skip this as it is done automatically)

`terraform apply -parallelism 7` until `terraform plan -detailed-exitcode` exits 0 (no changes). `task apply` does this for you, holding the node lock, and refuses changes that would break live [slice](slices.md) instances.

### Configure resources

Run Ansible to configure routers and services:

```bash
cd ../ansible
ansible-playbook main.yml
```

## Run GraySpace in a container

GraySpace's API, web UI and the tools it runs (Terraform, Ansible, `task`) run as one container on a Docker host. This is the way to run the web UI: it signs in through the control plane ([Deployments](deployments.md#sign-in)), which runs here. Slice jobs run where the API runs, so this is also the layout in which GHOSTS can create instances through the API.

### Docker host requirements

Choosing the Docker host is up to you, and it has to be able to reach:

- the Proxmox API (port 8006) of every node it deploys to;
- the Proxmox host over SSH (the provider uploads the VMs' cloud-init snippets);
- the management network (`TF_VAR_proxmox_mgmt_bridge`), for Ansible, consoles and Prometheus.

### Start it

From a checkout of this repository on the Docker host, with your environment file (see [Define environment variables](#define-environment-variables)) at `.environment` in the checkout or named by `GRAYSPACE_ENV_FILE`:

```bash
docker compose -f container/compose.yaml up -d --build
```

The UI is on port 8080 (`GRAYSPACE_PORT` changes it), with the API under `/api/v1`. The container runs several deployments ([Deployments](deployments.md)): the one the environment file describes is the default, at `/data/work`, and each deployment's API is under `/deployments/<id>/api/v1`. Set `TF_VAR_grayspace_url` to the container's address. The environment file also sets `GRAYSPACE_API_TOKEN`, without which the API refuses protected routes, and, if the GHOSTS frontend calls the API from a browser, `GRAYSPACE_CORS_ORIGINS`. On first start its Proxmox settings (and Crucible settings, with `TF_VAR_configure_crucible=true`) become the web app's first connections, named `site`; from then on admins manage connections in Admin > Platform, and new deployments choose one ([Platform connections](deployments.md#platform-connections)).

Everything that has to survive an upgrade lives on the `grayspace-data` volume, mounted at `/data`: Terraform state, the SSH keys Terraform generates, the generated tfvars and inventory, the base presets (`/data/deployments/presets`), the platform's connections (`/data/deployments/platform.json`), the slice and image Terraform state and inputs (`terraform/slice/`, `terraform/images/`, `ansible/slice-inventory/`), and the API's slices, instances and jobs (`terraform/grayspace-data/`). Deployments made through the API have the same in `/data/deployments/<id>/work`. On every start the container copies the image's code over `/data/work` and leaves those alone, so upgrading is `git pull` and the same `up -d --build`. Terraform state includes the router password and the SSH keys, so back the volume up and restrict who can read it.

### Operate it

Everything can go through the API, documented at `/api/docs` on the container's port (Swagger; Authorize with `GRAYSPACE_API_TOKEN`) and in [Terrain API](api.md), from nothing deployed to a base and slices: `GET /api/v1/base/preflight` says whether the site is ready, `POST /api/v1/base/deploy {"version": "grayspace/2026.11", "scope": "tier1"}` deploys it as a job, and `DELETE /api/v1/base` tears it down. Or run the usual tasks inside the container:

```bash
docker compose -f container/compose.yaml exec grayspace task parse
docker compose -f container/compose.yaml exec grayspace task apply
docker compose -f container/compose.yaml exec grayspace task configure
docker compose -f container/compose.yaml exec grayspace task slice:up SLICE=examples/slices/information-environment.yaml
# Another deployment's tree
docker compose -f container/compose.yaml exec -w /data/deployments/abd/work grayspace task plan
```

### Test the image in the devcontainer

The devcontainer has Docker-in-Docker. Nested containers there can't reach a DNS server on their own network, so add the test-only override, which builds and runs on the devcontainer's network (the UI is then on port 80 of the devcontainer):

```bash
COMPOSE_BAKE=false docker compose -f container/compose.yaml -f container/compose.dind.yaml up -d --build
```

`COMPOSE_BAKE=false` uses Compose's classic builder, which doesn't need BuildKit's `network.host` grant.

### Move an existing deployment into the container

If the deployment was created from a devcontainer, copy its state into the container once, so the container manages that deployment instead of creating a new one. From the old checkout's root, with the container running:

```bash
c="docker compose -f container/compose.yaml"
$c exec grayspace mkdir -p /data/work/.ssh /data/work/terraform/scenarios
$c cp terraform/terraform.tfstate grayspace:/data/work/terraform/
$c cp terraform/terraform.tfvars.json grayspace:/data/work/terraform/
$c cp .ssh/terraform_key grayspace:/data/work/.ssh/
$c cp .ssh/terraform_key.pub grayspace:/data/work/.ssh/
for f in ansible/inventory/*_inventory.ini; do $c cp "$f" grayspace:/data/work/ansible/inventory/; done
# Runtime changes tracked before presets; the backend moves them into terraform/grayspace-data/
[ -d terraform/scenarios ] && $c cp terraform/scenarios/. grayspace:/data/work/terraform/scenarios/
# Slice instances, if any: their state, inputs and records
for d in terraform/grayspace-data terraform/slice/terraform.tfstate.d terraform/slice/instances ansible/slice-inventory; do
  [ -d "$d" ] && $c exec grayspace mkdir -p "/data/work/$d" && $c cp "$d/." "grayspace:/data/work/$d/"
done
[ -f terraform/images/terraform.tfstate ] && $c cp terraform/images/terraform.tfstate grayspace:/data/work/terraform/images/
$c exec grayspace task init
$c exec grayspace task plan
```

The plan should show no changes to routers, links or services; a deployment from before still has the `grayspace-web` LXC, which it removes, and nothing else. After the move, run Terraform only from the container: two copies of the state applied from two places will diverge.

## Accessing the model

After the Ansible playbook successfully finishes, all resources will be available and configured on the Proxmox node you set in your environment file. Log into PVE and switch to Tag view. You will see the tag you defined in your environment with all LXC and VM resources underneath it. To confirm the configuration worked, connect to a router console and log in. Run the command `vtysh` and then `sh ip bgp sum` to confirm that the router's BGP neighbors are active.

## Remove Environment

Destroy terraform-managed resources:

```bash
cd terraform
terraform destroy -parallelism=7
```
