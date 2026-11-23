"""The deployments resource (/api/v1/deployments)."""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

ID_PATTERN = r"^[a-z][a-z0-9]{0,3}$"
CONNECTION_ID = r"^[a-z][a-z0-9-]{0,31}$"


# ----- Platform connections (control/platform.py): Proxmox and Crucible, defined once by admins -----

class ProxmoxNode(BaseModel):
    """A node deployments can run on, with the storage and bridges they use there."""
    name: str = Field(..., min_length=1, description="The Proxmox node (TF_VAR_proxmox_node)")
    blockstore: Optional[str] = Field(None, description="Block storage for disks (TF_VAR_proxmox_blockstore)")
    filestore: Optional[str] = Field(None, description="File storage for templates and snippets (TF_VAR_proxmox_filestore)")
    mgmt_bridge: Optional[str] = Field(None, description="Management bridge (TF_VAR_proxmox_mgmt_bridge)")
    vm_bridge: Optional[str] = Field(None, description="VLAN bridge (TF_VAR_proxmox_vm_bridge)")


class ProxmoxConnectionFields(BaseModel):
    name: Optional[str] = Field(None, description="Display name; the ID when unset")
    host: Optional[str] = Field(None, description="The API host (TF_VAR_proxmox_host_addr)")
    port: Optional[int] = Field(None, ge=1, le=65535, description="The API port; 8006 when unset")
    verify_ssl: Optional[bool] = Field(None, description="Check the API's certificate in the server's own probes")
    auth: Optional[Literal["token", "password"]] = Field(None, description="token: api_token; password: username and password")
    username: Optional[str] = Field(None, description="user@realm, for password auth (PROXMOX_VE_USERNAME)")
    ssh_username: Optional[str] = Field(None, description="SSH user on the nodes (PROXMOX_VE_SSH_USERNAME)")
    nodes: Optional[List[ProxmoxNode]] = None
    default_node: Optional[str] = Field(None, description="The node new deployments get; the first when unset")
    apply_parallelism: Optional[int] = Field(None, ge=0, le=100, description=(
        "Terraform's -parallelism for applies and destroys of the deployments on this cluster: how many guests are created at once. "
        "Creates contend for the storage lock, and too many time out. 0 clears it (the server's default, 10)"))
    plan_parallelism: Optional[int] = Field(None, ge=0, le=100, description=(
        "Terraform's -parallelism for plans, which only read. 0 clears it (the server's default, 30)"))
    proxy_mode: Optional[Literal["site", "none", "custom"]] = Field(None, description=(
        "The proxy the guests of this cluster's deployments install packages through: site, the site file's TF_VAR_proxy (the "
        "default); none, no proxy even where the site has one; custom, proxy and no_proxy here"))
    proxy: Optional[str] = Field(None, pattern=r"^(https?://\S+)?$", description=(
        "http://host:port, for proxy_mode custom (TF_VAR_proxy). Empty clears it"))
    no_proxy: Optional[str] = Field(None, description=(
        "Hosts and networks the guests reach directly, comma-separated, for proxy_mode custom (TF_VAR_no_proxy). Empty clears it"))
    api_token: Optional[str] = Field(None, description="user@realm!name=secret (PROXMOX_VE_API_TOKEN). Write-only; empty clears it")
    password: Optional[str] = Field(None, description="PROXMOX_VE_PASSWORD. Write-only; empty clears it")
    ssh_password: Optional[str] = Field(None, description="PROXMOX_VE_SSH_PASSWORD. Write-only; empty clears it")


class CreateProxmoxConnection(ProxmoxConnectionFields):
    id: str = Field(..., pattern=CONNECTION_ID, description="1-32 lowercase letters, digits and hyphens, starting with a letter")
    host: str = Field(..., min_length=1)
    nodes: List[ProxmoxNode] = Field(..., min_length=1)


class ProxmoxConnection(BaseModel):
    id: str
    name: str
    host: str
    port: int = 8006
    verify_ssl: bool = False
    auth: Literal["token", "password"] = "token"
    username: Optional[str] = None
    ssh_username: Optional[str] = None
    nodes: List[ProxmoxNode]
    default_node: str
    apply_parallelism: Optional[int] = Field(None, description="Unset: the server's default")
    plan_parallelism: Optional[int] = Field(None, description="Unset: the server's default")
    proxy_mode: Literal["site", "none", "custom"] = Field("site", description="site: the site file's TF_VAR_proxy")
    proxy: Optional[str] = None
    no_proxy: Optional[str] = None
    secrets: List[str] = Field(default_factory=list, description="Which write-only settings are set")
    deployments: List[str] = Field(default_factory=list, description="The deployments that run on it")


