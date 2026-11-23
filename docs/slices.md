# Enclaves

An **enclave** is a named, versioned addition to the simulated internet: enterprise sites with their
segments and hosts, internet hosts such as a news site, DNS names and certificates. GraySpace
builds an enclave at the edge of the core, as an **instance**, and tears it down again. It does this
from its own CLI and UI, or for its clients, Scenario Builder and GHOSTS, through the Terrain API
([API](api.md)).

!!! note "Enclaves were called slices"
    Contract 1.5 renamed slices to enclaves. The API serves `/api/v1/enclaves`, and `/api/v1/slices`
    stays as a deprecated alias until 2.0. The spec takes `kind: Enclave` or `kind: Slice` (one
    version either way). `task enclave:*` runs `task slice:*`. The rest of this page, the examples
    (`examples/slices/`) and the code still use the old word in places.

| Term | Meaning |
|---|---|
| Core | The simulated internet: ISPs, routers, links, core DNS, CAs. Fixed while slices run; changed only through core versions |
| Slice | A spec: what an exercise needs (`apiVersion: grayspace/v1`, `kind: Slice`). Versioned by content hash |
| Instance | A slice version built on Proxmox. Its ID is the slice's `prefix` (e.g. `mb`) |
| Overlay | Runtime changes made in the web UI or by a core preset, kept in `active_mutations.json`. Separate from instances |

The spec says *what* is needed; GraySpace decides *how*: which router, which addresses, vnets,
images and configuration.

## Quick start

```bash
task backend:venv                                    # once: the backend's Python (3.10-3.13)
task slice:validate SLICE=examples/slices/meridian-bank.yaml BASE=grayspace/2026.11   # no deployment needed
task slice:validate SLICE=examples/slices/meridian-bank.yaml   # against the deployed core
task slice:plan     SLICE=examples/slices/meridian-bank.yaml   # allocation preview + terraform plan
task slice:up       SLICE=examples/slices/meridian-bank.yaml   # build it (or resume a failed build)
task slice:status   SLICE=examples/slices/meridian-bank.yaml   # state and manifest
task slice:down     SLICE=examples/slices/meridian-bank.yaml   # tear it down
```

`PREFIX=mb2` runs a second copy of the same slice. The CLI runs the same code as the API's jobs,
takes the same node lock, and writes to the same data directory, so the UI and API see what it did.

The examples in `examples/slices/`:

| Example | Shows |
|---|---|
| `meridian-bank.yaml` | Two NAT sites, a published web server with a GrayTrust certificate, 45 GHOSTS clients in two pools |
| `information-environment.yaml` | Internet hosts: Pandora as a news site (TLS) and a company web site, and a reader site |
| `routed-campus.yaml` | A routed site with a public /24, dual-homed to Lumen and Arelion over eBGP |
| `windows-office.yaml` | A mail server, a Samba AD domain controller and file server, and a Windows GHOSTS client (needs a local Windows image) |
| `residential-customer.yaml` | A household behind a home router on Comcast, with a desktop in the browser (`grayspace-desktop`) |

## Placement

```
 core router lum-chi (Lumen)                    instance "mb"
 customer_net 209.151.130.0/24, vnet v332
   .1  lum-chi ─────────────┬──────────── .2  mb-hq-edge (WAN)  ── NAT ──┬─ hq-lan 192.168.0.0/24
   .20–.254 DHCP (core)     │                 .3  (published: web01)     └─ hq-dmz 192.168.20.0/24
                            │                                                  └─ mb-web01 192.168.20.10
                            └──────────── .4  ie-news (internet host)
```

| Kind | What GraySpace builds | Reachability |
|---|---|---|
| NAT site | An Alpine edge router with its WAN at a free static address on a core router's customer network, and one vnet per segment. The edge masquerades the segments | Outbound to the whole simulated internet; inbound only to published hosts |
| Published host | A site host given a second static address on the WAN; the edge does 1:1 NAT for it (also from inside the site) | From the internet at that address |
| Internet host | A host directly on a core router's customer network, gateway `.1` | From the internet, no NAT |
| Routed site (`routed: true`) | A public /24 from the first attachment's ISP block, split across the segments; the edge runs FRR with a private ASN and peers with each core router it attaches to | Every host is public |
| Dual-homed site | A routed site with `attach:` as a list: one WAN per core router | Fails over when a core link is cut |

