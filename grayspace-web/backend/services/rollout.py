"""The draft and the rollout: build a deployment, then change it, in one job.

The draft is what the deployment should have: a base version and scope, and the enclave instances
(slug, version, prefix). GET /deploy/plan compares it with what is deployed; POST /deploy queues a
rollout job that makes them match. Its stages are, in order:

    destroy:<id>   instances the draft no longer has (or has at another version)
    base           deploy or update the base, when the draft's differs
    preset         apply the draft's base preset, after the base or when another is active
    create:<id>    instances the draft has that aren't live
    player         the Player view (services/player.py): last, once the VMs exist; first when the
                   draft no longer has one, so it goes before anything it shows

Each stage but preset and player runs a child job (destroy_instance, deploy_base, create_instance), with
its own record and log, inline (the preset applies in the rollout itself, services/presets.py): the rollout holds the node lock
throughout, as one job. Enclaves have no presets (contract 1.19): a draft's enclaves[].preset is ignored, and a preset:<id>
stage of a rollout planned before is skipped. An instance is validated
when its turn comes, against the base the rollout has just deployed. The rollout stops at the
first child that fails; retrying it resumes there, the failed child itself resumed at its stage.

Only instances the draft manages are destroyed: those rollouts created and those the draft was
adopted from. Instances made outside it (by GHOSTS, POST /instances) are left alone.

    <data>/draft.json      the draft
    <data>/rollout.json    {"managed": [instance IDs]}
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from models.instances import STAGE_TITLES, InstanceRecord, JobRecord, SliceRef
from models.rollout import DeployPlan, Draft, DraftBase, DraftEnclave, DraftUpdate, PlannedBase, PlannedInstance
from services import base_versions, player, presets, request_user
from services.mutation_tracker import mutation_tracker
from services.base_service import version_ref
from services.slices import service, store
from services.slices.runner import KINDS, Cancelled, JobContext, StageError, execute_inline, new_job

logger = logging.getLogger(__name__)

KIND = "rollout"
TARGET = "deployment"
STAGE_TITLES[KIND] = {"base": "Deploy the core", "preset": "Apply the core preset", "destroy:*": "Destroy enclave {}",
                      "create:*": "Create enclave {}",
                      "preset:*": "Apply the preset to enclave {} (removed)", "player": "Update the Player view"}


class DraftError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _draft_file():
    return store.data_dir() / "draft.json"


def _rollout_file():
    return store.data_dir() / "rollout.json"


def managed() -> Set[str]:
    return set((store.read_json(_rollout_file()) or {}).get("managed") or [])


def _set_managed(ids: Set[str]) -> None:
    store.write_json(_rollout_file(), {"managed": sorted(ids)})


def forget(instance_id: str) -> None:
    """A destroyed instance is no longer managed: the next draft that names its prefix creates it again."""
    if instance_id in managed():
        _manage(instance_id, False)


def _manage(instance_id: str, on: bool) -> None:
    with store.locked():
        ids = managed()
        (ids.add if on else ids.discard)(instance_id)
        _set_managed(ids)


def _deployed_base() -> Optional[DraftBase]:
    ref = version_ref(service.tfvars())
    if ref is None or not ref.name:
        return None
    return DraftBase(version=ref.name, scope=ref.scope if ref.scope in ("full", "tier1") else "full", preset=mutation_tracker.preset)


def get_draft() -> Draft:
    """The saved draft; without one, a draft of what is deployed, which also starts managing its instances."""
    data = store.read_json(_draft_file())
    if data is not None:
        draft = _without_enclave_presets(Draft.model_validate(data))
        draft.player = player.without_retired(draft.player) if draft.player is not None else player.legacy_player()
        return draft
    owners = {s.slug: s.owner for s in store.list_slices()}
    adopted = [i for i in store.live_instances() if owners.get(i.slice.slug) != "ghosts"]
    with store.locked():
        if not _rollout_file().exists():
            _set_managed({i.id for i in adopted})
    return Draft(base=_deployed_base(), enclaves=[DraftEnclave(slug=i.slice.slug, version=i.slice.version, prefix=i.id) for i in adopted],
                 adopted=True, player=player.legacy_player())


def _preset_names(fn, *args) -> Optional[set]:
    try:
        return {p.name for p in fn(*args)}
    except presets.PresetError:
        return None


def _without_enclave_presets(draft):
    """Enclave presets were removed (contract 1.19): a draft's enclaves[].preset is ignored."""
    return draft.model_copy(update={"enclaves": [e.model_copy(update={"preset": None}) for e in draft.enclaves]})


