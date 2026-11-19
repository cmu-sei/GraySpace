"""The deployments registry: one directory per deployment under the deployments root.

    <root>/default                   the default deployment's ID
    <root>/locks/<proxmox host>.lock one lock per Proxmox cluster, shared by its deployments' jobs
    <root>/<id>/deployment.json      the record: its placement (a Proxmox connection and node, control/platform.py)
    <root>/<id>/secrets.json         write-only settings (0600): the router password
    <root>/<id>/environment          generated environment file (0600)
    <root>/<id>/work/                the work tree (managed deployments)
    <root>/<id>/run/                 the worker's socket and log (0700)

An external deployment is an existing GraySpace tree (the devcontainer checkout, the container's
/data/work) adopted on first start: its record points at the tree, which keeps its own environment
file, and the control plane never re-syncs or deletes it.
"""
import contextlib
import fcntl
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from control import envfile, platform
from control.models import ID_PATTERN
from control.settings import settings

# use_custom_image (contract 1.12) and deploy_ghosts (1.25) are still accepted but export nothing:
# every Alpine guest uses the GraySpace image, and the base has no GHOSTS VM


class RegistryError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def root() -> Path:
    return settings.root


def directory(deployment_id: str) -> Path:
    return root() / deployment_id


def run_dir(deployment_id: str) -> Path:
    path = directory(deployment_id) / "run"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def socket_path(deployment_id: str) -> Path:
    return run_dir(deployment_id) / "api.sock"


def work_dir(record: Dict) -> Path:
    if record["kind"] == "external":
        return Path(record["work_dir"])
    return directory(record["id"]) / "work"


def environment_file(record: Dict) -> Optional[Path]:
    """The file job_env() and task read: generated for managed deployments, the tree's own for external ones."""
    if record["kind"] == "managed":
        return directory(record["id"]) / "environment"
    for name in (".environment", "environment"):
        path = work_dir(record) / name
        if path.is_file():
            return path
    return None


def lock_file(host: Optional[str]) -> Path:
    """One lock per Proxmox cluster: SDN applies are cluster-wide (scripts/with-node-lock.sh)."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", host or "proxmox")
    path = root() / "locks"
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{name}.lock"


@contextlib.contextmanager
def locked() -> Iterator[None]:
    root().mkdir(parents=True, exist_ok=True)
    with open(root() / ".registry.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _read(path: Path) -> Optional[Dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write(path: Path, data: Dict, mode: int = 0o644):
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def default_id() -> Optional[str]:
    try:
        value = (root() / "default").read_text().strip()
    except OSError:
        return None
    return value if get(value) else None


def set_default(deployment_id: Optional[str]):
    path = root() / "default"
    if deployment_id is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(deployment_id + "\n")


def get(deployment_id: str) -> Optional[Dict]:
    if not re.match(ID_PATTERN, deployment_id or ""):
        return None
    return _read(directory(deployment_id) / "deployment.json")


def all_records() -> List[Dict]:
    if not root().is_dir():
        return []
    records = [_read(p / "deployment.json") for p in sorted(root().iterdir()) if p.is_dir() and re.match(ID_PATTERN, p.name)]
    return [r for r in records if r]


def secrets(deployment_id: str) -> Dict:
    return _read(directory(deployment_id) / "secrets.json") or {}


def effective_env(record: Dict) -> Dict[str, str]:
    """What the deployment's environment file gives a shell: the site's settings plus its own."""
    path = environment_file(record)
    return envfile.load(path) if path is not None and path.is_file() else {}


def _exports(record: Dict, secret: Dict) -> Dict[str, str]:
    exports = {"TF_VAR_environment_id": record["id"]}
    if secret.get("router_password"):
        exports["TF_VAR_router_password"] = secret["router_password"]
    exports["TF_VAR_write_home_ssh_key"] = "false"
    return exports


def write_environment(record: Dict):
    """Generate a managed deployment's environment file: the site's, then its connections' settings
    (control/platform.py) in place of the site's Proxmox and Crucible ones. The lock is the cluster's.
    Without a placement (no connections at all) the site's file decides where it runs."""
    site = settings.site_environment_file
    exports = _exports(record, secrets(record["id"]))
    path = environment_file(record)
    connected = platform.exports(record)
    if connected is not None:
        exports.update(connected[0])
        exports["GRAYSPACE_LOCK_FILE"] = str(lock_file(connected[0]["TF_VAR_proxmox_host_addr"]))
        envfile.write(path, envfile.render(record["id"], site, exports, connected[1]))
        return
    envfile.write(path, envfile.render(record["id"], site, exports))
    exports["GRAYSPACE_LOCK_FILE"] = str(lock_file(envfile.load(path).get("TF_VAR_proxmox_host_addr")))
    envfile.write(path, envfile.render(record["id"], site, exports))


def refuse_legacy(body) -> None:
    proxmox = getattr(body, "proxmox", None)
    if (proxmox is not None and proxmox.model_dump(exclude_none=True)) or getattr(body, "credentials", None) is not None:
        raise RegistryError(422, "Proxmox settings and credentials come from a connection (Admin > Platform, GET /api/v1/platform): "
                                 "give placement {connection, node} instead")


def _placement(placement: Optional[Dict], options: Dict) -> Optional[Dict]:
    try:
        return platform.resolve_placement(placement, bool(options.get("player")), options.get("player_connection"))
    except platform.PlatformError as e:
        raise RegistryError(e.status, e.detail) from None


