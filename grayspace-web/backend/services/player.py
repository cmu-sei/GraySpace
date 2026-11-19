"""The deployment's Crucible Player view: the draft's player section (models/rollout.py DraftPlayer)
applied in terraform/player/, its own workspace (docs/deployments.md, The Player view).

The view's teams see the VMs the draft assigns them: base guests by hostname, enclave hosts and
sites by the names their spec gives them, so every copy of a counted host and whatever prefix the
instance has. A host or site the spec marks crucible: true goes to the default team unless the draft
says otherwise. VM IDs come from Proxmox when the stage runs.

    terraform/player/                 the workspace (its state in the deployment's work tree)
    terraform/player/player.tfvars.json   written from the draft at each apply
    <data>/player.json                what was applied: the view's ID, team IDs, guests, the draft's hash

Applies refresh: the provider (2.6+) manages a view's teams and applications as a whole (inline
mode), so the state must see changes made in Player itself, or the apply would act on what is no
longer there. 2.5.0 panicked reading a view back, so applies were -refresh=false until 2.7.
Destroys stay -refresh=false: a destroy needs no fresh state. The base module's view from before
(modules/crucible, slice/crucible.tf) is destroyed first, once (migrate_legacy).
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from models.rollout import Draft, DraftPlayer, PlannedPlayer
from services.slices import environment, store

logger = logging.getLogger(__name__)

MANAGEMENT = ("prometheus",)
# Base guests the base no longer has (grafana: contract 1.13; the clients of grayspace/2026.10: 1.18;
# the GHOSTS VM: 1.25).
# A draft's entries for them are dropped when it is read or saved, without a warning: nobody can
# untick a guest the Player step doesn't list.
RETIRED = ("grafana", "lum-svc", "lum-ctl", "lum-wrk", "lum-fluxbox", "ghosts")


def enabled() -> bool:
    """Whether the deployment has the Player option (Settings): its environment file says so."""
    return environment.job_env().get("GRAYSPACE_PLAYER", "").lower() == "true"


def module() -> Path:
    return environment.codebase() / "terraform" / "player"


def tfvars_file() -> Path:
    return module() / "player.tfvars.json"


def _applied_file() -> Path:
    return store.data_dir() / "player.json"


def applied() -> Optional[Dict]:
    return store.read_json(_applied_file())


def config_hash(player: Optional[DraftPlayer]) -> Optional[str]:
    if player is None:
        return None
    return hashlib.sha256(json.dumps(player.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()[:16]


def view_url(view_id: Optional[str]) -> Optional[str]:
    """The view in Player's UI: the connection's player_url, or Player's API URL without /api
    (https://host/player/api -> https://host/player/view/<id>)."""
    if not view_id:
        return None
    env = environment.job_env()
    ui = env.get("TF_VAR_crucible_player_url", "").rstrip("/")
    api = env.get("TF_VAR_crucible_player_api_url", "").rstrip("/")
    if not ui and api:
        ui = api[:-len("/api")] if api.endswith("/api") else api
    return f"{ui}/view/{view_id}" if ui else None


def view_name(player: DraftPlayer) -> str:
    from config import settings

    return player.view_name or f"{settings.environment_id}-grayspace"


# --- Who sees what ---

def without_retired(player: Optional[DraftPlayer]) -> Optional[DraftPlayer]:
    """The view without its entries for retired base guests."""
    if player is None or not any(g.instance is None and g.name in RETIRED for g in player.guests):
        return player
    return player.model_copy(update={"guests": [g for g in player.guests if g.instance is not None or g.name not in RETIRED]})


def version_tfvars(version: Optional[str], scope: Optional[str]) -> Dict:
    """The tfvars of a base version: the deployed one's, or the version's own when it isn't the one
    deployed (nothing is, or another version or scope). The deployed tfvars when no version is given."""
    from services.slices import service

    tfvars = service.tfvars() or {}
    deployed = tfvars.get("base") or {}
    if version and (version != deployed.get("name") or (scope and scope != deployed.get("scope"))):
        return service.shipped_tfvars(version, scope or "full")
    return tfvars


def draft_tfvars(draft: Draft) -> Dict:
    """The tfvars of the base the draft deploys; the deployed tfvars when it names none or an unknown version."""
    try:
        return version_tfvars(draft.base.version if draft.base else None, draft.base.scope if draft.base else None)
    except ValueError:
        return version_tfvars(None, None)


def base_guests(tfvars: Dict) -> Dict[str, Dict]:
    """hostname -> {group, isp}: routers by ISP, core services, management."""
    routers = tfvars.get("routers") or {}
    out: Dict[str, Dict] = {h: {"group": "routers", "isp": r.get("isp_name")} for h, r in routers.items()}
    for key in ("core_dns", "core_ca"):
        for h, c in (tfvars.get(key) or {}).items():
            out[h] = {"group": "core_services", "role": c.get("role") or "ca", "isp": (routers.get(c.get("router")) or {}).get("isp_name")}
    for h in MANAGEMENT:
        out[h] = {"group": "management"}
    return out


def _guest(player: DraftPlayer, instance: Optional[str], name: str, site: bool):
    return next((g for g in player.guests if g.instance == instance and g.name == name and g.site == site), None)


def rule_teams(player: DraftPlayer, instance: Optional[str]) -> List[str]:
    """The teams assign gives the base's guests (instance None) or an enclave instance's."""
    scope = "base" if instance is None else f"enclave:{instance}"
    return next((list(r.teams) for r in player.assign if r.scope == scope), [])


def teams_for(player: DraftPlayer, instance: Optional[str], name: str, site: bool, hint: bool = False, alias: Optional[str] = None) -> List[str]:
    """A guest's teams: its own entry's, or its part's rule and, for one its spec marks crucible: true, the default team."""
    g = _guest(player, instance, name, site) or (alias and _guest(player, instance, alias, site))
    if g:
        return list(g.teams)
    teams = rule_teams(player, instance)
    default = player.default_team or player.teams[0].name
    return teams + [default] if hint and default not in teams else teams


def desired(player: DraftPlayer, draft: Draft, exclude: Set[str] = frozenset()) -> Dict[str, List[str]]:
    """hostname -> teams for every VM the view should have, now: the base's and the live instances'."""
    from services.slices import service

    out: Dict[str, List[str]] = {h: teams_for(player, None, h, False) for h in base_guests(service.tfvars())}
    prefixes = {e.prefix for e in draft.enclaves}
    for inst in store.live_instances():
        if inst.id in exclude or inst.id not in prefixes or inst.allocation is None:
            continue
        for h in inst.allocation.hosts:
            out[h.hostname] = teams_for(player, inst.id, h.spec_name, False, h.crucible, alias=h.name)
        for s in inst.allocation.sites:
            out[s.edge] = teams_for(player, inst.id, s.name, True, s.crucible)
    return {h: t for h, t in out.items() if t}


def vm_ids(hostnames) -> Tuple[Dict[str, Dict], List[str]]:
    """hostname -> {id, node, type} from Proxmox, and the hostnames it doesn't have."""
    from services.proxmox_client import proxmox_client

    found = {c["name"]: {"id": int(c["vmid"]), "node": c.get("node") or "", "type": "QEMU" if c.get("resource_type") == "qemu" else "LXC"}
             for c in proxmox_client.get_deployment_containers()}
    wanted = sorted(hostnames)
    return {h: found[h] for h in wanted if h in found}, [h for h in wanted if h not in found]


def candidates(draft: Draft, tfvars: Dict) -> Dict:
    """What the Player step offers: base guests grouped, and each draft enclave's sites and hosts
    with the spec's hints (GET /api/v1/player/guests)."""
    base = base_guests(tfvars)
    enclaves = []
    for e in draft.enclaves:
        stored = store.get_slice(e.slug, e.version)
        if stored is None:
            continue
        spec = stored[1]
        inst = store.get_instance(e.prefix)
        edges = {s.name: s.edge for s in inst.allocation.sites} if inst and inst.live and inst.allocation else {}
        enclaves.append({
            "prefix": e.prefix, "slug": e.slug,
            "sites": [{"name": s.name, "edge": edges.get(s.name, f"{e.prefix}-{s.name}-edge"), "hint": bool(s.crucible)} for s in spec.sites],
            "hosts": [{"name": h.name, "count": h.count or 1, "image": h.image, "hint": bool(h.crucible)} for h in spec.hosts],
        })
    return {"base": [{"hostname": h, **v} for h, v in sorted(base.items(), key=lambda kv: (kv[1]["group"], kv[1].get("isp") or "", kv[0]))],
            "enclaves": enclaves}


LEGACY_TEMPLATE = "e4698c62-7322-445b-946d-cafe1cf510bc"


def legacy_player() -> Optional[DraftPlayer]:
    """The view the base module made (modules/crucible), as a draft's player section: one "VMs"
    application, team Blue as View Admin, every base guest. For a deployment that has that view and
    no player section yet, so its first rollout makes the same view here before the old one goes."""
    from models.rollout import PlayerApplication, PlayerGuest, PlayerTeam
    from services.slices import service

    if not enabled() or not has_crucible_state(environment.base_module() / "terraform.tfstate"):
        return None
    guests = [PlayerGuest(name=h, teams=["Blue"]) for h in sorted(base_guests(service.tfvars()))]
    return DraftPlayer(applications=[PlayerApplication(name="VMs", template_id=LEGACY_TEMPLATE)],
                       teams=[PlayerTeam(name="Blue", role="View Admin", applications=["VMs"])], default_team="Blue", guests=guests)


# --- Validation and the plan ---

def problems(draft: Draft) -> List[str]:
    """What put_draft refuses about the player section."""
    p = draft.player
    if p is None:
        return []
    found = []
    if not enabled():
        found.append("This deployment has no Player view option; turn it on in its Settings first")
    teams = [t.name for t in p.teams]
    apps = [a.name for a in p.applications]
    for kind, names in (("team", teams), ("application", apps)):
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            found.append(f"A {kind} name is used twice: {', '.join(dup)}")
    for t in p.teams:
        missing = [a for a in t.applications if a not in apps]
        if missing:
            found.append(f"Team {t.name} has applications the view doesn't: {', '.join(missing)}")
    if p.default_team and p.default_team not in teams:
        found.append(f"The default team {p.default_team} isn't one of the teams")
    specs = {}
    for e in draft.enclaves:
        stored = store.get_slice(e.slug, e.version)
        if stored:
            specs[e.prefix] = stored[1]
    scopes = [r.scope for r in p.assign]
    for scope in sorted({s for s in scopes if scopes.count(s) > 1}):
        found.append(f"Two rules for {scope}")
    for r in p.assign:
        unknown = [t for t in r.teams if t not in teams]
        if unknown:
            found.append(f"{r.scope}: no team {', '.join(unknown)}")
        if r.scope.startswith("enclave:") and r.scope[len("enclave:"):] not in specs:
            found.append(f"{r.scope}: the draft has no enclave instance {r.scope[len('enclave:'):]}")
    for g in p.guests:
        unknown = [t for t in g.teams if t not in teams]
        if unknown:
            found.append(f"{g.name}: no team {', '.join(unknown)}")
        if g.instance is None:
            continue
        spec = specs.get(g.instance)
        if spec is None:
            found.append(f"{g.name}: the draft has no enclave instance {g.instance}")
        elif g.site and g.name not in {s.name for s in spec.sites}:
            found.append(f"{g.instance}: its spec has no site {g.name}")
        elif not g.site and g.name not in {h.name for h in spec.hosts}:
            found.append(f"{g.instance}: its spec has no host {g.name}")
    return found


def plan(draft: Draft, stages: List[str]) -> Optional[PlannedPlayer]:
    """What the rollout's player stage would do; None without the Player option. Makes no Proxmox call."""
    if not enabled():
        return None
    done = applied()
    p = draft.player
    if p is None:
        return PlannedPlayer(action="destroy" if done or _has_state() else "none", view=(done or {}).get("view"))
    if done is None:
        action = "create"
    elif done.get("config_hash") != config_hash(p) or any(s == "base" or s.startswith(("create:", "destroy:")) for s in stages):
        action = "update"
    else:
        action = "none"
    tfvars = draft_tfvars(draft)
    base = base_guests(tfvars)
    # Without routers there is no base to check against (base_guests always has the management hosts)
    warnings = [f"The base has no {g.name}" for g in p.guests if g.instance is None and g.teams and tfvars.get("routers") and g.name not in base]
    count = sum(1 for h in base if teams_for(p, None, h, False))
    for e in draft.enclaves:
        stored = store.get_slice(e.slug, e.version)
        if stored is None:
            continue
        for h in stored[1].hosts:
            count += (h.count or 1) if teams_for(p, e.prefix, h.name, False, bool(h.crucible)) else 0
        for site in stored[1].sites:
            count += 1 if teams_for(p, e.prefix, site.name, True, bool(site.crucible)) else 0
    return PlannedPlayer(action=action, view=view_name(p), teams=len(p.teams), guests=count, warnings=warnings)


# --- Terraform ---

def _has_state() -> bool:
    try:
        return bool(json.loads((module() / "terraform.tfstate").read_text()).get("resources"))
    except (OSError, ValueError):
        return False


def has_crucible_state(path: Path) -> bool:
    """Whether a state file holds Crucible resources, which 2.5.0 panicked refreshing: destroys of
    them still skip the refresh, which they don't need."""
    try:
        resources = json.loads(path.read_text()).get("resources") or []
    except (OSError, ValueError):
        return False
    return any(str(r.get("type", "")).startswith("crucible_") for r in resources)


def state_applications() -> Optional[List[str]]:
    """The names of the applications the view has in the workspace's state; None without a view."""
    try:
        resources = json.loads((module() / "terraform.tfstate").read_text()).get("resources") or []
    except (OSError, ValueError):
        return None
    for r in resources:
        if r.get("type") == "crucible_player_view":
            for inst in r.get("instances") or []:
                return [a.get("name") for a in (inst.get("attributes") or {}).get("application") or [] if a.get("name")]
    return None


def _tfvars(player: DraftPlayer, vms: Dict[str, Dict], teams: Dict[str, List[str]]) -> Dict:
    return {
        "view": {"name": view_name(player), "description": player.description},
        "applications": [{k: v for k, v in a.model_dump().items() if v is not None} for a in player.applications],
        "teams": [{k: v for k, v in t.model_dump().items() if v is not None} for t in player.teams],
        "vms": {h: {**v, "teams": teams[h]} for h, v in vms.items()},
    }


async def apply(ctx, draft: Draft, exclude: Set[str] = frozenset()) -> None:
    """Make the view the draft says, with the VMs that exist now."""
    from services import dev_simulation
    from services.slices.stages import tf_env, tf_init

    player = draft.player
    await migrate_legacy(ctx)
    teams = desired(player, draft, exclude)
    vms, missing = vm_ids(teams)
    for h in missing:
        ctx.log(f"WARNING {h} isn't on Proxmox: left out of the view")
    data = _tfvars(player, vms, teams)
    ctx.log(f"View {data['view']['name']}: {len(player.applications)} applications, {len(player.teams)} teams, {len(vms)} VMs")
    if dev_simulation.enabled():
        # The checkout is the server's own code in simulation: nothing is written there
        ctx.log("(simulated) terraform apply in terraform/player")
        team_ids = {t.name: f"team-{i}" for i, t in enumerate(player.teams)}
        view_id = (applied() or {}).get("view_id") or "view-simulated"
    else:
        store.write_json(tfvars_file(), data)
        await tf_init(ctx, module())
        apply_args = ["terraform", "apply", "-auto-approve", "-input=false", f"-var-file={tfvars_file()}"]
        existing = state_applications() or []
        new = [a.name for a in player.applications if a.name not in existing]
        if new:
            # The provider adds a team's instance of an application made in the same apply with an empty
            # application ID ("invalid UUID length: 0"): make the applications first, then the instances
            ctx.log(f"New applications {', '.join(new)}: creating them before the teams' instances of them")
            await ctx.run([*apply_args, "-var=instance_applications=" + json.dumps(existing)], module(), env=tf_env())
        await ctx.run(apply_args, module(), env=tf_env())
        _, out = await ctx.run(["terraform", "output", "-json"], module(), env=tf_env(), quiet=True)
        outputs = json.loads(out or "{}")
        view_id = (outputs.get("view_id") or {}).get("value")
        team_ids = (outputs.get("team_ids") or {}).get("value") or {}
    store.write_json(_applied_file(), {"view": data["view"]["name"], "view_id": view_id, "team_ids": team_ids, "guests": sorted(vms),
                                       "config_hash": config_hash(player),
                                       "applied": datetime.now(timezone.utc).isoformat(timespec="seconds")})


async def destroy(ctx) -> None:
    """Remove the view and every registration."""
    from services import dev_simulation
    from services.slices.stages import tf_env, tf_init

    if dev_simulation.enabled():
        ctx.log("(simulated) terraform destroy in terraform/player")
    elif _has_state():
        await tf_init(ctx, module())
        args = [f"-var-file={tfvars_file()}"] if tfvars_file().exists() else ["-var=view={name=\"gone\"}", "-var=teams=[{name=\"gone\"}]"]
        await ctx.run(["terraform", "destroy", "-auto-approve", "-input=false", "-refresh=false", *args], module(), env=tf_env())
    else:
        ctx.log("No Player view to remove")
    _applied_file().unlink(missing_ok=True)


async def migrate_legacy(ctx) -> None:
    """The view the base module made before this workspace (modules/crucible), and the instances'
    registrations in it (slice/crucible.tf): destroyed once, through the provider's delete path."""
    from services import dev_simulation
    from services.slices import stages

    if dev_simulation.enabled():
        return
    for inst in store.live_instances():
        path = stages.instance_tfvars(inst.id)
        data = store.read_json(path) or {}
        if not data.get("crucible_team_id"):
            continue
        ctx.log(f"Removing {inst.id}'s registrations in the base's old view")
        env = stages.tf_env(inst.id)
        await ctx.run(["terraform", "destroy", "-auto-approve", "-input=false", "-refresh=false", "-target=crucible_player_virtual_machine.guest",
                       f"-var-file={path}"], environment.slice_module(), env=env)
        # The rest of its inputs stay as rendered (certificates included)
        store.write_json(path, {k: v for k, v in data.items() if k not in ("crucible_team_id", "crucible_guests")})
    state = environment.base_module() / "terraform.tfstate"
    if has_crucible_state(state):
        ctx.log("Removing the base's old Player view (modules/crucible)")
        env = {**stages.tf_env(), "TF_VAR_configure_crucible": "true"}
        from services.base_jobs import _var_file_args

        await ctx.run(["terraform", "destroy", "-auto-approve", "-input=false", "-refresh=false", "-target=module.crucible", *_var_file_args()],
                      environment.base_module(), env=env)
