"""Who may do what: roles, deployment access, and the class of every worker route (docs/deployments.md, Access).

Roles are the server's: admin (everything), builder (creates deployments, builds and deploys the
ones they own or build), user (uses the deployments they are a member of). Access to a deployment
is its owner, or a member with build or use access. What that allows there:

    read      see it: every GET, the topology and events websockets
    operate   act on it at runtime: consoles, instance actions, traffic shaping,
              DNS and certificate changes, Reset changes, applying presets
    build     change what is deployed: the core (deploy, configure), enclaves, instances, jobs,
              presets (saving and deleting), images
    members:manage, delete   the owner's (and admins')

The proxy classes each request to a worker by method and path (route_class) and checks the caller
has that permission. tests/test_control_access.py fails if a worker route isn't in the table.
"""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

ADMIN_PERMISSIONS = ["deployments:create", "deployments:all", "users:manage", "keys:manage", "catalog:write", "nodes:read", "audit:read",
                     "platform:manage"]
SERVER_PERMISSIONS = {"admin": ADMIN_PERMISSIONS, "builder": ["deployments:create"], "user": []}
DEPLOYMENT_PERMISSIONS = ["read", "operate", "build", "members:manage", "delete"]
ACCESS_PERMISSIONS = {
    "admin": DEPLOYMENT_PERMISSIONS,
    "owner": DEPLOYMENT_PERMISSIONS,
    "build": ["read", "operate", "build"],
    "use": ["read", "operate"],
}


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    kind: str = "user"  # user, or service: GRAYSPACE_API_TOKEN
    session_id: Optional[str] = None
    must_change_password: bool = False

    @property
    def permissions(self) -> List[str]:
        return list(SERVER_PERMISSIONS.get(self.role, []))

    def can(self, permission: str) -> bool:
        return permission in self.permissions


SERVICE = Principal(username="service", role="admin", kind="service")


def access(principal: Principal, record: Dict) -> Optional[str]:
    """admin, owner, build or use; None when the deployment isn't the principal's to see."""
    if principal.role == "admin":
        return "admin"
    if record.get("owner") == principal.username:
        return "owner"
    for member in record.get("members") or []:
        if member.get("user") == principal.username:
            # Only builders build; a user is a member with use access whatever the record says
            return "build" if member.get("access") == "build" and principal.role == "builder" else "use"
    return None


def permissions(principal: Principal, record: Dict) -> List[str]:
    return list(ACCESS_PERMISSIONS.get(access(principal, record) or "", []))


# ----- Worker routes -----

_SEG = r"[^/]+"
# (methods, path pattern with {} for one segment, class). Reads (GET, HEAD) are read unless listed.
WORKER_ROUTES: List[Tuple[str, str, str]] = [
    ("WS", "/ws", "read"),
    ("WS", "/api/v1/events", "read"),
    ("WS", "/ws/console/{}", "operate"),
    ("WS", "/ws/desktop/{}", "operate"),
    # Runtime changes: tracked, undone by Reset changes (POST /changes/discard)
    ("POST", "/api/v1/topology/reload", "operate"),
    ("PUT DELETE", "/api/v1/traffic/{}/interface/{}", "operate"),
    ("PUT DELETE", "/api/v1/links/{}/traffic", "operate"),
    ("PUT", "/api/v1/router/{}/interface/{}/zone", "operate"),
    ("DELETE", "/api/v1/console/session/{}", "operate"),
    ("POST", "/api/v1/dns/domains", "operate"),
    ("POST", "/api/v1/dns/{}/records", "operate"),
    ("POST", "/api/v1/ca/certificates/issue", "operate"),
    ("POST", "/api/v1/ca/{}/certificates/revoke", "operate"),
    ("POST", "/api/v1/core/actions", "operate"),
    ("POST", "/api/v1/base/actions", "operate"),
    ("POST", "/api/v1/changes/discard", "operate"),
    ("POST", "/api/v1/scenarios/discard-changes", "operate"),
    ("POST", "/api/v1/core/presets/{}/apply", "operate"),
    ("POST", "/api/v1/base/presets/{}/apply", "operate"),
    ("POST", "/api/v1/reset/quick", "operate"),
    ("POST", "/api/v1/reset/cancel", "operate"),
    ("POST", "/api/v1/instances/{}/actions", "operate"),
    # What is deployed, and the presets a reset returns to
    ("POST", "/api/v1/containers/{}/start", "build"),
    ("POST", "/api/v1/containers/{}/stop", "build"),
    ("POST", "/api/v1/containers/{}/restart", "build"),
    ("PUT", "/api/v1/networks/vnet/{}/zone", "build"),
    ("POST", "/api/v1/networks/apply", "build"),
    ("POST", "/api/v1/core/presets", "build"),
    ("POST", "/api/v1/base/presets", "build"),
    ("PUT DELETE", "/api/v1/core/presets/{}", "build"),
    ("PUT DELETE", "/api/v1/base/presets/{}", "build"),
    ("POST", "/api/v1/core/deploy", "build"),
    ("POST", "/api/v1/base/deploy", "build"),
    ("PUT", "/api/v1/draft", "build"),
    ("POST", "/api/v1/deploy", "build"),
    ("POST", "/api/v1/core/configure", "build"),
    ("POST", "/api/v1/base/configure", "build"),
    ("DELETE", "/api/v1/core", "delete"),
    ("DELETE", "/api/v1/base", "delete"),
    ("PUT DELETE", "/api/v1/enclaves/{}", "build"),
    ("POST", "/api/v1/enclaves/{}/validate", "build"),
    ("POST", "/api/v1/enclaves/{}/plan", "build"),
    ("PUT DELETE", "/api/v1/slices/{}", "build"),
    ("POST", "/api/v1/slices/{}/validate", "build"),
    ("POST", "/api/v1/slices/{}/plan", "build"),
    ("POST", "/api/v1/instances", "build"),
    ("DELETE", "/api/v1/instances/{}", "build"),
    ("POST", "/api/v1/jobs/{}/cancel", "build"),
    ("POST", "/api/v1/jobs/{}/retry", "build"),
    ("POST", "/api/v1/images", "build"),
    ("DELETE", "/api/v1/images/{}", "build"),
]
READ_METHODS = {"GET", "HEAD", "OPTIONS"}

_COMPILED = [(set(methods.split()), re.compile("^" + re.escape(path).replace(r"\{\}", _SEG) + "$"), cls)
             for methods, path, cls in WORKER_ROUTES]


def listed_class(method: str, path: str) -> Optional[str]:
    """The table's class for the request (method WS for a websocket), or None when it isn't listed."""
    path = "/" + path.lstrip("/")
    for methods, pattern, cls in _COMPILED:
        if method in methods and pattern.match(path):
            return cls
    return None


def route_class(method: str, path: str) -> str:
    """read, operate, build or delete. Unlisted reads are read; unlisted writes build, websockets operate."""
    listed = listed_class(method, path)
    if listed:
        return listed
    if method in READ_METHODS:
        return "read"
    return "operate" if method == "WS" else "build"
