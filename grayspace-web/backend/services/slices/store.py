"""Files for slices, instances and jobs under settings.data_dir.

    slices/<slug>/index.json             versions, newest last
    slices/<slug>/versions/<hex>.json    one spec per content hash
    instances/<id>.json                  instance record, allocations included
    instances/<id>/                      certificates and keys the instance issued
    instances/archive/<id>-<time>.json   destroyed instances whose ID was reused
    jobs/<id>.json, jobs/<id>.log
    images.json                          local images

The API and the CLI may run at once, so every write replaces the file atomically, read-modify-write
cycles hold an flock on .store.lock, and creating an instance claims its ID with O_EXCL.
"""
import contextlib
import fcntl
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from config import settings
from models.instances import InstanceRecord, JobRecord
from models.slices import SliceSpec, SliceSummary, SliceVersion


def now(precise: bool = False) -> str:
    """UTC timestamp. Jobs use precise ones, so jobs created within a second keep their order."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds" if precise else "seconds")


def data_dir() -> Path:
    d = settings.data_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json(path: Path, data, mode: int = 0o644) -> None:
    """Write JSON so readers see the old file or the new one, never a partial one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=False)
            f.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def write_text(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


_thread_lock = threading.RLock()
_depth = threading.local()


@contextlib.contextmanager
def locked() -> Iterator[None]:
    """Serialize read-modify-write cycles across processes (API and CLI) and threads. Re-entrant:
    a holder may call functions that lock again."""
    with _thread_lock:
        depth = getattr(_depth, "n", 0)
        if depth:
            _depth.n = depth + 1
            try:
                yield
            finally:
                _depth.n = depth
            return
        with open(data_dir() / ".store.lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            _depth.n = 1
            try:
                yield
            finally:
                _depth.n = 0
                fcntl.flock(f, fcntl.LOCK_UN)


def _safe(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise ValueError(f"invalid name '{name}'")
    return name


# --- Slices ---


def _slice_dir(slug: str) -> Path:
    return data_dir() / "slices" / _safe(slug)


def _version_file(slug: str, version: str) -> Path:
    digest = version.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"invalid version '{version}'")
    return _slice_dir(slug) / "versions" / f"{digest}.json"


def put_slice(spec: SliceSpec) -> tuple:
    """Store a spec. Returns (version, created); created is False when the content is stored already."""
    version = spec.version()
    with locked():
        index_path = _slice_dir(spec.slug) / "index.json"
        index = read_json(index_path) or {"slug": spec.slug, "versions": []}
        created = not any(v["version"] == version for v in index["versions"])
        if created:
            write_json(_version_file(spec.slug, version), spec.canonical())
            index["versions"].append({"version": version, "created": now()})
        else:
            # Storing an older version again makes it the latest.
            entry = next(v for v in index["versions"] if v["version"] == version)
            index["versions"] = [v for v in index["versions"] if v["version"] != version] + [entry]
        index["description"] = spec.description
        index["owner"] = spec.owner
        write_json(index_path, index)
    return version, created


def slice_index(slug: str) -> Optional[dict]:
    try:
        return read_json(_slice_dir(slug) / "index.json")
    except ValueError:
        return None


def get_slice(slug: str, version: Optional[str] = None) -> Optional[tuple]:
    """(version, spec) for a stored slice, the latest version unless one is named."""
    index = slice_index(slug)
    if not index or not index["versions"]:
        return None
    if version is None:
        version = index["versions"][-1]["version"]
    elif not any(v["version"] == version for v in index["versions"]):
        return None
    data = read_json(_version_file(slug, version))
    return (version, SliceSpec.model_validate(data)) if data is not None else None


def list_slices() -> List[SliceSummary]:
    root = data_dir() / "slices"
    live = live_instances()
    out = []
    for d in sorted(root.glob("*")) if root.exists() else []:
        index = read_json(d / "index.json")
        if not index:
            continue
        out.append(
            SliceSummary(
                slug=index["slug"],
                kind="slice",
                description=index.get("description") or "",
                owner=index.get("owner"),
                latest=index["versions"][-1]["version"] if index["versions"] else None,
                versions=[SliceVersion(**v) for v in index["versions"]],
                instances=[i.id for i in live if i.slice.slug == index["slug"]],
            )
        )
    return out


def delete_slice(slug: str) -> bool:
    import shutil

    with locked():
        d = _slice_dir(slug)
        if not d.exists():
            return False
        shutil.rmtree(d)
    return True


# --- Instances ---


def _instance_file(instance_id: str) -> Path:
    return data_dir() / "instances" / f"{_safe(instance_id)}.json"


def instance_dir(instance_id: str) -> Path:
    d = data_dir() / "instances" / _safe(instance_id)
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def claim_instance(record: InstanceRecord) -> bool:
    """Create an instance record if its ID is free. A destroyed instance with the same ID is moved
    to instances/archive/ first. False when a live instance holds the ID."""
    with locked():
        path = _instance_file(record.id)
        existing = read_json(path)
        if existing is not None:
            if InstanceRecord.model_validate(existing).live:
                return False
            archive = data_dir() / "instances" / "archive" / f"{record.id}-{now().replace(':', '')}.json"
            archive.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, archive)
            old_dir = data_dir() / "instances" / record.id
            if old_dir.exists():
                os.replace(old_dir, archive.with_suffix(""))
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as f:
            f.write(record.model_dump_json(indent=2) + "\n")
    return True


def get_instance(instance_id: str) -> Optional[InstanceRecord]:
    try:
        data = read_json(_instance_file(instance_id))
    except ValueError:
        return None
    return InstanceRecord.model_validate(data) if data is not None else None


def save_instance(record: InstanceRecord) -> None:
    record.updated = now()
    write_json(_instance_file(record.id), record.model_dump(mode="json"))


@contextlib.contextmanager
def update_instance(instance_id: str) -> Iterator[InstanceRecord]:
    """Read, change and write an instance record under the store lock."""
    with locked():
        record = get_instance(instance_id)
        if record is None:
            raise KeyError(instance_id)
        yield record
        save_instance(record)


def list_instances() -> List[InstanceRecord]:
    root = data_dir() / "instances"
    out = []
    for path in sorted(root.glob("*.json")) if root.exists() else []:
        data = read_json(path)
        if data is not None:
            out.append(InstanceRecord.model_validate(data))
    return out


def live_instances() -> List[InstanceRecord]:
    return [i for i in list_instances() if i.live]


# --- Jobs ---


def _job_file(job_id: str) -> Path:
    return data_dir() / "jobs" / f"{_safe(job_id)}.json"


def job_log(job_id: str) -> Path:
    return data_dir() / "jobs" / f"{_safe(job_id)}.log"


def new_job_id() -> str:
    return uuid.uuid4().hex[:16]


def save_job(job: JobRecord) -> None:
    write_json(_job_file(job.id), job.model_dump(mode="json"))


def get_job(job_id: str) -> Optional[JobRecord]:
    try:
        data = read_json(_job_file(job_id))
    except ValueError:
        return None
    return JobRecord.model_validate(data) if data is not None else None


@contextlib.contextmanager
def update_job(job_id: str) -> Iterator[JobRecord]:
    with locked():
        job = get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        yield job
        save_job(job)


def list_jobs() -> List[JobRecord]:
    root = data_dir() / "jobs"
    out = []
    for path in root.glob("*.json") if root.exists() else []:
        data = read_json(path)
        if data is not None:
            out.append(JobRecord.model_validate(data))
    return sorted(out, key=lambda j: j.created)
