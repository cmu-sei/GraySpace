"""Whether this site can deploy a base.

GET /api/v1/core/preflight runs these checks, and so does a deploy job's first stage. Each check
is ok, warning, error or unchecked; a deploy is refused while any is an error. They cover what has
made first deploys fail: missing settings, the Proxmox API, storages that don't take snippets or
templates, missing bridges, SDN, SSH to the host, and capacity.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from config import settings
from models.base import Preflight, PreflightCheck
from services import base_versions
from services.proxmox_client import proxmox_client
from services.slices import environment, store
from services.topology_service import topology_service

REQUIRED = [
    "TF_VAR_proxmox_node",
    "TF_VAR_proxmox_host_addr",
    "TF_VAR_proxmox_blockstore",
    "TF_VAR_proxmox_filestore",
    "TF_VAR_proxmox_mgmt_bridge",
    "TF_VAR_proxmox_vm_bridge",
    "TF_VAR_environment_id",
    "TF_VAR_router_password",
]
# The Crucible provider's settings (terraform/providers.tf), needed when TF_VAR_configure_crucible is true
CRUCIBLE = ["username", "password", "auth_url", "token_url", "client_id", "client_scopes", "vm_api_url",
            "player_api_url", "caster_api_url", "user_id"]
# Container sizes the base Terraform sets itself (MiB, GiB); the rest come from the tfvars.
ROUTER = (256, 4)
MANAGEMENT = {"prometheus": (4096, 16)}
GIB = 1024 ** 3


def _check(name: str, status: str, message: str) -> PreflightCheck:
    return PreflightCheck(name=name, status=status, message=message)


def _true(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes")


def deployed_ref() -> Optional[dict]:
    """The deployed base version and scope from the tfvars, or None when nothing is deployed."""
    return topology_service.tfvars_data.get("base") if topology_service.tfvars_data else None


def resolve(version: Optional[str], scope: Optional[str]):
    """The version and scope a deploy means: the request's, else the deployed base's, else full."""
    deployed = deployed_ref() or {}
    return version or deployed.get("name"), scope or deployed.get("scope") or "full"


def generate_tfvars(version: str, scope: str) -> Dict:
    """The tfvars a deploy would write, generated into a temporary file (the parse stage's output)."""
    script = environment.codebase() / "scripts" / "generate_tfvars.py"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "tfvars.json"
        proc = subprocess.run([sys.executable, str(script), f"--scope={scope}", f"--base={version}", f"--output-file={out}"],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise ValueError((proc.stderr or proc.stdout).strip()[-1500:])
        return json.loads(out.read_text())


def required_capacity(tfvars: Dict, env: Dict[str, str]):
    """(memory MiB, disk GiB, guests) the base would allocate."""
    memory, disk, guests = 0, 0, 0
    for _ in tfvars.get("routers") or {}:
        memory, disk, guests = memory + ROUTER[0], disk + ROUTER[1], guests + 1
    for kind in ("core_dns", "core_ca"):
        for spec in (tfvars.get(kind) or {}).values():
            memory, disk, guests = memory + int(spec.get("memory") or 0), disk + int(spec.get("disk") or 0), guests + 1
    extra = list(MANAGEMENT.values())
    for m, d in extra:
        memory, disk, guests = memory + m, disk + d, guests + 1
    return memory, disk, guests


async def _ssh_port(host: str) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, 22), 5)
        writer.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


def _simulated(version: Optional[str], scope: Optional[str]) -> Preflight:
    """A simulated deployment (services/dev_jobs.py) has no site to check: only the version and live instances."""
    checks = [_check("simulated", "warning", "This deployment is simulated (DEV_SIMULATE_DEPLOYMENT): Proxmox, storage and SSH aren't checked")]
    if not version:
        checks.append(_check("version", "error", "No core is deployed; name a version (GET /api/v1/core/versions lists them)"))
    elif base_versions.get_version(version) is None:
        checks.append(_check("version", "error", base_versions.not_shipped(version)))
    else:
        checks.append(_check("version", "ok", f"{version} at {scope} scope"))
    deployed, live = deployed_ref(), store.live_instances()
    if live and deployed and version and (deployed.get("name"), deployed.get("scope")) != (version, scope):
        checks.append(_check("instances", "error", f"{len(live)} enclave instance(s) are live on {deployed.get('name')} ({deployed.get('scope')}); "
                             "destroy them before changing the core version or scope"))
    return Preflight(ready=not any(c.status == "error" for c in checks), version=version, scope=scope, checks=checks)


