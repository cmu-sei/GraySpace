"""Resolve a slice spec against the deployed base: findings, and the allocation when there are no errors.

Validation and the Allocate stage of a create job run this same function; validation discards the
allocation, the job persists it. It is pure: the base,
the catalog and what other instances hold are passed in, and the spec is never changed.
"""
import difflib
import ipaddress
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from models.capabilities import Image, Role
from models.instances import (
    Allocation,
    HostAllocation,
    InstanceRecord,
    SegmentAllocation,
    SiteAllocation,
    StaticAddress,
    Wan,
)
from models.slices import Attach, Finding, SliceSpec
from services.slices.base import BaseContext, BaseRouter, haversine_km
from services.slices.catalog import EDGE_IMAGE

# What the running GraySpace can build.
SLICE_FEATURES = [
    "nat-site",
    "published-host",
    "internet-host",
    "routed-site",
    "dual-homed-site",
    "oci-host",
    "vm-host",
    "link-conditions",
    "crucible-player",
]

# Hosts in a segment are numbered from .10; .1 is the edge and .2–.9 are left free.
FIRST_HOST_OFFSET = 10
VNET_LIMIT = 1000
PRIVATE_ASNS = range(64512, 65535)
EDGE_SIZE = {"cores": 1, "memory": 256, "disk": 4}
# Roles GraySpace applies itself; specs can't name them.
INTERNAL_ROLES = {"grayspace-host", "edge-nat", "edge-bgp"}
RFC1918 = [ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]


@dataclass
class Taken:
    """What live instances (other than the one being resolved) and the overlay already hold."""

    static: Dict[str, Set[str]] = field(default_factory=dict)
    vnets: Set[str] = field(default_factory=set)
    hostnames: Set[str] = field(default_factory=set)
    prefixes: Set[str] = field(default_factory=set)
    public_prefixes: List[ipaddress.IPv4Network] = field(default_factory=list)
    asns: Set[int] = field(default_factory=set)
    domains: Set[str] = field(default_factory=set)
    fqdns: Set[str] = field(default_factory=set)
    links: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_instances(cls, instances: Iterable[InstanceRecord], exclude: Optional[str] = None, overlay_domains=(), overlay_links=()):
        t = cls()
        for inst in instances:
            if not inst.live or inst.id == exclude:
                continue
            t.prefixes.add(inst.id)
            for d in inst.domains:
                t.domains.add(d.domain)
            for r in inst.records:
                t.fqdns.add(r.fqdn)
            for c in inst.conditions:
                t.links[c.link_id] = f"instance {inst.id}"
            for link in inst.links_down:
                t.links[link] = f"instance {inst.id}"
            a = inst.allocation
            if not a:
                continue
            for s in a.static_addresses:
                t.static.setdefault(s.router, set()).add(s.ip)
            t.vnets |= set(a.vnets)
            t.hostnames |= set(a.hostnames())
            t.public_prefixes += [ipaddress.IPv4Network(p) for p in a.public_prefixes]
            t.asns |= set(a.asns)
        t.domains |= {d for d in overlay_domains}
        for link in overlay_links:
            t.links.setdefault(link, "the core overlay (the web UI's changes or a core preset)")
        return t


@dataclass
class Settings:
    pool: str = "192.168.0.0/16"
    mgmt_subnet: str = "10.0.0.0/16"
    features: List[str] = field(default_factory=lambda: list(SLICE_FEATURES))


def _free_blocks(block, used):
    free = [block]
    for net in used:
        remaining = []
        for f in free:
            if not f.overlaps(net):
                remaining.append(f)
            elif not f.subnet_of(net):
                remaining.extend(f.address_exclude(net))
        free = remaining
    return sorted(free, key=lambda n: int(n.network_address))


def canonical_link(ctx: BaseContext, link_id: str) -> Optional[str]:
    """The link ID as the API uses it, accepting either router order."""
    if link_id in ctx.links:
        return link_id
    a, _, b = link_id.partition(":")
    return f"{b}:{a}" if f"{b}:{a}" in ctx.links else None


