"""The draft and the rollout (services/rollout.py): build a deployment, or change it, in one job.

    GET, PUT /api/v1/draft   what the deployment should have: a base and enclave instances
    GET /api/v1/deploy/plan  the draft against what is deployed
    POST /api/v1/deploy      a rollout job that makes them match
"""
from fastapi import APIRouter, Depends, HTTPException

from models.instances import JobAccepted
from models.rollout import DeployPlan, Draft, DraftUpdate
from services import base_jobs, rollout
from services.auth import require_token
from services.jobs_api import require_base_jobs

router = APIRouter(prefix="/api/v1", tags=["rollout"], dependencies=[Depends(require_token)])


@router.get("/draft", response_model=Draft)
async def get_draft():
    """The saved draft. Before anyone saves one, a draft of what is deployed (adopted: true), whose
    instances the draft then manages; instances GHOSTS owns are left out."""
    return rollout.get_draft()


@router.put("/draft", response_model=Draft, responses={422: {"description": "An unknown base version or enclave, or a prefix used twice"}})
async def put_draft(body: DraftUpdate):
    """Save what the deployment should have. Nothing changes until POST /deploy."""
    try:
        return rollout.put_draft(body)
    except rollout.DraftError as e:
        raise HTTPException(e.status, e.detail) from None


@router.get("/deploy/plan", response_model=DeployPlan)
async def get_plan():
    """What POST /deploy would do: instances to destroy, the base to deploy or update, instances to
    create, and the node's capacity now. problems lists what stops it."""
    from services.capabilities import capacity

    found = rollout.plan()
    found.capacity = await capacity()
    return found


@router.post("/deploy", response_model=JobAccepted, status_code=202, dependencies=[Depends(require_base_jobs)],
             responses={409: {"description": "Nothing to change, a base job or rollout is active, or the plan has problems"}})
async def deploy():
    """Queue a rollout: destroys, then the base, then creates, each a child job, stopping at the first
    that fails. Poll GET /jobs/{id} (its children, and GET /jobs?parent={id}); retry it to resume."""
    active = base_jobs.active_base_job()
    if active:
        raise HTTPException(409, f"Job {active.id} ({active.kind}) is {active.state}; wait for it or cancel it")
    found = rollout.plan()
    if found.problems:
        raise HTTPException(409, {"message": "The draft can't be deployed", "problems": found.problems})
    if not found.changes:
        raise HTTPException(409, "The draft matches what is deployed; nothing to change")
    from services.slices.runner import runner

    job = rollout.submit(found)
    runner.submit(job)
    return JobAccepted(job=job)
