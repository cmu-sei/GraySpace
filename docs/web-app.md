# GraySpace Web Application

The GraySpace web application provides a browser-based interface for visualizing, monitoring, and customizing the simulated internet. It connects directly to the deployed infrastructure through Proxmox, Prometheus, and SSH, giving you real-time visibility and control over routers, links, DNS, certificates, enclaves and network configuration.

## Overview

The application's main views, from the navigation:

- **Map** — Interactive world map showing all routers and links
- **DNS** — DNS server management and zone editing
- **CAs** — Certificate Authority management and certificate issuance
- **Slices** — Stored [slices](slices.md): versions, specs, validation, plans, and creating instances
- **Instances** — Slice instances: state, manifest, jobs with their logs, and destroy

### API token

The map layer, slices and every change need the deployment's API token (`GRAYSPACE_API_TOKEN`).
The application asks for it the first time the API refuses a request and keeps it in the browser;
the **Token** button in the header changes it. Without it the map is drawn from the topology alone
(a banner says so) and nothing can be changed.

The **Preset** box in the map sub-bar shows the active core preset and opens the presets: see [Presets](#presets).

## Map View

The map view is the primary interface. It displays all deployed routers and links on an interactive world map.

The map draws the **map layer** (`GET /api/v1/map`): the server computes router positions, curved
great-circle links (split at the 180° meridian), ISP colors in the core's ISP order, and link
colors by state and type, so this map and the GHOSTS map always agree. Slice sites appear as
diamonds and internet hosts as squares near the routers they attach to, joined by dashed lines;
click one for its instance, addresses and DNS names.

### Router markers

Routers appear as colored markers on the map, with colors assigned by ISP. Click a router marker to open the [router detail panel](#router-detail-panel). Use the **router search dropdown** in the sub-bar to jump to a specific router by hostname.

### Link lines

Links between routers are drawn as lines on the map, color-coded by relationship type:

- **Internal links** — Connections between routers in the same ISP
- **Peering** — Free traffic exchange between two ISPs
- **Transit** — Paid transit where one ISP provides route access to another

Click a link line to open the [link traffic panel](#link-traffic-panel).

### ISP filtering

Use the **ISP filter dropdown** in the sub-bar to show or hide specific ISPs on the map.

### Real-time state

Link lines update in real-time via WebSocket to reflect operational state (up, down, or shutdown). BGP peer state changes are also pushed to the interface. Map layer updates (`/api/v1/events`) change features by id, so slice actions and instance changes show up without reloading.

## Slices and Instances

The **Slices** tab lists stored slices. Select one to see its versions and spec, **Validate** it
against the deployed core (findings and suggestions), **Plan** it (allocation, `terraform plan`,
capacity needed), **Create instance** (optionally with another prefix), or delete it. Slices are
stored through the API or `task slice:up`; the UI doesn't edit them, and slices owned by GHOSTS
are shown read-only.

The **Enclaves** page lists instances with their state. A selected instance opens on its sites,
each with its hosts as cards (address, image, management address, DNS names and roles) and their
**Desktop**, **Console**, **Stop** and **Start**; internet hosts come last. **Map** jumps to a site.
**⚙ Manage** has the rest: **Reset actions**, DNS records, certificates, the jobs (stage
and attempt, the streamed log, **Cancel** and **Retry**) and **Destroy**. An instance with no hosts
yet (being built, or failed) opens on Manage.
Creating and destroying instances runs jobs where the API runs (the GraySpace container); see
[Slices](slices.md#jobs).

## Router Detail Panel

Clicking a router on the map opens a detail panel with three tabs.

### Info tab

Displays static and live information about the router:

- **Basic info** — Hostname, ISP, ASN, location, loopback IP, public block, roles (ABR, RR)
- **OSPF areas** — Areas this router participates in
- **BGP neighbors** — Live peer state (IP, hostname, remote ASN, connection state) polled from Prometheus
- **OSPF neighbors** — Live neighbor state (IP, ID, area, state)

### Interfaces tab

Lists all network interfaces on the router, split into two groups:

**Backbone interfaces** connect to other routers. Each entry shows the peer router, interface names on both sides, link type, and operational state.

**Customer interfaces** are ingress points for customer/stub networks. Each entry shows the interface name, IP address, customer CIDR, and current SDN zone assignment. Customer interfaces support:

- **Zone changes** — Reassign the interface to a different SDN zone (simple or VLAN-backed). This is how you connect GraySpace networks to external infrastructure.
- **Traffic stats** — View live RX/TX byte counts and rates.

### Attached tab

Shows the DNS servers and CAs on this router (hostname, type, IP, subnet, and a DNS server's zones),
each with a console button.

## Link Traffic Panel

Clicking a link on the map opens the link traffic panel, which provides:

- **Live traffic rates** — RX/TX bytes per second on both endpoints of the link
- **Traffic shaping controls** — Applied symmetrically to both ends of the link:
    - **Latency** — Additional delay in milliseconds (0–1000)
    - **Jitter** — Latency variation in milliseconds (0–500)
    - **Bandwidth** — Maximum throughput in Mbps (1–1000)
    - **Packet loss** — Percentage of dropped packets (0–50%)

Use **Apply** to commit changes and **Clear** to remove all traffic shaping from the link.

!!! note
    Traffic shaping is applied using Linux `tc` (traffic control) rules on the router containers. Clearing traffic shaping removes all `tc` rules; it does not restore any base latency that may have been configured by Ansible during provisioning.

## DNS View

The DNS tab displays all DNS servers grouped by role:

- **Root DNS Servers** — Authoritative for the root zone (`.`)
- **TLD Servers** — Authoritative for top-level domains (`.com`, `.net`, `.org`, etc.)
- **Hosting DNS Servers** — Host zone files for second-level domains (e.g., `example.com`)
- **Resolvers / Caching Servers** — Recursive resolvers (e.g., Google DNS, Quad9)

Each server card shows the hostname, IP address, hosting router, ISP, location, deployment status, and the zones it serves.

### Creating a domain

Click **Create Domain** to add a new domain to the simulation. The form asks for:

- **Domain name** — e.g., `example.com`
- **Hosting server** — Which DNS hosting provider will serve this domain
- **IP address** — Optional A record for the domain apex
- **MX records** — Optional mail exchange records
- **Create www** — Optionally create a `www` subdomain pointing to the same IP

Domain creation automatically:

1. Creates a zone file on the hosting server
2. Adds NS delegation records to the appropriate TLD server
3. Tracks the domain as a runtime change, which Reset changes undoes and a preset can hold

### Managing zone records

Click a DNS server to view its zones and records. From the zone detail view you can:

- Browse all records (A, AAAA, NS, MX, CNAME, TXT, PTR, SRV, CAA)
- Add new records to any zone hosted on that server

### Console access

Each DNS server card has a console button that opens an interactive terminal session to the container via SSH.

## CAs View

The CAs tab shows all Certificate Authority servers as clickable cards. Each card displays the CA name, hostname, IP, hosting router, and location.

### CA detail modal

Click a CA card to open its detail modal, which shows:

- **CA information** — name, IP address, hosting router, ISP, location, deployment status
- **Issue Certificate form** — enter a domain name to issue a new certificate from this CA
- **Certificates table** — all certificates issued by this CA with serial, subject, dates, and status
- **Download links** — after issuing, download the `.crt` and `.key` files separately
- **Revoke button** — revoke any active certificate

### Issuing certificates

1. Click a deployed CA card to open the detail modal
2. Enter a domain name in the "Issue Certificate" form
3. Click "Issue" — the certificate and private key are generated
4. Download both files using the download buttons that appear

Certificates are issued without domain validation. The private key is only available at the time of issuance and is not stored server-side.

## Desktops

Enclave hosts made from an image with the `desktop` feature (`grayspace-desktop`, as in
`examples/slices/residential-customer.yaml`) have a **Desktop** button in the Enclaves page, next to
Console, for anyone with operate access. It opens an in-browser noVNC session connected to the
host's desktop.

## Console

The console provides interactive shell access to any container in the simulation — routers, DNS servers, CAs and enclave hosts. It opens as a modal overlay with a full terminal emulator.

Console sessions are maintained over WebSocket and include automatic keepalive to prevent timeout. The default session timeout is 30 minutes.

!!! tip
    On routers, run `vtysh` to access the FRR CLI for inspecting BGP tables, OSPF state, and routing configuration.

## Presets

A preset is a named set of runtime changes on top of the core. The core itself is the
default: no preset is the core as deployed. Make changes on a live deployment, save them as a
preset, and the preset is offered from then on. Presets replace the scenarios of earlier versions.

A preset belongs to a core version (e.g. `grayspace/2026.11`) and holds link conditions, interface
zones, and DNS domains and records. It is offered to every deployment on that version, on this
server. Enclaves have no presets: an enclave is what its spec builds, so a variant of one is
another enclave, and an instance's **Reset actions** undoes what was done to it.

Certificates aren't part of presets: Reset changes revokes the ones issued, and one revoked stays
revoked.

### The preset box

The **Preset** box in the map sub-bar names the active core preset (**None** for the core as
deployed). An asterisk (**\***) means changes were made since it. Opening it shows:

- **Active**: the preset, and each change made since it, with who made it and when.
  **Reset changes** undoes them and applies the active preset again.
- **Save as a preset** (build access): saves every change on the core, the active preset's
  included, as a preset of the deployed core version. It becomes the active preset. Saving an
  existing name replaces that preset, for every deployment on the version.
- **Presets for** the core version: **Apply** resets the changes, then applies the preset, so
  the result is exactly the preset whatever was active before. **None** goes back to the core as
  deployed. A preset saved on another deployment may name targets this one doesn't have (a
  link of the full scope on a Tier 1 deployment): they are listed, and skipped when it is
  applied. **Download** and **Upload…** move a preset between servers as JSON. **Delete** removes it
  for every deployment on the version; where it is active its changes stay, as changes on the core.

!!! info
    Applying may take a few seconds per DNS change (SSH to containers). Link conditions and zone changes are near-instant.

### Presets in the build

The builder's **Core** step chooses the core preset. Deploying applies it after the core is built,
and again when another preset is active, so a rebuilt deployment comes back with the same changes. With no preset chosen, the rollout
leaves runtime changes as they are.

## Platform and Player views

**Admin > Platform** (admins) holds the Proxmox clusters and Crucible instances deployments run on:
add a connection with its credentials and the nodes deployments can use (**Discover** fills their
storage and bridges), **Test** it, and choose the default. A new deployment picks a connection and
node, and whether it has a **Crucible Player view** ([Platform connections](deployments.md#platform-connections)).

A deployment with a Player view has a **Player** step in its builder, before Review:

- **View:** its name (`<deployment>-grayspace` unless set) and description.
- **Applications:** from Player's application templates (VMs, maps, chat…), or a URL of your own.
- **Teams:** each with a role and the applications it gets, in order, and one default team.
- **Who sees which VMs:** a column of checkboxes per team. The core's VMs are grouped by kind
  (routers by ISP, core services, management), each enclave's by site and host; a
  group's checkbox assigns all of it. A host or site its enclave spec marks `crucible: true` starts
  in the default team (**from the spec**) until you change it.

Review shows the view's change, and deploying applies it last, once the VMs exist. Removing the view
from the draft removes it at the next deploy. The connection's service user is the view's admin;
who joins each team is Player's ([The Player view](deployments.md#the-player-view)).

## Configuration

The web application backend requires several environment variables. These are typically set in the systemd service file or sourced from the environment file.

### Required variables

| Variable | Description |
|----------|-------------|
| `ENVIRONMENT_ID` | Deployment prefix for resource isolation (must match Terraform's `TF_VAR_environment_id`) |
| `PROMETHEUS_URL` | Prometheus server URL for live router state |
| `PROXMOX_API_HOST` | Proxmox VE API host address |
| `PROXMOX_NODE` | Proxmox node name |
| `PROXMOX_API_TOKEN` | Proxmox API token (or use `PROXMOX_API_USER`/`PROXMOX_API_PASSWORD`) |

### Optional variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TFVARS_PATH` | `/workspaces/terraform/terraform.tfvars.json` | Path to generated Terraform variables |
| `SSH_KEY_PATH` | `/root/.ssh/terraform_key` | SSH private key for container access |
| `CODEBASE_PATH` | `/workspaces` | Root path to GraySpace codebase (for reset operations) |
| `CONSOLE_SESSION_TIMEOUT` | `1800` | Console session timeout in seconds |
| `STATE_POLL_INTERVAL` | `15` | Interval in seconds for polling router state |

### Running the backend

The backend is a FastAPI application served by Uvicorn:

```bash
cd grayspace-web/backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### Building the frontend

The frontend is a React application that builds to static files served by the backend:

```bash
cd grayspace-web/frontend
npm install
npm run build
```

The build output is served by FastAPI's static file mount at the root path.