def put_draft(update: DraftUpdate) -> Draft:
    update = _without_enclave_presets(update.model_copy(update={"player": player.without_retired(update.player)}))
    problems = []
    if update.base is not None and base_versions.get_version(update.base.version) is None:
        problems.append(f"{base_versions.not_shipped(update.base.version)} (GET /base/versions)")
    elif update.base is not None and update.base.preset and update.base.preset not in (_preset_names(presets.list_base, update.base.version) or ()):
        problems.append(f"No preset '{update.base.preset}' for {update.base.version} (GET /base/presets?base=)")
    seen = set()
    for e in update.enclaves:
        if store.get_slice(e.slug, e.version) is None:
            problems.append(f"No enclave '{e.slug}'" + (f" version {e.version}" if e.version else ""))
        if e.prefix in seen:
            problems.append(f"Prefix '{e.prefix}' is used twice")
        seen.add(e.prefix)
    problems += player.problems(Draft(**update.model_dump()))
    if problems:
        raise DraftError(422, "; ".join(problems))
    if not _rollout_file().exists():
        get_draft()  # start managing what is deployed before the draft replaces it
    draft = Draft(**update.model_dump(), updated=datetime.now(timezone.utc).isoformat(timespec="seconds"), updated_by=request_user.current())
    store.write_json(_draft_file(), draft.model_dump(mode="json"))
    return draft


def plan(draft: Optional[Draft] = None) -> DeployPlan:
    """The draft against what is deployed. Doesn't ask Proxmox anything (capacity is the router's)."""
    draft = draft or get_draft()
    problems: List[str] = []
    deployed = version_ref(service.tfvars())
    if draft.base is None:
        base = PlannedBase(action="none", deployed=deployed)
        if deployed is None and draft.enclaves:
            problems.append("Nothing is deployed: choose a core version")
    else:
        if base_versions.resolve(draft.base.version) is None:
            problems.append(base_versions.not_shipped(draft.base.version))
        same = deployed is not None and deployed.name == draft.base.version and deployed.scope == draft.base.scope
        base = PlannedBase(action="none" if same else "update" if deployed else "deploy", deployed=deployed, version=draft.base.version,
                           scope=draft.base.scope, configure=draft.base.configure, preset=draft.base.preset,
                           active_preset=mutation_tracker.preset)
    live: Dict[str, InstanceRecord] = {i.id: i for i in store.live_instances()}
    ours = managed()
    instances: List[PlannedInstance] = []
    for e in draft.enclaves:
        stored = store.get_slice(e.slug, e.version)
        version = stored[0] if stored else e.version
        if stored is None:
            problems.append(f"No enclave '{e.slug}'" + (f" version {e.version}" if e.version else ""))
        inst = live.get(e.prefix)
        if inst is None:
            action = "create"
        elif inst.slice.slug == e.slug and inst.slice.version == version and inst.state != "failed":
            action = "keep"
        elif inst.id in ours:
            action = "replace"
        else:
            problems.append(f"Instance '{inst.id}' ({inst.slice.slug}) was made outside the draft; give the draft's {e.slug} another prefix")
            action = "keep"
        instances.append(PlannedInstance(id=e.prefix, slug=e.slug, version=version, action=action, state=inst.state if inst else None))
    wanted = {e.prefix for e in draft.enclaves}
    for inst in live.values():
        if inst.id not in wanted and inst.id in ours:
            instances.append(PlannedInstance(id=inst.id, slug=inst.slice.slug, version=inst.slice.version, action="destroy", state=inst.state))
    unmanaged = sorted(i for i in live if i not in wanted and i not in ours)
    stages = [f"destroy:{i.id}" for i in instances if i.action in ("destroy", "replace")] + (["base"] if base.action != "none" else [])
    if base.preset and (base.action != "none" or base.preset != base.active_preset):
        stages.append("preset")
    stages += [f"create:{i.id}" for i in instances if i.action in ("create", "replace")]
    planned_player = player.plan(draft, stages)
    if planned_player is None and draft.player is not None:
        problems.append("The draft has a Player view, but the deployment has no Player option; turn it on in its Settings")
    elif planned_player is not None and planned_player.action == "destroy":
        stages = ["player"] + stages
    elif planned_player is not None and planned_player.action in ("create", "update"):
        stages = stages + ["player"]
    last = last_rollout()
    return DeployPlan(base=base, instances=instances, unmanaged=unmanaged, problems=problems, stages=stages, changes=bool(stages),
                      player=planned_player, resumable_rollout=last.id if last is not None and resumable(last, draft) else None)


