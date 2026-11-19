"""The base's runtime changes as actions: making them, saving them as presets, and undoing them all.

Every change to the base is an action, applied by apply() here: the web UI's routes (link traffic,
interface zones, DNS) turn their requests into one, as do POST /api/v1/base/actions
and a preset being applied (services/presets.py), so a preset does exactly what the UI does. Each
change is recorded in services/mutation_tracker.py, whose contents from_tracker() turns back into
actions for a preset:

- link.condition {target: link ID, params: latency_ms, jitter_ms, bandwidth_mbps, packet_loss_percent}
- link.clear {target: link ID}: not in presets, which start from a reset
- interface.zone {target: hostname:interface, params: zone_id, vlan_tag}
- dns.domain.create {params: domain, hosting_server, ip_address, mx_records, create_www, additional_records}
- dns.record.set {params: server, zone, name, type, data, ttl}

Certificates aren't actions: one issued is revoked by a reset, one revoked stays revoked.
"""
import asyncio
import logging
import re
from typing import Any, Dict, List

from models.presets import PresetAction
from services.mutation_tracker import mutation_tracker

logger = logging.getLogger(__name__)

TRAFFIC_PARAMS = ("latency_ms", "jitter_ms", "bandwidth_mbps", "packet_loss_percent")


DOMAIN = r'^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)+$'
RECORD_TYPES = {"A", "AAAA", "NS", "MX", "CNAME", "TXT", "PTR", "SRV", "CAA"}


class ChangeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def from_tracker() -> List[PresetAction]:
    """What the tracker holds, as actions: zones first, then DNS, then link conditions."""
    actions = [PresetAction(action="interface.zone", target=k, params={"zone_id": z.get("zone_id", ""), "vlan_tag": z.get("vlan_tag")})
                for k, z in mutation_tracker.zone_changes.items()]
    actions += [PresetAction(action="dns.domain.create", params={k: v for k, v in d.items() if k != "tld_server"})
                for d in mutation_tracker.dns_domains]
    actions += [PresetAction(action="dns.record.set", params=dict(r)) for r in mutation_tracker.dns_records]
    actions += [PresetAction(action="link.condition", target=link, params={k: c.get(k) for k in TRAFFIC_PARAMS if c.get(k) is not None})
                for link, c in mutation_tracker.traffic_configs.items()]
    return actions


def _bridges(tfvars: Dict, router: str) -> set:
    return {i.get("bridge") for i in tfvars["routers"][router].get("interfaces", []) if i.get("bridge")}


def problems(actions: List[PresetAction], tfvars: Dict) -> List[str]:
    """Targets the deployed base doesn't have (validate_against_tfvars's checks, for actions)."""
    routers = set(tfvars.get("routers", {}))
    hosts = routers | set(tfvars.get("core_dns", {})) | set(tfvars.get("core_ca", {}))
    found = []
    for a in actions:
        if a.action == "link.condition":
            ends = (a.target or "").split(":", 1)
            if len(ends) != 2 or not set(ends) <= routers or not _bridges(tfvars, ends[0]) & _bridges(tfvars, ends[1]):
                found.append(f"No link {a.target}")
        elif a.action == "client.move":
            found.append(f"client.move {a.target}: base clients were retired (contract 1.18)")
        elif a.action == "interface.zone":
            if (a.target or "").split(":", 1)[0] not in hosts:
                found.append(f"No host for {a.target}")
        elif a.action == "dns.record.set":
            if a.params.get("server") not in hosts:
                found.append(f"No DNS server {a.params.get('server')}")
    return found


async def apply(a: PresetAction) -> Any:
    """Make one change and track it: the one way the base changes, for the routes, POST /base/actions
    and presets alike. Returns the route's response; ChangeError says why the change wasn't made."""
    handler = HANDLERS.get(a.action)
    if handler is None:
        raise ChangeError(422, f"{a.action} isn't a base action ({', '.join(HANDLERS)})")
    return await handler(a)


