"""Models for the deployed base (GET /api/v1/base)."""
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class BaseVersionRef(BaseModel):
    """The base version a deployment's tfvars were generated from."""

    name: Optional[str] = Field(..., description="Shipped base version, e.g. grayspace/2026.11. Null when the CSVs came from another directory")
    csv_sha256: str = Field(..., description="sha256 of the base version's CSVs; identifies the version's content")
    scope: str = Field(..., description="Deployment scope: full or tier1")


class RouterHealth(BaseModel):
    """Routers in the deployed base, as Proxmox sees them."""

    expected: int = Field(..., description="Routers in the deployed tfvars")
    running: Optional[int] = Field(..., description="Expected routers Proxmox reports as running. Null when Proxmox is unavailable")
    not_running: List[str] = Field(default_factory=list, description="Expected routers that are missing or not running")


class BgpPeer(BaseModel):
    router: str
    peer_address: str
    state: str = Field(..., description="down, shutdown or unknown")


class BgpHealth(BaseModel):
    """BGP sessions across all routers, from Prometheus (frr_exporter)."""

    total: Optional[int] = Field(..., description="BGP sessions reported. Null when Prometheus is unavailable or reports none")
    established: Optional[int] = Field(..., description="Sessions in the up state")
    not_established: List[BgpPeer] = Field(default_factory=list)


class DeployedBase(BaseModel):
    """The deployed base version and its health."""

    version: Optional[BaseVersionRef] = Field(..., description="Null when the tfvars predate version recording; re-run task parse")
    status: Literal["not_deployed", "healthy", "degraded", "unknown"] = Field(
        ..., description="not_deployed: no base tfvars (nothing deployed, or destroyed). healthy: every router running and every BGP session up. "
        "unknown: Proxmox or Prometheus gave no answer"
    )
    routers: RouterHealth
    bgp: BgpHealth


class VersionSummary(BaseModel):
    isps: int
    locations: int
    routers: int
    links: int
    core_services: int
    clients: int = Field(0, description="Deprecated (contract 1.18): base clients were retired; always 0", deprecated=True)


class BaseVersion(BaseModel):
    """A shipped base version under bases/."""

    name: str = Field(..., description="e.g. grayspace/2026.11")
    csv_sha256: Optional[str] = Field(..., description="Null when the version fails to load")
    deployed: bool = Field(..., description="Whether this is the version in the deployed tfvars (matched by csv_sha256)")
    summary: Optional[VersionSummary] = Field(..., description="Null when the version fails to load")
    problems: List[str] = Field(default_factory=list, description="Load errors, if the version's CSVs are invalid")


class SnapshotIsp(BaseModel):
    name: str
    asn: Optional[int]
    public_block: Optional[str]


class SnapshotLocation(BaseModel):
    name: str
    latitude: Optional[float]
    longitude: Optional[float]


class SnapshotRouter(BaseModel):
    hostname: str
    isp: Optional[str]
    location: Optional[str]
    roles: List[str]
    ospf_areas: List[str]
    loopback_ip: Optional[str]
    customer_net: Optional[str]


class SnapshotLink(BaseModel):
    start: str
    end: str
    type: str = Field(..., description="INTERNAL_LINK, PEERS_WITH or PROVIDES_TRANSIT_TO")
    subnet: Optional[str]
    ospf_area: Optional[str]


class BaseVersionDetail(BaseVersion):
    """A base version and its topology snapshot: what slice compatibility is checked against."""

    isps: List[SnapshotIsp] = Field(default_factory=list)
    locations: List[SnapshotLocation] = Field(default_factory=list)
    routers: List[SnapshotRouter] = Field(default_factory=list)
    links: List[SnapshotLink] = Field(default_factory=list)


# --- GET /api/v1/base/inventory: what the deployed base consists of ---

def _status():
    return Field(..., description="Proxmox status: running, stopped, missing (not in Proxmox), or null when Proxmox is unavailable")


class InventoryInterface(BaseModel):
    name: str = Field(..., description="eth0, eth1, … in the order Terraform creates them")
    bridge: str = Field(..., description="The base vnet (without environment_id), e.g. v213")
    ip: str
    prefix_len: Optional[int]
    ospf_area: Optional[str] = Field(..., description="Null on the customer network")


class InventoryRouter(BaseModel):
    hostname: str
    isp: Optional[str]
    asn: Optional[int]
    public_block: Optional[str]
    location: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    roles: List[str]
    ospf_areas: List[str]
    loopback_ip: Optional[str]
    customer_net: Optional[str] = Field(..., description="The router's customer network; the router is .1 on it")
    interfaces: List[InventoryInterface]
    status: Optional[str] = _status()


class InventoryLink(BaseModel):
    id: str = Field(..., description="r1:r2, as in /map and instance actions")
    source: str
    target: str
    type: str = Field(..., description="INTERNAL_LINK, PEERS_WITH or PROVIDES_TRANSIT_TO")
    bridge: str
    subnet: Optional[str]
    source_interface: str
    source_ip: Optional[str]
    target_interface: str
    target_ip: Optional[str]
    latency_ms: Optional[float] = Field(..., description="The latency the base sets on the link (tc), from the distance")
    state: str = Field(..., description="up, down or unknown: BGP, OSPF and interface state from Prometheus")


