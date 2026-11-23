# GraySpace Graph Data Model

The project's network topology, ISP data, and geographic information are defined in CSVs. Each shipped core version is a directory under `bases/`, e.g. `bases/grayspace/2026.11`. They describe a graph of nodes and relationships, and `task parse` reads them directly (`scripts/topology.py`) to generate `terraform/terraform.tfvars.json`. No database is involved.

The CSVs use the neo4j-admin header format, which declares each column's name and type: `hostname:ID(Router)` is a node ID, `:START_ID(Router)` and `:END_ID(Location)` are the two ends of a relationship, and `asn:int`, `latitude:FLOAT` and `roles:string[]` are typed properties. Array columns are split on `;`, so a value such as `"0,10"` is a single element. Empty fields mean the property is absent.

The files are kept in a canonical form: UTF-8 without a byte-order mark, LF line endings, a final newline, and quotes only where a value contains a comma. Hand edits don't have to follow it, but anything written by the tooling (`topology.dump()`) does, and the tests check that the shipped files are canonical.

Loading validates the files: a relationship that names an unknown node, a duplicate ID, or a value that doesn't match its column type fails `task parse` with every problem listed.

A core version has no hosts of its own besides its DNS servers and CAs: what attaches to it is an [enclave](slices.md). Core versions up to `grayspace/2026.10` also had clients (`clients.csv`, `client_of.csv`); they were retired with that version, and a directory that still has those files fails to load.

## Nodes

Nodes represent the core entities of the simulation: routers, ISPs, locations, core DNS servers and CAs, and OS images.

`routers.csv`

This file defines the `Router` nodes. Each row represents a single router.

- Node Label: `Router`

- Unique ID: `hostname`

- Properties:
    - `hostname`: The unique identifier for the router (e.g., lum-sfr).

    - `roles`: A list of special roles for the router (e.g., ABR, RR).

    - `ospf_areas`: A list of OSPF areas this router belongs to (e.g., ["0", "10"]).

    - `loopback_ip`: The router's loopback IP, used as its BGP router-id.

    - `customer_net`: Any customer/stub networks advertised by this router.

`isps.csv`

This file defines the ISP nodes (Internet Service Providers).

- Node Label: `ISP`

- Unique ID: `name`

- Properties:
    - `name`: The unique name of the ISP (e.g., Lumen).

    - `public_block`: The main public IP block allocated to this ISP.

    - `asn`: The Autonomous System Number for the ISP.

`core_dns.csv`

This file defines CoreService nodes for the DNS infrastructure. Each row represents a DNS server in the simulated delegation chain.

- Node Label: `CoreService`

- Unique ID: `hostname`

- Properties:
    - `hostname`: The unique identifier for this DNS server (e.g., root-a, dns-gtld, dns-cloudflare).

    - `image`: An image name referencing an `Image` node (e.g., `alpine-3.22`).

    - `role`: The DNS role this server fulfills. One of:
        - `root-dns` — Root DNS server, authoritative for the `.` zone.
        - `tld` — TLD server, authoritative for top-level domains (e.g., com, net, org). Contains stub zones (SOA + NS records only) that delegate to hosting providers.
        - `hosting` — DNS hosting provider (e.g., Cloudflare, Namecheap, AWS). Starts with an empty zone directory (`/etc/bind/zones.d/`), ready for dynamic zone file creation via the webapp.
        - `resolver` — Recursive resolver (e.g., Google DNS, Quad9). Uses root hints to resolve queries through the delegation chain.

    - `zones`: A comma-separated list of zones this server is authoritative for. Empty for `hosting` and `resolver` roles.

`core_ca.csv`

This file defines CoreService nodes for the Certificate Authority infrastructure. Each row represents an independent CA running step-ca.

- Node Label: `CoreService`

- Unique ID: `hostname`

- Properties:
    - `hostname`: The unique identifier for this CA (e.g., ca-graytrust, ca-securesign).

    - `image`: An image name referencing an `Image` node (e.g., `alpine-3.22`).

    - `role`: Always `ca` for certificate authority nodes.

    - `ca_name`: The brand identity of the CA (e.g., GrayTrust, SecureSign). Displayed in the web UI.

`os_images.csv`

This file defines Image nodes that map image names to download URLs. Used to resolve image references in `core_dns.csv` and `core_ca.csv`.

- Node Label: `Image`

- Unique ID: `name`

- Properties:
    - `name`: The unique image name (e.g., `alpine-3.22`).

    - `url`: The download URL for the image template or disk.

    - `type`: The deployment mechanism for this image (`lxc`). Core services are LXC containers made from the GraySpace Alpine image (`images/images.csv`), whatever this names.