def _valid(model, params: Dict[str, Any]):
    """params checked as the route's request model checks its body."""
    from pydantic import ValidationError

    unknown = set(params) - set(model.model_fields)
    if unknown:
        raise ChangeError(422, f"Unknown params: {', '.join(sorted(unknown))}")
    try:
        return model(**params)
    except ValidationError as e:
        raise ChangeError(422, "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()))


def _traffic(a: PresetAction) -> Dict[str, Any]:
    from models.traffic import TrafficConfigRequest

    return _valid(TrafficConfigRequest, a.params).model_dump()


def _link(a: PresetAction) -> Dict[str, Any]:
    """The link's interfaces, once no instance holds it."""
    from services.instance_actions import link_owner
    from services.topology_service import topology_service

    owner = link_owner(a.target or "")
    if owner:
        raise ChangeError(409, f"Link {a.target} is held by slice instance {owner}; change it with that instance's actions")
    conn = topology_service.get_connection_interfaces(a.target or "")
    if not conn:
        raise ChangeError(404, f"Link {a.target} not found")
    return conn


def _ends(conn: Dict[str, Any]) -> Dict[str, str]:
    return {"source": f"{conn['source_hostname']}:{conn['source_interface']}", "target": f"{conn['target_hostname']}:{conn['target_interface']}"}


async def _link_condition(a: PresetAction) -> Dict[str, Any]:
    from models.traffic import TrafficConfig
    from services.traffic_service import traffic_service

    config = _traffic(a)
    conn = _link(a)
    ok, message = await traffic_service.apply_link_traffic_config(a.target, TrafficConfig(**config))
    if not ok:
        raise ChangeError(500, message)
    mutation_tracker.set_traffic_config(a.target, config)
    return {"status": "success", "message": message, "config": config, "applied_to": _ends(conn)}


async def _link_clear(a: PresetAction) -> Dict[str, Any]:
    from services.traffic_service import traffic_service

    conn = _link(a)
    ok, message = await traffic_service.clear_link_traffic_config(a.target)
    if not ok:
        raise ChangeError(500, message)
    mutation_tracker.remove_traffic_config(a.target)
    return {"status": "success", "message": message, "cleared_from": _ends(conn)}


async def _interface_zone(a: PresetAction):
    from services.network_service import network_service
    from services.proxmox_client import proxmox_client

    from models.network import VNetZoneChangeRequest

    hostname, _, interface = (a.target or "").partition(":")
    request = _valid(VNetZoneChangeRequest, a.params)
    zone_id, vlan_tag = request.zone_id, request.vlan_tag
    if not await asyncio.to_thread(proxmox_client.get_container_by_hostname, hostname):
        raise ChangeError(404, f"Container {hostname} not found or not in this deployment")
    # The original zone, for Reset changes
    original = await asyncio.to_thread(network_service.get_customer_interface_network_info, hostname, interface)
    result = await asyncio.to_thread(network_service.change_customer_interface_zone, hostname, interface, zone_id, vlan_tag)
    if not result.success:
        raise ChangeError(400, result.message)
    mutation_tracker.set_zone_change(a.target, zone_id, vlan_tag, (original.zone if original else "") or "",
                                     original.vlan_tag if original else None)
    return result


async def _dns_domain(a: PresetAction):
    from services.dns_service import dns_service

    p = a.params
    domain = str(p.get("domain") or "").strip().rstrip(".")
    if not re.match(DOMAIN, domain):
        raise ChangeError(400, f"Invalid domain format: '{p.get('domain')}'")
    result = await dns_service.create_domain(domain=domain, hosting_server=p.get("hosting_server"), ip_address=p.get("ip_address"),
                                             mx_records=p.get("mx_records"), additional_records=p.get("additional_records"),
                                             create_www=p.get("create_www", True))
    if not result.success:
        raise ChangeError(400, result.message)
    return result


async def _dns_record(a: PresetAction):
    from services.dns_service import dns_service
    from services.topology_service import topology_service

    p = a.params
    server, rtype = p.get("server") or "", str(p.get("type") or "A").upper()
    if not topology_service.tfvars_data.get("core_dns", {}).get(server):
        raise ChangeError(404, f"DNS server '{server}' not found")
    if rtype not in RECORD_TYPES:
        raise ChangeError(400, f"Invalid record type '{p.get('type')}'. Valid types: {', '.join(sorted(RECORD_TYPES))}")
    result = await dns_service.add_record(hostname=server, zone=p.get("zone", ""), name=p.get("name", ""), rtype=rtype,
                                          data=p.get("data", ""), ttl=int(p.get("ttl") or 3600))
    if not result.success:
        raise ChangeError(400, result.message)
    return result


HANDLERS = {
    "link.condition": _link_condition,
    "link.clear": _link_clear,
    "interface.zone": _interface_zone,
    "dns.domain.create": _dns_domain,
    "dns.record.set": _dns_record,
}


async def revert_all() -> List[str]:
    """Undo every tracked change, keeping the preset's name. Returns what couldn't be undone."""
    errors = []

    # 1. Traffic shaping — clear all tracked configs
    if mutation_tracker.traffic_configs:
        from services.traffic_service import traffic_service
        for link_id in list(mutation_tracker.traffic_configs.keys()):
            try:
                success, msg = await traffic_service.clear_link_traffic_config(link_id)
                if not success:
                    errors.append(f"Traffic {link_id}: {msg}")
            except Exception as e:
                errors.append(f"Traffic {link_id}: {e}")

    # 2. DNS records — remove from zone files
    if mutation_tracker.dns_records:
        from services.dns_service import dns_service
        for rec in reversed(mutation_tracker.dns_records):
            try:
                success, msg = await dns_service.revert_record(rec)
                if not success:
                    errors.append(f"DNS record: {msg}")
            except Exception as e:
                errors.append(f"DNS record: {e}")

    # 3. DNS domains — remove zone files and TLD delegations
    if mutation_tracker.dns_domains:
        from services.dns_service import dns_service
        for domain_info in reversed(mutation_tracker.dns_domains):
            try:
                success, msg = await dns_service.revert_domain(domain_info)
                if not success:
                    errors.append(f"DNS domain: {msg}")
            except Exception as e:
                errors.append(f"DNS domain: {e}")

    # 4. Zone changes — revert to original zones
    if mutation_tracker.zone_changes:
        from services.network_service import network_service
        for key, params in list(mutation_tracker.zone_changes.items()):
            original_zone = params.get("original_zone_id", "")
            original_vlan = params.get("original_vlan_tag")
            if not original_zone:
                continue
            try:
                parts = key.split(":", 1)
                if len(parts) == 2:
                    result = await asyncio.to_thread(
                        network_service.change_customer_interface_zone, parts[0], parts[1], original_zone, original_vlan
                    )
                    if not result.success:
                        errors.append(f"Zone {key}: {result.message}")
            except Exception as e:
                errors.append(f"Zone {key}: {e}")

    # 5. Certificates issued: revoke them. One revoked can't be taken back; say so
    if mutation_tracker.certificates_issued:
        from services.ca_service import ca_service
        for cert in list(mutation_tracker.certificates_issued):
            if not cert.get("serial"):
                errors.append(f"Certificate for {cert.get('domain')}: its serial is unknown, so it stays valid")
                continue
            try:
                result = await ca_service.revoke_certificate(cert["ca_hostname"], cert["serial"], "superseded")
                if not result["success"]:
                    errors.append(f"Certificate {cert['serial']}: {result['message']}")
            except Exception as e:
                errors.append(f"Certificate {cert['serial']}: {e}")
    for cert in mutation_tracker.certificates_revoked:
        errors.append(f"Certificate {cert['serial']} on {cert['ca_hostname']} stays revoked: a CA can't take a revocation back")

    mutation_tracker.clear(keep_preset=True)
    return errors
