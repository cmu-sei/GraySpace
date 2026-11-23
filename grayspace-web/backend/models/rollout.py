"""The draft (what a deployment should have) and the rollout that makes it so (services/rollout.py)."""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from models.base import BaseVersionRef
from models.capabilities import Capacity
from models.slices import PREFIX


class DraftBase(BaseModel):
    version: str = Field(..., description="A shipped core version, e.g. grayspace/2026.11 (GET /base/versions)")
    scope: Literal["full", "tier1"] = "full"
    configure: bool = Field(True, description="Run Ansible after the apply")
    preset: Optional[str] = Field(None, description="A core preset of the version (GET /base/presets?base=), applied after the core; "
                                  "null leaves the runtime changes as they are")


class DraftEnclave(BaseModel):
    slug: str = Field(..., description="A stored enclave (GET /enclaves)")
    version: Optional[str] = Field(None, description="A stored version; the latest when the rollout runs if unset")
    prefix: str = Field(..., pattern=PREFIX, description="The instance's ID and hostname prefix")
    preset: Optional[str] = Field(None, deprecated=True, description="Deprecated and ignored: enclaves have no presets (contract 1.19)")


class PlayerApplication(BaseModel):
    name: str = Field(..., min_length=1, description="What Player calls it; teams name it to get it")
    template_id: Optional[str] = Field(None, description="A Player application template (GET /player/templates)")
    url: Optional[str] = Field(None, description="Its own URL, instead of the template's")
    icon: Optional[str] = None
    embeddable: bool = True
    load_in_background: bool = False


class PlayerTeam(BaseModel):
    name: str = Field(..., min_length=1)
    role: Optional[str] = Field(None, description="A Player team role (GET /player/roles), e.g. View Member")
    permissions: List[str] = Field(default_factory=list)
    applications: List[str] = Field(default_factory=list, description="The view's applications the team gets, in display order")


class PlayerGuest(BaseModel):
    """A VM and the teams that see it."""
    instance: Optional[str] = Field(None, description="An enclave instance of the draft (its prefix); null for a core guest")
    name: str = Field(..., description="A core hostname (lum-chi, prometheus), or an enclave host or site as its spec names it; "
                      "a counted host is every copy")
    site: bool = Field(False, description="name is a site: its edge router")
    teams: List[str] = Field(default_factory=list, description="Empty: not in the view, even if the spec marks it crucible: true")


class PlayerRule(BaseModel):
    """Teams for a whole part of the deployment: every core guest, or every site and host of an enclave instance."""
    scope: str = Field(..., pattern=r"^(base|enclave:[a-z][a-z0-9]{0,7})$", description="base (the core's guests), or enclave:<prefix> (a draft enclave instance)")
    teams: List[str] = Field(default_factory=list)


class DraftPlayer(BaseModel):
    """The deployment's Player view: its applications, teams, and which VMs each team sees. Needs
    the deployment's Player option (Settings); applied by the rollout's player stage."""
    view_name: Optional[str] = Field(None, description="The view's name; <environment_id>-grayspace when unset")
    description: str = ""
    applications: List[PlayerApplication] = Field(default_factory=list)
    teams: List[PlayerTeam] = Field(..., min_length=1)
    default_team: Optional[str] = Field(None, description="Where hosts and sites an enclave spec marks crucible: true go unless "
                                        "guests says otherwise; the first team when unset")
    assign: List[PlayerRule] = Field(default_factory=list, description="Teams for every guest of the core or of an enclave instance. "
                                     "A guest in guests has the teams it names instead; an enclave's crucible: true hints add the default team")
    guests: List[PlayerGuest] = Field(default_factory=list, description="Guests whose teams differ from what assign gives them")


class DraftUpdate(BaseModel):
    base: Optional[DraftBase] = Field(None, description="Null: keep the deployed core, or deploy none")
    enclaves: List[DraftEnclave] = Field(default_factory=list, description="The instances the deployment should have")
    player: Optional[DraftPlayer] = Field(None, description="The Player view; null for none (removed if there is one)")


class Draft(DraftUpdate):
    updated: Optional[str] = None
    updated_by: Optional[str] = None
    adopted: bool = Field(False, description="Made from what is deployed, because nobody had saved a draft")


class PlannedBase(BaseModel):
    action: Literal["none", "deploy", "update"]
    deployed: Optional[BaseVersionRef] = None
    version: Optional[str] = None
    scope: Optional[str] = None
    configure: bool = True
    preset: Optional[str] = Field(None, description="The core preset the rollout applies")
    active_preset: Optional[str] = Field(None, description="The core preset applied now")


class PlannedInstance(BaseModel):
    id: str
    slug: str
    version: Optional[str] = Field(None, description="The stored version it is (or will be) built from")
    action: Literal["create", "destroy", "replace", "keep"] = Field(
        ..., description="replace: a live instance of another version, or a failed one, is destroyed and built again")
    state: Optional[str] = Field(None, description="The live instance's state")


class PlannedPlayer(BaseModel):
    action: Literal["none", "create", "update", "destroy"]
    view: Optional[str] = None
    teams: int = 0
    guests: int = Field(0, description="VMs registered, as far as the plan can tell (enclaves not built yet count as their spec has)")
    warnings: List[str] = Field(default_factory=list)


class DeployPlan(BaseModel):
    """What POST /deploy would do: the draft against what is deployed."""
    base: PlannedBase
    instances: List[PlannedInstance]
    unmanaged: List[str] = Field(default_factory=list, description="Live instances made outside the draft (by GHOSTS, the API): left alone")
    problems: List[str] = Field(default_factory=list, description="Why the draft can't be deployed as it is")
    stages: List[str] = Field(default_factory=list, description="The rollout's stages, in order: destroys, the core (base) and its preset "
                              "(preset), creates, then the Player view (player); a view being removed goes first")
    changes: bool = Field(..., description="Whether deploying would change anything")
    capacity: Optional[Capacity] = Field(None, description="The Proxmox node's headroom now; null when Proxmox is unavailable")
    player: Optional[PlannedPlayer] = Field(None, description="The Player view; null when the deployment has no Player option")
    resumable_rollout: Optional[str] = Field(
        None, description="The last rollout, when it failed or was cancelled and the draft is still the one it deployed: retrying it "
        "resumes where it stopped (since 1.22). Null otherwise; after the draft changes, deploy instead (a retry is refused)")
