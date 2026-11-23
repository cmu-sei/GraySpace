"""Jobs: progress, log, cancel and retry."""
import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from models.instances import JobAccepted, JobRecord
from services import base_jobs, rollout
from services.auth import require_token
from services.jobs_api import require_base_jobs, require_jobs
from services.slices import stages, store
from services.slices.runner import new_job, runner

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"], dependencies=[Depends(require_token)])

FINISHED = ("succeeded", "failed", "cancelled")


def _job(job_id: str) -> JobRecord:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job '{job_id}'")
    return job


@router.get("", response_model=List[JobRecord])
async def list_jobs(target: Optional[str] = Query(None, description="Only jobs for this instance or slice"),
                    kind: Optional[List[str]] = Query(None, description="Only jobs of these kinds (repeat for several)"),
                    state: Optional[List[str]] = Query(None, description="Only jobs in these states (repeat for several)"),
                    parent: Optional[str] = Query(None, description="Only the jobs this rollout ran"),
                    limit: int = Query(50, ge=1, le=1000)):
    """Jobs, newest first."""
    jobs = [j for j in store.list_jobs() if (target is None or j.target == target) and (not kind or j.kind in kind)
            and (not state or j.state in state) and (parent is None or j.parent == parent)]
    return list(reversed(jobs))[:limit]


@router.get("/{job_id}", response_model=JobRecord)
async def get_job(job_id: str):
    """State, current stage and attempt (e.g. "apply attempt 3/6"), error, and plan results."""
    return _job(job_id)


@router.get("/{job_id}/log", response_class=StreamingResponse,
            responses={200: {"content": {"text/plain": {}}, "description": "The log; streamed until the job finishes when follow is true"}})
async def get_job_log(job_id: str, offset: int = Query(0, ge=0, description="Start at this byte"),
                      follow: bool = Query(True, description="Keep streaming until the job finishes")):
    _job(job_id)
    path = store.job_log(job_id)

    async def stream():
        position = offset
        while True:
            chunk = b""
            if path.exists():
                with open(path, "rb") as f:
                    f.seek(position)
                    chunk = f.read()
            if chunk:
                position += len(chunk)
                yield chunk
            job = store.get_job(job_id)
            if not follow or job is None or job.state in FINISHED:
                if job is not None and path.exists() and path.stat().st_size > position:
                    continue
                return
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")


@router.post("/{job_id}/cancel", response_model=JobRecord)
async def cancel_job(job_id: str):
    """Cancel: interrupt the command the job is running (Terraform stops gracefully), or stop at the next stage boundary. A create that is cancelled leaves the instance failed, to be retried or destroyed."""
    job = _job(job_id)
    if job.state in FINISHED:
        raise HTTPException(status_code=409, detail=f"Job {job_id} has {job.state}")
    with store.update_job(job_id) as j:
        j.cancel_requested = True
    if job.kind == rollout.KIND:
        rollout.cancel_children(store.get_job(job_id))
    return store.get_job(job_id)


@router.post("/{job_id}/retry", response_model=JobAccepted, status_code=202, dependencies=[Depends(require_base_jobs)])
async def retry_job(job_id: str):
    """Run a failed or cancelled job again. Creates and core jobs resume at the stage that failed; destroys and plans start over, which is safe because every stage is idempotent."""
    job = _job(job_id)
    if job.state not in ("failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"Job {job_id} is {job.state}; only failed or cancelled jobs can be retried")
    if job.parent:
        raise HTTPException(status_code=409, detail=f"Job {job_id} ran in rollout {job.parent}; retry the rollout, which resumes it")
    if job.kind == rollout.KIND:
        active = base_jobs.active_base_job()
        if active:
            raise HTTPException(status_code=409, detail=f"Job {active.id} ({active.kind}) is {active.state}")
        if not rollout.resumable(job):
            raise HTTPException(status_code=409, detail=f"The draft changed since rollout {job_id}; deploy again to apply it")
        retry = new_job(job.kind, job.target, job.stages, resume_from=job.stage, retry_of=job.id, result=job.result)
        with store.update_job(retry.id) as r:
            r.children = list(job.children)
        runner.submit(retry)
        return JobAccepted(job=store.get_job(retry.id))
    if job.kind in base_jobs.KIND_NAMES:
        active = base_jobs.active_base_job()
        if active:
            raise HTTPException(status_code=409, detail=f"Core job {active.id} ({active.kind}) is {active.state}")
        retry = new_job(job.kind, job.target, job.stages, resume_from=job.stage, retry_of=job.id, result=job.result)
        runner.submit(retry)
        return JobAccepted(job=retry)
    require_jobs()
    if job.kind != "plan_slice":
        record = store.get_instance(job.target)
        if record is None or not record.live:
            raise HTTPException(status_code=409, detail=f"Instance '{job.target}' no longer exists")
        if any(j.target == job.target and j.state in ("queued", "running") for j in store.list_jobs()):
            raise HTTPException(status_code=409, detail=f"Instance '{job.target}' already has a job queued or running")
        if job.kind == "create_instance" and (record.state == "destroying" or stages.destroy_started(record)):
            raise HTTPException(status_code=409, detail=f"Instance '{job.target}' has been partly destroyed; retry its destroy job instead")
    retry = new_job(job.kind, job.target, job.stages, slice_ref=job.slice, resume_from=job.stage, retry_of=job.id,
                    result={"prefix": (job.result or {}).get("prefix")} if job.kind == "plan_slice" else None)
    if job.kind != "plan_slice":
        with store.update_instance(job.target) as r:
            r.jobs.append(retry.id)
    runner.submit(retry)
    return JobAccepted(instance=job.target if job.kind != "plan_slice" else None, job=retry)
