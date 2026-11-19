"""What the deployed base consists of and the names it serves: GET /api/v1/base/inventory and
GET /api/v1/base/dns.

Both are read from the deployed tfvars, which hold only what Terraform built (the scope already
applied), so a client sees the routers, hosts and addresses that exist rather than a base
version's CSVs. Slice instances are not in the inventory; their manifests (GET /instances/{id})
describe them. The DNS view is declarative: the zones Ansible templates (db.root.j2, db.tld.j2),
plus the domains and records the overlay (active_mutations.json) and live instances created at
runtime, the same ones quick reset and destroy undo.
"""
import asyncio
import ipaddress
import logging
from typing import Dict, List, Optional

from models.base import (
    DnsRecord,
    DnsView,
    DnsZone,
    DnsZoneServer,
    Inventory,
    InventoryCoreService,
    InventoryInterface,
    InventoryLink,
    InventoryRouter,
    SnapshotIsp,
)
from services.base_service import version_ref
from services.slices import base

logger = logging.getLogger(__name__)

# The TTLs of the zone files: $TTL in db.root.j2 and db.tld.j2, and in the zones dns_service writes.
BASE_TTL = 86400
DOMAIN_TTL = 3600


def guest_statuses(containers: Optional[List[dict]]) -> Optional[Dict[str, str]]:
    """{hostname: Proxmox status}, or None when Proxmox gave no answer. The client returns [] when
    listing fails, which can't be told apart from nothing deployed (base_service._router_health)."""
    if not containers:
        return None
    return {c["name"]: c.get("status", "unknown") for c in containers}


async def proxmox_statuses() -> Optional[Dict[str, str]]:
    from services.proxmox_client import proxmox_client

    try:
        return guest_statuses(await asyncio.to_thread(proxmox_client.get_deployment_containers))
    except Exception as e:
        logger.warning(f"Proxmox unavailable for the inventory: {e}")
        return None


def guest_kinds(tfvars: dict) -> Dict[str, str]:
    """{hostname: router or core-service} for every base guest in the tfvars."""
    kinds = {h: "router" for h in tfvars.get("routers") or {}}
    for group in ("core_dns", "core_ca"):
        kinds.update({h: "core-service" for h in tfvars.get(group) or {}})
    return kinds


def _status(statuses: Optional[Dict[str, str]], hostname: str) -> Optional[str]:
    return None if statuses is None else statuses.get(hostname, "missing")


