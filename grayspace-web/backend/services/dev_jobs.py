"""Simulated jobs for local development (DEV_SIMULATE_DEPLOYMENT, services/dev_simulation.py).

Without Proxmox, Terraform or Ansible, the base and instance jobs walk their stages, a second or so
each (DEV_SIMULATION_STAGE_SECONDS), and do what the UI can see:

- deploy_base writes the tfvars (the simulation's, with the requested version and scope) and
  reloads the topology; destroy_base removes them
- create_instance validates and allocates for real, so its hosts appear on the map and in the
  simulated Proxmox, then writes the manifest; destroy_instance frees it
- plan_slice validates and allocates, and reports a made-up plan

A stage fails on purpose when DEV_SIMULATION_FAIL names it (kind:stage, e.g. create_instance:configure),
to try failures, retries and rollouts that stop.
"""
import asyncio
import json
import logging
import os
from pathlib import Path

from config import settings
from models.instances import JobRecord
from services import base_jobs
from services.slices import service, stages, store
from services.slices.runner import FAILURE_HOOKS, KINDS, JobContext, StageError

logger = logging.getLogger(__name__)


async def _walk(ctx: JobContext, job: JobRecord, names, do=None):
    """Each stage from the one a retry resumes at; do(name) for those that do something."""
    names = list(names)
    if job.resume_from in names:
        names = names[names.index(job.resume_from):]
    fail = os.environ.get("DEV_SIMULATION_FAIL", "")
    for name in names:
        ctx.stage(name)
        await asyncio.sleep(settings.dev_simulation_stage_seconds)
        if fail == f"{job.kind}:{name}" and not job.retry_of:
            raise StageError(f"Simulated failure at {name} (DEV_SIMULATION_FAIL); retry the job")
        ctx.log(f"(simulated) {stages.TITLES.get(name) or base_jobs.TITLES.get(name) or name}")
        if do:
            await do(name)


async def deploy_base(ctx: JobContext, job: JobRecord):
    request = job.result or {}

    async def do(name):
        if name == "parse":
            data = json.loads(Path(settings.dev_simulation_tfvars).read_text()) if settings.dev_simulation_tfvars else {}
            base = data.get("base") or {}
            data["base"] = {**base, "name": request.get("version"), "scope": request.get("scope") or "full",
                            "csv_sha256": base.get("csv_sha256") or "simulated"}
            Path(settings.tfvars_path).write_text(json.dumps(data, indent=2))
            ctx.log(f"Wrote {settings.tfvars_path}: {len(data.get('routers') or {})} routers")
        elif name == "refresh":
            await base_jobs.refresh(ctx)
        elif name == "player":
            await base_jobs.stage_player(ctx, remove=False)

    await _walk(ctx, job, job.stages, do)


async def configure_base(ctx: JobContext, job: JobRecord):
    await _walk(ctx, job, job.stages)


async def destroy_base(ctx: JobContext, job: JobRecord):
    async def do(name):
        if name == "guard" and store.live_instances():
            raise StageError("Live instances would break; destroy them first")
        if name == "player":
            await base_jobs.stage_player(ctx, remove=True)
        elif name == "destroy":
            Path(settings.tfvars_path).unlink(missing_ok=True)
        elif name == "refresh":
            await base_jobs.refresh(ctx, destroyed=True)

    await _walk(ctx, job, job.stages, do)


async def create_instance(ctx: JobContext, job: JobRecord):
    async def do(name):
        record = stages.get_record(job.target)
        if name == "validate":
            _, findings = await service.validate(stages.spec_of(record), prefix=record.id, exclude=record.id)
            errors = service.errors(findings)
            if errors:
                raise StageError("Validation failed: " + "; ".join(f"{f.code}: {f.message}" for f in errors))
        elif name == "allocate":
            await stages.stage_allocate(ctx, record)
        elif name == "ready":
            await stages.stage_ready(ctx, record)
        elif name == "player":
            await stages.stage_player(ctx)
            return
        with store.update_instance(record.id) as r:
            if name not in r.stages_done:
                r.stages_done.append(name)
            if r.state in ("pending", "failed"):
                r.state = "provisioning"

    await _walk(ctx, job, [s for s in job.stages if s not in stages.get_record(job.target).stages_done], do)


async def destroy_instance(ctx: JobContext, job: JobRecord):
    async def do(name):
        if name == "free":
            await stages.destroy_free(ctx, stages.get_record(job.target))
        elif name == "player":
            await stages.stage_player(ctx, exclude=job.target)

    stages.set_state(job.target, "destroying")
    await _walk(ctx, job, job.stages, do)


async def plan_slice(ctx: JobContext, job: JobRecord):
    stored = store.get_slice(job.slice.slug, job.slice.version)
    if stored is None:
        raise StageError(f"No slice {job.slice.slug} version {job.slice.version}")
    prefix = (job.result or {}).get("prefix") or stored[1].prefix
    result = {"prefix": prefix}

    async def do(name):
        if name == "validate":
            allocation, findings = await service.validate(stored[1], prefix=prefix)
            result["findings"] = [f.model_dump() for f in findings]
            if allocation is None:
                raise StageError("Validation failed: " + "; ".join(f"{f.code}: {f.message}" for f in service.errors(findings)))
            result["allocation"] = allocation.model_dump(mode="json")
            result["capacity_required"] = stages.plan.required_capacity(allocation)
            hosts = len(allocation.hosts) + len(allocation.sites)
            result["plan"] = {"exit_code": 2, "summary": f"Plan: {hosts} to add, 0 to change, 0 to destroy. (simulated)", "add": hosts,
                              "change": 0, "destroy": 0}
        ctx.update(result=result)

    await _walk(ctx, job, job.stages, do)


def register():
    """Replace the real job kinds with these. Called at startup when the deployment is simulated."""
    KINDS.update(deploy_base=deploy_base, configure_base=configure_base, destroy_base=destroy_base, create_instance=create_instance,
                 destroy_instance=destroy_instance, plan_slice=plan_slice)
    FAILURE_HOOKS.update(create_instance=stages.create_failed, destroy_instance=stages.destroy_failed)
    logger.info("Simulated jobs: every stage takes %ss", settings.dev_simulation_stage_seconds)
