"""Runtime actions on a ready instance.

POST /api/v1/instances/{id}/actions applies one of:

- link.condition {target: link ID, params: latency_ms, jitter_ms, packet_loss_percent, bandwidth_mbps}
- link.clear {target: link ID}
- link.down / link.up {target: link ID}: the link's interfaces on both base routers
- dns.record.set {params: name, type, host | data, ttl}: within the instance's domains
- dns.record.delete {params: name, type}
- host.start / host.stop {target: host name or hostname in the instance, or a site's name or edge}
- reset: undo the instance's runtime actions: clear the conditions actions set, bring up the links
  actions took down, delete the DNS records actions added, and start the hosts that are stopped.
  What the spec built (its conditions and records) stays

Everything an action changes is recorded in the instance, never in active_mutations.json, so
Reset changes leaves it alone, and destroying the instance undoes it. An enclave preset is a
list of these actions, applied after a reset (services/presets.py). Only
mechanisms Terraform doesn't model are used: tc, interface state, DNS records, start and stop.
A base link takes conditions from one owner at a time: the base overlay or one instance;
another owner's request is refused with LINK_OWNED.
"""
import asyncio
import logging
import re
from typing import Any, Callable, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from models.instances import CreatedRecord, InstanceRecord, LinkConditionApplied
from models.slices import FQDN, record_data_problem
from services import events
from services.slices import plan, service, store

logger = logging.getLogger(__name__)

ACTIONS = ["link.condition", "link.clear", "link.down", "link.up", "dns.record.set", "dns.record.delete", "host.start", "host.stop", "reset"]

LINK_COLORS = {"down": "#e74c3c", "shutdown": "#f39c12"}


class ActionRequest(BaseModel):
    action: Literal["link.condition", "link.clear", "link.down", "link.up", "dns.record.set", "dns.record.delete", "host.start", "host.stop",
                    "reset"]
    target: Optional[str] = Field(None, description="Link ID (r1:r2) for link actions; host name or hostname for host actions")
    params: Dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseModel):
    action: str
    target: Optional[str]
    message: str
    instance: str


class ActionError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def canonical(link_id: str) -> str:
    """A link ID in the order the API uses (r1:r2); the topology routes accept either order."""
    return plan.canonical_link(service.context(), link_id) or link_id


def _held_by(inst: InstanceRecord, link_id: str) -> bool:
    return any(c.link_id == link_id for c in inst.conditions) or link_id in inst.links_down


def _owner(link_id: str, instance_id: str) -> Optional[str]:
    """Who else holds a link (a canonical ID): another instance or the base overlay."""
    from services.mutation_tracker import mutation_tracker

    if any(canonical(k) == link_id for k in mutation_tracker.traffic_configs):
        return "the base overlay"
    for inst in store.live_instances():
        if inst.id != instance_id and _held_by(inst, link_id):
            return f"instance {inst.id}"
    return None


def link_owner(link_id: str) -> Optional[str]:
    """The live instance holding a link, in either router order, for the overlay to refuse it."""
    link_id = canonical(link_id)
    for inst in store.live_instances():
        if _held_by(inst, link_id):
            return inst.id
    return None


def _link(target: Optional[str], instance_id: str) -> str:
    if not target:
        raise ActionError(422, "INVALID_SPEC", "target: give a link ID, e.g. lum-chi:are-chi")
    canonical = plan.canonical_link(service.context(), target)
    if canonical is None:
        raise ActionError(404, "UNKNOWN_LINK", f"No link '{target}' between two deployed routers")
    owner = _owner(canonical, instance_id)
    if owner:
        raise ActionError(409, "LINK_OWNED", f"{canonical} is held by {owner}")
    return canonical


