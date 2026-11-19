"""The manifest of an instance: what exists, where, and how to reach it.

Everything in it is generic, so GHOSTS can pre-register machines from `hosts` (hostname, IP,
labels) and other consumers can use it the same way.
"""
from typing import Dict, Optional

from models.instances import (
    InstanceRecord,
    Manifest,
    ManifestCertificate,
    ManifestHost,
    ManifestRecord,
    ManifestSegment,
    ManifestSite,
)


def build(record: InstanceRecord, statuses: Optional[Dict[str, str]] = None) -> Manifest:
    """statuses: {hostname: Proxmox status}; hosts missing from it are 'unknown'."""
    statuses = statuses or {}
    alloc = record.allocation
    fqdns: Dict[str, list] = {}
    for r in record.records:
        if r.type == "A":
            for h in alloc.hosts if alloc else []:
                if r.data in (h.ip, h.public_ip):
                    fqdns.setdefault(h.hostname, []).append(r.fqdn)
    sites = []
    hosts = []
    if alloc:
        for s in alloc.sites:
            first = s.wans[0]
            sites.append(ManifestSite(
                name=s.name, router=first.router, isp=first.isp, location=first.location, wan_ip=first.ip,
                routed=s.routed, public_prefix=s.public_prefix, wans=s.wans, edge=s.edge,
                segments=[ManifestSegment(name=g.name, cidr=g.cidr, gateway=g.gateway) for g in s.segments],
            ))
        for h in alloc.hosts:
            hosts.append(ManifestHost(
                name=h.name, hostname=h.hostname, site=h.site, segment=h.segment, ip=h.ip, public_ip=h.public_ip,
                mgmt_ip=record.mgmt_ips.get(h.hostname), fqdns=sorted(set(fqdns.get(h.hostname, []))), roles=h.roles,
                labels=h.labels, image=h.image, status=statuses.get(h.hostname, "unknown"),
            ))
    return Manifest(
        id=record.id,
        slice=record.slice,
        base=record.base,
        state=record.state,
        sites=sites,
        hosts=hosts,
        dns=[ManifestRecord(name=r.fqdn, type=r.type, data=r.data) for r in record.records],
        certificates=[ManifestCertificate(host=c.host, names=c.names, ca=c.ca, serial=c.serial) for c in record.certificates],
    )