def _content(draft: Draft) -> Dict:
    """What a draft asks for, without who saved it when."""
    return draft.model_dump(mode="json", exclude={"updated", "updated_by", "adopted"})


def resumable(job: JobRecord, draft: Optional[Draft] = None) -> bool:
    """Whether retrying a rollout still deploys the draft: its stages were planned from the draft it
    recorded, so after the draft changes a retry would carry out the old plan (create an enclave the
    draft no longer has, say)."""
    if job.kind != KIND or job.state not in ("failed", "cancelled"):
        return False
    recorded = (job.result or {}).get("draft")
    if recorded is None:
        return True  # rollouts before 1.22 kept no copy of their draft
    try:
        return _content(Draft.model_validate(recorded)) == _content(draft or get_draft())
    except ValueError:
        return False


def last_rollout() -> Optional[JobRecord]:
    return max((j for j in store.list_jobs() if j.kind == KIND), key=lambda j: j.created, default=None)


def active_rollout() -> Optional[JobRecord]:
    return next((j for j in store.list_jobs() if j.kind == KIND and j.state in ("queued", "running")), None)


def submit(deploy_plan: DeployPlan) -> JobRecord:
    """Queue the rollout; the caller has checked the plan has no problems and no base job or rollout is
    active. The job keeps the draft it deploys: its player stage applies that draft's view."""
    draft = get_draft()
    return new_job(KIND, TARGET, deploy_plan.stages, result={"plan": deploy_plan.model_dump(mode="json"), "children": {},
                                                             "draft": draft.model_dump(mode="json")})


# --- The job ---

def _previous(job: JobRecord, stage: str) -> Optional[JobRecord]:
    """The child a retried rollout's earlier run made for the stage."""
    child_id = ((job.result or {}).get("children") or {}).get(stage)
    return store.get_job(child_id) if child_id else None


def _retry_child(job: JobRecord, previous: JobRecord) -> JobRecord:
    """Creates and base deploys resume at the stage that failed; destroys start over (every stage is idempotent)."""
    resume = previous.stage if previous.kind != "destroy_instance" else None
    child = new_job(previous.kind, previous.target, previous.stages, slice_ref=previous.enclave, resume_from=resume, retry_of=previous.id,
                    result=previous.result if previous.kind == "deploy_base" else None, parent=job.id, by=job.by)
    if previous.kind != "deploy_base":
        with store.update_instance(previous.target) as r:
            r.jobs.append(child.id)
    return child