async def run(version: Optional[str] = None, scope: Optional[str] = None) -> Preflight:
    version, scope = resolve(version, scope)
    if settings.dev_simulate_deployment:
        return _simulated(version, scope)
    checks: List[PreflightCheck] = []

    available, why = environment.base_jobs_available()
    checks.append(_check("jobs", "ok" if available else "error", "This GraySpace can run core jobs" if available else why))
    env = environment.job_env() if environment.environment_file() else dict(os.environ)

    missing = [name for name in REQUIRED if not env.get(name)]
    if not env.get("PROXMOX_VE_API_TOKEN") and not (env.get("PROXMOX_VE_USERNAME") and env.get("PROXMOX_VE_PASSWORD")):
        missing.append("PROXMOX_VE_API_TOKEN (or PROXMOX_VE_USERNAME and PROXMOX_VE_PASSWORD)")
    env_id = env.get("TF_VAR_environment_id") or ""
    if missing:
        checks.append(_check("environment", "error", "Missing from the environment file: " + ", ".join(missing)))
    elif len(env_id) > 4:
        checks.append(_check("environment", "error", f"TF_VAR_environment_id '{env_id}' is longer than 4 characters; Proxmox vnet IDs would exceed 8 (V1)"))
    else:
        checks.append(_check("environment", "ok", "Every required setting is present"))

    tfvars = None
    if not version:
        checks.append(_check("version", "error", "No core is deployed; name a version (GET /api/v1/core/versions lists them)"))
    elif base_versions.get_version(version) is None:
        checks.append(_check("version", "error", base_versions.not_shipped(version)))
    else:
        try:
            tfvars = await asyncio.to_thread(generate_tfvars, version, scope)
            checks.append(_check("version", "ok", f"{version} at {scope} scope: {len(tfvars.get('routers') or {})} routers"))
        except (ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            checks.append(_check("version", "error", f"{version} at {scope} scope doesn't generate: {e}"))

    deployed = deployed_ref()
    live = store.live_instances()
    if live and deployed and version and (deployed.get("name"), deployed.get("scope")) != (version, scope):
        checks.append(_check("instances", "error", f"{len(live)} enclave instance(s) are live on {deployed.get('name')} ({deployed.get('scope')}); "
                             "destroy them before changing the core version or scope"))

    node = await asyncio.to_thread(proxmox_client.get_node_status)
    if node is None:
        checks.append(_check("proxmox", "error", f"The Proxmox API at {env.get('TF_VAR_proxmox_host_addr') or '?'} or node "
                             f"'{env.get('TF_VAR_proxmox_node') or '?'}' didn't answer"))
    else:
        checks.append(_check("proxmox", "ok", f"Node '{env.get('TF_VAR_proxmox_node')}' answers"))
        checks.extend(await _node_checks(env))
        if tfvars is not None:
            checks.append(await _capacity_check(node, tfvars, env))

    host = env.get("TF_VAR_proxmox_host_addr")
    if host:
        ok = await _ssh_port(host)
        checks.append(_check("ssh", "ok" if ok else "error", f"SSH to {host} is open" if ok else
                             f"SSH to {host}:22 is closed; the provider uploads snippets (VM cloud-init) over SSH"))

    if crucible_enabled(env):
        checks.extend(await _crucible_checks(env))

    return Preflight(ready=not any(c.status == "error" for c in checks), version=version, scope=scope, checks=checks)


async def _node_checks(env: Dict[str, str]) -> List[PreflightCheck]:
    checks = []
    storages = await asyncio.to_thread(proxmox_client.get_node_storages) or []
    by_name = {s.get("storage"): s for s in storages}
    for setting, content in (("TF_VAR_proxmox_blockstore", ["rootdir"]), ("TF_VAR_proxmox_filestore", ["snippets", "vztmpl"])):
        name = env.get(setting)
        if not name:
            continue
        storage = by_name.get(name)
        if storage is None:
            checks.append(_check("storage", "error", f"Storage '{name}' ({setting}) isn't on the node"))
            continue
        has = set((storage.get("content") or "").split(","))
        lacking = [c for c in content if c not in has]
        checks.append(_check("storage", "error" if lacking else "ok",
                             f"Storage '{name}' doesn't allow {', '.join(lacking)} content; enable it in Datacenter > Storage" if lacking
                             else f"Storage '{name}' allows {', '.join(content)}"))

    # A bridge setting may name a bridge on the node or an SDN vnet (which Proxmox lists apart).
    networks = await asyncio.to_thread(proxmox_client.get_node_networks) or []
    vnets = await asyncio.to_thread(proxmox_client.get_sdn_vnets) or []
    ifaces = {n.get("iface") for n in networks}
    sdn = {v.get("vnet") for v in vnets}
    for setting in ("TF_VAR_proxmox_mgmt_bridge", "TF_VAR_proxmox_vm_bridge"):
        name = env.get(setting)
        if not name:
            continue
        if name in ifaces or name in sdn:
            checks.append(_check("bridges", "ok", f"Bridge '{name}' exists" + (" (SDN vnet)" if name in sdn and name not in ifaces else "")))
        else:
            checks.append(_check("bridges", "error", f"Bridge '{name}' ({setting}) is neither on the node nor an SDN vnet"))

    zones = await asyncio.to_thread(proxmox_client.get_sdn_zones)
    checks.append(_check("sdn", "ok" if zones is not None else "error",
                         "SDN is available" if zones is not None else "SDN can't be read; the core creates SDN zones and vnets"))
    if zones is not None:
        checks.append(_environment_id_check(env, {z.get("zone") for z in zones}))
    return checks


def _environment_id_check(env: Dict[str, str], zones) -> PreflightCheck:
    """Another deployment on the cluster may use the same environment_id (several deployments can
    share a cluster). Its zone is ours only when this deployment has deployed (it has a tfvars)."""
    env_id = env.get("TF_VAR_environment_id") or ""
    zone = f"{env_id}1"
    if env_id and zone in zones and deployed_ref() is None:
        return _check("environment-id", "error", f"SDN zone '{zone}' already exists on the cluster, but this deployment has deployed "
                      f"nothing: another deployment uses environment_id '{env_id}'. Pick another, or destroy what uses it")
    return _check("environment-id", "ok", f"environment_id '{env_id}' is this deployment's on the cluster")


async def _capacity_check(node: Dict, tfvars: Dict, env: Dict[str, str]) -> PreflightCheck:
    memory, disk, guests = required_capacity(tfvars, env)
    mem = node.get("memory") or {}
    free_mem = mem.get("available", mem.get("free"))
    storage = await asyncio.to_thread(proxmox_client.get_storage_status, env.get("TF_VAR_proxmox_blockstore") or "")
    free_disk = (storage or {}).get("avail")
    short = []
    if free_mem is not None and memory * 1024 * 1024 > free_mem:
        short.append(f"memory: needs {memory} MiB, {free_mem // (1024 * 1024)} MiB available")
    if free_disk is not None and disk * GIB > free_disk:
        short.append(f"storage: needs {disk} GiB, {free_disk // GIB} GiB free")
    if short:
        return _check("capacity", "warning", f"{guests} guests may not fit ({'; '.join(short)}). Proxmox can overcommit, but guests may fail to start")
    return _check("capacity", "ok", f"{guests} guests, about {memory} MiB and {disk} GiB")


def crucible_enabled(env: Dict[str, str]) -> bool:
    return _true(env.get("TF_VAR_configure_crucible"))


async def _crucible_checks(env: Dict[str, str]) -> List[PreflightCheck]:
    """The settings, a token from Keycloak for the provider's account, and the Player API accepting it
    (services/crucible_api.py)."""
    from services import crucible_api

    missing = [f"TF_VAR_crucible_{name}" for name in CRUCIBLE if not env.get(f"TF_VAR_crucible_{name}")]
    if missing:
        return [_check("crucible", "error", "TF_VAR_configure_crucible is true, but these are empty: " + ", ".join(missing))]
    cfg = {name: env[f"TF_VAR_crucible_{name}"] for name in CRUCIBLE}
    return [_check("crucible", status, message) for status, message in await crucible_api.check(cfg)]