class InventoryClient(BaseModel):
    """Deprecated (contract 1.18): a host the base placed on a router's customer network (clients.csv). Base
    clients were retired; hosts on the simulated internet are enclaves."""

    hostname: str
    type: Literal["lxc", "vm", "oci"]
    category: Optional[str] = Field(..., description="e.g. social, other")
    feature: Optional[str] = Field(..., description="desktop, ubuntu-desktop or null (/capabilities host_features)")
    image: Optional[str] = Field(..., description="The image source the base deployed")
    ip: Optional[str] = Field(..., description="Null when the client takes a DHCP lease, e.g. after a move")
    subnet: Optional[str]
    router: Optional[str]
    isp: Optional[str]
    location: Optional[str]
    status: Optional[str] = _status()


class InventoryCoreService(BaseModel):
    """A DNS server or CA the base hosts on a router (core_dns.csv, core_ca.csv)."""

    hostname: str
    role: str = Field(..., description="root-dns, tld, hosting, resolver or ca")
    ip: Optional[str]
    subnet: Optional[str]
    router: Optional[str]
    isp: Optional[str]
    location: Optional[str]
    zones: List[str] = Field(default_factory=list, description="TLD servers: the TLDs they serve. Root servers: [\".\"]")
    ca_name: Optional[str] = Field(None, description="CAs: e.g. GrayTrust")
    status: Optional[str] = _status()


class Inventory(BaseModel):
    """The deployed base: what Terraform built from the tfvars (the scope applied), with live state."""

    version: Optional[BaseVersionRef]
    customer_dhcp_start: int = Field(..., description="Customer networks lease from this host number; below it are static addresses")
    isps: List[SnapshotIsp]
    routers: List[InventoryRouter]
    links: List[InventoryLink]
    clients: List[InventoryClient] = Field(default_factory=list, description="Deprecated (contract 1.18): base clients "
                                           "were retired; always empty", deprecated=True)
    core_services: List[InventoryCoreService]


# --- GET /api/v1/base/dns: every zone and record the simulated DNS serves ---

Origin = Literal["base", "overlay", "instance"]
ORIGIN_DESCRIPTION = ("base: written by task configure. overlay: created at runtime in the web UI or by a base preset; "
                      "quick reset removes it. instance: created by a slice instance; destroying it removes it")


class DnsRecord(BaseModel):
    name: str = Field(..., description="Fully qualified, with the trailing dot, e.g. www.dailychronicle.com.")
    type: str
    data: str
    ttl: int
    origin: Origin = Field(..., description=ORIGIN_DESCRIPTION)
    instance: Optional[str] = Field(None, description="The instance that owns the record")


class DnsZoneServer(BaseModel):
    hostname: str
    ip: Optional[str]


class DnsZone(BaseModel):
    name: str = Field(..., description="'.', a TLD (com) or a domain (dailychronicle.com)")
    kind: Literal["root", "tld", "domain"]
    servers: List[DnsZoneServer] = Field(..., description="The authoritative servers")
    origin: Origin = Field(..., description=ORIGIN_DESCRIPTION)
    instance: Optional[str] = None
    records: List[DnsRecord] = Field(..., description="Every record except the SOA, including those of other origins in this zone (delegations)")


class DnsView(BaseModel):
    """The simulated DNS: the zones the base's root, TLD and hosting servers serve, and resolvers."""

    resolvers: List[DnsZoneServer] = Field(..., description="Recursive resolvers hosts can use")
    zones: List[DnsZone]


class DeployBaseRequest(BaseModel):
    """What to deploy. Omitted fields keep the deployed base's."""

    model_config = {"json_schema_extra": {"examples": [{"version": "grayspace/2026.11", "scope": "tier1"}]}}

    version: Optional[str] = Field(None, description="Shipped base version, e.g. grayspace/2026.11. Required when nothing is deployed")
    scope: Optional[Literal["full", "tier1"]] = Field(None, description="Deployment scope; full when nothing is deployed")
    configure: bool = Field(True, description="Run Ansible after the apply. False stops after Terraform")
    mgmt_gateway: Optional[str] = Field(
        None, description="The management network's gateway, for hosts deployed before configure recorded it (CLAUDE.md, Ansible Configuration)"
    )


class ConfigureBaseRequest(BaseModel):
    mgmt_gateway: Optional[str] = Field(None, description="As for deploy")


class PreflightCheck(BaseModel):
    name: str = Field(..., description="e.g. proxmox-api, storage, bridges")
    status: Literal["ok", "warning", "error", "unchecked"]
    message: str


class Preflight(BaseModel):
    """Whether this site can deploy a base. ready is false when any check is an error."""

    ready: bool
    version: Optional[str] = None
    scope: Optional[str] = None
    checks: List[PreflightCheck]
