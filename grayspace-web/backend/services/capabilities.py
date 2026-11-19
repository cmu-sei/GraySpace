"""What slices can use on a base version: /capabilities and /attachment-points."""
import asyncio
import ipaddress
from typing import List, Optional

from config import settings
from models.base import BaseVersionRef
from models.capabilities import (
    AddressSpace,
    AttachmentPoint,
    Capabilities,
    Capacity,
    CertificateAuthority,
    DnsCapabilities,
    DnsServer,
    IspCapability,
)
from services import base_versions, player
from services.proxmox_client import proxmox_client
from services.slices import catalog, environment, store
from services.slices.plan import SLICE_FEATURES

def _one(topo, node_id, rel_type):
    rels = topo.out(node_id, rel_type)
    return rels[0].end if rels else None


def _server(topo, node, cls, **extra):
    hosted = topo.out(node.id, "HOSTED_BY")
    router = hosted[0].end if hosted else None
    ip = (hosted[0].props.get("service_ip") or "").split("/")[0] if hosted else ""
    return cls(hostname=node.id, ip=ip or None, router=router, isp=_one(topo, router, "OPERATED_BY"), **extra)


def in_scope(isp, scope):
    """Whether an ISP is deployed at a scope: tier1 deploys only the Tier 1 ISPs."""
    return scope == "full" or isp in base_versions.topology().TIER1_ISPS


def blockstore() -> Optional[str]:
    """The block storage guests' disks go on: the backend setting, else the jobs' environment."""
    return settings.proxmox_blockstore or environment.tf_var("proxmox_blockstore")


async def capacity() -> Optional[Capacity]:
    status = await asyncio.to_thread(proxmox_client.get_node_status)
    if not status:
        return None
    memory = status.get("memory") or {}
    storage_name = blockstore()
    storage = await asyncio.to_thread(proxmox_client.get_storage_status, storage_name) if storage_name else None
    return Capacity(
        node=settings.proxmox_node,
        cpus=(status.get("cpuinfo") or {}).get("cpus"),
        cpu_usage=status.get("cpu"),
        memory_total=memory.get("total"),
        # available counts the page cache Linux gives back; free doesn't, and image downloads fill
        # the cache. Older Proxmox reports only free
        memory_free=memory.get("available", memory.get("free")),
        storage=storage_name,
        storage_total=(storage or {}).get("total"),
        storage_free=(storage or {}).get("avail"),
    )


async def capabilities(name, directory, topo, scope) -> Capabilities:
    t = base_versions.topology()
    core = [cs for cs in topo.of("CoreService") if in_scope(_one(topo, _one(topo, cs.id, "HOSTED_BY"), "OPERATED_BY"), scope)]
    dns = [cs for cs in core if cs.props.get("role") != "ca"]
    isps = []
    for isp in topo.of("ISP"):
        if not in_scope(isp.id, scope):
            continue
        routers = [r.start for r in topo.into(isp.id, "OPERATED_BY")]
        locations = sorted({loc for loc in (_one(topo, r, "LOCATED_IN") for r in routers) if loc})
        isps.append(IspCapability(name=isp.id, asn=isp.props.get("asn"), locations=locations))
    jobs, why_not = environment.jobs_available()
    base_jobs, base_why_not = environment.base_jobs_available()
    return Capabilities(
        base=BaseVersionRef(name=name, csv_sha256=t.csv_sha256(directory), scope=scope),
        images=sorted(catalog.images().values(), key=lambda i: i.name),
        roles=list(catalog.roles().values()),
        host_features=[],
        enclave_features=SLICE_FEATURES,
        slice_features=SLICE_FEATURES,
        jobs=jobs,
        jobs_unavailable_reason=why_not,
        base_jobs=base_jobs,
        crucible=environment.crucible_team_id() is not None or bool((player.applied() or {}).get("view_id")),
        base_jobs_unavailable_reason=base_why_not,
        management_subnet=settings.mgmt_subnet,
        enclave_pool=settings.slice_pool,
        slice_pool=settings.slice_pool,
        dns=DnsCapabilities(
            tlds=sorted({z for cs in dns if cs.props.get("role") == "tld" for v in cs.props.get("zones") or [] for z in v.split(",")}),
            hosting=[_server(topo, cs, DnsServer) for cs in dns if cs.props.get("role") == "hosting"],
            resolvers=[_server(topo, cs, DnsServer) for cs in dns if cs.props.get("role") == "resolver"],
        ),
        cas=[_server(topo, cs, CertificateAuthority, name=cs.props.get("ca_name")) for cs in core if cs.props.get("role") == "ca"],
        isps=isps,
        capacity=await capacity(),
    )