NAT sites and internet hosts change no core resource: core routers already route their customer
networks, and a container joins a vnet by bridge name. Routed sites add a BGP neighbor to each
core router they attach to (below).

Only routers with a customer network can take attachments (105 of 134 in `grayspace/2026.11`).
Each has 18 static addresses (`.2`–`.19`), less what the core places there, shared by all
instances: a NAT site takes one, plus one per published host; an internet host takes one.
`GET /api/v1/attachment-points` lists the routers and their free addresses.

## The spec

```yaml
apiVersion: grayspace/v1
kind: Slice
slug: meridian-bank          # lowercase, digits, hyphens; not "base"
prefix: mb                   # hostname prefix and instance ID, ≤ 8 characters
description: …
owner: ghosts                # optional; the UI shows ghosts-owned slices read-only
sites:
  - name: hq
    attach: { isp: Lumen, location: Chicago }      # or { router: lum-chi }; a list dual-homes (routed only)
    routed: false
    segments:
      - name: hq-lan                               # cidr omitted: a /24 from the slice pool
      - name: hq-dmz
        cidr: 192.168.20.0/24                      # explicit private ranges are honored (NAT sites)
hosts:
  - name: web01
    segment: hq-dmz                                # or internet: { isp: …, location: … | router: … }
    image: grayspace-ubuntu-lxc
    roles: [web]
    publish: true
  - name: ws
    count: 40                                      # ws-01 … ws-40
    segment: hq-lan
    image: grayspace-ubuntu-lxc
    roles: [ghosts-client]
    size: { cores: 1, memory: 1024, disk: 8 }      # default: the image's size
    labels: { ghosts.pool: tellers }               # free-form, returned in the manifest
    crucible: true                                 # participants see these in Crucible Player (every copy)
  - name: news
    internet: { isp: Arelion, location: Ashburn }
    image: ghosts-pandora
    env: { MODE_TYPE: website, SITE_NAME: The Daily Chronicle }   # OCI images only
dns:
  domains:
    - { domain: meridian-bank.com, hosting_server: dns-cloudflare }
  records:
    - { name: www.meridian-bank.com, host: web01 }              # A: the host's public address
    - { name: meridian-bank.com, type: MX, data: "10 mail.meridian-bank.com." }
tls:
  - { host: web01, names: [www.meridian-bank.com], ca: ca-graytrust }
conditions:
  links: { "lum-chi:lum-ash": { latency_ms: 80, packet_loss_percent: 2 } }   # core links, while the instance lives
role_vars:
  ghosts-client: { client_url: …, api_url: …, api_host: … }
```

The JSON Schema is `openapi/grayspace-slice-v1.json`. Storing a spec that changes content makes a
new version (`sha256:…` of its content, defaults left out); instances pin a version.

### Defaults and allocation

- Segments without a CIDR get /24s from `SLICE_POOL` (`192.168.0.0/16`), skipping every network the
  core uses and the management subnet (`MGMT_SUBNET`, `10.0.0.0/16`). Instances are isolated by NAT,
  so they may reuse private ranges; only public addresses are global.
- On a segment, `.1` is the edge and hosts are numbered from `.10`.
- `attach` by ISP and/or location picks the matching router with the most free static addresses.
- Hosts use the core resolver nearest their attachment router.
- Hostnames are `<prefix>-<name>`; edges are `<prefix>-<site>-edge`.
- Slice vnets are `<environment_id>s000` … `s999`, allocated across instances, never in the core's
  positional `v100…` sequence. `environment_id` must be at most 4 characters (Proxmox allows 8).
- Routed sites get the first free /24 in their ISP's `public_block` and a private ASN from 64512.

### Findings

Validation never changes the spec; it returns findings, with a `suggestion` where one fits (another
router, a free prefix, the expanded name of a counted host). An error blocks an instance; a
warning doesn't.

