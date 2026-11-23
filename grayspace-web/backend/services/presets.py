"""Presets: named sets of runtime changes on top of the base, applied after a reset.

A preset belongs to a base version: the changes the web UI makes (link conditions, interface zones,
DNS domains and records; services/base_changes.py), saved from a deployment and offered to every
deployment on that version. No preset is the base as deployed. Applying one resets first (Reset
changes), so the result is exactly the preset. The rollout applies the one the draft names
(services/rollout.py).

Enclaves have no presets (contract 1.19): an enclave is what its spec builds, and a variant is
another enclave. An instance's actions (services/instance_actions.py) are live changes its reset
undoes. Files an earlier version left in <data>/slices/<slug>/presets/ are ignored.

    <presets dir>/<base name>/<version>/<name>.json   base presets (settings.presets_dir: the server's)
"""
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from config import settings
from models.presets import (BASE_ACTIONS, PRESET_NAME, ApplyPresetResult, Preset, PresetAction, PresetOwner,
                            PresetSummary)
from services import base_changes, request_user
from services.mutation_tracker import mutation_tracker
from services.slices import store

logger = logging.getLogger(__name__)

BASE_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*"


class PresetError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _name(name: str) -> str:
    if not re.fullmatch(PRESET_NAME, name):
        raise PresetError(422, f"'{name}' isn't a preset name: lowercase letters, digits and hyphens")
    return name


def _base_dir(base: str) -> Path:
    if not re.fullmatch(BASE_NAME, base or ""):
        raise PresetError(422, f"'{base}' isn't a core version")
    return settings.presets_dir / base


def _read(directory: Path) -> List[Preset]:
    found = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            found.append(Preset.model_validate(store.read_json(path)))
        except Exception as e:
            logger.warning(f"Skipping preset {path}: {e}")
    return found


def _summary(p: Preset, problems: List[str]) -> PresetSummary:
    return PresetSummary(name=p.name, description=p.description, owner=p.owner, created=p.created, created_by=p.created_by,
                         actions=len(p.actions), kinds=dict(Counter(a.action for a in p.actions)), problems=problems)


def _write(directory: Path, preset: Preset) -> Preset:
    store.write_json(directory / f"{preset.name}.json", preset.model_dump(mode="json"))
    return preset


def _new(kind: str, ref: str, name: str, description: str, actions: List[PresetAction]) -> Preset:
    return Preset(name=_name(name), description=description, owner=PresetOwner(kind=kind, ref=ref), created=store.now(),
                  created_by=request_user.current(), actions=actions)


def check(actions: List[PresetAction], allowed: List[str]) -> None:
    unknown = sorted({a.action for a in actions} - set(allowed))
    if unknown:
        raise PresetError(422, f"Not preset actions here: {', '.join(unknown)} (allowed: {', '.join(allowed)})")


# --- Base presets ---

def deployed_base() -> Optional[str]:
    """The deployed base version's name; None when nothing is deployed or it came from local CSVs."""
    from services.topology_service import topology_service

    return ((topology_service.tfvars_data or {}).get("base") or {}).get("name")


def _need_base(base: Optional[str]) -> str:
    base = base or deployed_base()
    if not base:
        raise PresetError(409, "No core version is deployed; core presets belong to one")
    return base


def list_base(base: Optional[str] = None) -> List[PresetSummary]:
    """A version's presets (the deployed one's by default), with the targets the deployment lacks when it's that version."""
    from services.topology_service import topology_service

    base = _need_base(base)
    here = base == deployed_base()
    return [_summary(p, base_changes.problems(p.actions, topology_service.tfvars_data) if here else []) for p in _read(_base_dir(base))]


def get_base(name: str, base: Optional[str] = None) -> Preset:
    base = _need_base(base)
    data = store.read_json(_base_dir(base) / f"{_name(name)}.json")
    if data is None:
        raise PresetError(404, f"No preset '{name}' for {base}")
    return Preset.model_validate(data)


def save_base(name: str, description: str) -> Preset:
    """The deployment's tracked changes as a preset of its base version, which then is the active preset."""
    base = _need_base(None)
    preset = _write(_base_dir(base), _new("base", base, name, description, base_changes.from_tracker()))
    mutation_tracker.set_preset(preset.name)
    return preset


def put_base(preset: Preset) -> Preset:
    """Store a preset as given (an import, or one written by hand) under its own base version."""
    if preset.owner.kind != "base":
        raise PresetError(422, "Not a core preset")
    check(preset.actions, BASE_ACTIONS)
    return _write(_base_dir(preset.owner.ref), preset.model_copy(update={"name": _name(preset.name)}))


def delete_base(name: str, base: Optional[str] = None) -> None:
    base = _need_base(base)
    path = _base_dir(base) / f"{_name(name)}.json"
    if not path.exists():
        raise PresetError(404, f"No preset '{name}' for {base}")
    path.unlink()
    if mutation_tracker.preset == name and base == deployed_base():
        # Its changes stay, and are now changes on the base itself
        mutation_tracker.preset = None
        mutation_tracker.save()


def _label(a: PresetAction) -> str:
    what = a.target or a.params.get("name") or a.params.get("domain")
    return f"{a.action} {what}" if what else a.action


async def _replay(preset: Preset, apply_one) -> ApplyPresetResult:
    applied: Dict[str, int] = {}
    errors: List[str] = []
    warnings: List[str] = []
    for a in preset.actions:
        try:
            result = await apply_one(a)
            applied[a.action] = applied.get(a.action, 0) + 1
            warnings += [f"{_label(a)}: {w}" for w in getattr(result, "warnings", None) or []]
        except Exception as e:
            errors.append(f"{_label(a)}: {e}")
    return ApplyPresetResult(success=not errors, message=f"Applied {preset.name}" + (f" with {len(errors)} error(s)" if errors else ""),
                             applied=applied, warnings=warnings, errors=errors)


async def apply_base(name: Optional[str]) -> ApplyPresetResult:
    """Reset changes, then apply the preset (None: none, the base as deployed)."""
    preset = get_base(name) if name else None
    warnings = [f"Reset: {e}" for e in await base_changes.revert_all()]
    if preset is None:
        mutation_tracker.set_preset(None)
        return ApplyPresetResult(success=True, message="Back to the core as deployed" + (f", with {len(warnings)} warning(s)" if warnings else ""),
                                 warnings=warnings)
    result = await _replay(preset, base_changes.apply)
    mutation_tracker.set_preset(preset.name)
    result.warnings = warnings + result.warnings
    return result


async def discard() -> ApplyPresetResult:
    """Reset changes: undo what was changed since the active preset, by applying it again."""
    name = mutation_tracker.preset
    if name and deployed_base():
        try:
            get_base(name)
        except PresetError:
            name = None  # deleted since; back to the base
    return await apply_base(name)