def _used_networks(topo):
    """Every network the base already uses: loopbacks, customer networks, and the subnets of links
    and hosted services. Slice allocation must avoid all of them, not only those inside
    an ISP's block: customer networks sit outside their ISP's block (e.g. 8.8.8.0/24)."""
    nets = []
    for router in topo.of("Router"):
        for key in ("loopback_ip", "customer_net"):
            if router.props.get(key):
                nets.append(ipaddress.ip_network(router.props[key], strict=False))
    for rel in topo.rels:
        if rel.props.get("subnet"):
            nets.append(ipaddress.ip_network(rel.props["subnet"], strict=False))
    return nets


def _free(block, used):
    free = [block]
    for net in used:
        remaining = []
        for f in free:
            if not f.overlaps(net):
                remaining.append(f)
            elif not f.subnet_of(net):
                remaining.extend(f.address_exclude(net))
        free = remaining
    return free


def _static_used(topo):
    """{router: addresses the base assigns on its customer network}: the router (.1) and the
    core services placed there (hosted_by.csv)."""
    used = {}
    for router in topo.of("Router"):
        if router.props.get("customer_net"):
            net = ipaddress.ip_network(router.props["customer_net"])
            used[router.id] = (net, {str(next(net.hosts()))})
    for rel in topo.rels:
        ip = rel.props.get("service_ip")
        if rel.type == "HOSTED_BY" and ip and rel.end in used:
            address = ip.split("/")[0]
            if ipaddress.ip_address(address) in used[rel.end][0]:
                used[rel.end][1].add(address)
    return used


def attachment_points(topo, scope, isp: Optional[str] = None, location: Optional[str] = None, deployed: bool = True,
                      dhcp_start: int = 20) -> List[AttachmentPoint]:
    """Routers with a customer network, and their free static addresses. On the deployed version
    the addresses live instances hold are not free."""
    used = _used_networks(topo)
    space = {}
    for i in topo.of("ISP"):
        block = ipaddress.ip_network(i.props["public_block"])
        free = _free(block, used)
        largest = min(free, key=lambda n: n.prefixlen) if free else None
        space[i.id] = AddressSpace(
            public_block=str(block),
            free_addresses=sum(n.num_addresses for n in free),
            largest_free_prefix=str(largest) if largest else None,
        )
    static_used = _static_used(topo)
    taken = {}
    if deployed:
        for inst in store.live_instances():
            for a in inst.allocation.static_addresses if inst.allocation else []:
                taken.setdefault(a.router, set()).add(a.ip)
    points = []
    for router in topo.of("Router"):
        router_isp = _one(topo, router.id, "OPERATED_BY")
        router_loc = _one(topo, router.id, "LOCATED_IN")
        if not router.props.get("customer_net"):
            continue
        if not in_scope(router_isp, scope) or (isp and router_isp != isp) or (location and router_loc != location):
            continue
        net, base_used = static_used[router.id]
        static = [str(a) for a in list(net.hosts())[1 : dhcp_start - 1]]
        free_static = [a for a in static if a not in base_used and a not in taken.get(router.id, set())]
        loc = topo.nodes["Location"].get(router_loc)
        isp_node = topo.nodes["ISP"][router_isp]
        points.append(
            AttachmentPoint(
                router=router.id,
                isp=router_isp,
                asn=isp_node.props.get("asn"),
                location=router_loc,
                latitude=loc.props.get("latitude") if loc else None,
                longitude=loc.props.get("longitude") if loc else None,
                customer_net=router.props["customer_net"],
                free_static_addresses=len(free_static),
                address_space=space[router_isp],
            )
        )
    return points