| Code | When |
|---|---|
| `INVALID_SPEC` | The spec doesn't parse (with the field path), or a field is ignored (warning) |
| `DUPLICATE_NAME` | A site, segment, host, hostname or domain is defined twice |
| `UNKNOWN_ROUTER` | No such router, ISP or location in the deployed core, or the router isn't at the ISP/location given |
| `NO_CUSTOMER_NET` | The router (or every router matching) has no customer network |
| `NO_STATIC_ADDRESSES` | Not enough free static addresses on the router, counting other instances |
| `CIDR_OVERLAP` | A segment overlaps a core network or another segment, or the pool is exhausted |
| `CIDR_OVERLAPS_MGMT` | A segment overlaps the management subnet (an error), or the pool does (a warning) |
| `INVALID_CIDR` | A NAT segment isn't RFC 1918 or is smaller than /28; a routed segment gives a CIDR |
| `SEGMENT_FULL` | More hosts than a segment holds |
| `UNKNOWN_SEGMENT`, `UNKNOWN_HOST`, `UNKNOWN_DOMAIN`, `UNKNOWN_LINK` | A reference to something the spec or core doesn't have |
| `UNKNOWN_IMAGE`, `UNKNOWN_ROLE` | Not in the catalog (`GET /api/v1/images`, `/roles`) |
| `INCOMPATIBLE_ROLE` | The role doesn't support the image's OS family or type (a role's `image_types`, e.g. `ad-dc` runs only on VMs); OCI images take no roles |
| `MISSING_ROLE_VAR` | A role's required `role_vars` entry is missing |
| `UNSUPPORTED_FEATURE` | A feature this GraySpace doesn't build (see `slice_features` in `/capabilities`), dual-homing a NAT site, or a certificate for an image that can't serve one |
| `HOSTNAME_TAKEN`, `PREFIX_TAKEN` | Used by the core or another live instance |
| `UNKNOWN_TLD`, `UNKNOWN_HOSTING_SERVER`, `UNKNOWN_CA` | Not in the deployed core |
| `DNS_NAME_TAKEN` | The domain or name belongs to another instance or the overlay |
| `UNREACHABLE_RECORD` | A record points at a host with only a private address, or a certificate name has no record (warning) |
| `LINK_OWNED` | The link has conditions from another instance or the overlay |
| `CAPACITY` | The node lacks the memory or storage (errors), or CPUs are oversubscribed or Proxmox is unreachable (warnings) |

## Images and roles

The image catalog is `images/images.csv`, separate from core versions; sites add **local images**
(`POST /api/v1/images`), such as Windows templates built with `packer/windows/` and registered as
`template:<vmid>`. Each image a live instance uses is downloaded once, by `terraform/images/`,
however many instances use it; images no live instance uses are removed after a destroy. GraySpace's
own images (`grayspace-*`, built in `packer/`) are uploaded instead when `TF_VAR_alpine_image_transfer`
is `upload`: the machine running Terraform fetches them, as the core then does its Alpine image, for
a server the node may not reach. By default (`download`) the node downloads them from their GitHub
release like any other image.

`grayspace-desktop` (`packer/desktop-lxc/`) is a desktop in the browser: Fluxbox with desktop icons
and a panel for Firefox, a terminal, a text editor and a file manager, plus the usual admin and
network tools, for the user `user` (passwordless sudo). The web UI's desktop proxy reaches it over
management (noVNC on 6080, no password): hosts made from an image with the `desktop` feature have
a **Desktop** button in the Enclaves page, for anyone with operate access.

`grayspace-ubuntu-lxc` (`packer/ubuntu-lxc/`) is the container for Ubuntu hosts: Proxmox's Ubuntu
24.04 template with the packages of the roles that run in a container (`dns`, `file`, `mail`, `web`,
`ghosts-client`) and their services off, so configuring needs no package index download either. It
replaced Proxmox's stock `ubuntu-22.04-lxc`, which the catalog no longer has.

`grayspace-ubuntu-vm` (`packer/ubuntu-vm/`) is the VM for hosts whose roles need one (`ad-dc`), or
any Ubuntu server host: Ubuntu 24.04 with the QEMU guest agent (stock cloud images lack it, and
Terraform waits for the agent to report the VM's addresses), cloud-init for Proxmox's drive, and the
packages of the host roles (`ad-dc`, `dns`, `file`, `mail`, `web`, `ghosts-client`) with their
services off. Each role turns on its own and skips its install when the image has every package
(`grayspace-host`'s `packages.yml`), so configuring needs no package index download. systemd-resolved
runs without its stub listener, which would hold port 53 against Samba's DNS. It replaced the stock
Ubuntu cloud image (`ubuntu-24.04-vm`), which the catalog no longer has: every shipped Linux image is
now GraySpace's own, and a site that wants another adds it as a local image.

Roles are Ansible roles with a `meta/grayspace.yml` describing them, the OS families they support,
the image types they run on when not every type (`image_types`: `ad-dc` needs a VM) and the `role_vars` they read:

| Role | OS families | Does |
|---|---|---|
| `web` | ubuntu, debian | nginx with a placeholder page; HTTPS when the host has a certificate |
| `ghosts-client` | ubuntu, debian, windows | Downloads the GHOSTS client from `client_url`, writes `application.json` with `ApiRootUrl` = `api_url` and `Id.Format: hostname`, runs it as a service. `api_host` stays reachable over management |
| `mail` | ubuntu, debian | Postfix and Dovecot for `domain` (default the slice's first domain), mailboxes from `users` |
| `dns` | ubuntu, debian | Authoritative for `<prefix>.internal` (every host of the site), forwarding to the host's resolver: BIND, or Samba's DNS on a host that also has `ad-dc` (both need port 53) |
| `file` | ubuntu, debian | A Samba share |
| `ad-dc` | ubuntu, debian | A Samba AD domain controller (`realm`, `domain`, `admin_password`); needs a VM image. The other hosts of its site use it as their resolver (the first DC, if several), and it forwards to the core resolver; it serves and registers only its simulated address |

GraySpace also applies internal roles: `grayspace-host` on every managed guest (no management
default route, the simulated route and resolver, the GraySpace CA roots, certificates at
`/etc/ssl/grayspace/host.{crt,key}`), `edge-nat` on NAT edges and `edge-bgp` on routed edges.
OCI images (Pandora) take their settings through `env` at creation and have no management NIC;
images with the `tls-kestrel` feature serve their certificate over HTTPS on 443 (and HTTP on 80).

## Jobs

Everything that builds or tears down runs as a job; the API returns the job, and
`GET /api/v1/jobs/{id}` and `/log` follow it. Jobs run one at a time and hold the **node lock**
(`terraform/.grayspace.lock`, or `GRAYSPACE_LOCK_FILE`), which `task apply`, `task destroy` and
`task configure` take too. With several deployments on one Proxmox cluster, they all share one lock,
so jobs run one at a time across the cluster ([Deployments](deployments.md)).

**Create** stages, each idempotent and recorded when done, so a retry resumes where it failed:

| Stage | Does |
|---|---|
| validate | The pinned spec against the deployed core, other instances and capacity |
| allocate | Addresses, vnets, hostnames, prefixes and ASNs, persisted in the instance |
| render | Issues certificates from the core CAs (OCI hosts take theirs at creation), writes `terraform/slice/instances/<id>.tfvars.json` and the images input |
| apply | `terraform/images/`, then the instance in workspace `<id>` of `terraform/slice/`, each until `terraform plan -detailed-exitcode` exits 0 (6 attempts) |
| wait | SSH on every managed guest's management address |
| configure | Routed sites: BGP neighbors on their core routers (`slice-attach.yml`). Then `ansible-playbook -i inventory/proxy.ini -i slice-inventory/<id>.ini slice.yml`: packages come over management, through the guests' proxy when there is one (`TF_VAR_proxy`, or the Proxmox connection's; the GHOSTS API stays direct), and are skipped when the image has them |
| dns | Domains (delegated from the core's TLD) and records on the core's hosting servers |
| conditions | The spec's link conditions |
| check | Each edge reaches its core router; one host per site resolves a name in the core, reaches an internet host and fetches each certificate's name over HTTPS; each public address answers from its core router |
| ready | The manifest |

**Destroy** works from any state: revoke certificates, delete records and domains, release link
conditions, remove BGP attachments, `terraform destroy`, delete the workspace, remove unused images,
free the allocations. Its first `terraform destroy` doesn't refresh (a refresh waits up to the guest
agent's 15 minutes on a VM without one), and retries do. **Plan** shows the allocation, `terraform plan` from a scratch workspace,
and the capacity needed, without allocating anything.

Instance states: `pending → provisioning → configuring → ready`, plus `failed`, `destroying`,
`destroyed`. Job states: `queued → running → succeeded | failed | cancelled`. Cancel interrupts the
running command within a few seconds (Terraform gets SIGINT, so it saves its state and releases its
lock), or stops the job at the next stage boundary; `POST /jobs/{id}/retry` runs a failed or cancelled job again.

Jobs run where a GraySpace checkout, its Terraform state and its environment file are: the
GraySpace container (each deployment's work tree) or a devcontainer. Without them `/capabilities`
reports `jobs: false`; slices can still be stored and validated there.

## The manifest

`GET /api/v1/instances/{id}`: sites (router, ISP, location, WAN address, segments), hosts
(hostname, address, public address, management address, DNS names, roles, labels, Proxmox status),
DNS records and certificates. GHOSTS pre-registers machines from `hosts`.

## Runtime actions

`POST /api/v1/instances/{id}/actions` on a ready instance:

| Action | Target | Params |
|---|---|---|
| `link.condition`, `link.clear` | A core link ID (`r1:r2`) | `latency_ms`, `jitter_ms`, `packet_loss_percent`, `bandwidth_mbps` |
| `link.down`, `link.up` | A core link ID | |
| `dns.record.set`, `dns.record.delete` | | `name` (in the instance's domains), `type`, `host` or `data`, `ttl` |
| `host.start`, `host.stop` | A host of the instance | |

What an action changes belongs to the instance: quick reset and "revert to core" leave it alone,
and destroying the instance undoes it. A core link takes conditions from one owner at a time, the
overlay or one instance (`LINK_OWNED`); the overlay's link-traffic routes refuse links an instance
holds.

## The map layer and events

`GET /api/v1/map` is the map as GeoJSON (RFC 7946): routers (positions fanned out per city), links
(great-circle LineStrings split at the 180° meridian), and slice sites, internet hosts and their
attachment lines, each with a stable `id`, a `kind`, colors and state. The web UI draws it;
GHOSTS draws the same layer on its own map. `?instance=` limits the slice features.

`/api/v1/events` is a websocket (token in the `Authorization` header or `?token=`) of
`feature.update` messages keyed by feature id, `guest.update` when a core router's, client's or
core service's Proxmox status changes, and `job.update`, `instance.update` and `action.applied`. The schema is `openapi/grayspace-events-v1.json`.

## API

The Terrain API contract is `openapi/grayspace-v1.json`. Every contract route needs the bearer
token `GRAYSPACE_API_TOKEN`, as does every write route of the web UI and its console and desktop
sessions; without a configured token they return 503. Browsers may call the API only from
`GRAYSPACE_CORS_ORIGINS`.

| Route | |
|---|---|
| `GET /base`, `/base/versions`, `/base/versions/{name}` | The deployed version and its health (`not_deployed` when nothing is); shipped versions and their topology snapshots |
| `GET /base/preflight?version=&scope=` | Whether the site can deploy: settings, Proxmox API and node, storages and their content types, bridges (node or SDN vnet), SDN, SSH to the host, capacity, live instances a version change would break, and, with Crucible on, a Keycloak token and the Player API |
| `POST /base/deploy {version, scope, configure, mgmt_gateway}` | A job: preflight, parse, guards, Terraform until converged, Ansible, refresh, then a wait for routers and BGP. Re-running it changes nothing |
| `POST /base/configure`, `DELETE /base?destroy_instances=` | Jobs: Ansible again; destroy the core (refused while instances are live, unless their destroy jobs are queued first) |
| `GET /base/inventory` | What the deployment built: ISPs; routers with interfaces and addresses; links with both ends' addresses and state; clients and core services (DNS servers, CAs) with addresses; Proxmox status throughout |
| `GET /base/dns` | Every zone the simulated DNS serves with its records (all but the SOA), and the resolvers. Each zone and record has an `origin`: `base`, `overlay` (web UI or core preset) or `instance` |
| `GET /capabilities`, `/attachment-points` | What a slice can use: images, roles, `slice_features`, `jobs` (slice jobs) and `base_jobs`, management subnet, slice pool, DNS, CAs, ISPs, capacity (CPU, memory, storage); routers with free static addresses |
| `GET /enclaves`, `GET/PUT/DELETE /enclaves/{slug}` | Stored enclaves and versions. `PUT` returns the version and findings. Also at the deprecated `/slices` |
| `POST /enclaves/{slug}/validate`, `/plan` | Findings only; a plan job |
| `POST /instances`, `GET /instances`, `GET/DELETE /instances/{id}` | Create (a job), list, manifest, destroy (a job) |
| `POST /instances/{id}/actions` | Runtime actions |
| `GET /jobs`, `/jobs/{id}`, `/jobs/{id}/log`; `POST /jobs/{id}/cancel`, `/retry` | Progress, streamed log, cancel, retry |
| `GET/POST /images`, `DELETE /images/{name}`, `GET /roles` | The catalog and local images |
| `GET /ca/bundle` | The core CAs' root certificates (PEM) |
| `GET /map` | The map layer |

## Living alongside the core

- **Node lock.** `task apply`, `task destroy`, `task configure` and slice jobs take one flock, so
  they never run at once. SDN applies are cluster-wide: an apply from any state also applies the
  others' pending SDN changes.
- **Core guard.** `task apply` and `task destroy` run `scripts/check_slice_attachments.py` under the
  lock: destroy is refused while any instance exists, and apply is refused if a router an instance
  uses would lose its customer network or change its vnet, if a core address would collide with an
  instance's address, or if the core would use a routed site's prefix. Destroy the instances first.
- **Ownership.** Terraform owns everything it sets on slice guests. Runtime actions use only what
  Terraform doesn't model (tc, interface state, DNS records, start and stop). Moving a slice host
  is a spec change.
- **The overlay is separate.** Slice DNS, certificates and link conditions live in the instance
  records, not `active_mutations.json`.
- **TLD delegations** live in `/var/bind/delegations.<tld>`, included by each TLD zone, so domains
  created at runtime survive `task configure`.
- **Routed attachments** are merged into the core routers' BGP neighbors by the dynamic inventory,
  so `task configure` keeps them. `task configure` re-applies the core's own tc latency on router
  links, which clears slice link conditions; re-apply them with actions.
- **Core version switches** need the instances destroyed first; the guard enforces it.
- **Crucible.** `crucible: true` on a host (every copy of a counted host) or a site (its edge router)
  is a hint: in a deployment with a Player view, it puts the guest in the view's default team unless
  the deployment's Player step assigns it otherwise ([deployments.md, The Player view](deployments.md#the-player-view)).
  `/capabilities` reports `crucible` when the deployment has a view; without one, marks give a
  `CRUCIBLE_OFF` warning. An adopted tree whose core Terraform module still makes the view
  (`TF_VAR_configure_crucible=true`) registers marked guests in its team as before
  (`terraform/slice/crucible.tf`), and destroying the instance removes them without a refresh, since
  the Crucible provider's refresh can panic.

## Settings

| Setting | Default | |
|---|---|---|
| `GRAYSPACE_API_TOKEN` | — | Required for the API; `scripts/setup-env.sh` generates one |
| `GRAYSPACE_CORS_ORIGINS` | — | Other browser origins allowed, comma-separated |
| `DATA_PATH` | `<tfvars dir>/grayspace-data` | Slices, instances, jobs, local images. In a checkout, `terraform/grayspace-data` (the guard and dynamic inventory read it there; `GRAYSPACE_DATA_PATH` overrides theirs) |
| `CODEBASE_PATH` | `/workspaces` | The checkout jobs run Terraform and Ansible from |
| `ENVIRONMENT_FILE` | `.environment`, then `environment`, in the checkout | The `TF_VAR_*` and `PROXMOX_VE_*` settings jobs use |
| `SLICE_POOL`, `MGMT_SUBNET` | `192.168.0.0/16`, `10.0.0.0/16` | |
| `SLICE_MGMT_GATEWAY`, `SLICE_MGMT_RESOLVER` | — | For guests whose DHCP lease doesn't show the management gateway or DNS |
| `SLICE_APPLY_ATTEMPTS`, `SLICE_SSH_TIMEOUT` | 6, 600 s | |
| `TERRAFORM_APPLY_PARALLELISM`, `TERRAFORM_PLAN_PARALLELISM` | 10, 30 | Terraform's `-parallelism` for applies and destroys, and for plans. A Proxmox connection's own values (Admin > Platform) override them |
| `SLICE_CERTIFICATE_VALIDITY` | `720h` | Up to the CAs' maximum, which `task configure` sets to 2160h |

## Verifying on a deployment

These need a Proxmox deployment. The first deployment (2026-10-16) ran step 1, and everything
passed except the Pandora host's HTTPS check. Steps 2 and 3 haven't been run:

1. `task slice:up SLICE=examples/slices/information-environment.yaml`; the job's check stage
   proves a reader browses `https://www.dailychronicle.com` and that `198.41.0.2` answers from
   `are-ash`. Then `task slice:down …` and `task plan` reports no changes.
2. `task slice:up SLICE=examples/slices/routed-campus.yaml`. On `rc-campus-edge`, take the WAN to
   `lum-chi` down (`ip link set eth0 down`) and confirm `rc-www` still answers from a core router
   (through `are-chi`). Run `task configure` and check that `vtysh -c "show bgp summary"` on
   `lum-chi` still lists `209.151.130.2`.
3. Register a Windows template and run `examples/slices/windows-office.yaml`.