async def _set_interfaces(link_id: str, up: bool) -> None:
    from services.ssh_client import ssh_client
    from services.slices.stages import base_mgmt_ip

    link = service.context().links[link_id]
    done = []
    for router, iface in ((link["source"], link["source_interface"]), (link["target"], link["target_interface"])):
        ip = await base_mgmt_ip(router)
        error = None
        if not ip:
            error = f"No management address for {router}"
        else:
            code, _, err = await ssh_client.execute_command(ip, f"ip link set {iface} {'up' if up else 'down'}", timeout=15)
            if code != 0:
                error = f"{router}: {err.strip()}"
        if error:
            # Don't leave the link half changed: undo the end already done.
            for ip_done, iface_done in done:
                await ssh_client.execute_command(ip_done, f"ip link set {iface_done} {'down' if up else 'up'}", timeout=15)
            raise ActionError(502, "UNREACHABLE", error)
        done.append((ip, iface))


async def _link_condition(record: InstanceRecord, req: ActionRequest) -> str:
    from models.traffic import TrafficConfig
    from services.traffic_service import traffic_service

    link_id = _link(req.target, record.id)
    allowed = {"latency_ms", "jitter_ms", "packet_loss_percent", "bandwidth_mbps"}
    unknown = set(req.params) - allowed
    if unknown:
        raise ActionError(422, "INVALID_SPEC", f"Unknown params: {', '.join(sorted(unknown))}")
    try:
        condition = LinkConditionApplied(link_id=link_id, origin="action", **req.params)
        config = TrafficConfig(**{k: v for k, v in req.params.items()})
    except ValueError as e:
        raise ActionError(422, "INVALID_SPEC", str(e))
    ok, message = await traffic_service.apply_link_traffic_config(link_id, config)
    if not ok:
        raise ActionError(502, "UNREACHABLE", message)
    with store.update_instance(record.id) as r:
        r.conditions = [c for c in r.conditions if c.link_id != link_id] + [condition]
    events.feature_update(link_id, "link", {"condition": condition.model_dump(exclude={"link_id", "origin"}, exclude_none=True), "instance": record.id})
    return message


async def _link_clear(record: InstanceRecord, req: ActionRequest) -> str:
    from services.traffic_service import traffic_service

    link_id = _link(req.target, record.id)
    if not any(c.link_id == link_id for c in record.conditions):
        return f"{link_id} has no conditions from this instance"
    ok, message = await traffic_service.clear_link_traffic_config(link_id)
    if not ok:
        raise ActionError(502, "UNREACHABLE", message)
    with store.update_instance(record.id) as r:
        r.conditions = [c for c in r.conditions if c.link_id != link_id]
    events.feature_update(link_id, "link", {"condition": None, "instance": None})
    return message


async def _link_down(record: InstanceRecord, req: ActionRequest) -> str:
    link_id = _link(req.target, record.id)
    await _set_interfaces(link_id, up=False)
    with store.update_instance(record.id) as r:
        if link_id not in r.links_down:
            r.links_down.append(link_id)
    events.feature_update(link_id, "link", {"state": "down", "color": LINK_COLORS["down"], "instance": record.id})
    return f"{link_id} is down"


async def _link_up(record: InstanceRecord, req: ActionRequest) -> str:
    link_id = _link(req.target, record.id)
    await _set_interfaces(link_id, up=True)
    with store.update_instance(record.id) as r:
        r.links_down = [x for x in r.links_down if x != link_id]
    events.feature_update(link_id, "link", {"state": "unknown", "instance": None})
    return f"{link_id} is up; its state updates once routing converges"


def _domains(record: InstanceRecord) -> List[str]:
    return [d.domain for d in record.domains]