async def _new_child(ctx: JobContext, job: JobRecord, stage: str, planned: Dict) -> Optional[JobRecord]:
    from services import base_jobs
    from services.slices import stages

    step, _, target = stage.partition(":")
    if step == "base":
        base = planned["base"]
        request = {"version": base["version"], "scope": base["scope"], "configure": base["configure"], "mgmt_gateway": None}
        return new_job("deploy_base", base_jobs.TARGET, base_jobs.deploy_stages(base["configure"]), result=request, parent=job.id, by=job.by)
    if step == "destroy":
        record = store.get_instance(target)
        if record is None or not record.live:
            ctx.log(f"Instance {target} is already destroyed")
            return None
        child = new_job("destroy_instance", target, stages.DESTROY_STAGES, slice_ref=record.slice, parent=job.id, by=job.by)
        with store.update_instance(target) as r:
            r.jobs.append(child.id)
        return child
    entry = next(i for i in planned["instances"] if i["id"] == target)
    stored = store.get_slice(entry["slug"], entry["version"])
    if stored is None:
        raise StageError(f"Enclave {entry['slug']} version {entry['version']} is no longer stored")
    version, spec = stored
    existing = store.get_instance(target)
    if existing is not None and existing.live:
        raise StageError(f"An instance '{target}' already exists (made since the rollout was planned); destroy it, or change the draft's prefix")
    _, findings = await service.validate(spec, prefix=target, check_capacity=True)
    for f in findings:
        ctx.log(f"{f.severity.upper()} {f.code} at {f.path or '/'}: {f.message}")
    errors = service.errors(findings)
    if errors:
        raise StageError(f"{entry['slug']} can't be built here: " + "; ".join(f"{f.code}: {f.message}" for f in errors))
    now = store.now()
    record = InstanceRecord(id=target, slice=SliceRef(slug=entry["slug"], version=version), state="pending", created=now, updated=now)
    if not store.claim_instance(record):
        raise StageError(f"An instance '{target}' already exists")
    _manage(target, True)
    child = new_job("create_instance", target, stages.CREATE_STAGES, slice_ref=record.slice, parent=job.id, by=job.by)
    with store.update_instance(target) as r:
        r.jobs.append(child.id)
    return child


async def _apply_preset(ctx: JobContext, stage: str, planned: Dict) -> None:
    """Apply the draft's base preset (stage preset), resetting first. A preset:<id> stage, from a rollout
    planned before enclave presets were removed, does nothing."""
    _, _, target = stage.partition(":")
    if target:
        ctx.log(f"Enclave presets were removed; nothing to apply to {target}")
        return
    try:
        name = planned["base"]["preset"]
        result = await presets.apply_base(name)
    except presets.PresetError as e:
        raise StageError(str(e)) from None
    for line in result.warnings:
        ctx.log(f"WARNING {line}")
    ctx.log(result.message)
    if result.errors:
        raise StageError(f"Preset {name}: " + "; ".join(result.errors))


async def _apply_player(ctx: JobContext, job: JobRecord) -> None:
    """The Player view of the draft the rollout deploys; removed when that draft has none."""
    saved = (job.result or {}).get("draft")
    draft = Draft.model_validate(saved) if saved else get_draft()
    if draft.player is None:
        await player.destroy(ctx)
    else:
        await player.apply(ctx, draft)


async def run_rollout(ctx: JobContext, job: JobRecord):
    planned = (job.result or {}).get("plan") or {}
    todo = job.stages[job.stages.index(job.resume_from):] if job.resume_from in job.stages else job.stages
    for stage in todo:
        ctx.stage(stage)
        if stage.partition(":")[0] == "preset":
            await _apply_preset(ctx, stage, planned)
            continue
        if stage == "player":
            await _apply_player(ctx, job)
            continue
        previous = _previous(job, stage)
        if previous is not None and previous.state == "succeeded":
            ctx.log(f"Done by {previous.id} before")
            continue
        child = _retry_child(job, previous) if previous is not None and previous.state in ("failed", "cancelled") else await _new_child(
            ctx, job, stage, planned)
        if child is None:
            continue
        current = ctx.job
        job = ctx.update(children=current.children + [child.id],
                         result={**(current.result or {}), "children": {**((current.result or {}).get("children") or {}), stage: child.id}})
        ctx.log(f"{stage}: job {child.id} ({child.kind})")
        final = await execute_inline(child.id)
        if final.state == "cancelled":
            raise Cancelled()
        if final.state != "succeeded":
            raise StageError(f"{stage} ({final.kind} {final.id}) failed at {final.stage}: {final.error}")
        if stage.startswith("destroy:"):
            _manage(final.target, False)


def cancel_children(job: JobRecord) -> None:
    """A cancelled rollout cancels the child it is running, which interrupts the command that child is running."""
    for child_id in job.children:
        with store.update_job(child_id) as child:
            if child.state in ("queued", "running"):
                child.cancel_requested = True


KINDS[KIND] = run_rollout
