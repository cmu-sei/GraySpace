"""Enclaves (formerly slices): stored specs with versions, validation and plans. The routes are defined once and served at /api/v1/enclaves and, deprecated since contract
1.5, at /api/v1/slices."""
from typing import List, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from models.instances import JobAccepted, SliceRef
from models.slices import PREFIX, PutSliceResult, SliceSpec, SliceSummary, ValidateResult
from services.auth import require_token
from services.base_service import version_ref
from services.jobs_api import require_jobs
from services.slices import service, stages, store
from services.slices.runner import new_job, runner
from services.topology_service import topology_service

routes = APIRouter()

BASE_SLICE = "base"


class SliceDetail(SliceSummary):
    """A slice and one of its versions' spec (the latest unless ?version= names one)."""

    version: Optional[str] = Field(None, description="The version of `spec`")
    spec: Optional[SliceSpec] = Field(None, description="Null for the base slice")
    base: Optional[dict] = Field(None, description="The base slice: the deployed base version")


class PlanRequest(BaseModel):
    version: Optional[str] = Field(None, description="Defaults to the latest version")
    prefix: Optional[str] = Field(None, pattern=r"^[a-z][a-z0-9]{0,7}$", description="Plan as if instantiated with this prefix")


def _base_slice() -> SliceDetail:
    ref = version_ref(topology_service.tfvars_data)
    return SliceDetail(slug=BASE_SLICE, kind="base", description="The deployed internet with no additions",
                       base=ref.model_dump() if ref else None)


def _check_slug(slug: str, spec: SliceSpec):
    if spec.slug != slug:
        raise HTTPException(status_code=422, detail=f"The spec's slug '{spec.slug}' doesn't match the path's '{slug}'")


@routes.get("", response_model=List[SliceSummary])
async def list_slices():
    """The reserved `base` slice, then every stored slice with its versions and live instances."""
    return [_base_slice()] + store.list_slices()


@routes.get("/{slug}", response_model=SliceDetail)
async def get_slice(slug: str, version: Optional[str] = Query(None, description="sha256:… ; defaults to the latest")):
    if slug == BASE_SLICE:
        return _base_slice()
    summary = next((s for s in store.list_slices() if s.slug == slug), None)
    stored = store.get_slice(slug, version) if summary else None
    if stored is None:
        raise HTTPException(status_code=404, detail=f"No enclave '{slug}'" + (f" version {version}" if version else ""))
    return SliceDetail(**summary.model_dump(), version=stored[0], spec=stored[1])


@routes.put("/{slug}", response_model=PutSliceResult)
async def put_slice(slug: str, spec: SliceSpec = Body(...)):
    """
    Store a spec. A new version is stored whenever the content changes; storing the same content
    again returns the same version (content hash). Returns the findings against the deployed
    base: a spec with errors is stored, but can't be instantiated until they are resolved.
    """
    _check_slug(slug, spec)
    version, created = store.put_slice(spec)
    _, findings = await service.validate(spec)
    return PutSliceResult(slug=slug, version=version, created=created, findings=findings)


@routes.delete("/{slug}", status_code=204, response_class=Response)
async def delete_slice(slug: str):
    """Delete a slice and all its versions. Refused while an instance of it exists."""
    if slug == BASE_SLICE:
        raise HTTPException(status_code=409, detail="The base can't be deleted")
    live = [i.id for i in store.live_instances() if i.slice.slug == slug]
    if live:
        raise HTTPException(status_code=409, detail=f"Enclave '{slug}' has live instances: {', '.join(live)}")
    if not store.delete_slice(slug):
        raise HTTPException(status_code=404, detail=f"No enclave '{slug}'")
    return Response(status_code=204)


@routes.post("/{slug}/validate", response_model=ValidateResult)
async def validate_slice(slug: str, spec: SliceSpec = Body(...),
                         base: Optional[str] = Query(None, description="A shipped base version to check against instead of the deployed one, "
                                                     "e.g. grayspace/2026.11: for a deployment still being built (its draft's base)"),
                         scope: Literal["full", "tier1"] = Query("full", description="With base: its scope"),
                         instance: Optional[str] = Query(None, pattern=PREFIX, description="Check it as this instance (a draft's prefix): "
                                                         "with that prefix, and when that instance is live and of this enclave, "
                                                         "what it holds is its own")):
    """Findings for a spec against the deployed base, live instances and the node's capacity; or,
    with base, against that shipped version as it would deploy. Nothing is stored."""
    _check_slug(slug, spec)
    if base:
        try:
            _, findings = await service.validate_on(spec, base, scope, prefix=instance, instance=instance)
        except ValueError as e:
            raise HTTPException(status_code=404 if "No shipped" in str(e) else 422, detail=str(e)) from None
    else:
        _, findings = await service.validate_as(spec, instance)
    return ValidateResult(valid=not service.errors(findings), version=spec.version(), findings=findings)


@routes.post("/{slug}/plan", response_model=JobAccepted, status_code=202, dependencies=[Depends(require_jobs)])
async def plan_slice(slug: str, request: PlanRequest = Body(default_factory=PlanRequest)):
    """
    A plan job: the allocation preview, `terraform plan` in a scratch workspace, and the capacity
    the instance would need. The job's `result` holds them when it finishes.
    """
    stored = store.get_slice(slug, request.version)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"No enclave '{slug}'" + (f" version {request.version}" if request.version else ""))
    job = new_job("plan_slice", slug, stages.PLAN_STAGES, slice_ref=SliceRef(slug=slug, version=stored[0]),
                  result={"prefix": request.prefix} if request.prefix else None)
    runner.submit(job)
    return JobAccepted(job=job)


router = APIRouter(tags=["enclaves"], dependencies=[Depends(require_token)])
router.include_router(routes, prefix="/api/v1/enclaves")
# The 1.4 names, for clients built against them (GHOSTS master). They go in contract 2.0
legacy_router = APIRouter(tags=["slices (deprecated)"], deprecated=True, dependencies=[Depends(require_token)])
legacy_router.include_router(routes, prefix="/api/v1/slices")