async def _dns_set(record: InstanceRecord, req: ActionRequest) -> str:
    from services.dns_service import dns_service
    from services.slices.stages import record_target, zone_of

    name = (req.params.get("name") or "").lower().rstrip(".")
    rtype = (req.params.get("type") or "A").upper()
    zone = zone_of(name, _domains(record))
    if zone is None:
        raise ActionError(422, "UNKNOWN_DOMAIN", f"'{name}' is not inside a domain of instance {record.id}: {', '.join(_domains(record)) or 'none'}")
    if rtype not in ("A", "CNAME", "TXT", "MX"):
        raise ActionError(422, "INVALID_SPEC", f"Unsupported record type {rtype}")
    if not re.fullmatch(FQDN, name) or len(name) > 253:
        raise ActionError(422, "INVALID_SPEC", f"'{name}' is not a fully qualified name")
    data = record_target(record, req.params["host"]) if req.params.get("host") else req.params.get("data")
    if not data:
        raise ActionError(422, "UNKNOWN_HOST" if req.params.get("host") else "INVALID_SPEC", "Give params.data, or params.host naming a host of the instance")
    problem = record_data_problem(rtype, str(data))
    if problem:
        raise ActionError(422, "INVALID_SPEC", problem)
    try:
        ttl = int(req.params.get("ttl") or 3600)
    except (TypeError, ValueError):
        raise ActionError(422, "INVALID_SPEC", "ttl must be a number of seconds")
    if not 60 <= ttl <= 604800:
        raise ActionError(422, "INVALID_SPEC", "ttl must be between 60 and 604800")
    server = next(d.hosting_server for d in record.domains if d.domain == zone)
    relative = "@" if name == zone else name[: -len(zone) - 1]
    for old in [r for r in record.records if r.fqdn == name and r.type == rtype]:
        await dns_service.revert_record({"server": old.server, "zone": old.zone, "name": old.name, "type": old.type, "data": old.data, "ttl": old.ttl})
        with store.update_instance(record.id) as r:
            r.records = [x for x in r.records if x != old]
    result = await dns_service.add_record(server, zone, relative, rtype, data, ttl, track=False)
    if not result.success:
        raise ActionError(502, "UNREACHABLE", result.message)
    with store.update_instance(record.id) as r:
        r.records.append(CreatedRecord(server=server, zone=zone, name=relative, fqdn=name, type=rtype, data=data, ttl=ttl, origin="action"))
    return f"{name} {rtype} {data}"


async def _dns_delete(record: InstanceRecord, req: ActionRequest) -> str:
    from services.dns_service import dns_service

    name = (req.params.get("name") or "").lower().rstrip(".")
    rtype = (req.params.get("type") or "A").upper()
    matches = [r for r in record.records if r.fqdn == name and r.type == rtype]
    if not matches:
        raise ActionError(404, "UNKNOWN_DOMAIN", f"Instance {record.id} has no {rtype} record {name}")
    for old in matches:
        await dns_service.revert_record({"server": old.server, "zone": old.zone, "name": old.name, "type": old.type, "data": old.data, "ttl": old.ttl})
        with store.update_instance(record.id) as r:
            r.records = [x for x in r.records if x != old]
    return f"Deleted {rtype} {name}"


def _hostname(record: InstanceRecord, target: Optional[str]) -> str:
    alloc = record.allocation
    for h in alloc.hosts if alloc else []:
        if target in (h.name, h.hostname):
            return h.hostname
    for s in alloc.sites if alloc else []:
        if target in (s.edge, s.name):
            return s.edge
    raise ActionError(404, "UNKNOWN_HOST", f"No host '{target}' in instance {record.id}")


async def _host_power(record: InstanceRecord, req: ActionRequest, start: bool) -> str:
    from services.proxmox_client import has_tag, proxmox_client

    hostname = _hostname(record, req.target)
    container = await asyncio.to_thread(proxmox_client.get_container_by_hostname, hostname)
    if not container or not has_tag(container.get("tags"), f"slice-{record.id}"):
        raise ActionError(404, "UNKNOWN_HOST", f"{hostname} is not running on Proxmox as part of instance {record.id}")
    ok = await asyncio.to_thread(proxmox_client.start_container if start else proxmox_client.stop_container, hostname)
    if not ok:
        raise ActionError(502, "UNREACHABLE", f"Proxmox did not {'start' if start else 'stop'} {hostname}")
    events.feature_update(f"{record.id}/{_feature_name(record, hostname)}", "host", {"status": "running" if start else "stopped"})
    return f"{hostname} {'started' if start else 'stopped'}"


