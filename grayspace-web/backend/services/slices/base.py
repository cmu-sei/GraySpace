"""What the deployed base offers a slice, read from the deployed tfvars.

The tfvars hold only what is deployed (the scope already applied), with the vnet names
Terraform created, so they are what a slice attaches to.
"""
import ipaddress
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

# Management guests in the base state (terraform/main.tf); slice hostnames must avoid them. ghosts and
# grayspace-web stay reserved after the base lost its GHOSTS VM (contract 1.25) and web LXC: a
# deployment from before may keep one until its next apply.
MANAGEMENT_HOSTNAMES = {"prometheus", "grayspace-web", "ghosts"}

DEFAULT_DHCP_START = 20


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _address(value: Optional[str]) -> Optional[str]:
    return value.split("/")[0] if value else None


@dataclass
class BaseRouter:
    hostname: str
    isp: str
    asn: int
    public_block: Optional[str]
    location: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    customer_net: Optional[str]
    customer_bridge: Optional[str]
    loopback_ip: Optional[str]


@dataclass
class BaseServer:
    hostname: str
    ip: str
    router: Optional[str]
    role: str
    zones: List[str] = field(default_factory=list)
    ca_name: Optional[str] = None


@dataclass
class BaseContext:
    routers: Dict[str, BaseRouter]
    dns: Dict[str, BaseServer]
    cas: Dict[str, BaseServer]
    hostnames: Set[str]
    used_networks: List[ipaddress.IPv4Network]
    used_addresses: Dict[str, Set[str]]
    dhcp_start: int
    links: Dict[str, dict]
    base: Optional[dict]

    @property
    def tlds(self) -> Dict[str, str]:
        """{tld: TLD server hostname}"""
        return {z: s.hostname for s in self.dns.values() if s.role == "tld" for z in s.zones}

    @property
    def hosting(self) -> Dict[str, BaseServer]:
        return {h: s for h, s in self.dns.items() if s.role == "hosting"}

    @property
    def resolvers(self) -> List[BaseServer]:
        return [s for s in self.dns.values() if s.role == "resolver"]

    def static_range(self, router: str) -> List[str]:
        """The static addresses of a router's customer network: .2 up to the DHCP start."""
        net = ipaddress.IPv4Network(self.routers[router].customer_net)
        hosts = list(net.hosts())
        return [str(a) for a in hosts[1 : self.dhcp_start - 1]]

    def free_static(self, router: str, taken: Set[str]) -> List[str]:
        used = self.used_addresses.get(router, set()) | taken
        return [a for a in self.static_range(router) if a not in used]

    def nearest_resolver(self, router: str) -> Optional[BaseServer]:
        resolvers = self.resolvers
        r = self.routers.get(router)
        if not resolvers:
            return None
        if not r or r.latitude is None:
            return sorted(resolvers, key=lambda s: s.hostname)[0]

        def distance(s):
            home = self.routers.get(s.router)
            if not home or home.latitude is None:
                return float("inf")
            return haversine_km(r.latitude, r.longitude, home.latitude, home.longitude)

        return min(resolvers, key=lambda s: (distance(s), s.hostname))


def _customer_bridge(router: dict) -> Optional[str]:
    """The vnet of a router's customer network: its interface on that subnet with no OSPF area
    (scripts/generate_tfvars.py, "Process Customer Network Interfaces")."""
    net = router.get("customer_net")
    if not net:
        return None
    network = ipaddress.IPv4Network(net)
    for iface in router.get("interfaces") or []:
        if iface.get("ospf_area") is not None or not iface.get("cidr"):
            continue
        if ipaddress.IPv4Network(f"{iface['ip']}/{iface['cidr']}", strict=False) == network:
            return iface["bridge"]
    return None


def _zones(values) -> List[str]:
    return [z for v in values or [] for z in v.split(",") if z]


