"""Base jobs: deploy, configure and destroy the base through the API.

They run in the slice job runner, under the same node lock, and do what task parse, task apply,
task configure and task destroy do:

    deploy_base:    preflight → parse → guard → init → apply → configure → refresh → check [→ player]
    configure_base: configure → refresh → check
    destroy_base:   guard → player → destroy → refresh

player: a deploy outside a rollout applies the Player view again when there is one (its VM IDs may
have changed); a destroy removes it first (services/player.py).

Every stage is idempotent, so a retried job resumes at the stage that failed. The job's result
holds its request (version, scope, configure, mgmt_gateway) and, after check, the base's health.
"""
import asyncio
import ipaddress
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

from config import settings
from models.instances import STAGE_TITLES, JobRecord
from services import base_preflight
from services.slices import environment, store
from services.slices.runner import KINDS, JobContext, StageError, new_job
from services.slices.stages import ansible_env, converge, tf_env, tf_init

TARGET = "base"
DEPLOY_STAGES = ["preflight", "parse", "guard", "init", "apply", "configure", "refresh", "check"]
CONFIGURE_STAGES = ["configure", "refresh", "check"]
DESTROY_STAGES = ["guard", "player", "destroy", "refresh"]
KIND_NAMES = ("deploy_base", "configure_base", "destroy_base")
TITLES = {
    "preflight": "Check the platform",
    "parse": "Prepare the core",
    "guard": "Check the enclaves are not affected",
    "init": "Prepare the build",
    "apply": "Create the routers and services",
    "configure": "Configure routing and services",
    "refresh": "Update the map",
    "check": "Wait for routing to come up",
    "destroy": "Remove the routers and services",
    "player": "Update the Player view",
}
for _kind, _stages in (("deploy_base", DEPLOY_STAGES + ["player"]), ("configure_base", CONFIGURE_STAGES), ("destroy_base", DESTROY_STAGES)):
    STAGE_TITLES[_kind] = {s: TITLES[s] for s in _stages}
STAGE_TITLES["destroy_base"]["player"] = "Remove the Player view"



def deploy_stages(configure: bool = True) -> List[str]:
    return DEPLOY_STAGES if configure else [s for s in DEPLOY_STAGES if s not in ("configure", "check")]


def tfvars_path() -> Path:
    return Path(settings.tfvars_path)


def _var_file_args() -> List[str]:
    """terraform/terraform.tfvars.json is loaded automatically; a tfvars elsewhere is passed."""
    path = tfvars_path()
    return [] if path.resolve() == (environment.base_module() / "terraform.tfvars.json").resolve() else [f"-var-file={path}"]


def active_base_job() -> Optional[JobRecord]:
    """A base job or a rollout (which may change the base) queued or running."""
    return next((j for j in store.list_jobs() if j.kind in KIND_NAMES + ("rollout",) and j.state in ("queued", "running")), None)