class CrucibleConnectionFields(BaseModel):
    name: Optional[str] = None
    auth_url: Optional[str] = Field(None, description="Keycloak's authorization endpoint (TF_VAR_crucible_auth_url)")
    token_url: Optional[str] = Field(None, description="Keycloak's token endpoint (TF_VAR_crucible_token_url)")
    client_id: Optional[str] = Field(None, description="The OAuth client of the Terraform provider (TF_VAR_crucible_client_id)")
    client_scopes: Optional[List[str]] = Field(None, description="OAuth scopes, e.g. vm-api, player-api")
    username: Optional[str] = Field(None, description="The service account (TF_VAR_crucible_username)")
    vm_api_url: Optional[str] = None
    player_api_url: Optional[str] = None
    caster_api_url: Optional[str] = None
    service_user_id: Optional[str] = Field(None, description="The Player user made admin of every view (TF_VAR_crucible_user_id)")
    player_url: Optional[str] = Field(None, description="Player's UI, when it isn't the Player API URL without /api (the views' links)")
    password: Optional[str] = Field(None, description="The service account's password. Write-only; empty clears it")


class CreateCrucibleConnection(CrucibleConnectionFields):
    id: str = Field(..., pattern=CONNECTION_ID)
    token_url: str = Field(..., min_length=1)
    player_api_url: str = Field(..., min_length=1)


class CrucibleConnection(BaseModel):
    id: str
    name: str
    auth_url: str = ""
    token_url: str
    client_id: str = ""
    client_scopes: List[str] = Field(default_factory=list)
    username: str = ""
    vm_api_url: str = ""
    player_api_url: str
    caster_api_url: str = ""
    service_user_id: str = ""
    player_url: str = Field("", description="Player's UI; the Player API URL without /api when empty")
    secrets: List[str] = Field(default_factory=list)
    deployments: List[str] = Field(default_factory=list, description="The deployments with a Player view through it")


class Platform(BaseModel):
    proxmox: List[ProxmoxConnection]
    crucible: List[CrucibleConnection]
    default_proxmox: Optional[str] = Field(None, description="The connection new deployments get when they don't say")
    imported_from: Optional[str] = Field(None, description="The site environment file the first connections came from")


class PlatformDefaults(BaseModel):
    default_proxmox: Optional[str] = None


class ConnectionCheck(BaseModel):
    name: str
    status: Literal["ok", "warning", "error"]
    message: str


class ConnectionTest(BaseModel):
    ok: bool
    checks: List[ConnectionCheck]


class DiscoveredNode(BaseModel):
    name: str
    status: Optional[str] = None
    storages: List[Dict[str, Any]] = Field(default_factory=list, description="{storage, type, content}")
    bridges: List[str] = Field(default_factory=list)


class Discovery(BaseModel):
    version: Optional[str] = None
    nodes: List[DiscoveredNode]
    vnets: List[str] = Field(default_factory=list)


class ConnectionChange(BaseModel):
    restart_pending: List[str] = Field(default_factory=list, description="Deployments whose worker restarts once their job ends")


class Placement(BaseModel):
    connection: str = Field(..., description="A Proxmox connection (GET /api/v1/platform)")
    node: Optional[str] = Field(None, description="One of its nodes; its default when unset")


class ProxmoxSettings(BaseModel):
    """Where the deployment runs, resolved from its placement. Read-only: give a placement."""
    host_addr: Optional[str] = Field(None, description="Proxmox API host (TF_VAR_proxmox_host_addr); another cluster than the site's")
    node: Optional[str] = Field(None, description="Proxmox node (TF_VAR_proxmox_node)")
    blockstore: Optional[str] = Field(None, description="Block storage for disks (TF_VAR_proxmox_blockstore)")
    filestore: Optional[str] = Field(None, description="File storage for templates and snippets (TF_VAR_proxmox_filestore)")
    mgmt_bridge: Optional[str] = Field(None, description="Management bridge (TF_VAR_proxmox_mgmt_bridge)")
    vm_bridge: Optional[str] = Field(None, description="VLAN bridge (TF_VAR_proxmox_vm_bridge)")


class ProxmoxCredentials(BaseModel):
    """Deprecated: credentials are the connection's (Admin > Platform). Refused when set."""
    api_token: Optional[str] = Field(None, description="PROXMOX_VE_API_TOKEN")
    username: Optional[str] = Field(None, description="PROXMOX_VE_USERNAME")
    password: Optional[str] = Field(None, description="PROXMOX_VE_PASSWORD")
    ssh_username: Optional[str] = Field(None, description="PROXMOX_VE_SSH_USERNAME")
    ssh_password: Optional[str] = Field(None, description="PROXMOX_VE_SSH_PASSWORD")