`locations.csv`

This file defines the Location nodes.

- Node Label: `Location`

- Unique ID: `name`

- Properties:
    - `name`: The unique geographic location name (e.g., San Francisco).

    - `latitude`: The geographic latitude coordinate.

    - `longitude`: The geographic longitude coordinate.

## Relationships

Relationships connect the nodes to form the complete network graph.

`connections.csv`

This file defines the physical and logical links between routers.

- Schema: `(Router)-[link]->(Router)`

- Start Node: `Router` (identified by `hostname`)

- End Node: `Router` (identified by `hostname`)

- Relationship Type:
    - `INTERNAL_LINK`: Connection between routers operated by the same ISP
    - `PEERS_WITH`: Peering connection (free exchange) between routers operated by two different ISPs
    - `PROVIDES_TRANSIT_TO`: Connection between routers operated by two different ISPs where ISP2 is paying ISP1 for access to internet routes

- Properties:
    - `subnet`: The CIDR block for the point-to-point link.

    - `start_ip`: The IP address for the start router's interface.

    - `end_ip`: The IP address for the end router's interface.

    - `ospf_area`: The OSPF area this link participates in (if any).

#### Routing Policy

The eBGP link type determines the route-maps rendered by `ansible/templates/frr.conf.j2`. For `PROVIDES_TRANSIT_TO`, the start router is the **provider** and the end router is the **customer**. Both ends of a `PEERS_WITH` link are **peers**. Each router tags routes with communities of the form `<asn>:N`:

| Learned from | Community | Local-pref | Inbound filters | Exported to |
| :----------- | :-------- | :--------- | :-------------- | :---------- |
| Originated locally | `<asn>:10` | — | — | Customers, peers, providers |
| Customer | `<asn>:100` | 200 | Bogons/too specific (> /24), AS-path must be only the customer's ASN | Customers, peers, providers |
| Peer | `<asn>:200` | 100 | Bogons/too specific (> /24) | Customers only |
| Provider | `<asn>:300` | 50 | Bogons/too specific (> /24) | Customers only |

As a result, Tier-2 ISPs with more than one provider never pass routes between their providers, and peers only exchange their own and their customers' routes. Note that the customer AS-path filter assumes customers have no downstream customers of their own.

`operated_by.csv`

This file links each router to its parent ISP.

- Schema: `(Router)-[:OPERATED_BY]->(ISP)`

- Start Node: `Router` (identified by `hostname`)

- End Node: `ISP` (identified by `name`)

- Relationship Type: `OPERATED_BY`

`located_in.csv`

This file links each router to its physical location.

- Schema: `(Router)-[:LOCATED_IN]->(Location)`

- Start Node: `Router` (identified by `hostname`)

- End Node: `Location` (identified by `name`)

- Relationship Type: `LOCATED_IN`

`hosted_by.csv`

This file links each core service (DNS server or CA) to the router that hosts it.

- Schema: `(CoreService)-[:HOSTED_BY]->(Router)`

- Start Node: `CoreService` (identified by `hostname`)

- End Node: `Router` (identified by `hostname`)

- Relationship Type: `HOSTED_BY`

- Properties:
    - `subnet`: The CIDR block for the service network.

    - `service_ip`: The IPv4 address for the service's interface in CIDR notation (e.g., 198.41.0.4/24).

## Core Service Architecture

### DNS Delegation Chain

The core DNS infrastructure models a realistic delegation hierarchy:

```
Root Servers (root-dns)
  └── TLD Servers (tld)
        └── Hosting Providers (hosting)

Resolvers (resolver) — walk the chain via root hints
```

1. **Root servers** (e.g., root-a, root-k, root-m) are authoritative for `.` and delegate TLDs to the appropriate TLD servers.
2. **TLD servers** (e.g., dns-gtld, dns-gov, dns-ru) are authoritative for top-level domains and contain stub zones with SOA and NS records that delegate to hosting providers.
3. **Hosting providers** (e.g., dns-cloudflare, dns-aws, dns-namecheap) start with empty zone directories. The GraySpace webapp can SSH into these servers to dynamically create zone files for second-level domains.
4. **Resolvers** (e.g., dns-google, dns-quad9) are recursive resolvers that use root hints to walk the delegation chain.

### Certificate Authorities

Four independent CAs (ca-graytrust, ca-securesign, ca-netcert, ca-cyberlock) are distributed globally. Each runs step-ca and issues certificates on request. See [Core Services](services.md) for usage details.