class Resolver:
    def __init__(self, spec: SliceSpec, ctx: BaseContext, images: Dict[str, Image], roles: Dict[str, Role], taken: Taken, settings: Settings, prefix: Optional[str] = None):
        self.spec = spec
        self.ctx = ctx
        self.images = images
        self.roles = roles
        self.taken = taken
        self.settings = settings
        self.prefix = prefix or spec.prefix
        self.findings: List[Finding] = []
        # Static addresses this spec takes, per router, as it is resolved, and how many more an
        # attachment has reserved but not yet taken
        self.spec_static: Dict[str, List[str]] = {}
        self.reserved: Dict[str, int] = {}
        self.vnets: Dict[str, str] = {}
        self.static: List[StaticAddress] = []

    # --- findings ---

    def error(self, code, path, message, suggestion=None):
        self.findings.append(Finding(code=code, severity="error", path=path, message=message, suggestion=suggestion))

    def warning(self, code, path, message, suggestion=None):
        self.findings.append(Finding(code=code, severity="warning", path=path, message=message, suggestion=suggestion))

    def feature(self, name, path, what) -> bool:
        if name in self.settings.features:
            return True
        self.error("UNSUPPORTED_FEATURE", path, f"{what} ({name}) is not supported by this GraySpace")
        return False

    # --- attachment ---

    def _usable(self, r: BaseRouter) -> bool:
        return bool(r.customer_net and r.customer_bridge)

    def _free_count(self, router: str) -> int:
        taken = self.taken.static.get(router, set()) | set(self.spec_static.get(router, []))
        return len(self.ctx.free_static(router, taken)) - self.reserved.get(router, 0)

    def _nearest(self, candidates: List[BaseRouter], lat, lon, need: int) -> Optional[BaseRouter]:
        usable = [r for r in candidates if self._usable(r) and self._free_count(r.hostname) >= need]
        if not usable:
            return None
        if lat is None:
            return max(usable, key=lambda r: (self._free_count(r.hostname), r.hostname))
        return min(usable, key=lambda r: (haversine_km(lat, lon, r.latitude or 0, r.longitude or 0), r.hostname))

    def _alternative(self, near: Optional[BaseRouter], isp: Optional[str], location: Optional[str], need: int, exclude=()) -> Optional[dict]:
        """A placement that would work: same location first, then the same ISP nearest."""
        routers = [r for r in self.ctx.routers.values() if r.hostname not in exclude]
        lat = near.latitude if near else None
        lon = near.longitude if near else None
        if location is None and near is not None:
            location = near.location
        for pool in (
            [r for r in routers if location and r.location == location and (not isp or r.isp == isp)],
            [r for r in routers if location and r.location == location],
            [r for r in routers if isp and r.isp == isp],
        ):
            pick = self._nearest(pool, lat, lon, need)
            if pick:
                return {"router": pick.hostname}
        return None

    def attach(self, att: Attach, path: str, need: int, avoid=()) -> Optional[BaseRouter]:
        """Pick the base router an attachment joins, with `need` free static addresses on its
        customer network, and reserve them. Default: the matching router with the most free
        addresses."""
        router = self._attach(att, path, need, avoid)
        if router is not None:
            self.reserved[router.hostname] = self.reserved.get(router.hostname, 0) + need
        return router

    def _attach(self, att: Attach, path: str, need: int, avoid=()) -> Optional[BaseRouter]:
        ctx = self.ctx
        if att.router:
            r = ctx.routers.get(att.router)
            if r is None:
                self.error("UNKNOWN_ROUTER", f"{path}.router", f"No router '{att.router}' in the deployed core",
                           self._alternative(None, att.isp, att.location, need))
                return None
            if (att.isp and r.isp != att.isp) or (att.location and r.location != att.location):
                self.error("UNKNOWN_ROUTER", f"{path}.router", f"{r.hostname} is {r.isp} in {r.location}, not {att.isp or r.isp} in {att.location or r.location}",
                           {"router": r.hostname})
                return None
            if not self._usable(r):
                self.error("NO_CUSTOMER_NET", f"{path}.router", f"{r.hostname} has no customer network to attach to",
                           self._alternative(r, r.isp, r.location, need, exclude={r.hostname}))
                return None
            if self._free_count(r.hostname) < need:
                self.error("NO_STATIC_ADDRESSES", f"{path}.router",
                           f"{r.hostname} has {self._free_count(r.hostname)} free static addresses on {r.customer_net}; {need} needed",
                           self._alternative(r, r.isp, r.location, need, exclude={r.hostname}))
                return None
            return r

        isps = {r.isp for r in ctx.routers.values()}
        locations = {r.location for r in ctx.routers.values()}
        if att.isp and att.isp not in isps:
            self.error("UNKNOWN_ROUTER", f"{path}.isp", f"No ISP '{att.isp}' in the deployed core", None)
            return None
        if att.location and att.location not in locations:
            self.error("UNKNOWN_ROUTER", f"{path}.location", f"No routers in '{att.location}' in the deployed core",
                       self._alternative(None, att.isp, None, need))
            return None
        candidates = [r for r in ctx.routers.values() if (not att.isp or r.isp == att.isp) and (not att.location or r.location == att.location) and r.hostname not in avoid]
        if not candidates:
            near = next((r for r in ctx.routers.values() if r.location == att.location), None)
            self.error("UNKNOWN_ROUTER", path, f"{att.isp} has no router in {att.location}",
                       self._alternative(near, att.isp, None, need) if near else None)
            return None
        usable = [r for r in candidates if self._usable(r)]
        if not usable:
            self.error("NO_CUSTOMER_NET", path, f"No {att.isp or ''} router in {att.location or 'the core'} has a customer network".replace("  ", " "),
                       self._alternative(candidates[0], att.isp, att.location, need, exclude={r.hostname for r in candidates}))
            return None
        best = max(usable, key=lambda r: (self._free_count(r.hostname), r.hostname))
        if self._free_count(best.hostname) < need:
            self.error("NO_STATIC_ADDRESSES", path,
                       f"The routers matching have at most {self._free_count(best.hostname)} free static addresses; {need} needed",
                       self._alternative(best, att.isp, att.location, need, exclude={r.hostname for r in usable}))
            return None
        return best

    def take_static(self, router: str, use: str) -> str:
        taken = self.taken.static.get(router, set()) | set(self.spec_static.get(router, []))
        ip = self.ctx.free_static(router, taken)[0]
        self.spec_static.setdefault(router, []).append(ip)
        self.reserved[router] = max(0, self.reserved.get(router, 0) - 1)
        self.static.append(StaticAddress(router=router, ip=ip, use=use))
        return ip

    def take_vnet(self, alias: str) -> str:
        for n in range(VNET_LIMIT):
            vnet = f"s{n:03d}"
            if vnet not in self.taken.vnets and vnet not in self.vnets:
                self.vnets[vnet] = alias
                return vnet
        raise RuntimeError("no free slice vnet IDs")

    # --- names ---

    def hostname(self, name: str, path: str, seen: Dict[str, str]) -> str:
        hostname = f"{self.prefix}-{name}"
        if len(hostname) > 63:
            self.error("INVALID_SPEC", path, f"Hostname '{hostname}' is longer than 63 characters")
        if hostname in self.ctx.hostnames or hostname in self.taken.hostnames:
            self.error("HOSTNAME_TAKEN", path, f"Hostname '{hostname}' is already in use in this deployment")
        if hostname in seen:
            self.error("DUPLICATE_NAME", path, f"Hostname '{hostname}' is also {seen[hostname]}")
        seen[hostname] = path
        return hostname

    # --- addressing ---

    def _conflicts(self, net: ipaddress.IPv4Network, others: List[ipaddress.IPv4Network]) -> Optional[ipaddress.IPv4Network]:
        return next((o for o in others if net.overlaps(o)), None)

    def run(self) -> Optional[Allocation]:
        spec, ctx = self.spec, self.ctx
        mgmt = ipaddress.IPv4Network(self.settings.mgmt_subnet)
        pool = ipaddress.IPv4Network(self.settings.pool)
        base_nets = ctx.used_networks

        if self.prefix in self.taken.prefixes:
            free = next(f"{self.prefix[:7]}{n}" for n in range(2, 100) if f"{self.prefix[:7]}{n}" not in self.taken.prefixes)
            self.error("PREFIX_TAKEN", "prefix", f"An instance with prefix '{self.prefix}' already exists", free)
        if pool.overlaps(mgmt):
            self.warning("CIDR_OVERLAPS_MGMT", "", f"The enclave address pool {pool} overlaps the management subnet {mgmt}; those /24s are skipped")

        # Names: sites, segments, hosts
        seen_hostnames: Dict[str, str] = {}
        site_names: Dict[str, int] = {}
        segment_site: Dict[str, Tuple[int, int]] = {}
        for i, site in enumerate(spec.sites):
            if site.name in site_names:
                self.error("DUPLICATE_NAME", f"sites[{i}].name", f"Site '{site.name}' is defined twice")
            site_names[site.name] = i
            for j, seg in enumerate(site.segments):
                if seg.name in segment_site:
                    self.error("DUPLICATE_NAME", f"sites[{i}].segments[{j}].name", f"Segment '{seg.name}' is defined twice")
                segment_site[seg.name] = (i, j)

        host_entries = []  # (index, expanded name)
        expanded_of: Dict[str, List[str]] = {}
        for k, host in enumerate(spec.hosts):
            names = host.expanded_names()
            expanded_of[host.name] = names
            for name in names:
                host_entries.append((k, name))

        # Count the static addresses each site needs on its WAN routers: 1 per WAN, plus 1 per
        # published host on the first WAN of a NAT site.
        published: Dict[int, int] = {}
        for k, host in enumerate(spec.hosts):
            if host.segment and host.segment in segment_site and host.publish:
                i = segment_site[host.segment][0]
                if not spec.sites[i].routed:
                    published[i] = published.get(i, 0) + len(expanded_of[host.name])

        explicit = []
        for i, site in enumerate(spec.sites):
            for j, seg in enumerate(site.segments):
                if seg.cidr:
                    explicit.append((ipaddress.IPv4Network(seg.cidr), f"sites[{i}].segments[{j}].cidr"))

        # --- Sites ---
        sites: List[SiteAllocation] = []
        site_index: Dict[str, SiteAllocation] = {}
        assigned_nets: List[ipaddress.IPv4Network] = []
        new_public: List[ipaddress.IPv4Network] = []
        new_asns: List[int] = []
        for i, site in enumerate(spec.sites):
            path = f"sites[{i}]"
            atts = site.attachments
            if not atts:
                self.error("INVALID_SPEC", f"{path}.attach", "Give at least one attachment")
                continue
            if not site.routed:
                self.feature("nat-site", path, "NAT sites")
            elif not self.feature("routed-site", f"{path}.routed", "Routed sites"):
                continue
            if len(atts) > 1:
                if not site.routed:
                    self.error("UNSUPPORTED_FEATURE", f"{path}.attach", "Dual-homing needs a routed site (routed: true): a NAT site has one WAN address", True)
                    continue
                if not self.feature("dual-homed-site", f"{path}.attach", "Dual-homed sites"):
                    continue
            routers = []
            ok = True
            for j, att in enumerate(atts):
                need = 1 + (published.get(i, 0) if j == 0 else 0)
                apath = f"{path}.attach" if not isinstance(site.attach, list) else f"{path}.attach[{j}]"
                r = self.attach(att, apath, need, avoid={x.hostname for x in routers})
                if r is None:
                    ok = False
                    continue
                if any(x.hostname == r.hostname for x in routers):
                    self.error("DUPLICATE_NAME", apath, f"The site attaches to {r.hostname} twice")
                    ok = False
                routers.append(r)
            edge = self.hostname(f"{site.name}-edge", f"{path}.name", seen_hostnames)
            if not ok:
                continue

            wans = []
            for r in routers:
                ip = self.take_static(r.hostname, f"{edge} WAN")
                wans.append(Wan(router=r.hostname, isp=r.isp, asn=r.asn, location=r.location, customer_net=r.customer_net,
                                bridge=r.customer_bridge, ip=ip, gateway=str(next(ipaddress.IPv4Network(r.customer_net).hosts()))))

            segments = []
            public_prefix = None
            asn = None
            if site.routed:
                block = ipaddress.IPv4Network(routers[0].public_block)
                used = base_nets + self.taken.public_prefixes + new_public
                free24 = next((s for f in _free_blocks(block, used) if f.prefixlen <= 24 for s in f.subnets(new_prefix=24)), None)
                if free24 is None:
                    self.error("CAPACITY", f"{path}.routed", f"No free /24 left in {routers[0].isp}'s block {block}")
                    continue
                new_public.append(free24)
                public_prefix = str(free24)
                asn = next((a for a in PRIVATE_ASNS if a not in self.taken.asns and a not in new_asns), None)
                if asn is None:
                    self.error("CAPACITY", f"{path}.routed", "No private ASN left for the edge")
                    continue
                new_asns.append(asn)
                n = len(site.segments)
                bits = math.ceil(math.log2(n)) if n > 1 else 0
                if bits > 4:
                    self.error("SEGMENT_FULL", f"{path}.segments", "A routed site has at most 16 segments in its /24")
                    continue
                parts = list(free24.subnets(prefixlen_diff=bits))
                for j, seg in enumerate(site.segments):
                    if seg.cidr:
                        self.error("INVALID_CIDR", f"{path}.segments[{j}].cidr", "Routed sites allocate their segments from the site's public /24; omit cidr")
                    net = parts[j]
                    segments.append(SegmentAllocation(name=seg.name, cidr=str(net), gateway=str(next(net.hosts())), vnet=self.take_vnet(f"{self.prefix} {seg.name}")))
            else:
                for j, seg in enumerate(site.segments):
                    spath = f"{path}.segments[{j}]"
                    if seg.cidr:
                        net = ipaddress.IPv4Network(seg.cidr)
                        if not any(net.subnet_of(p) for p in RFC1918):
                            self.error("INVALID_CIDR", f"{spath}.cidr", f"{net} is not a private (RFC 1918) range; NAT sites use private addresses")
                            continue
                        if net.prefixlen > 28:
                            self.error("INVALID_CIDR", f"{spath}.cidr", f"{net} is too small; use /28 or larger")
                            continue
                        if net.overlaps(mgmt):
                            self.error("CIDR_OVERLAPS_MGMT", f"{spath}.cidr", f"{net} overlaps the management subnet {mgmt}")
                            continue
                        clash = self._conflicts(net, base_nets)
                        if clash:
                            self.error("CIDR_OVERLAP", f"{spath}.cidr", f"{net} overlaps {clash}, which the core uses")
                            continue
                        clash = self._conflicts(net, assigned_nets + [n for n, p in explicit if p != f"{spath}.cidr"])
                        if clash:
                            self.error("CIDR_OVERLAP", f"{spath}.cidr", f"{net} overlaps {clash}, another segment of this enclave")
                            continue
                    else:
                        avoid = [mgmt] + base_nets + assigned_nets + [n for n, _ in explicit]
                        net = next((s for s in pool.subnets(new_prefix=24) if not self._conflicts(s, avoid)), None) if pool.prefixlen <= 24 else None
                        if net is None:
                            self.error("CIDR_OVERLAP", f"{spath}", f"No free /24 left in the enclave address pool {pool}")
                            continue
                    assigned_nets.append(net)
                    segments.append(SegmentAllocation(name=seg.name, cidr=str(net), gateway=str(next(net.hosts())), vnet=self.take_vnet(f"{self.prefix} {seg.name}")))
            resolver = self.ctx.nearest_resolver(routers[0].hostname)
            alloc = SiteAllocation(name=site.name, routed=site.routed, edge=edge, wans=wans, segments=segments,
                                   public_prefix=public_prefix, asn=asn, resolver=resolver.ip if resolver else "", crucible=site.crucible)
            if resolver is None:
                self.error("CAPACITY", path, "The deployed core has no DNS resolver for the site's hosts")
            sites.append(alloc)
            site_index[site.name] = alloc

        # --- Hosts ---
        hosts: List[HostAllocation] = []
        seg_next: Dict[str, int] = {}
        # Edges use the base's copy of the edge image (render.base_alpine_volume): no download
        used_images = set()
        if spec.sites and EDGE_IMAGE not in self.images:
            self.error("UNKNOWN_IMAGE", "sites", f"The edge router image '{EDGE_IMAGE}' is not in the image catalog")
        for k, host in enumerate(spec.hosts):
            path = f"hosts[{k}]"
            image = self.images.get(host.image)
            if image is None:
                close = difflib.get_close_matches(host.image, sorted(self.images), n=1, cutoff=0.6)
                self.error("UNKNOWN_IMAGE", f"{path}.image", f"No image '{host.image}' in the catalog; register local images with POST /api/v1/images",
                           close[0] if close else None)
            else:
                used_images.add(image.name)
                if image.type == "vm":
                    self.feature("vm-host", f"{path}.image", "VM hosts")
                if image.type == "oci":
                    self.feature("oci-host", f"{path}.image", "OCI hosts")
                if image.type != "oci" and host.env:
                    self.warning("INVALID_SPEC", f"{path}.env", "env applies to OCI images only; it is ignored")
            for r, role in enumerate(host.roles):
                rpath = f"{path}.roles[{r}]"
                known = self.roles.get(role)
                if known is None or role in INTERNAL_ROLES:
                    self.error("UNKNOWN_ROLE", rpath, f"No role '{role}'", None)
                elif image is not None and (image.type == "oci" or image.os_family not in known.os_families):
                    self.error("INCOMPATIBLE_ROLE", rpath,
                               f"Role '{role}' supports {', '.join(known.os_families)}, not {image.name} ({image.os_family})"
                               + (": OCI hosts are configured only by env" if image.type == "oci" else ""))
                elif image is not None and known.image_types and image.type not in known.image_types:
                    self.error("INCOMPATIBLE_ROLE", rpath,
                               f"Role '{role}' runs on {' or '.join(t.upper() if t == 'vm' else t for t in known.image_types)} images, "
                               f"not {image.name} ({image.type})")
            size = host.size.model_dump() if host.size else ({"cores": image.cores, "memory": image.memory, "disk": image.disk} if image else EDGE_SIZE)

            site_alloc = None
            segment = None
            router = None
            if host.segment:
                if host.segment not in segment_site:
                    self.error("UNKNOWN_SEGMENT", f"{path}.segment", f"No segment '{host.segment}'",
                               next(iter(segment_site), None))
                else:
                    i, j = segment_site[host.segment]
                    site_alloc = site_index.get(spec.sites[i].name)
                    if site_alloc:
                        segment = next((s for s in site_alloc.segments if s.name == host.segment), None)
                    if host.publish:
                        if spec.sites[i].routed:
                            self.warning("INVALID_SPEC", f"{path}.publish", "Hosts of routed sites have public addresses already; publish is ignored")
                        else:
                            self.feature("published-host", f"{path}.publish", "Published hosts")
            else:
                self.feature("internet-host", f"{path}.internet", "Internet hosts")

            for name in expanded_of[host.name]:
                hostname = self.hostname(name, f"{path}.name", seen_hostnames)
                if host.segment:
                    if not (site_alloc and segment and image):
                        continue
                    net = ipaddress.IPv4Network(segment.cidr)
                    offset = seg_next.get(segment.name, FIRST_HOST_OFFSET)
                    if offset >= net.num_addresses - 1:
                        self.error("SEGMENT_FULL", f"{path}.count" if host.count else f"{path}.segment", f"Segment '{segment.name}' ({net}) has no address left for {hostname}")
                        continue
                    seg_next[segment.name] = offset + 1
                    ip = str(net.network_address + offset)
                    public_ip = None
                    if site_alloc.routed:
                        public_ip = ip
                    elif host.publish:
                        public_ip = self.take_static(site_alloc.wans[0].router, f"{hostname} published")
                    hosts.append(HostAllocation(
                        name=name, spec_name=host.name, hostname=hostname, type=image.type, image=image.name, os_family=image.os_family,
                        site=site_alloc.name, segment=segment.name, bridge=segment.vnet, ip=ip, prefixlen=net.prefixlen,
                        gateway=segment.gateway, public_ip=public_ip, router=None, resolver=site_alloc.resolver,
                        roles=list(host.roles), labels=dict(host.labels), env=dict(host.env), managed=image.type != "oci",
                        crucible=host.crucible, **size))
                else:
                    if router is None:
                        # Reserves an address per expanded host; they are taken one by one below.
                        router = self.attach(host.internet, f"{path}.internet", len(expanded_of[host.name]))
                        if router is None:
                            break
                    if image is None:
                        continue
                    ip = self.take_static(router.hostname, hostname)
                    net = ipaddress.IPv4Network(router.customer_net)
                    resolver = self.ctx.nearest_resolver(router.hostname)
                    hosts.append(HostAllocation(
                        name=name, spec_name=host.name, hostname=hostname, type=image.type, image=image.name, os_family=image.os_family,
                        site=None, segment=None, bridge=router.customer_bridge, ip=ip, prefixlen=net.prefixlen,
                        gateway=str(next(net.hosts())), public_ip=ip, router=router.hostname,
                        resolver=resolver.ip if resolver else "", roles=list(host.roles), labels=dict(host.labels),
                        env=dict(host.env), managed=image.type != "oci", crucible=host.crucible, **size))

        # A site's domain controller is its other hosts' resolver, so they find the domain (and the
        # internal zone, when the DC has the dns role). The DC keeps the base resolver, which its DNS
        # forwards everything else to. Several DCs in a site are separate domains: the first serves.
        for site_alloc in sites:
            dc = next((h for h in hosts if h.site == site_alloc.name and "ad-dc" in h.roles), None)
            for h in hosts:
                if dc is not None and h.site == site_alloc.name and h is not dc:
                    h.resolver = dc.ip

        by_name = {h.name: h for h in hosts}
        spec_names = {h.name: expanded_of[h.name] for h in spec.hosts}

        def find_host(name, path) -> Optional[HostAllocation]:
            if name in by_name:
                return by_name[name]
            if name in spec_names and len(spec_names[name]) > 1:
                self.error("UNKNOWN_HOST", path, f"'{name}' is {len(spec_names[name])} hosts; name one, e.g. {spec_names[name][0]}", spec_names[name][0])
                return None
            if name in spec_names:
                return None  # The host itself had an error.
            self.error("UNKNOWN_HOST", path, f"No host '{name}'", None)
            return None

        # --- DNS ---
        domains = {}
        for d, dom in enumerate(spec.dns.domains):
            path = f"dns.domains[{d}]"
            if dom.domain in domains:
                self.error("DUPLICATE_NAME", f"{path}.domain", f"Domain '{dom.domain}' is listed twice")
            domains[dom.domain] = dom
            tld = dom.domain.rsplit(".", 1)[-1]
            if tld not in ctx.tlds:
                self.error("UNKNOWN_TLD", f"{path}.domain", f"The core has no TLD server for .{tld}; TLDs: {', '.join(sorted(ctx.tlds))}")
            if dom.hosting_server not in ctx.hosting:
                self.error("UNKNOWN_HOSTING_SERVER", f"{path}.hosting_server", f"No hosting DNS server '{dom.hosting_server}'",
                           sorted(ctx.hosting)[0] if ctx.hosting else None)
            if dom.domain in self.taken.domains:
                self.error("DNS_NAME_TAKEN", f"{path}.domain", f"'{dom.domain}' already exists in this deployment")
        record_names = set()
        for r, rec in enumerate(spec.dns.records):
            path = f"dns.records[{r}]"
            zone = max((d for d in domains if rec.name == d or rec.name.endswith("." + d)), key=len, default=None)
            if zone is None:
                self.error("UNKNOWN_DOMAIN", f"{path}.name", f"'{rec.name}' is not inside a domain of the enclave", None)
            if rec.name in self.taken.fqdns:
                self.error("DNS_NAME_TAKEN", f"{path}.name", f"'{rec.name}' already exists in this deployment")
            record_names.add(rec.name)
            if rec.host:
                h = find_host(rec.host, f"{path}.host")
                if h and not h.public_ip:
                    self.warning("UNREACHABLE_RECORD", f"{path}.host",
                                 f"{h.name} has no public address; '{rec.name}' resolves to {h.ip}, reachable only inside site {h.site}",
                                 True)

        # --- TLS ---
        tls_hosts: Dict[str, int] = {}
        for t, tls in enumerate(spec.tls):
            path = f"tls[{t}]"
            h = find_host(tls.host, f"{path}.host")
            if h is not None:
                if h.hostname in tls_hosts:
                    self.error("DUPLICATE_NAME", f"{path}.host",
                               f"{h.name} already has a certificate (tls[{tls_hosts[h.hostname]}]); put every name on one entry")
                tls_hosts[h.hostname] = t
            if tls.ca not in ctx.cas:
                self.error("UNKNOWN_CA", f"{path}.ca", f"No CA '{tls.ca}' in the deployed core", sorted(ctx.cas)[0] if ctx.cas else None)
            if h is not None:
                image = self.images.get(h.image)
                if h.type == "oci" and (image is None or "tls-kestrel" not in image.features):
                    self.error("UNSUPPORTED_FEATURE", f"{path}.host", f"Image {h.image} can't take a certificate")
            for n, name in enumerate(tls.names):
                if name not in record_names and not any(name == d for d in domains):
                    self.warning("UNREACHABLE_RECORD", f"{path}.names[{n}]", f"No DNS record in the enclave for '{name}'")

        # --- Conditions ---
        if spec.conditions.links:
            self.feature("link-conditions", "conditions.links", "Link conditions")
        for link_id in spec.conditions.links:
            path = f"conditions.links.{link_id}"
            canonical = canonical_link(ctx, link_id)
            if canonical is None:
                self.error("UNKNOWN_LINK", path, f"No link '{link_id}' between two deployed routers")
            elif canonical in self.taken.links:
                self.error("LINK_OWNED", path, f"{canonical} already has conditions set by {self.taken.links[canonical]}")

        # --- Role variables ---
        used_roles = {r for h in spec.hosts for r in h.roles}
        for role_name, values in spec.role_vars.items():
            if role_name not in self.roles or role_name in INTERNAL_ROLES:
                self.warning("UNKNOWN_ROLE", f"role_vars.{role_name}", f"No role '{role_name}'; its variables are ignored")
        for role_name in sorted(used_roles):
            role = self.roles.get(role_name)
            if not role:
                continue
            values = spec.role_vars.get(role_name, {})
            for var, meta in role.variables.items():
                if meta.required and var not in values:
                    self.error("MISSING_ROLE_VAR", f"role_vars.{role_name}.{var}", f"Role '{role_name}' needs {var}: {meta.description}")

        if any(f.severity == "error" for f in self.findings):
            return None
        return Allocation(
            prefix=self.prefix,
            sites=sites,
            hosts=hosts,
            static_addresses=self.static,
            vnets=self.vnets,
            public_prefixes=[str(n) for n in new_public],
            asns=new_asns,
            images=sorted(used_images),
        )