def create(body, kind: str = "managed", external_dir: Optional[Path] = None, owner: Optional[str] = None) -> Dict:
    """Write a new deployment's directory, record and environment file. The work tree and the
    worker are the supervisor's. owner: the user creating it (none for the service token)."""
    with locked():
        if get(body.id) is not None:
            raise RegistryError(409, f"A deployment '{body.id}' already exists")
        path = directory(body.id)
        if path.exists():
            raise RegistryError(409, f"{path} exists but isn't a deployment; remove it first")
        options = body.options.model_dump(exclude_none=True)
        if kind == "managed":
            refuse_legacy(body)
            placement = _placement(body.placement.model_dump(exclude_none=True) if body.placement else None, options)
        else:
            placement = None
        path.mkdir(parents=True)
        record = {
            "id": body.id, "name": body.name or body.id, "description": body.description or "", "kind": kind, "schema": 2,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "placement": placement, "options": options, "owner": owner, "members": [],
        }
        if external_dir is not None:
            record["work_dir"] = str(external_dir)
        secret = {}
        if body.router_password:
            secret["router_password"] = body.router_password
        _write(path / "secrets.json", secret, 0o600)
        _write(path / "deployment.json", record)
        run_dir(body.id)
        if kind == "managed":
            write_environment(record)
        if body.default or default_id() is None:
            set_default(body.id)
        return record


def update(deployment_id: str, body, settings_change: bool) -> Dict:
    with locked():
        record = get(deployment_id)
        if record is None:
            raise RegistryError(404, f"No deployment '{deployment_id}'")
        if body.name is not None:
            record["name"] = body.name
        if body.description is not None:
            record["description"] = body.description
        if settings_change:
            if record["kind"] == "external":
                raise RegistryError(409, f"'{deployment_id}' is an adopted tree; change its settings in {environment_file(record)}")
            refuse_legacy(body)
            if body.options is not None:
                record["options"] = {**record.get("options", {}), **body.options.model_dump(exclude_none=True)}
            if body.placement is not None or body.options is not None:
                wanted = body.placement.model_dump(exclude_none=True) if body.placement is not None else record.get("placement")
                record["placement"] = _placement(wanted, record["options"])
            secret = secrets(deployment_id)
            if body.router_password is not None:
                secret["router_password"] = body.router_password
            _write(directory(deployment_id) / "secrets.json", secret, 0o600)
        _write(directory(deployment_id) / "deployment.json", record)
        if settings_change:
            write_environment(record)
        if body.default:
            set_default(deployment_id)
        elif body.default is False and default_id() == deployment_id:
            set_default(None)
        return record


def save_migrated(record: Dict, secret: Dict) -> None:
    """A record platform.migrate_records gave a placement: saved, with its environment file again."""
    with locked():
        _write(directory(record["id"]) / "secrets.json", secret, 0o600)
        _write(directory(record["id"]) / "deployment.json", record)
        write_environment(record)


def rewrite_environments(ids: List[str]) -> None:
    """After a connection changed: the environment files of the managed deployments that use it."""
    for record in all_records():
        if record["id"] in ids and record["kind"] == "managed":
            write_environment(record)


def set_members(deployment_id: str, members: List[Dict]) -> Dict:
    with locked():
        record = get(deployment_id)
        if record is None:
            raise RegistryError(404, f"No deployment '{deployment_id}'")
        record["members"] = members
        _write(directory(deployment_id) / "deployment.json", record)
        return record


def remove_member(username: str) -> None:
    """A deleted user: off every deployment's members, and no longer anyone's owner."""
    with locked():
        for record in all_records():
            members = [m for m in record.get("members") or [] if m.get("user") != username]
            if members != (record.get("members") or []) or record.get("owner") == username:
                record["members"] = members
                if record.get("owner") == username:
                    record["owner"] = None
                _write(directory(record["id"]) / "deployment.json", record)


def adopt_owners(owner: str) -> List[str]:
    """Deployments from before owners (and those the service token created) get one. The IDs changed."""
    changed = []
    with locked():
        for record in all_records():
            if not record.get("owner"):
                record["owner"] = owner
                record.setdefault("members", [])
                _write(directory(record["id"]) / "deployment.json", record)
                changed.append(record["id"])
    return changed


def remove(deployment_id: str):
    """Forget the deployment; a managed one's directory (state, keys, work tree) goes too."""
    with locked():
        record = get(deployment_id)
        if record is None:
            return
        if default_id() == deployment_id:
            set_default(None)
        path = directory(deployment_id)
        if record["kind"] == "managed":
            shutil.rmtree(path)
        else:
            shutil.rmtree(path / "run", ignore_errors=True)
            for name in ("deployment.json", "secrets.json"):
                (path / name).unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                path.rmdir()
        others = [r["id"] for r in all_records()]
        if default_id() is None and len(others) == 1:
            set_default(others[0])


def adopt(tree: Path) -> Optional[Dict]:
    """Register an existing single-deployment tree as an external, default deployment. Its ID is its
    TF_VAR_environment_id. None when the tree has no environment file or no valid ID."""
    from control.models import CreateDeployment

    record = {"kind": "external", "work_dir": str(tree)}
    path = environment_file(record)
    if path is None:
        return None
    env_id = envfile.load(path).get("TF_VAR_environment_id", "")
    if not re.match(ID_PATTERN, env_id) or get(env_id) is not None:
        return None
    return create(CreateDeployment(id=env_id, name=env_id, description=f"Adopted from {tree}", default=True), kind="external",
                  external_dir=tree)


def warnings(record: Dict, env: Dict[str, str]) -> List[str]:
    out = [record["migration_warning"]] if record.get("migration_warning") else []
    if record["kind"] == "external" and not env.get("GRAYSPACE_LOCK_FILE"):
        out.append(f"{environment_file(record) or 'The tree'} doesn't set GRAYSPACE_LOCK_FILE, so task apply there doesn't wait "
                   "for other deployments' jobs on the same cluster; the API's jobs do")
    return out