def _prefix_len(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_inventory(tfvars: dict, statuses: Optional[Dict[str, str]], link_states: Dict[str, dict]) -> Inventory:
    routers = tfvars.get("routers") or {}

    def router_place(hostname):
        r = routers.get(hostname) or {}
        return r.get("isp_name"), (r.get("location") or {}).get("name")

    isps: Dict[str, SnapshotIsp] = {}
    inventory_routers = []
    for hostname, r in routers.items():
        if r.get("isp_name") and r["isp_name"] not in isps:
            isps[r["isp_name"]] = SnapshotIsp(name=r["isp_name"], asn=r.get("asn"), public_block=r.get("public_block"))
        location = r.get("location") or {}
        inventory_routers.append(InventoryRouter(
            hostname=hostname,
            isp=r.get("isp_name"),
            asn=r.get("asn"),
            public_block=r.get("public_block"),
            location=location.get("name"),
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
            roles=r.get("roles") or [],
            ospf_areas=r.get("ospf_areas") or [],
            loopback_ip=r.get("loopback_ip"),
            customer_net=r.get("customer_net"),
            interfaces=[
                InventoryInterface(name=f"eth{i}", bridge=f["bridge"], ip=f["ip"], prefix_len=_prefix_len(f.get("cidr")), ospf_area=f.get("ospf_area"))
                for i, f in enumerate(r.get("interfaces") or []) if f.get("bridge") and f.get("ip")
            ],
            status=_status(statuses, hostname),
        ))

    networks = tfvars.get("networks") or {}

    def interface_ip(hostname, interface):
        ifaces = (routers.get(hostname) or {}).get("interfaces") or []
        index = int(interface[3:])
        return ifaces[index].get("ip") if index < len(ifaces) else None

    links = []
    for link_id, link in base.links(tfvars).items():
        net = networks.get(link["bridge"]) or {}
        links.append(InventoryLink(
            id=link_id, source=link["source"], target=link["target"], type=link["type"], bridge=link["bridge"],
            subnet=net.get("subnet"),
            source_interface=link["source_interface"], source_ip=interface_ip(link["source"], link["source_interface"]),
            target_interface=link["target_interface"], target_ip=interface_ip(link["target"], link["target_interface"]),
            latency_ms=net.get("latency_ms"),
            state=(link_states.get(link_id) or {}).get("state", "unknown"),
        ))

    core = []
    for group in ("core_dns", "core_ca"):
        for hostname, s in (tfvars.get(group) or {}).items():
            isp, location = router_place(s.get("router"))
            core.append(InventoryCoreService(
                hostname=hostname, role=s.get("role") or ("ca" if group == "core_ca" else "unknown"), ip=base._address(s.get("ip_address")),
                subnet=s.get("subnet"), router=s.get("router"), isp=isp, location=location, zones=base._zones(s.get("zones")),
                ca_name=s.get("ca_name"), status=_status(statuses, hostname),
            ))

    return Inventory(
        version=version_ref(tfvars),
        customer_dhcp_start=int(tfvars.get("customer_dhcp_start") or base.DEFAULT_DHCP_START),
        isps=list(isps.values()),
        routers=inventory_routers,
        links=links,
        core_services=core,
    )


def fqdn(name: str, zone: str) -> str:
    """A name as written in a zone file (relative, @ or absolute), fully qualified with the trailing dot."""
    origin = "." if zone.strip(".") == "" else zone.strip(".").lower() + "."
    name = name.strip()
    if name in ("", "@"):
        return origin
    if name.endswith("."):
        return name.lower()
    return f"{name.lower()}.{origin}" if origin != "." else f"{name.lower()}."


def _zone_key(zone: str) -> str:
    return zone.strip(".").lower() or "."


def build_dns(tfvars: dict, overlay_domains: List[dict], overlay_records: List[dict], instances: List) -> DnsView:
    ctx = base.from_tfvars(tfvars)
    servers = sorted(ctx.dns.values(), key=lambda s: s.hostname)
    zones: Dict[str, DnsZone] = {}

    def server(hostname) -> DnsZoneServer:
        s = ctx.dns.get(hostname)
        return DnsZoneServer(hostname=hostname, ip=s.ip if s else None)

    def add(zone, name, rtype, data, ttl, origin="base", instance=None):
        zones[zone].records.append(DnsRecord(name=name, type=rtype, data=data, ttl=ttl, origin=origin, instance=instance))

    roots = [s for s in servers if s.role == "root-dns"]
    tlds = [(z.lower(), s) for s in servers if s.role == "tld" for z in s.zones]
    if roots:
        zones["."] = DnsZone(name=".", kind="root", servers=[server(s.hostname) for s in roots], origin="base", records=[])
        for s in roots:
            add(".", ".", "NS", f"{s.hostname}.root-servers.net.", BASE_TTL)
        for s in roots:
            add(".", f"{s.hostname}.root-servers.net.", "A", s.ip, BASE_TTL)
        for tld, s in tlds:
            add(".", f"{tld}.", "NS", f"ns1.{tld}.", BASE_TTL)
            add(".", f"ns1.{tld}.", "A", s.ip, BASE_TTL)
    for tld, s in sorted(tlds, key=lambda t: t[0]):
        if tld in zones:
            zones[tld].servers.append(server(s.hostname))
            continue
        zones[tld] = DnsZone(name=tld, kind="tld", servers=[server(s.hostname)], origin="base", records=[])
        add(tld, f"{tld}.", "NS", f"ns1.{tld}.", BASE_TTL)
        add(tld, f"ns1.{tld}.", "A", s.ip, BASE_TTL)

    def add_domain(domain, hosting, origin, instance=None):
        """A domain dns_service.create_domain made: the delegation in its TLD zone, and its own zone."""
        domain = _zone_key(domain)
        hosting_ip = server(hosting).ip if hosting else None
        tld = domain.rsplit(".", 1)[-1]
        zones[domain] = DnsZone(name=domain, kind="domain", servers=[server(hosting)] if hosting else [], origin=origin,
                                instance=instance, records=[])
        for zone, ttl in ((tld, BASE_TTL), (domain, DOMAIN_TTL)):
            if zone not in zones:
                continue
            add(zone, f"{domain}.", "NS", f"ns1.{domain}.", ttl, origin, instance)
            if hosting_ip:
                add(zone, f"ns1.{domain}.", "A", hosting_ip, ttl, origin, instance)
        return domain

    def add_record(rec_server, zone, name, rtype, data, ttl, origin, instance=None):
        key = _zone_key(zone)
        if key not in zones:
            # A record in a zone nothing above made, e.g. one created on a server by hand
            zones[key] = DnsZone(name=key, kind="domain", servers=[server(rec_server)], origin=origin, instance=instance, records=[])
        add(key, fqdn(name, key), rtype.upper(), data, int(ttl or DOMAIN_TTL), origin, instance)

    for d in overlay_domains:
        domain = add_domain(d["domain"], d.get("hosting_server"), "overlay")
        if d.get("ip_address"):
            add(domain, f"{domain}.", "A", d["ip_address"], DOMAIN_TTL, "overlay")
            if d.get("create_www", True):
                add(domain, f"www.{domain}.", "CNAME", f"{domain}.", DOMAIN_TTL, "overlay")
        if d.get("mx_records"):
            add(domain, f"{domain}.", "MX", d["mx_records"], DOMAIN_TTL, "overlay")
        for rec in d.get("additional_records") or []:
            if rec.get("data"):
                add_record(d.get("hosting_server"), domain, rec.get("name", "@"), rec.get("type", "A"), rec["data"], rec.get("ttl"), "overlay")
    for inst in instances:
        for d in inst.domains:
            add_domain(d.domain, d.hosting_server, "instance", inst.id)
    # Records after every domain: the overlay may add records to an instance's domain
    for r in overlay_records:
        add_record(r.get("server"), r["zone"], r["name"], r["type"], r["data"], r.get("ttl"), "overlay")
    for inst in instances:
        for r in inst.records:
            add_record(r.server, r.zone, r.name, r.type, r.data, r.ttl, "instance", inst.id)

    order = {"root": 0, "tld": 1, "domain": 2}
    return DnsView(
        resolvers=[DnsZoneServer(hostname=s.hostname, ip=s.ip) for s in servers if s.role == "resolver"],
        zones=sorted(zones.values(), key=lambda z: (order[z.kind], z.name)),
    )