class DeploymentOptions(BaseModel):
    deploy_ghosts: Optional[bool] = Field(
        None, deprecated=True,
        description="Deprecated (contract 1.25), ignored: the core has no GHOSTS VM. Enclave hosts run GHOSTS clients (the ghosts-client role)")
    use_custom_image: Optional[bool] = Field(
        None, deprecated=True,
        description="Deprecated (contract 1.12), ignored: every deployment's Alpine guests use the GraySpace image")
    player: Optional[bool] = Field(None, description="Give the deployment a Crucible Player view")
    player_connection: Optional[str] = Field(None, description="The Crucible connection; the only one when unset")


class CreateDeployment(BaseModel):
    id: str = Field(..., pattern=ID_PATTERN, description="The deployment's environment_id: 1-4 lowercase letters and digits, "
                    "starting with a letter. It prefixes every Proxmox resource the deployment creates")
    name: Optional[str] = Field(None, description="Display name; the ID when unset")
    description: str = ""
    placement: Optional[Placement] = Field(None, description="Where it runs; the default connection and its default node when unset")
    proxmox: ProxmoxSettings = Field(default_factory=ProxmoxSettings, deprecated=True,
                                     description="Deprecated: give placement. Refused when set")
    credentials: Optional[ProxmoxCredentials] = Field(None, deprecated=True, description="Deprecated: the connection's. Refused when set")
    options: DeploymentOptions = Field(default_factory=DeploymentOptions)
    router_password: Optional[str] = Field(None, description="Root password of the routers (TF_VAR_router_password). Write-only")
    default: bool = Field(False, description="Make it the default deployment, which the un-prefixed /api routes reach")


class UpdateDeployment(BaseModel):
    """name and description change any time (owner); default (admins); the rest only while nothing is deployed."""
    name: Optional[str] = None
    description: Optional[str] = None
    default: Optional[bool] = None
    placement: Optional[Placement] = None
    proxmox: Optional[ProxmoxSettings] = Field(None, deprecated=True, description="Deprecated: give placement. Refused when set")
    credentials: Optional[ProxmoxCredentials] = Field(None, deprecated=True, description="Deprecated: the connection's. Refused when set")
    options: Optional[DeploymentOptions] = None
    router_password: Optional[str] = None


class WorkerStatus(BaseModel):
    state: Literal["stopped", "starting", "running", "crashed"]
    pid: Optional[int] = None
    started: Optional[str] = None
    restarts: int = 0
    error: Optional[str] = None


class BaseSummary(BaseModel):
    status: str = Field(..., description="The deployed core's status (GET /deployments/{id}/api/v1/base), or unknown when the worker "
                        "doesn't answer")
    version: Optional[Any] = None
    scope: Optional[str] = Field(None, description="full or tier1, when deployed")
    routers: Optional[int] = Field(None, description="Routers in the deployed core")


class JobBrief(BaseModel):
    id: str
    kind: str
    state: str
    stage: Optional[str] = None
    attempt: Optional[str] = None
    finished: Optional[str] = None


class DraftState(BaseModel):
    saved: bool = Field(..., description="Whether anyone saved a draft (PUT /deployments/{id}/api/v1/draft)")
    changes: Optional[bool] = Field(None, description="Whether deploying the draft would change anything")


class DeploymentSummary(BaseModel):
    """What the deployments list shows of each: its enclave instances and jobs (GET /deployments/{id}/api/v1/summary)."""
    instances_live: int = 0
    instances_failed: int = 0
    active_job: Optional[JobBrief] = Field(None, description="The newest queued or running job")
    last_base_job: Optional[JobBrief] = Field(None, description="The newest core job (deploy, configure, destroy)")
    updated: Optional[str] = Field(None, description="When its newest job changed")
    draft: Optional[DraftState] = None


class Member(BaseModel):
    user: str = Field(..., description="A username")
    access: Literal["build", "use"] = Field(..., description="build: build and deploy (builders only); use: view and act at runtime")


class Members(BaseModel):
    members: List[Member]


class Deployment(BaseModel):
    id: str
    name: str
    description: str = ""
    kind: Literal["managed", "external"] = Field(..., description="managed: a work tree the control plane made; external: an "
                                                  "existing GraySpace tree it adopted, which it never re-syncs or deletes")
    default: bool
    created: str
    url: str = Field(..., description="The deployment's base URL: the Terrain API is at {url}/api/v1")
    placement: Optional[Placement] = Field(None, description="The Proxmox connection and node; null for an adopted tree that matches none")
    proxmox: ProxmoxSettings = Field(..., description="Where it runs, resolved: the connection's host and the node's settings")
    options: DeploymentOptions
    secrets: List[str] = Field(default_factory=list, description="Which write-only settings are set")
    worker: WorkerStatus
    base: Optional[BaseSummary] = None
    summary: Optional[DeploymentSummary] = Field(None, description="Null when the worker isn't running")
    owner: Optional[str] = Field(None, description="The user who created it; null when the service token did, or it predates owners")
    members: List[Member] = Field(default_factory=list)
    my_access: List[str] = Field(default_factory=list, description="What the caller may do here: read, operate, build, "
                                 "members:manage, delete")
    warnings: List[str] = Field(default_factory=list)