def resolve(spec: SliceSpec, ctx: BaseContext, images: Dict[str, Image], roles: Dict[str, Role], taken: Taken,
            settings: Optional[Settings] = None, prefix: Optional[str] = None) -> Tuple[Optional[Allocation], List[Finding]]:
    r = Resolver(spec, ctx, images, roles, taken, settings or Settings(), prefix)
    allocation = r.run()
    return allocation, r.findings


def required_capacity(allocation: Allocation) -> dict:
    hosts = [{"cores": h.cores, "memory": h.memory, "disk": h.disk} for h in allocation.hosts] + [EDGE_SIZE for _ in allocation.sites]
    return {
        "guests": len(hosts),
        "cores": sum(h["cores"] for h in hosts),
        "memory_mib": sum(h["memory"] for h in hosts),
        "disk_gib": sum(h["disk"] for h in hosts),
    }


def capacity_findings(allocation: Allocation, capacity) -> List[Finding]:
    """CAPACITY findings: errors when the node lacks the memory or storage the instance needs.
    capacity is models.capabilities.Capacity, or None when Proxmox is unavailable."""
    need = required_capacity(allocation)
    if capacity is None:
        return [Finding(code="CAPACITY", severity="warning", path="", message="Proxmox is unavailable; capacity was not checked")]
    out = []
    if capacity.memory_free is not None and need["memory_mib"] * 2**20 > capacity.memory_free:
        out.append(Finding(code="CAPACITY", severity="error", path="hosts",
                           message=f"The instance needs {need['memory_mib']} MiB of memory; the node has {capacity.memory_free // 2**20} MiB free"))
    if capacity.storage_free is not None and need["disk_gib"] * 2**30 > capacity.storage_free:
        out.append(Finding(code="CAPACITY", severity="error", path="hosts",
                           message=f"The instance needs {need['disk_gib']} GiB of disk; {capacity.storage} has {capacity.storage_free // 2**30} GiB free"))
    if capacity.cpus and need["cores"] > capacity.cpus * 4:
        out.append(Finding(code="CAPACITY", severity="warning", path="hosts",
                           message=f"The instance needs {need['cores']} cores, over 4 per CPU on a {capacity.cpus}-CPU node"))
    return out