def links(tfvars: dict) -> Dict[str, dict]:
    """Router-to-router links keyed by the ID the API uses (r1:r2), built like
    topology_service.get_connections but from the tfvars alone: two routers on one vnet."""
    routers = tfvars.get("routers") or {}
    by_bridge: Dict[str, list] = {}
    for hostname, r in routers.items():
        for idx, iface in enumerate(r.get("interfaces") or []):
            if iface.get("bridge"):
                by_bridge.setdefault(iface["bridge"], []).append((hostname, idx, iface))
    out = {}
    for bridge, ends in by_bridge.items():
        if len(ends) != 2:
            continue
        (h1, i1, f1), (h2, i2, f2) = ends
        if f"{h2}:{h1}" in out or f"{h1}:{h2}" in out:
            continue
        link_type = "INTERNAL_LINK"
        for n in routers[h1].get("bgp_neighbors") or []:
            if n.get("hostname") == h2 and n.get("type") != "INTERNAL_LINK":
                link_type = n["type"]
                break
        out[f"{h1}:{h2}"] = {
            "id": f"{h1}:{h2}",
            "source": h1,
            "target": h2,
            "source_interface": f"eth{i1}",
            "target_interface": f"eth{i2}",
            "bridge": bridge,
            "type": link_type,
        }
    return out


def from_tfvars(tfvars: dict) -> BaseContext:
    routers = {}
    for hostname, r in (tfvars.get("routers") or {}).items():
        loc = r.get("location") or {}
        routers[hostname] = BaseRouter(
            hostname=hostname,
            isp=r.get("isp_name"),
            asn=r.get("asn"),
            public_block=r.get("public_block"),
            location=loc.get("name"),
            latitude=loc.get("latitude"),
            longitude=loc.get("longitude"),
            customer_net=r.get("customer_net"),
            customer_bridge=_customer_bridge(r),
            loopback_ip=r.get("loopback_ip"),
        )
    dns = {
        h: BaseServer(hostname=h, ip=_address(d.get("ip_address")), router=d.get("router"), role=d.get("role"), zones=_zones(d.get("zones")))
        for h, d in (tfvars.get("core_dns") or {}).items()
    }
    cas = {
        h: BaseServer(hostname=h, ip=_address(d.get("ip_address")), router=d.get("router"), role="ca", ca_name=d.get("ca_name"))
        for h, d in (tfvars.get("core_ca") or {}).items()
    }

    hostnames = set(MANAGEMENT_HOSTNAMES)
    for key in ("routers", "core_dns", "core_ca"):
        hostnames |= set(tfvars.get(key) or {})

    nets = set()
    for net in (tfvars.get("networks") or {}).values():
        if net.get("subnet"):
            nets.add(ipaddress.IPv4Network(net["subnet"], strict=False))
    for r in (tfvars.get("routers") or {}).values():
        for key in ("loopback_ip", "customer_net"):
            if r.get(key):
                nets.add(ipaddress.IPv4Network(r[key], strict=False))

    # Addresses the base assigns on each router's customer network: the router itself, and the
    # core services placed there.
    used: Dict[str, Set[str]] = {}
    for hostname, r in routers.items():
        if r.customer_net:
            used[hostname] = {str(next(ipaddress.IPv4Network(r.customer_net).hosts()))}
    for key in ("core_dns", "core_ca"):
        for entry in (tfvars.get(key) or {}).values():
            ip = _address(entry.get("ip_address"))
            if not ip:
                continue
            for hostname, r in routers.items():
                if r.customer_net and ipaddress.IPv4Address(ip) in ipaddress.IPv4Network(r.customer_net):
                    used[hostname].add(ip)

    return BaseContext(
        routers=routers,
        dns=dns,
        cas=cas,
        hostnames=hostnames,
        used_networks=sorted(nets, key=lambda n: (int(n.network_address), n.prefixlen)),
        used_addresses=used,
        dhcp_start=int(tfvars.get("customer_dhcp_start") or DEFAULT_DHCP_START),
        links=links(tfvars),
        base=tfvars.get("base"),
    )
