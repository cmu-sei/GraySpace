"""The audit log: every write through the control plane, and every sign-in, with who did it.

    <root>/audit.jsonl   one JSON object per line, oldest first (0600)

AuditMiddleware records each POST, PUT, PATCH and DELETE once its response starts: the user (or
service), method, path, the deployment it reached and the status. Sign-ins are recorded by the
auth routes themselves (record()), since a failed one has no user yet and a body is never logged.
"""
import fcntl
import json
import os
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional

from control.settings import settings

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# The control plane's own routes; every other /api route is the default deployment's (deprecated alias)
CONTROL_PREFIXES = ("/api/v1/deployments", "/api/v1/auth", "/api/v1/me", "/api/v1/users", "/api/v1/audit", "/api/v1/nodes", "/api/v1/directory",
                    "/api/v1/platform")
# Recorded by the routes, with what happened
SELF_RECORDED = {"/api/v1/auth/login"}


def _path():
    return settings.root / "audit.jsonl"


def record(user: Optional[str], method: str, path: str, status: int, deployment: Optional[str] = None, detail: str = "",
           ip: str = "") -> None:
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "user": user, "method": method, "path": path,
             "deployment": deployment, "status": status}
    if detail:
        entry["detail"] = detail
    if ip:
        entry["ip"] = ip
    settings.root.mkdir(parents=True, exist_ok=True)
    fd = os.open(_path(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(entry) + "\n")
        fcntl.flock(f, fcntl.LOCK_UN)


def entries(deployment: Optional[str] = None, user: Optional[str] = None, limit: int = 100) -> List[Dict]:
    """Newest first."""
    out: deque = deque(maxlen=limit)
    try:
        with open(_path()) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if (deployment is None or entry.get("deployment") == deployment) and (user is None or entry.get("user") == user):
                    out.append(entry)
    except OSError:
        return []
    return list(reversed(out))


def deployment_of(path: str) -> Optional[str]:
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "deployments":
        return parts[1]
    if len(parts) >= 4 and parts[:3] == ["api", "v1", "deployments"] and parts[3] != "events":
        return parts[3]
    return None


class AuditMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in WRITE_METHODS or scope["path"] in SELF_RECORDED:
            await self.app(scope, receive, send)
            return
        from control import auth, registry

        principal = auth.resolve(auth.header_token(scope.get("headers") or []))
        deployment = deployment_of(scope["path"])
        if deployment is None and scope["path"].startswith("/api/") and not scope["path"].startswith(CONTROL_PREFIXES):
            deployment = registry.default_id()  # the deprecated un-prefixed alias
        recorded = False

        async def watch(message):
            nonlocal recorded
            if message["type"] == "http.response.start" and not recorded:
                recorded = True
                record(principal.username if principal else None, scope["method"], scope["path"], message["status"], deployment,
                       ip=auth.scope_ip(scope))
            await send(message)

        await self.app(scope, receive, watch)
