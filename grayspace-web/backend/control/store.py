"""JSON files under the deployments root (users.json, sessions.json, platform.json), re-read when
they change on disk and written atomically with mode 0600."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
from typing import Dict, Iterator, Tuple

from control.settings import settings


class Store:
    """A JSON file under the deployments root, re-read when it changes on disk."""

    def __init__(self, name: str):
        self.name = name
        self._cache: Dict[str, Tuple[int, Dict]] = {}

    def path(self) -> Path:
        return settings.root / self.name

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        settings.root.mkdir(parents=True, exist_ok=True)
        with open(settings.root / f".{self.name}.lock", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def read(self) -> Dict:
        path = self.path()
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return {}
        cached = self._cache.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return {}
        self._cache[str(path)] = (mtime, data)
        return data

    def write(self, data: Dict) -> None:
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
        self._cache.pop(str(path), None)
