# ![grayspace icon](assets/greybox-app-dark.svg){: style="height:75px;width:75px"} GraySpace - An internet backbone for modeling, simulation, and experimentation

![GraySpace Map](map/graybackbone.svg)

GraySpace is a multi-provider simulation of the global internet suitable for modeling, simulation, and experimentation within cyber security and/or operational information environment scenarios.

## Key Features

- Realistic modeling of Internet Service Provider (ISP) architecture
- 15 ISPs across all global regions (three Tier 1, twelve Tier 2)
- BGP and OSPF routing protocols
- Peering (free) and transit (paid) relationships modeled, increasing path diversity
- Geographically relevant IP addressing
- Provides connectivity for internet services, corporate networks, residential networks, etc.
- Supports external connections to existing ranges or other infrastructure
- Integrates with CMU/SEI's [GHOSTS NPC Orchestration Framework](https://github.com/cmu-sei/GHOSTS) to provide simulated user traffic, interactive social media endpoints, and more
- Built on [Proxmox VE](https://www.proxmox.com/) using LXC
- Graph database and infrastructure-as-code tools make creating, using, and modifying GraySpace straightforward

## Web application

GraySpace includes a web application for real-time visualization and control of the simulated internet. See the [Web Application](web-app.md) documentation for details on the map view, traffic shaping, DNS management, client mobility, presets, and more.

## Internet service providers

See the [ISP Documentation](isps.md) for a list of all ISPs, their routers, networks, peers, and other details.

## Core services

See the [Core Services](services.md) documentation for a list of all core internet services provided by GraySpace, including DNS and Certificate Authorities

## Data model

For more information on the graph data model on which GraySpace relies, see the [graph model documentation](graph_model.md)
