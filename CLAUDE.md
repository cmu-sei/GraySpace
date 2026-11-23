# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GraySpace is an internet backbone simulation and modeling system that creates a multi-provider network with BGP/OSPF routing. The topology (ISP relationships, router topologies, and network services) is a graph defined in CSV files. The infrastructure is deployed via Terraform to Proxmox (LXC containers) and configured with Ansible.

## Architecture

The core (the provider network GraySpace builds: ISPs, routers, links and core services such as DNS and CAs) was called the base. Since contract 1.26 the API says core (`/api/v1/core`, a draft's `core`, `core_jobs`, `last_core_job`), with the `base` names as deprecated aliases until 2.0; values keep theirs (the `base` rollout stage, job kinds such as `deploy_base`, the `base` Player scope and DNS origin). CLI flags and file names still say base (`BASE=`, `--base`, `bases/`, `base_jobs.py`).

### Data Flow

1. **Topology CSVs** (`bases/<name>/<version>/*.csv`, e.g. `bases/grayspace/2026.11`) define the network as nodes (ISPs, Routers, CoreServices, Locations, Images) and relationships (router-to-router links, OPERATED_BY, LOCATED_IN, HOSTED_BY). Everything else on the simulated internet is an enclave: the core's clients (`clients.csv`, `client_of.csv`) were retired with `grayspace/2026.10`, and the loader refuses a directory that still has them
2. **scripts/generate_tfvars.py** loads a core version's CSVs through `scripts/topology.py` and generates `terraform.tfvars.json` with deployment scope (full or tier1)
3. **Terraform** provisions LXC containers on Proxmox using the generated tfvars, creates SDN networks, and outputs Ansible inventory files
4. **Ansible** configures routers with FRR (BGP/OSPF), sets up monitoring (Prometheus, which the web app reads), and configures services

### Key Components

**Topology Model** (`bases/<name>/<version>/*.csv`, loaded by `scripts/topology.py`):

- Nodes: Router, ISP, CoreService, Location, Image
- Relationships: router-to-router links in `connections.csv`, typed per row as INTERNAL_LINK, PEERS_WITH or PROVIDES_TRANSIT_TO (with IP assignments and OSPF areas); OPERATED_BY (router-to-ISP); LOCATED_IN (router-to-location); HOSTED_BY (core service-to-router)
- The CSVs keep the neo4j-admin header format (`hostname:ID(Router)`, `asn:int`, `roles:string[]`); loading validates references, IDs and types
- See `docs/graph_model.md` for detailed schema

**Terraform** (`terraform/`):

- `main.tf` - Core infrastructure (SSH keys, the management container: prometheus)
- `routers.tf` - Router LXC containers with dynamic network interfaces
- `networks.tf` - Proxmox SDN zones (simple and VLAN) and vnets for interconnecting routers
- `lxc_services.tf`, `vm_services.tf`, `oci_services.tf` - Service deployment for different container types
- `vars.tf` - Input variables loaded from `terraform.tfvars.json`
- Variables loaded from `terraform.tfvars.json` (generated) and environment variables (TF_VAR_*)

**Slices** (`docs/slices.md`): additions at the edge of the core (sites, internet hosts, DNS, certificates), built as instances from a spec:

- `grayspace-web/backend/services/slices/` - spec resolution and allocation (`plan.py`), file store (`store.py`), rendering (`render.py`), job runner and stages (`runner.py`, `stages.py`); shared by the API and `slicectl.py` (the CLI behind `task slice:*`)
- `terraform/slice/` - one workspace per instance (`crucible.tf` registers its guests in the core's Crucible Player view, from the core state's `crucible` output); `terraform/images/` - one download per image across instances
- `ansible/slice.yml` (per-instance inventory in `ansible/slice-inventory/`, never `ansible/inventory/`), `ansible/slice-attach.yml` (routed sites' BGP neighbors on core routers), roles in `ansible/roles/` (catalog metadata in `meta/grayspace.yml`)
- `images/images.csv` - the slice image catalog; `examples/slices/` - example specs, validated against every shipped core version by the backend tests
- `scripts/with-node-lock.sh` (one flock for apply, destroy, configure and jobs), `scripts/converge.sh` (plan to a file and apply it until `plan -detailed-exitcode` is 0), `scripts/check_slice_attachments.py` (core guard)

**Deployments** (`docs/deployments.md`): one GraySpace runs several deployments (a core and its enclaves each, own `environment_id`), on one Proxmox cluster or several:

- `grayspace-web/backend/control/` - the control plane, `uvicorn control.main:app`: the registry (`/api/v1/deployments`, `registry.py`), one backend worker (`main:app`) per deployment on a unix socket (`supervisor.py`, settings from `worker_env.py`), and forwarding of `/deployments/{id}/...` to it (`proxy.py`); un-prefixed `/api` and `/ws` reach the default deployment (deprecated alias)
- Each deployment lives in `<deployments root>/<id>/` (`deployments/` in a checkout, `/data/deployments` in the container): record, write-only secrets, a generated environment file that sources the site's, and a work tree (`work/`, refreshed by `scripts/sync-worktree.sh`) with its own state, inventory, keys and data. The existing checkout or `/data/work` is adopted as the default deployment on first start
- Jobs on one Proxmox cluster share `locks/<proxmox host>.lock` (`GRAYSPACE_LOCK_FILE`): SDN applies are cluster-wide
- The draft and the rollout (docs/deployments.md, Building a deployment; `services/rollout.py`, `routers/rollout.py`): `PUT /api/v1/draft` (core and enclave instances), `GET /api/v1/deploy/plan`, `POST /api/v1/deploy` queues a `rollout` job whose stages (`destroy:<id>`, `base`, `create:<id>`) each run a child job inline under the node lock; it stops at the first failure and its retry resumes there, while the draft is the one it deployed (after a change, deploy instead; `resumable_rollout` in the plan). Only instances the draft manages (`<data>/rollout.json`) are destroyed. Runtime changes are tracked with who made them (`X-Grayspace-User`, `services/request_user.py`; `GET /api/v1/changes`) and Reset changes is `POST /api/v1/changes/discard`; an instance's own actions reset with `{"action": "reset"}`
- Presets (docs/deployments.md, Presets; `services/presets.py`, `services/base_changes.py`, `routers/presets.py`): named sets of runtime changes to the core, applied after a reset. They belong to a core version and are the server's (`<deployments root>/presets/<base>/<version>/`, `PRESETS_PATH`), as lists of actions; every core change, from the UI's routes, `POST /api/v1/core/actions` or a preset, goes through `base_changes.apply()`. The draft names one (`core.preset`) and the rollout applies it in its `preset` stage. Enclaves have no presets (contract 1.19: a variant is another enclave; `enclaves[].preset` is ignored, an old rollout's `preset:<id>` stages are skipped). They replaced the scenarios; `/api/v1/scenarios/changes`, `/mutations/count` and `/discard-changes` remain as deprecated aliases
- Platform connections (docs/deployments.md, Platform connections; `control/platform.py`, `platform_api.py`, `probes.py`): admins define Proxmox clusters (host, credentials, nodes with storage and bridges) and Crucible instances once, in Admin > Platform (`/api/v1/platform`, `platform:manage`); a deployment's `placement` names a connection and node, and `options.player` a Player view. `<deployments root>/platform.json` and `platform-secrets.json` (0600). Its environment file sources the site's, unsets the site's Proxmox and Crucible settings and exports the connections'. The site file is imported once as the connections named `site`, and older records are migrated (`migrate_records`)
- The Player view (docs/deployments.md, The Player view; `services/player.py`, `routers/player.py`, `terraform/player/`): the draft's `player` section (applications, teams, which core and enclave guests each team sees; a spec's `crucible: true` is a hint to the default team) is applied in its own workspace by the rollout's `player` stage (last; first when removed), with a refresh: provider cmu-sei/crucible 2.7 manages a view's teams and applications as a whole (inline mode) and matches them by position in name order, so the module sorts them (2.5.0 panicked reading a view, and logged in to Keycloak on every API call, which locked out the service account); destroys skip the refresh. The core Terraform module's old view (`modules/crucible`) is migrated away by `player.migrate_legacy`; `destroy_args` checks the state for Crucible resources. A core or instance destroy job's first attempt is always `-refresh=false` (a refresh waits up to the guest agent's 15m `agent.timeout` on any VM without an IP); later attempts refresh unless Crucible is in the state
- Sign-in and access (docs/deployments.md, Sign-in and Access): every request through the control plane needs a session (`POST /api/v1/auth/login`, local accounts in `<deployments root>/users.json`, `control/users.py`, `auth.py`, `accounts.py`) or the service token `GRAYSPACE_API_TOKEN` (acts as an admin). Roles admin, builder, user; each deployment has an owner and members with build or use access, and others get 404. The proxy classes every worker route as read, operate, build or delete (`control/access.py` `WORKER_ROUTES`; a test fails if a new route isn't listed) and calls the worker with the service token and `X-Grayspace-User`. `GRAYSPACE_BOOTSTRAP_ADMIN` makes the first admin (one-time password in the log); `task user:create NAME= ROLE=`; the audit log is `<deployments root>/audit.jsonl` (`GET /api/v1/audit`)

**Ansible** (`ansible/`):

- `main.yml` - Main playbook with plays for routers, core services and prometheus
- `versions.yml` - release binaries (exporters, step-ca): `main.yml`'s first play downloads each once on the controller into `ansible/.cache/downloads/`, and the plays copy them to the hosts. The router and CA plays run with `strategy: free`; packages already present are not reinstalled (no package index download)
- `templates/frr.conf.j2` - FRR routing configuration (BGP, OSPF)
- `templates/dnsmasq.conf.j2` - DNS/DHCP for customer networks
- `templates/prometheus.yml.j2` - Prometheus scrape config for all routers
- Inventory files auto-generated by Terraform in `ansible/inventory/`

## Common Commands

All common operations are managed via [Taskfile](https://taskfile.dev/). Run `task --list` to see available tasks.

### Taskfile Tasks

```bash
task env              # Interactive setup of .environment from template (first run only)
task parse            # Generate terraform.tfvars.json from the topology CSVs
task parse SCOPE=tier1 # Generate tfvars for Tier1 ISPs only (default: full)
task init             # Initialize Terraform providers (auto-skips if unchanged)
task plan             # Plan Terraform changes
task apply            # Apply Terraform (auto-retries up to 5 times until converged)
task apply MAX_RETRIES=3 # Override retry count
task apply APPLY_PARALLELISM=5 PLAN_PARALLELISM=20 # Override Terraform's -parallelism (defaults: the environment file's, else 10 and 30)
task configure        # Run Ansible to configure all containers
task deploy           # Apply + configure in sequence
task lint             # Lint Ansible playbooks
task destroy          # Destroy all Terraform resources (prompts for confirmation; refused while slice instances live). Skips the refresh; REFRESH=true if something deleted outside Terraform fails
task user:create NAME=<name> ROLE=admin|builder|user  # A local account for the web UI; prints its one-time password
task backend:venv     # Create grayspace-web/backend/.venv (Python 3.10-3.13) for slice tasks and backend tests
task slice:validate SLICE=examples/slices/<name>.yaml [BASE=grayspace/2026.11]  # Findings against the deployed (or a shipped) core
task slice:plan SLICE=...    # Allocation preview + terraform plan in a scratch workspace
task slice:up SLICE=...      # Create an instance (or resume a failed one); PREFIX= for a second copy
task slice:down SLICE=...    # Destroy it
task slice:status SLICE=...  # State and manifest
task demo             # Narrated slices demo; its live act stages instances in task dev's simulated stack (-- --auto, --offline, --clean)
task tunnel           # Start sshuttle tunnel to Proxmox network
task tunnel:stop      # Stop sshuttle tunnel
```

Parallelism: applies and destroys run at `-parallelism=10` (each create takes the cluster's storage lock, and too many at once time out with `got lock request timeout`), plans at 30. A Proxmox connection's `apply_parallelism` / `plan_parallelism` (Admin > Platform) override these for its deployments through `GRAYSPACE_APPLY_PARALLELISM` / `GRAYSPACE_PLAN_PARALLELISM` in their environment files; the server's own defaults are `TERRAFORM_APPLY_PARALLELISM` / `TERRAFORM_PLAN_PARALLELISM` (`config.py`).

Note: `task apply` automatically retries because multiple runs may be needed due to Proxmox SDN dependencies and container startup timing. It is converged when `terraform plan -detailed-exitcode` exits 0; each attempt plans to a file (refreshing the state once) and applies that file (`scripts/converge.sh`, `stages.converge`). `task apply`, `task destroy`, `task configure` and API jobs hold one node lock (`terraform/.grayspace.lock`), and apply/destroy first run the core guard, which refuses changes that would break live slice instances.

The API runs the core's lifecycle too (`grayspace-web/backend/services/base_jobs.py`, `base_preflight.py`): `GET /api/v1/core/preflight`, `POST /api/v1/core/deploy` (parse, guards, apply until converged, configure, refresh, health check), `POST /api/v1/core/configure`, `DELETE /api/v1/core`. Core jobs need only a checkout, the tools and the environment file; slice jobs also need the core's SSH key, checked per request.

### Local development (Aspire)

`aspire/GraySpace.AppHost` runs the web app for development: F5 in VS Code (configs in `.vscode/launch.json`), `task dev`, or `dotnet run --project aspire/GraySpace.AppHost`. No container runtime is needed. See `docs/development.md`.

- **simulated** (default): no Proxmox. The API is the control plane with two simulated deployments, `dev` (the default) and `dev2` (`GraySpace:Deployments`), and the accounts `admin`, `builder` and `user` (password `grayspace-dev`); their workers run with `DEV_SIMULATE_DEPLOYMENT=true` (`services/dev_simulation.py`: every tfvars guest reports as a running container), `aspire/simulator/router_metrics.py` serves frr/node exporter metrics that a local Prometheus scrapes (`POST :9342/links/<r1:r2>/down` to break a link), plus mkdocs. API token `dev-token` (the UI signs in with the accounts instead).
- **deployment** (`--launch-profile deployment`, `task dev MODE=deployment`): the control plane against a real deployment with `.environment`, adopting the checkout as the default deployment.
- The debugger attaches to the control plane; `GRAYSPACE_WORKER_DEBUG=<id>:<port>` runs one worker under debugpy.
- Ports: dashboard 18888, ui 3000, api 8000 (contract Swagger `/api/docs`, every route at `/docs`), prometheus 9090, router-metrics 9342, docs 8001. Generated state in `aspire/.data`; the Prometheus binary in `~/.cache/grayspace-dev`.
- The UI's websockets go to the API directly in development (`REACT_APP_WS_URL` in `grayspace-web/frontend/.env.development`): the CRA dev server keeps `/ws` for hot reload. HTTP `/api` goes through `src/setupProxy.js`.
- `scripts/backend-venv.sh` makes `grayspace-web/backend/.venv` with Python 3.10-3.13 (pydantic 2.9 has no 3.14 build); it is idempotent and reinstalls when requirements change.

### Generate Terraform Variables

Generate `terraform.tfvars.json` from the topology CSVs:

```bash
./scripts/generate_tfvars.py --scope=full    # Deploy entire model (Tier1 + Tier2)
./scripts/generate_tfvars.py --scope=tier1   # Deploy only Tier1 ISPs
./scripts/generate_tfvars.py --scope=full --base=grayspace/2026.11   # A specific shipped core version (the default)
./scripts/generate_tfvars.py --scope=full --csv-dir=/path/to/csvs    # CSVs from any directory
./scripts/generate_tfvars.py --scope=full --output-file=/custom/path/terraform.tfvars.json
```

This outputs router definitions, network links, and service configurations.

**Parser Details**:
- Paths are relative to the GraySpace root (found from the script's location); reads `bases/grayspace/2026.11` and outputs to `terraform/terraform.tfvars.json` by default. `task parse BASE=<name>/<version>` picks another shipped version
- Shipped core versions are read-only directories under `bases/`; change the core by adding a new version directory, not by editing a published one
- Invalid CSVs (unknown references, duplicate IDs, bad values) fail with every problem listed
- Output order is significant: vnet names (`v100`, …) are assigned in link order, so reordering renames vnets and Terraform replaces them. See `tests/golden/README.md`
- Tests: `python -m unittest discover -s tests -t .` (topology, golden tfvars, the core guard, the dynamic inventory, slice Ansible templates, `terraform test` of the slice and images modules with a mocked provider, `packer validate`). The golden test requires byte-identical tfvars at both scopes; regenerate its fixtures only for intentional output changes
- Backend tests: `cd grayspace-web/backend && python -m unittest discover -s tests -t .` with `requirements-dev.txt` on Python 3.13 or earlier (pydantic 2.9 has no 3.14 build)
- The tfvars carry `customer_dhcp_start` (20): dnsmasq leases from there; `.2`-`.19` are static addresses for the core and slices

### Terraform Deployment

```bash
cd terraform
source ../environment          # Load environment variables
terraform init                 # Only needed once or when providers change
terraform apply -parallelism=7 # Deploy infrastructure, repeat until converged
terraform destroy -parallelism=7  # Tear down all resources
```

Note: Multiple `terraform apply` runs may be needed due to Proxmox SDN dependencies and container startup timing.

### Ansible Configuration

```bash
cd ansible
ansible-playbook main.yml  # Configure all routers and services
```

The playbook:

- Installs FRR on Alpine routers and configures BGP/OSPF
- Sets up Prometheus to scrape FRR exporters from all routers
- Ends by removing the management default route from simulated hosts (routers and core services), so their traffic goes through the simulated internet. It records each host's management gateway in `/etc/grayspace/mgmt_gateway` first, and the playbook's first play restores the route for the run, so re-runs can still install packages
- Hosts deployed before the gateway was recorded need one run with `-e mgmt_gateway=<gateway>` (the management network's DHCP gateway, e.g. `10.0.0.1`); later runs need nothing extra

### Python Virtual Environment

```bash
source .venv/bin/activate      # Activate existing venv in devcontainer
# OR for manual setup:
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

### Linting

```bash
ansible-lint ansible/main.yml  # Lint Ansible playbooks (available in devcontainer)
```

### Documentation

Build and preview MkDocs documentation:
```bash
mkdocs serve  # Preview documentation locally at http://127.0.0.1:8000
mkdocs build  # Build static HTML documentation to site/
```

## Development Notes

### Environment Variables

Required environment variables are defined in `scripts/environment.template`. Copy to `environment` (or `.environment`) and fill in values:

Critical variables:

- `GRAYSPACE_API_TOKEN` - The server's service token: scripts, task, GHOSTS, and the control plane's calls to workers ("generate" in the template makes one)
- `GRAYSPACE_BOOTSTRAP_ADMIN` - The web UI's first admin account, made on first start with a one-time password in the log

- `TF_VAR_proxmox_node` - Proxmox node name
- `TF_VAR_proxmox_host_addr` - Proxmox IP address
- `TF_VAR_router_password` - Password for router root accounts
- `TF_VAR_proxmox_blockstore` - Block storage for disks
- `TF_VAR_proxmox_filestore` - File storage for templates
- `TF_VAR_proxmox_mgmt_bridge` - Management network bridge
- `TF_VAR_proxmox_vm_bridge` - VM network bridge
- `TF_VAR_environment_id` - Unique prefix for this deployment
- `PROXMOX_VE_USERNAME` / `PROXMOX_VE_PASSWORD` or `PROXMOX_VE_API_TOKEN` - Proxmox authentication

Optional proxy variables for environments behind corporate proxies (commented out in the template; `task env` asks):

- `TF_VAR_proxy`, `TF_VAR_no_proxy` - the proxy guests install packages through (`ansible/inventory/proxy.ini`). Unset, the controller's own `http_proxy`/`no_proxy`; set empty, no proxy. A Proxmox connection's `proxy_mode` (`site`, `none`, `custom`; Admin > Platform, contract 1.23) overrides them for its deployments

### The GraySpace Alpine Image

Routers, core DNS and CAs, and slice edges are made from the GraySpace Alpine LXC template (`packer/alpine-lxc/`): Proxmox's Alpine 3.24 template with sshd (key login for root), python3, FRR, iproute2, dnsmasq, BIND, the step-ca dependencies, iptables and both exporters (versions from `ansible/versions.yml`). Guests need no hook script, and the plays skip installs the image already has.

- `task image:build` builds it with Packer and Docker into `packer/alpine-lxc/output/grayspace-alpine-<version>.tar.zst` (and `.sha256`); bump `image_version` for every published build
- Its URL is the `grayspace-alpine` row of `images/images.csv`, read by the core (`terraform/routers.tf`, via `csvdecode`); `TF_VAR_alpine_image_url` overrides it. Slice edges use the core's copy (`render.base_alpine_volume`), not a download of their own. The file is named after the URL's file name, so the same build from another server keeps its volume ID
- `TF_VAR_alpine_image_transfer`: `download` (default) has the node fetch it through the cluster's proxy (`datacenter.cfg` `http_proxy`, which has no exceptions); `upload` fetches it on the machine running Terraform, directly (the provider ignores proxy settings, so it times out behind one), and uploads it over the API. The two name their files differently (`<env>-base-up-…`, `<env>-base-…`), so switching never deletes the file it creates
- The images are assets of a GitHub release of cmu-sei/grayspace (`images-<date>`, e.g. `images-2026.11`): publish each new build there under its new file name (`gh release upload`) and point its `images/images.csv` row at it. `download` needs the cluster's proxy to allow `github.com` and `release-assets.githubusercontent.com` (release downloads redirect there); `upload` works only where the Terraform host reaches GitHub without a proxy
- Guests made before the image keep their template (`ignore_changes`); the old hook-script snippet is left on the node (`removed { destroy = false }`) for slice edges that still name it. `TF_VAR_use_custom_image` and `options.use_custom_image` are gone (the option is accepted and ignored)

### The GraySpace Desktop Image

The desktop users get in enclaves (`packer/desktop-lxc/`, catalog image `grayspace-desktop`, feature `desktop`): Proxmox's Ubuntu 24.04 template with a Fluxbox desktop for the user `user` (passwordless sudo, no password), desktop icons (pcmanfm) and a panel (tint2) for Firefox (Mozilla's deb; the Ubuntu package is a snap), a terminal, a text editor and a file manager, plus the usual admin and network tools. Xvfb, the session, x11vnc (localhost:5900, no password) and websockify (:6080, where `routers/desktop.py` connects over management) are systemd units. Firefox's policies (`/etc/firefox/policies/policies.json`) turn off DNS over HTTPS (it would bypass the simulated DNS), load the system trust store (the GraySpace CAs) and skip the first-run pages.

- `task image:build:desktop` builds it into `packer/desktop-lxc/output/grayspace-desktop-<version>.tar.zst`; bump `image_version` for every published build and publish it in a GitHub release like the Alpine image
- The images module (`terraform/images/`) uploads GraySpace's own images (`grayspace-*`, `render.uploaded`) when `TF_VAR_alpine_image_transfer` is `upload`, as `<env>-img-up-<name>`, and downloads everything else through the node

### The GraySpace Ubuntu LXC Image

The multi-purpose container for enclave hosts (`packer/ubuntu-lxc/`, catalog image `grayspace-ubuntu-lxc`; it replaced Proxmox's stock `ubuntu-22.04-lxc`): Proxmox's Ubuntu 24.04 template with the packages of the host roles that run in a container (dns, file, mail, web, ghosts-client; not ad-dc, which needs a VM), their services disabled, no unattended-upgrades or apt timers. Proxmox sets the hostname, network, resolver, root's key and new SSH host keys at creation.

- `task image:build:ubuntu-lxc` builds it with Packer and Docker, from the same imported Proxmox template as the desktop (`grayspace/pve-ubuntu:24.04`), into `packer/ubuntu-lxc/output/grayspace-ubuntu-lxc-<version>.tar.zst`; bump `image_version` for every published build and publish it in a GitHub release like the other images
- `tests/test_packer.py` checks that it has the packages of every role whose `image_types` allow `lxc`, as it checks the VM image for `vm`
- The core's Prometheus is made from it too (`terraform/main.tf`, fetched as `routers.tf` fetches the Alpine image, following `TF_VAR_alpine_image_transfer`; `TF_VAR_ubuntu_lxc_image_url` overrides the catalog URL). A Prometheus made before keeps its Proxmox 22.04 template (`ignore_changes`); `terraform apply -replace=proxmox_virtual_environment_container.prometheus` moves it, losing its metrics. Only the deprecated web LXC still downloads 22.04, and only when it exists. `terraform/tests/base_images.tftest.hcl` checks this with mocked providers

### The GraySpace Ubuntu VM Image

The multi-purpose VM for enclave hosts (`packer/ubuntu-vm/`, catalog image `grayspace-ubuntu-vm`), first for the `ad-dc` role (Samba's sysvol needs a VM): Ubuntu's minimal Noble cloud image (250 MB, the standard one is 600) with the QEMU guest agent (the stock image has none, and Terraform waits for it to report addresses), cloud-init limited to Proxmox's drive (NoCloud, ConfigDrive), no unattended-upgrades, apt timers or snapd, and the packages of the host roles (ad-dc with `samba-ad-dc` and `samba-ad-provision`, dns, file, mail, web, ghosts-client) with their services off (`samba-ad-dc` also masked until the role provisions a domain). Each role turns on its own service. systemd-resolved has no stub listener (Samba's DNS needs port 53). Nothing is provisioned at build time: a domain's realm, password and SIDs are per instance.

- `task image:build:ubuntu-vm` runs Packer's QEMU builder in a container (`packer/ubuntu-vm/builder/`), with `/dev/kvm` when the host has it and software emulation otherwise (much slower; the devcontainer has no KVM). Output `packer/ubuntu-vm/output/grayspace-ubuntu-vm-<version>.qcow2` (and `.sha256`), 8G virtual, grown to the instance's disk by cloud-init; bump `image_version` for every published build and publish it in a GitHub release like the other images
- Roles install packages through `grayspace-host`'s `packages.yml` (`gs_packages`), which skips the install when dpkg has them all; `tests/test_packer.py` fails if a role's package is missing from `scripts/provision.sh`, so add a role's packages to both
- Uploaded like the other `grayspace-*` images (`import` content). It replaced the stock `ubuntu-24.04-vm`: every shipped Linux image in `images/images.csv` is GraySpace's own (other images are a site's local ones). The packages test covers roles whose `image_types` allow `vm`

### Proxmox Permissions

Only guests made before the GraySpace image ran the hook script, which needed sudo for `pct exec` for non-root Proxmox users (`/etc/sudoers.d/pct-exec-perms`):

```bash
username    ALL=(ALL) NOPASSWD: /usr/sbin/pct exec *
```

The provider still uploads snippets (the VMs' cloud-init vendor data) over SSH.

### Inventory Generation

Terraform generates Ansible inventory files in `ansible/inventory/`:

- `*_inventory.ini` - Generated from Terraform outputs with host IPs and variables
- Inventory groups: `routers`, `ungrouped` (prometheus)

### Router Configuration Variables

Routers receive these Ansible variables (from Terraform):

- `loopback_ip` - BGP router ID
- `isp_name`, `asn` - ISP identification
- `public_block` - IP block for this ISP
- `ospf_areas` - List of OSPF areas the router participates in
- `customer_net` - Customer network CIDR (if any)
- `bgp_neighbors` - List of eBGP peers with ASN and IP
- `ibgp_neighbors` - List of iBGP peers (route reflector clients or RR)

### Default Credentials

**SSH/Proxmox Access**:
- Username: `root`
- Password: `grayspace`

Change these defaults in production deployments via environment variables.

### Packer Automation

The `packer/` directory contains automation to build a Proxmox virtual appliance for hosting the GraySpace model. This creates a standalone VM with all dependencies pre-configured.

**Prerequisites**:
- Packer installed (available in devcontainer)
- vCenter/vSphere access configured in `packer/variables.pkrvars.hcl`

**Build Commands**:
```bash
cd packer
packer validate -var-file=variables.pkrvars.hcl .  # Validate configuration
packer build -var-file=variables.pkrvars.hcl .     # Build appliance (45-90 minutes)
```

**Output**: OVA file at `packer/output_ovf/gray-pve-appliance.ova`

**Appliance Deployment** (being reworked, deferred: it is to run the GraySpace container; appliance mode, which installed the web app on the Proxmox host, is gone, and `packer/environment.appliance` still sets its old variable):
- Resource requirements: 4+ CPU cores, 16GB+ RAM, 600GB storage
- Assign network interfaces during import (management and VM networks)
- Until then: use it as a Proxmox host and run GraySpace from a container

### Claude Code / AWS Bedrock

The devcontainer includes Claude Code configured for AWS Bedrock. To use:

1. Copy `.devcontainer/.aws/credentials.example` to `.devcontainer/.aws/credentials`
2. Add your AWS access key and secret key for Bedrock
3. Rebuild the devcontainer
4. Run `claude` in the terminal

## Important Files

**Source Files** (version controlled):
- `CLAUDE.md` (this file) - Instructions for Claude Code
- `README.md` - User-facing documentation
- `Taskfile.yml` - Task runner for all common operations
- `scripts/environment.template` - Template for environment variables
- `scripts/setup-env.sh` - Interactive environment setup script
- `scripts/generate_tfvars.py` - Generates Terraform variables from a core version's CSVs
- `bases/` - Shipped core versions (topology CSVs), one directory per version
- `container/` - GraySpace as one container (Terrain API, UI, Terraform, Ansible, task) for a Docker host that can reach Proxmox; `docker compose -f container/compose.yaml up -d --build`, then `docker compose ... exec grayspace task <cmd>` (docs/install.md). The only home of the web UI: the core's `grayspace-web` LXC and the appliance's web app on the Proxmox host (`grayspace_appliance_mode`, the "Deploy GraySpace Web Application" play) were removed, since neither ran the control plane to sign in through. `grayspace-web` stays a reserved hostname
- `docs/deployments.md` - several deployments on one server: addressing, the registry, layout, locks, adoption
- `docs/api.md` - the contract rendered in the docs site (`mkdocs-swagger-ui-tag`; `docs/openapi` is a symlink to `openapi/`). A running backend serves Swagger for the contract at `/api/docs` (`routers/api_docs.py`): Try it out sends only GETs unless `GRAYSPACE_API_DOCS_WRITES=true`; the container ships Swagger UI (`SWAGGER_UI_PATH`)
- `openapi/grayspace-v1.json` - Terrain API contract for Scenario Builder and GHOSTS (1.6: a deployment's routes under `servers` `/deployments/{deployment}`, `/api/v1/deployments` at the root; 1.8: presets in the draft; 1.9: platform connections, `placement`; 1.10: the draft's Player view; 1.11: a Proxmox connection's `apply_parallelism` and `plan_parallelism`; 1.12: `options.use_custom_image` deprecated and ignored; 1.13: no `grafana` guest; 1.14: the Player view's `url`; 1.15: the Player view's `assign` rules; 1.16: `GET /api/v1/base/preview`, a Crucible connection's `player_url`; 1.17: a draft's `grafana` guest is dropped without a warning; 1.18: no core clients; 1.19: no enclave presets; 1.20: validating a spec as a draft's instance; 1.21: a role's `image_types`; 1.22: a rollout resumes only for the draft it deployed, `resumable_rollout`; 1.23: a Proxmox connection's `proxy_mode`, `proxy` and `no_proxy`; 1.24: a site's domain controller is its hosts' resolver; 1.25: no GHOSTS VM, `options.deploy_ghosts` deprecated; 1.26: the base is the core, `/api/v1/core` and `core` fields, with the `base` names as deprecated aliases), generated by `grayspace-web/backend/contract.py` with `grayspace-enclave-v1.json` (enclave spec schema; `grayspace-slice-v1.json` is the same under its deprecated name; slices were renamed enclaves in contract 1.5, and `/api/v1/slices` is a deprecated alias of `/api/v1/enclaves`) and `grayspace-events-v1.json` (events schema); a test fails if any is stale
- `tests/golden/slices/` - each example slice rendered for Terraform and Ansible; regenerate with `GOLDEN_REGENERATE=1 python -m unittest tests.test_slice_render` (in grayspace-web/backend) only for intentional changes
- `scripts/topology.py` - Loads and validates the topology CSVs
- `tests/` - Golden tfvars test and loader tests
- `requirements.txt` - Python dependencies
- `docs/graph_model.md` - Topology CSV schema documentation

**Generated Files** (git-ignored, recreated on each deployment):
- `terraform/terraform.tfvars.json` - Generated by generate_tfvars.py
- `ansible/inventory/*_inventory.ini` - Generated by Terraform outputs
- `.ssh/terraform_key` and `.ssh/terraform_key.pub` - SSH keypair generated by Terraform
- `site/` - MkDocs static documentation output
- `terraform/grayspace-data/` - slices, instances (with their allocations), jobs and logs, local images
- `terraform/slice/instances/*.tfvars.json`, `terraform/slice/terraform.tfstate.d/`, `terraform/images/` state, `ansible/slice-inventory/` - per-instance Terraform and Ansible inputs and state
- `deployments/` - the control plane's deployments (records, environment files, work trees with their own state)
