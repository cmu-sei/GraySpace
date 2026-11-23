"""Slice instances: create, inspect, act on and destroy."""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import Field

from models.instances import CreateInstanceRequest, InstanceRecord, InstanceSummary, JobAccepted, Manifest, SliceRef
from models.slices import Finding
from services import instance_actions
from services.auth import require_token
from services.jobs_api import require_jobs
from services.slices import manifest, service, stages, store
from services.slices.runner import new_job, runner

router = APIRouter(prefix="/api/v1/instances", tags=["instances"], dependencies=[Depends(require_token)])


class InstanceView(Manifest):
    """An instance: its manifest, complete once ready, plus its lifecycle."""

    created: str
    updated: str
    error: Optional[str] = Field(None, description="Why the last job failed")
    stages_done: List[str] = Field(..., description="Create stages completed; a retried create resumes after them")
    jobs: List[str] = Field(..., description="The instance's jobs, oldest first")


def _summary(r: InstanceRecord) -> InstanceSummary:
    a = r.allocation
    return InstanceSummary(id=r.id, slice=r.slice, state=r.state, created=r.created, updated=r.updated, error=r.error,
                           hosts=len(a.hosts) if a else 0, sites=len(a.sites) if a else 0, last_job=r.jobs[-1] if r.jobs else None)


def _active_jobs(instance_id: str):
    return [j for j in store.list_jobs() if j.target == instance_id and j.state in ("queued", "running") and j.kind != "plan_slice"]


@router.get("", response_model=List[InstanceSummary])
async def list_instances(all: bool = Query(False, description="Include destroyed instances")):
    return [_summary(r) for r in store.list_instances() if all or r.live]


@router.post("", response_model=JobAccepted, status_code=202, dependencies=[Depends(require_jobs)],
             responses={422: {"description": "The spec has errors against the deployed core; detail.findings lists them"}})
async def create_instance(request: CreateInstanceRequest = Body(...)):
    """
    Create an instance of a stored slice version (the latest unless one is pinned). The instance ID
    is the slice's prefix, or `prefix` to run a second copy. Returns the instance ID and its
    create job; poll GET /jobs/{id}, then GET /instances/{id} for the manifest.
    """
    stored = store.get_slice(request.slice, request.version)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"No enclave '{request.slice}'" + (f" version {request.version}" if request.version else ""))
    version, spec = stored
    instance_id = request.prefix or spec.prefix
    existing = store.get_instance(instance_id)
    if existing is not None and existing.live:
        raise HTTPException(status_code=409, detail=f"An instance '{instance_id}' already exists; destroy it or pass another prefix")
    _, findings = await service.validate(spec, prefix=instance_id, check_capacity=True)
    errors: List[Finding] = service.errors(findings)
    if errors:
        raise HTTPException(status_code=422, detail={"message": "The slice can't be instantiated", "findings": [f.model_dump() for f in errors]})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = InstanceRecord(id=instance_id, slice=SliceRef(slug=request.slice, version=version), state="pending", created=now, updated=now)
    if not store.claim_instance(record):
        raise HTTPException(status_code=409, detail=f"An instance '{instance_id}' already exists; destroy it or pass another prefix")
    job = new_job("create_instance", instance_id, stages.with_player(stages.CREATE_STAGES), slice_ref=record.slice)
    with store.update_instance(instance_id) as r:
        r.jobs.append(job.id)
    runner.submit(job)
    return JobAccepted(instance=instance_id, job=job)


@router.get("/{instance_id}", response_model=InstanceView)
async def get_instance(instance_id: str):
    """The instance's state and manifest: sites, hosts (with management addresses and Proxmox status), DNS records and certificates."""
    record = store.get_instance(instance_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No instance '{instance_id}'")
    built = manifest.build(record, await stages.statuses(instance_id) if record.live else {})
    return InstanceView(**built.model_dump(), created=record.created, updated=record.updated, error=record.error,
                        stages_done=record.stages_done, jobs=record.jobs)


@router.delete("/{instance_id}", response_model=JobAccepted, status_code=202, dependencies=[Depends(require_jobs)])
async def destroy_instance(instance_id: str):
    """
    A destroy job: revoke certificates, delete DNS records and domains, undo link conditions and BGP
    attachments, terraform destroy, delete the workspace, free the allocations. Allowed from any
    state, including a failed create; a create still queued or running is cancelled first.
    """
    record = store.get_instance(instance_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No instance '{instance_id}'")
    if not record.live:
        raise HTTPException(status_code=409, detail=f"Instance '{instance_id}' is already destroyed")
    active = _active_jobs(instance_id)
    if any(j.kind == "destroy_instance" for j in active):
        raise HTTPException(status_code=409, detail=f"Instance '{instance_id}' is already being destroyed")
    for j in active:
        with store.update_job(j.id) as job:
            job.cancel_requested = True
    job = new_job("destroy_instance", instance_id, stages.with_player(stages.DESTROY_STAGES, first=True), slice_ref=record.slice)
    with store.update_instance(instance_id) as r:
        r.jobs.append(job.id)
    runner.submit(job)
    return JobAccepted(instance=instance_id, job=job)


@router.post("/{instance_id}/actions", response_model=instance_actions.ActionResult,
             responses={409: {"description": "The instance isn't ready, or another owner holds the link (LINK_OWNED)"}})
async def act(instance_id: str, request: instance_actions.ActionRequest = Body(...)):
    """
    A runtime action on a ready instance: link.condition, link.clear, link.down, link.up
    on core links; dns.record.set and dns.record.delete within the instance's domains; host.start and
    host.stop; reset, which undoes the instance's actions. Owned by the instance: Reset changes
    (POST /changes/discard) leaves them, reset and destroy undo them.
    """
    try:
        return await instance_actions.apply(instance_id, request)
    except instance_actions.ActionError as e:
        raise HTTPException(status_code=e.status, detail={"code": e.code, "message": str(e)})