def valid_gateway(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        raise ValueError(f"mgmt_gateway '{value}' is not an IP address")


# --- Stages ---


async def stage_preflight(ctx: JobContext, request: Dict):
    result = await base_preflight.run(request.get("version"), request.get("scope"))
    for check in result.checks:
        ctx.log(f"{check.status.upper():9} {check.name}: {check.message}")
    if not result.ready:
        raise StageError("Preflight failed: " + "; ".join(f"{c.name}: {c.message}" for c in result.checks if c.status == "error"))
    request.update(version=result.version, scope=result.scope)
    ctx.update(result=request)


async def stage_parse(ctx: JobContext, request: Dict):
    script = environment.codebase() / "scripts" / "generate_tfvars.py"
    tfvars_path().parent.mkdir(parents=True, exist_ok=True)
    await ctx.run([sys.executable, str(script), f"--scope={request['scope']}", f"--base={request['version']}",
                   f"--output-file={tfvars_path()}"], environment.codebase())


async def stage_guard(ctx: JobContext, mode: str):
    scripts = environment.codebase() / "scripts"
    await ctx.run([sys.executable, str(scripts / "check_slice_attachments.py"), mode, f"--tfvars={tfvars_path()}",
                   f"--data-dir={store.data_dir()}"], environment.codebase())


async def stage_init(ctx: JobContext):
    await tf_init(ctx, environment.base_module(), quiet=False)


async def stage_apply(ctx: JobContext):
    from services import player

    # The base module's view from before terraform/player/ goes first: refreshing it panics
    await player.migrate_legacy(ctx)
    await converge(ctx, environment.base_module(), _var_file_args(), what="core apply")


async def stage_configure(ctx: JobContext, request: Dict):
    argv = ["ansible-playbook", "main.yml"]
    if request.get("mgmt_gateway"):
        argv += ["-e", f"mgmt_gateway={request['mgmt_gateway']}"]
    await ctx.run(argv, environment.codebase() / "ansible", env=ansible_env())


def prometheus_from_inventory() -> Optional[str]:
    """Prometheus's address from the inventory Terraform writes, as the container's entrypoint reads it."""
    path = environment.codebase() / "ansible" / "inventory" / "ungrouped_inventory.ini"
    try:
        for line in path.read_text().splitlines():
            parts = line.split()
            if parts and parts[0] == "prometheus":
                m = re.search(r"ansible_host=(\S+)", line)
                if m:
                    return f"http://{m.group(1)}:9090"
    except OSError:
        pass
    return None


async def refresh(ctx: Optional[JobContext] = None, destroyed: bool = False):
    """Reload what the backend read at startup: the tfvars, link topology, Prometheus's address and
    the SSH key. Unlike POST /topology/reload, overlay changes survive unless the base was destroyed."""
    from services.prometheus_client import prometheus_client
    from services.ssh_client import ssh_client
    from services.topology_service import topology_service
    from tasks.state_monitor import state_monitor

    def log(message):
        if ctx:
            ctx.log(message)

    topology_service.deployed_hostnames_cache = None
    topology_service.cache_timestamp = None
    topology_service._load_tfvars()
    await state_monitor._load_ip_mapping()
    state_monitor._load_link_topology()
    ssh_client.reset()
    log(f"Reloaded the tfvars: {len(topology_service.tfvars_data.get('routers') or {})} routers")
    if settings.prometheus_from_inventory:
        url = prometheus_from_inventory()
        if url and url != prometheus_client.base_url:
            settings.prometheus_url = url
            prometheus_client.base_url = url
            log(f"Prometheus is at {url}")
    if destroyed:
        from services.mutation_tracker import mutation_tracker

        mutation_tracker.clear()
        log("Cleared the overlay's tracked changes: the core they applied to is gone")


async def stage_check(ctx: JobContext, request: Dict):
    """Wait for every router to run and every BGP session to come up. Fails only if no router runs."""
    from services.base_service import get_deployed_base

    deadline = asyncio.get_running_loop().time() + settings.base_check_timeout
    while True:
        ctx.check_cancel()
        health = await get_deployed_base()
        r, b = health.routers, health.bgp
        ctx.attempt(f"routers {r.running if r.running is not None else '?'}/{r.expected}, "
                    f"BGP {b.established if b.established is not None else '?'}/{b.total if b.total is not None else '?'}")
        if health.status == "healthy" or asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(15)
    ctx.attempt(None)
    request["health"] = health.model_dump()
    ctx.update(result=request)
    if not r.running:
        raise StageError(f"No router is running ({health.status}); see the log, then retry the job")
    if health.status != "healthy":
        ctx.log(f"WARNING: the core is {health.status} after {settings.base_check_timeout}s: "
                f"not running {r.not_running[:10]}, BGP not established {len(b.not_established)}")
    else:
        ctx.log(f"Healthy: {r.running}/{r.expected} routers, {b.established}/{b.total} BGP sessions")


def destroy_args() -> List[str]:
    """With Crucible resources in the state, destroy without refreshing: the Crucible provider
    (2.5.0) panics while it reads the Player view during the refresh (playerViewRead); its delete
    path works. The state says, not TF_VAR_configure_crucible: managed deployments have it false
    now, with the base module's old view still in state until it is migrated."""
    from services import player

    if player.has_crucible_state(environment.base_module() / "terraform.tfstate"):
        return ["-refresh=false"]
    return []


async def stage_player(ctx: JobContext, remove: bool):
    from services import player, rollout

    if remove:
        if player.applied() is None and not player.has_crucible_state(player.module() / "terraform.tfstate"):
            ctx.log("No Player view")
            return
        await player.destroy(ctx)
        return
    draft = rollout.get_draft()
    if draft.player is None or not player.enabled():
        ctx.log("No Player view in the draft")
        return
    await player.apply(ctx, draft)


async def stage_destroy(ctx: JobContext):
    module = environment.base_module()
    # A provider constraint may have changed since the base was deployed (init -upgrade if so)
    await tf_init(ctx, module)
    attempts = settings.slice_apply_attempts
    crucible = destroy_args()
    if crucible:
        ctx.log("Crucible is configured: destroying with -refresh=false")
    for i in range(1, attempts + 1):
        ctx.check_cancel()
        ctx.attempt(f"destroy attempt {i}/{attempts}")
        # The first attempt doesn't refresh: a destroy needs no fresh state, and a refresh waits on
        # every VM's guest agent for its IPs (agent.timeout), which a VM that never got one turns
        # into the whole timeout. Guests already gone are fine (the provider's delete takes "does
        # not exist" as done); for anything else left, later attempts refresh, which drops it.
        extra = crucible or (["-refresh=false"] if i == 1 else [])
        await ctx.run(["terraform", "destroy", "-auto-approve", "-input=false", *extra, *_var_file_args(),
                       f"-parallelism={environment.parallelism('apply')}"], module, env=tf_env(), check=False)
        _, out = await ctx.run(["terraform", "state", "list"], module, env=tf_env(), check=False, quiet=True)
        if not out.strip():
            ctx.attempt(None)
            ctx.log(f"Destroyed on attempt {i}")
            break
        ctx.log(f"{len(out.split())} resources remain")
    else:
        raise StageError(f"The core still has resources after {attempts} destroy attempts; see the log, then retry the job")
    # Nothing is deployed now: GET /base reports not_deployed until the next deploy parses again.
    if tfvars_path().exists():
        tfvars_path().unlink()
        ctx.log(f"Removed {tfvars_path()}")


# --- Jobs ---


def _resume(job: JobRecord, stages: List[str]) -> List[str]:
    """A retried job starts at the stage that failed."""
    if job.resume_from and job.resume_from in stages:
        return stages[stages.index(job.resume_from):]
    return stages


async def _run(ctx: JobContext, job: JobRecord, handlers: Dict):
    request = dict(job.result or {})
    for name in _resume(job, job.stages):
        ctx.stage(name)
        await handlers[name](request)


async def run_deploy(ctx: JobContext, job: JobRecord):
    await _run(ctx, job, {
        "preflight": lambda r: stage_preflight(ctx, r),
        "parse": lambda r: stage_parse(ctx, r),
        "guard": lambda r: stage_guard(ctx, "apply"),
        "init": lambda r: stage_init(ctx),
        "apply": lambda r: stage_apply(ctx),
        "configure": lambda r: stage_configure(ctx, r),
        "refresh": lambda r: refresh(ctx),
        "check": lambda r: stage_check(ctx, r),
        "player": lambda r: stage_player(ctx, remove=False),
    })


async def run_configure(ctx: JobContext, job: JobRecord):
    await _run(ctx, job, {
        "configure": lambda r: stage_configure(ctx, r),
        "refresh": lambda r: refresh(ctx),
        "check": lambda r: stage_check(ctx, r),
    })


async def run_destroy(ctx: JobContext, job: JobRecord):
    await _run(ctx, job, {
        "guard": lambda r: stage_guard(ctx, "destroy"),
        "player": lambda r: stage_player(ctx, remove=True),
        "destroy": lambda r: stage_destroy(ctx),
        "refresh": lambda r: refresh(ctx, destroyed=True),
    })


def submit(kind: str, request: Dict) -> JobRecord:
    """Queue a base job; the caller has checked no other base job is active. A deploy re-applies the
    Player view when there is one (a rollout's base child doesn't: the rollout does, at its end)."""
    from services import player

    stages = {"deploy_base": deploy_stages(request.get("configure", True)), "configure_base": CONFIGURE_STAGES,
              "destroy_base": DESTROY_STAGES}[kind]
    if kind == "deploy_base" and player.applied() is not None:
        stages = stages + ["player"]
    return new_job(kind, TARGET, stages, result=request)


KINDS["deploy_base"] = run_deploy
KINDS["configure_base"] = run_configure
KINDS["destroy_base"] = run_destroy
