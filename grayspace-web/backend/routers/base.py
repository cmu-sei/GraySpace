"""The deployed core: version, health, inventory and DNS, and its
lifecycle as jobs: preflight, deploy, configure, destroy."""
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from models.base import (
    BaseVersion,
    BaseVersionDetail,
    ConfigureBaseRequest,
    DeployBaseRequest,
    DeployedBase,
    DnsView,
    Inventory,
    Preflight,
)
from models.instances import JobAccepted, JobRecord
from services import base_jobs, base_preflight, base_versions, inventory
from services.auth import require_token
from services.base_service import get_deployed_base
from services.jobs_api import require_base_jobs
from services.mutation_tracker import mutation_tracker
from services.slices import stages, store
from services.slices.runner import new_job, runner
from services.topology_service import topology_service

routes = APIRouter()


@routes.get("", response_model=DeployedBase)
async def get_base():
    """
    The deployed core version and its health.

    The version comes from the `base` block that `task parse` writes into terraform.tfvars.json.
    Health compares the routers in the tfvars with what Proxmox reports as running, and counts
    BGP sessions up across all routers from Prometheus.
    """
    return await get_deployed_base()


@routes.get("/inventory", response_model=Inventory)
async def get_inventory():
    """
    What the deployed core consists of: ISPs; routers with their interfaces and addresses; links
    with both ends' addresses and their state; clients (the hosts the core places on customer
    networks) and core services (DNS servers and CAs) with their addresses. Read from the deployed
    tfvars, so only what the deployment scope built. Status is Proxmox's, link state comes from
    Prometheus. Slice instances are not included: GET /instances/{id} returns each one's manifest.
    """
    from tasks.state_monitor import state_monitor

    statuses = await inventory.proxmox_statuses()
    return inventory.build_inventory(topology_service.tfvars_data, statuses, state_monitor.previous_link_states)


@routes.get("/dns", response_model=DnsView)
async def get_dns():
    """
    Every zone the simulated DNS serves and its records (all but the SOA), with the resolvers.
    Each zone and record has an origin: `base` (the root and TLD zones task configure writes),
    `overlay` (domains and records created in the web UI or by a core preset) or `instance` (created by a
    slice instance, named in `instance`). Delegations sit in their TLD zone with the origin of the
    domain they delegate.
    """
    return inventory.build_dns(topology_service.tfvars_data, mutation_tracker.dns_domains, mutation_tracker.dns_records,
                               store.live_instances())


@routes.get("/versions", response_model=List[BaseVersion])
async def list_base_versions():
    """
    Shipped core versions under bases/, with a summary of each and which one is deployed.

    A version whose CSVs fail to load is listed with its problems instead of a summary.
    """
    return base_versions.list_versions()


@routes.get("/versions/{name:path}", response_model=BaseVersionDetail)
async def get_base_version(name: str):
    """
    A core version and its topology snapshot: ISPs, locations, routers (with ISP and location)
    and links. Version names contain a slash, e.g. /api/v1/core/versions/grayspace/2026.11.
    """
    version = base_versions.get_version(name)
    if version is None:
        raise HTTPException(status_code=404, detail=f"No shipped core version '{name}'")
    return version


# --- Lifecycle ---


def _no_active_base_job():
    active = base_jobs.active_base_job()
    if active:
        raise HTTPException(status_code=409, detail=f"Core job {active.id} ({active.kind}) is {active.state}; wait for it or cancel it")


def _gateway(value: Optional[str]) -> Optional[str]:
    try:
        return base_jobs.valid_gateway(value)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@routes.get("/preflight", response_model=Preflight)
async def get_preflight(version: Optional[str] = Query(None, description="Core version to check; the deployed one by default"),
                        scope: Optional[str] = Query(None, pattern="^(full|tier1)$", description="full or tier1; the deployed scope, else full")):
    """
    Whether this site can deploy the core version at the scope: the settings, the Proxmox API and
    node, storages (and their content types), bridges, SDN, SSH to the Proxmox host, capacity, and
    live instances that a version change would break. A deploy is refused while any check is an
    error, and runs these again as its first stage.
    """
    return await base_preflight.run(version, scope)


