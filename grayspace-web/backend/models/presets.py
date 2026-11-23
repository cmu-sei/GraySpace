"""Presets: named sets of runtime changes on top of the base (services/presets.py).

A preset belongs to a base version and holds base changes, as a list of actions in the shape
POST /base/actions takes. Enclaves have no presets (contract 1.19): an enclave is what its spec
builds, and a variant is another enclave.
"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

PRESET_NAME = r"^[a-z0-9][a-z0-9-]{0,47}$"

# The base's changes: what the web UI's map and DNS views make, and Reset changes undoes
BASE_ACTIONS = ["link.condition", "interface.zone", "dns.domain.create", "dns.record.set"]


class PresetAction(BaseModel):
    action: str = Field(..., description=", ".join(BASE_ACTIONS))
    target: Optional[str] = Field(None, description="A link ID (r1:r2) or hostname:interface")
    params: Dict[str, Any] = Field(default_factory=dict)


class PresetOwner(BaseModel):
    kind: Literal["base"] = Field(..., description="Always `base` (enclave presets were removed in contract 1.19)")
    ref: str = Field(..., description="The core version (grayspace/2026.11)")


class Preset(BaseModel):
    name: str = Field(..., pattern=PRESET_NAME)
    description: str = ""
    owner: PresetOwner
    created: str
    created_by: Optional[str] = Field(None, description="Who saved it; null when it came with the service token")
    actions: List[PresetAction] = Field(default_factory=list, description="Applied in order, after a reset")


class PresetSummary(BaseModel):
    name: str
    description: str
    owner: PresetOwner
    created: str
    created_by: Optional[str]
    actions: int
    kinds: Dict[str, int] = Field(..., description="How many of each action")
    problems: List[str] = Field(default_factory=list, description="Targets the deployment doesn't have; applying skips them")


class SavePresetRequest(BaseModel):
    name: str = Field(..., pattern=PRESET_NAME, description="Lowercase letters, digits and hyphens. Saving a name again replaces it")
    description: str = ""


class ApplyPresetResult(BaseModel):
    success: bool
    message: str
    applied: Dict[str, int] = Field(default_factory=dict, description="How many of each action were applied")
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class TrackedChange(BaseModel):
    """A runtime change since the preset (services/mutation_tracker.py)."""
    kind: str = Field(..., description="traffic, dns_domain, dns_record, zone, certificate_issued, certificate_revoked")
    key: str
    summary: str
    by: Optional[str] = Field(None, description="Who made it; null when it came with the service token")
    at: str


class TrackedChanges(BaseModel):
    preset: Optional[str] = Field(..., description="The core preset Reset changes returns to; null for the core as deployed")
    base: Optional[str] = Field(None, description="The deployed core version the preset belongs to")
    baseline: str = Field(..., description="Deprecated: preset, or GraySpace for none", deprecated=True)
    count: int = Field(..., description="Tracked mutations, the preset's included")
    changes: List[TrackedChange] = Field(..., description="Changes since the preset, newest first")
