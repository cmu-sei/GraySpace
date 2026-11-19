"""What the API and the CLI share: the deployed base as slices see it, what other instances hold,
and validation."""
import asyncio
from typing import Dict, List, Optional, Tuple

from config import settings
from models.instances import Allocation
from models.slices import Finding, SliceSpec
from services.slices import base, catalog, plan, store


def tfvars() -> dict:
    from services.topology_service import topology_service

    return topology_service.tfvars_data


def context() -> base.BaseContext:
    return base.from_tfvars(tfvars())


def taken(exclude: Optional[str] = None) -> plan.Taken:
    """What live instances other than `exclude` hold, and what the overlay holds: its DNS domains
    and link conditions, which a slice must not take over (C14)."""
    from services.mutation_tracker import mutation_tracker

    links = base.links(tfvars())
    return plan.Taken.from_instances(
        store.live_instances(),
        exclude=exclude,
        overlay_domains=[d.get("domain") for d in mutation_tracker.dns_domains],
        # The overlay may have stored a link in either router order.
        overlay_links=[k if k in links else ":".join(reversed(k.split(":", 1))) for k in mutation_tracker.traffic_configs],
    )


def plan_settings() -> plan.Settings:
    return plan.Settings(pool=settings.slice_pool, mgmt_subnet=settings.mgmt_subnet)


def resolve(spec: SliceSpec, prefix: Optional[str] = None, exclude: Optional[str] = None) -> Tuple[Optional[Allocation], List[Finding]]:
    if not tfvars().get("routers"):
        return None, [Finding(code="INVALID_SPEC", severity="error", path="", message="No base is deployed: the tfvars have no routers (run task parse)")]
    return plan.resolve(spec, context(), catalog.images(), catalog.roles(), taken(exclude), plan_settings(), prefix)


async def validate(spec: SliceSpec, prefix: Optional[str] = None, exclude: Optional[str] = None,
                   check_capacity: bool = True) -> Tuple[Optional[Allocation], List[Finding]]:
    """Findings for a spec against the deployed base, live instances and (optionally) the node's
    free capacity. Never changes anything."""
    allocation, findings = await asyncio.to_thread(resolve, spec, prefix, exclude)
    if allocation is not None and (any(s.crucible for s in allocation.sites) or any(h.crucible for h in allocation.hosts)):
        from services.slices import environment

        from services import player

        if environment.crucible_team_id() is None and not player.enabled():
            findings.append(Finding(code="CRUCIBLE_OFF", severity="warning", path="hosts",
                                    message="Guests are marked crucible: true, but the deployment has no Crucible Player view (its "
                                    "Settings); they won't be in one"))
    if allocation is not None and check_capacity:
        from services.capabilities import capacity

        findings += plan.capacity_findings(allocation, await capacity())
    return allocation, findings


_shipped: Dict[Tuple[str, str, Optional[str]], dict] = {}


def shipped_tfvars(version: str, scope: str) -> dict:
    """The tfvars a deploy of a shipped version would write (task parse), kept while the version's CSVs are the same."""
    import json
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    from services import base_versions

    found = next((v for v in base_versions.list_versions() if v.name == version), None)
    if found is None:
        raise ValueError(f"No shipped base version '{version}'")
    key = (version, scope, found.csv_sha256)
    if key not in _shipped:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tfvars.json"
            proc = subprocess.run([sys.executable, str(Path(settings.scripts_path) / "generate_tfvars.py"), f"--scope={scope}",
                                   f"--base={version}", f"--output-file={out}"], capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                raise ValueError((proc.stderr or proc.stdout).strip()[-1500:])
            _shipped[key] = json.loads(out.read_text())
    return _shipped[key]


async def validate_as(spec: SliceSpec, instance: Optional[str] = None) -> Tuple[Optional[Allocation], List[Finding]]:
    """validate() for the instance a draft names (its prefix). When that instance is live and built
    from this enclave, its prefix, DNS names and links are its own, not taken, and it already has its
    capacity; another enclave's instance with that prefix still takes it (PREFIX_TAKEN)."""
    if instance is None:
        return await validate(spec)
    live = store.get_instance(instance)
    ours = live is not None and live.live and live.slice.slug == spec.slug
    return await validate(spec, prefix=instance, exclude=instance if ours else None, check_capacity=not ours)


async def validate_on(spec: SliceSpec, version: str, scope: str, prefix: Optional[str] = None,
                      instance: Optional[str] = None) -> Tuple[Optional[Allocation], List[Finding]]:
    """Findings for a spec against a shipped base version, for a deployment still being built. On the
    deployed version and scope it is validate() (validate_as() for a draft's instance); on another,
    nothing is live there yet, so no instance holds anything and capacity isn't checked."""
    from services.base_service import version_ref

    deployed = version_ref(tfvars())
    if deployed is not None and deployed.name == version and deployed.scope == scope:
        return await validate_as(spec, instance) if instance else await validate(spec, prefix)
    target = await asyncio.to_thread(shipped_tfvars, version, scope)
    empty = plan.Taken.from_instances([], overlay_domains=[], overlay_links=[])
    return await asyncio.to_thread(plan.resolve, spec, base.from_tfvars(target), catalog.images(), catalog.roles(), empty, plan_settings(), prefix)


def errors(findings: List[Finding]) -> List[Finding]:
    return [f for f in findings if f.severity == "error"]