class DeploymentDetail(Deployment):
    work_dir: str
    log: List[str] = Field(default_factory=list, description="The worker log's last lines")


def secret_names(credentials: Optional[Dict[str, Any]], router_password: Optional[str]) -> List[str]:
    names = [f"credentials.{k}" for k, v in (credentials or {}).items() if v]
    if router_password:
        names.append("router_password")
    return names


class DeploymentEvent(BaseModel):
    """A message on the /api/v1/deployments/events websocket (after a {"type": "hello"} greeting)."""
    type: Literal["deployment.created", "deployment.updated", "deployment.deleted", "deployment.worker"]
    id: str = Field(..., description="The deployment")
    deployment: Optional[Deployment] = Field(None, description="Its state now; null once deleted")


# ----- Sign-in, users, the audit log and nodes -----

class LoginRequest(BaseModel):
    username: str
    password: str


class SessionInfo(BaseModel):
    id: str
    created: str
    last_used: float = Field(..., description="Unix time")
    expires: float = Field(..., description="Unix time; slides forward while the session is used")
    ip: str = ""
    agent: str = ""
    current: bool = False


class LoginResponse(BaseModel):
    token: str = Field(..., description="Send as Authorization: Bearer <token> (or ?token= on websockets)")
    session: SessionInfo
    must_change_password: bool = Field(..., description="Only /me, /auth/password and /auth/logout work until it is changed")


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class Providers(BaseModel):
    providers: List[str] = Field(..., description="Sign-in sources: local, oidc")


class User(BaseModel):
    username: str
    name: str = ""
    email: str = ""
    role: Literal["admin", "builder", "user"]
    source: Literal["local", "oidc"] = "local"
    disabled: bool = False
    must_change_password: bool = False
    created: Optional[str] = None
    last_seen: Optional[str] = None


class DirectoryEntry(BaseModel):
    username: str
    name: str
    role: Literal["admin", "builder", "user"]


class Me(BaseModel):
    user: Optional[User] = Field(None, description="Null for the service token")
    kind: Literal["user", "service"]
    role: str
    permissions: List[str] = Field(..., description="Server-wide; a deployment's are its my_access")
    must_change_password: bool = False
    providers: List[str]


class CreateUser(BaseModel):
    username: str = Field(..., description="1-32 lowercase letters, digits, '.', '_' or '-', starting with a letter")
    name: str = ""
    email: str = ""
    role: Literal["admin", "builder", "user"] = "user"
    deployments: List[str] = Field(default_factory=list, description="Deployments to add the user to, with use access")


class UpdateUser(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[Literal["admin", "builder", "user"]] = None
    disabled: Optional[bool] = Field(None, description="A disabled user can't sign in; their sessions end")


class OneTimePassword(BaseModel):
    user: User
    one_time_password: str = Field(..., description="Shown once. The user must change it at first sign-in")


class AuditEntry(BaseModel):
    at: str
    user: Optional[str] = Field(None, description="Null when the request had no valid token")
    method: str
    path: str
    deployment: Optional[str] = None
    status: int
    detail: str = ""
    ip: str = ""


class Capacity(BaseModel):
    cpus: Optional[int] = None
    cpu_usage: Optional[float] = None
    memory_total: Optional[int] = None
    memory_free: Optional[int] = None
    storage: Optional[str] = None
    storage_total: Optional[int] = None
    storage_free: Optional[int] = None


class Node(BaseModel):
    connection: Optional[str] = Field(None, description="The Proxmox connection; null for an adopted tree that matches none")
    connection_name: Optional[str] = None
    host: Optional[str] = Field(None, description="The Proxmox API host (TF_VAR_proxmox_host_addr)")
    node: Optional[str] = Field(None, description="The Proxmox node (TF_VAR_proxmox_node)")
    deployments: List[str]
    capacity: Optional[Capacity] = Field(None, description="From Proxmox, or a running deployment's capabilities; null when neither answers")
    locked: bool = Field(..., description="Whether a job holds the cluster's lock now (locks/<host>.lock)")
    lock_holder: Optional[str] = Field(None, description="The deployment whose job is running, when one is")