@routes.post("/deploy", response_model=JobAccepted, status_code=202, dependencies=[Depends(require_base_jobs)],
             responses={409: {"description": "Another core job is queued or running, or live instances would break"},
                        422: {"description": "Preflight failed; detail.checks lists the errors"}})
async def deploy_base(request: DeployBaseRequest = Body(DeployBaseRequest())):
    """
    Deploy or update the core: preflight, parse, the guards, Terraform until converged, Ansible,
    then a health check that waits for routers and BGP. Returns a job; poll GET /jobs/{id} or follow
    /events, then GET /base. Deploying the deployed version again changes nothing. Without a
    version, the deployed one is redeployed; with nothing deployed, a version is required.
    """
    _no_active_base_job()
    gateway = _gateway(request.mgmt_gateway)
    result = await base_preflight.run(request.version, request.scope)
    errors = [c for c in result.checks if c.status == "error"]
    if errors:
        status = 409 if any(c.name == "instances" for c in errors) else 422
        raise HTTPException(status_code=status, detail={"message": "Preflight failed", "checks": [c.model_dump() for c in errors]})
    job = base_jobs.submit("deploy_base", {"version": result.version, "scope": result.scope, "configure": request.configure,
                                           "mgmt_gateway": gateway})
    runner.submit(job)
    return JobAccepted(job=job)


@routes.post("/configure", response_model=JobAccepted, status_code=202, dependencies=[Depends(require_base_jobs)],
             responses={409: {"description": "Nothing is deployed, or another core job is queued or running"}})
async def configure_base(request: ConfigureBaseRequest = Body(ConfigureBaseRequest())):
    """Run Ansible on the deployed core again (task configure), then the health check. Returns a job."""
    _no_active_base_job()
    if not topology_service.tfvars_data:
        raise HTTPException(status_code=409, detail="Nothing is deployed; POST /api/v1/core/deploy first")
    job = base_jobs.submit("configure_base", {"mgmt_gateway": _gateway(request.mgmt_gateway)})
    runner.submit(job)
    return JobAccepted(job=job)


@routes.delete("", response_model=List[JobRecord], status_code=202, dependencies=[Depends(require_base_jobs)],
               responses={409: {"description": "Live instances exist (pass destroy_instances=true), or another job is active"}})
async def destroy_base(destroy_instances: bool = Query(False, description="Destroy every live slice instance first")):
    """
    Destroy the core (task destroy). Refused while slice instances are live, unless
    destroy_instances is true: then each live instance's destroy job is queued first, and the
    core's destroy job last, which refuses to run if any instance is still live. Returns the jobs
    in the order they run; the last is the core's.
    """
    _no_active_base_job()
    live = store.live_instances()
    if live and not destroy_instances:
        raise HTTPException(status_code=409, detail=f"{len(live)} slice instance(s) are live ({', '.join(r.id for r in live)}); "
                            "destroy them first, or pass destroy_instances=true")
    busy = [r.id for r in live if any(j.target == r.id and j.state in ("queued", "running") for j in store.list_jobs())]
    if busy:
        raise HTTPException(status_code=409, detail=f"Instance(s) {', '.join(busy)} already have a job queued or running")
    jobs = []
    for record in live:
        job = new_job("destroy_instance", record.id, stages.DESTROY_STAGES, slice_ref=record.slice)
        with store.update_instance(record.id) as r:
            r.jobs.append(job.id)
        jobs.append(job)
    jobs.append(base_jobs.submit("destroy_base", {}))
    for job in jobs:
        runner.submit(job)
    return jobs


router = APIRouter(tags=["core"], dependencies=[Depends(require_token)])
router.include_router(routes, prefix="/api/v1/core")
# The names before 1.26, when the core was the base. They go in contract 2.0
legacy_router = APIRouter(tags=["base (deprecated)"], deprecated=True, dependencies=[Depends(require_token)])
legacy_router.include_router(routes, prefix="/api/v1/base")