def _feature_name(record: InstanceRecord, hostname: str) -> str:
    for h in record.allocation.hosts:
        if h.hostname == hostname:
            return h.name
    return hostname


async def _reset(record: InstanceRecord, req: ActionRequest) -> str:
    from services.dns_service import dns_service
    from services.proxmox_client import proxmox_client

    done: List[str] = []
    for c in [c for c in record.conditions if c.origin == "action"]:
        await _link_clear(record, ActionRequest(action="link.clear", target=c.link_id))
        done.append(f"cleared {c.link_id}")
    for link_id in list(record.links_down):
        await _link_up(store.get_instance(record.id), ActionRequest(action="link.up", target=link_id))
        done.append(f"{link_id} up")
    for old in [r for r in store.get_instance(record.id).records if r.origin == "action"]:
        await dns_service.revert_record({"server": old.server, "zone": old.zone, "name": old.name, "type": old.type, "data": old.data, "ttl": old.ttl})
        with store.update_instance(record.id) as r:
            r.records = [x for x in r.records if x != old]
        done.append(f"deleted {old.type} {old.fqdn}")
    names = [h.hostname for h in record.allocation.hosts] + [s.edge for s in record.allocation.sites] if record.allocation else []
    for hostname in names:
        container = await asyncio.to_thread(proxmox_client.get_container_by_hostname, hostname)
        if container and container.get("status") == "stopped":
            await _host_power(record, ActionRequest(action="host.start", target=hostname), True)
            done.append(f"started {hostname}")
    return "Reset: " + ", ".join(done) if done else "Nothing to reset"


HANDLERS: Dict[str, Callable] = {
    "link.condition": _link_condition,
    "link.clear": _link_clear,
    "link.down": _link_down,
    "link.up": _link_up,
    "dns.record.set": _dns_set,
    "dns.record.delete": _dns_delete,
    "host.start": lambda r, q: _host_power(r, q, True),
    "host.stop": lambda r, q: _host_power(r, q, False),
    "reset": _reset,
}


async def apply(instance_id: str, req: ActionRequest) -> ActionResult:
    record = store.get_instance(instance_id)
    if record is None or not record.live:
        raise ActionError(404, "UNKNOWN_INSTANCE", f"No live instance '{instance_id}'")
    if record.state != "ready":
        raise ActionError(409, "NOT_READY", f"Instance {instance_id} is {record.state}; actions need a ready instance")
    message = await HANDLERS[req.action](record, req)
    events.publish({"type": "action.applied", "instance": instance_id, "action": req.action, "target": req.target, "message": message})
    return ActionResult(action=req.action, target=req.target, message=message, instance=instance_id)


async def release_links(instance_id: str, log: Callable[[str], None] = logger.info) -> None:
    """Undo an instance's link conditions and bring up the links it took down (destroy)."""
    from services.traffic_service import traffic_service

    record = store.get_instance(instance_id)
    for c in list(record.conditions):
        ok, message = await traffic_service.clear_link_traffic_config(c.link_id)
        log(message)
        with store.update_instance(instance_id) as r:
            r.conditions = [x for x in r.conditions if x.link_id != c.link_id]
        events.feature_update(c.link_id, "link", {"condition": None, "instance": None})
    for link_id in list(record.links_down):
        try:
            await _set_interfaces(link_id, up=True)
            log(f"{link_id} is up")
        except ActionError as e:
            log(f"WARNING: could not bring {link_id} up: {e}")
        with store.update_instance(instance_id) as r:
            r.links_down = [x for x in r.links_down if x != link_id]
