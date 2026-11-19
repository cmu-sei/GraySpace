"""A simulated Proxmox deployment for local development (DEV_SIMULATE_DEPLOYMENT=true).

Without a Proxmox node, the topology routes show nothing: they list only what Proxmox reports as
deployed. In this mode the backend instead reports every guest of the tfvars, and the guests of
live slice instances, as running containers. Start, stop and restart change that in memory, and
the stopped hostnames are written to settings.dev_simulation_state_file, which the router-metrics
simulator (aspire/simulator/router_metrics.py) reads, so a router stopped in the UI goes down on
the map too. Nothing here is reachable over SSH, so DNS, CA, console and traffic views report
errors, and slice jobs are unavailable.

Never enable it against a real deployment: the Aspire AppHost turns it on only in its simulated mode.
"""
import json
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional

from config import settings

logger = logging.getLogger(__name__)

MANAGEMENT = ["prometheus"]
GUEST_GROUPS = ["routers", "core_dns", "core_ca"]

_lock = threading.Lock()
_stopped: Optional[set] = None


def enabled() -> bool:
    return bool(settings.dev_simulate_deployment)


def state_file() -> Path:
    return Path(settings.dev_simulation_state_file) if settings.dev_simulation_state_file else settings.data_dir / "dev-simulation-stopped.json"


def _load() -> set:
    global _stopped
    if _stopped is None:
        try:
            _stopped = set(json.loads(state_file().read_text()))
        except (OSError, ValueError):
            _stopped = set()
    return _stopped


def _save():
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(_stopped)))
    tmp.replace(path)


def _guests() -> List[Dict]:
    """(hostname, resource_type, tags) for everything a deployment of the tfvars would have."""
    from services.slices import store
    from services.topology_service import topology_service

    tfvars = topology_service.tfvars_data
    env = settings.environment_id
    out = []
    for group in GUEST_GROUPS:
        for hostname in tfvars.get(group) or {}:
            out.append((hostname, "lxc", env))
    out += [(name, "lxc", env) for name in MANAGEMENT]
    for inst in store.live_instances():
        alloc = inst.allocation
        if not alloc or "apply" not in inst.stages_done:
            continue
        tags = f"{env};slice-{inst.id}"
        out += [(s.edge, "lxc", tags) for s in alloc.sites]
        out += [(h.hostname, "qemu" if h.type == "vm" else "lxc", tags) for h in alloc.hosts]
    return out


def containers() -> List[Dict]:
    with _lock:
        stopped = _load()
        return [
            {
                "vmid": 10000 + i,
                "name": name,
                "node": settings.proxmox_node,
                "status": "stopped" if name in stopped else "running",
                "tags": tags,
                "uptime": 0 if name in stopped else 3600,
                "maxmem": 512 * 2**20,
                "maxdisk": 8 * 2**30,
                "resource_type": resource_type,
            }
            for i, (name, resource_type, tags) in enumerate(_guests())
        ]


def set_running(hostname: str, running: bool) -> bool:
    if hostname not in {c["name"] for c in containers()}:
        return False
    with _lock:
        stopped = _load()
        (stopped.discard if running else stopped.add)(hostname)
        _save()
    logger.info(f"Simulated deployment: {hostname} {'started' if running else 'stopped'}")
    return True


def node_status() -> Dict:
    return {"cpu": 0.12, "cpuinfo": {"cpus": 64}, "memory": {"total": 512 * 2**30, "free": 384 * 2**30}}


def storage_status() -> Dict:
    return {"total": 4 * 2**40, "used": 2**40, "avail": 3 * 2**40}
