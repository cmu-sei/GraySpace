<p align="center">
  <img src="docs/assets/greybox-app-dark.svg" alt="GraySpace" width="128">
</p>

<h2 align="center">GraySpace Development Environment</h2>

<p align="center"><em>Internet Backbone Simulation & Modeling</em></p>

---

# Overview

This project creates a multi-provider simulation of the global internet. It uses BGP and OSPF routing to model peering and transit relationships between different tiers of internet service providers, while also providing connectivity for internet services and customer networks.

![GraySpace Map](docs/map/graybackbone.svg)

Terraform and Ansible are used to deploy and configure Alpine Linux running [FRR](https://frrouting.org/) based on a topology defined in CSV files.

Building GraySpace requires this repo and supported virtualization/containerization infrastructure.

Supported infrastructure:

- Proxmox (LXC)
- VMWare via GraySpace Appliance (being reworked to run the GraySpace container)

### Features

- **15 ISPs** (three Tier 1, twelve Tier 2) with realistic BGP peering and transit relationships
- **130+ routers** running FRR with OSPF and BGP, deployed as LXC containers
- **Web management application** — interactive map, real-time link monitoring, and configuration
- **DNS infrastructure** — full delegation chain (root, TLD, hosting, resolver) with dynamic domain creation
- **Certificate Authorities** — 4 independent CAs (step-ca) for issuing SSL/TLS certificates
- **Desktops** — browser-accessible Fluxbox desktops via noVNC for exercise participants, as enclave hosts (`grayspace-desktop`)
- **Presets** — save runtime changes (traffic shaping, DNS, enclave actions) as named presets of a core version or an enclave, apply them from the map or the build
- **Traffic shaping** — configurable bandwidth and latency on any link
- **Slices** — enterprise sites, internet hosts, DNS names and certificates built at the edge of the core from a spec, by the CLI, the web UI or GHOSTS through the [Terrain API](openapi/grayspace-v1.json) ([docs](docs/slices.md))

### Getting Started

| Step | Command | Description |
|------|---------|-------------|
| 1 | `task env` | Configure environment variables (first run only) |
| 2 | `task parse` | Generate Terraform variables from the topology CSVs |
| 3 | `task plan` | Preview infrastructure changes |
| 4 | `task deploy` | Apply infrastructure + configure with Ansible |

### Development

Press **F5** in VS Code (or `task dev`) to run the web application's API and UI with a simulated
deployment, simulated router metrics and Prometheus, with no Proxmox needed. See
[Development](docs/development.md).

### Common Tasks

| Command | Description |
|---------|-------------|
| `task --list` | Show all available tasks |
| `task configure` | Re-run Ansible configuration |
| `task tunnel` | Start sshuttle tunnel to Proxmox |
| `task destroy` | Tear down all resources |
| `task lint` | Lint Ansible playbooks |
| `task slice:up SLICE=examples/slices/meridian-bank.yaml` | Build a slice instance (`slice:validate`, `slice:plan`, `slice:status`, `slice:down` too) |

### Architecture

```
Topology CSVs     -->  generate_tfvars.py  -->  terraform.tfvars.json
                                                       |
                                                 Terraform (LXC on Proxmox)
                                                       |
                                                 Ansible (FRR/BGP/OSPF)
```

### Key Paths

- `bases/` — Shipped core versions: topology CSVs for routers, ISPs, connections, services
- `terraform/` — Infrastructure as code
- `ansible/` — Configuration management
- `grayspace-web/` — Web management application (FastAPI backend + React frontend) and the Terrain API
- `examples/slices/` — Example slice specs
- `images/` — The image catalog slice hosts use
- `openapi/` — The Terrain API contract, and the slice spec and events schemas
- `scripts/` — Tooling (parser, env setup)
- `docs/` — Documentation and graph model schema

### Resources

- [Full Documentation](https://cmu-sei.github.io/grayspace)
- [Graph Model Schema](docs/graph_model.md)
- [Environment Template](scripts/environment.template)
