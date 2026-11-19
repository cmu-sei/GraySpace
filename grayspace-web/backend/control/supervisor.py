"""One backend worker (main:app) per deployment, on a unix socket in the deployment's run/.

A managed deployment's work tree is refreshed from the source tree before each start, so a
restart brings new code to Terraform and Ansible. Workers that exit are restarted with backoff.
"""
import asyncio
import contextlib
import ctypes
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import httpx

from control import registry, worker_env
from control.models import WorkerStatus
from control.settings import settings

logger = logging.getLogger(__name__)

BACKEND = Path(__file__).resolve().parents[1]
MAX_BACKOFF = 60.0


def _die_with_parent():
    """A worker outlives a control plane that's killed outright otherwise, and keeps running jobs
    next to the worker the next control plane starts."""
    if sys.platform.startswith("linux"):
        with contextlib.suppress(OSError, AttributeError):
            ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG


def _stop_leftover(pidfile: Path):
    """Stop a worker an earlier control plane left running on this deployment."""
    try:
        pid = int(pidfile.read_text().strip())
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError):
        return
    if b"uvicorn" in cmdline and b"main:app" in cmdline:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
        for _ in range(60):
            if not Path(f"/proc/{pid}").exists():
                break
            time.sleep(0.25)


def client(deployment_id: str, timeout=None) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=str(registry.socket_path(deployment_id)))
    return httpx.AsyncClient(transport=transport, base_url="http://worker",
                             timeout=timeout or httpx.Timeout(connect=5.0, read=None, write=60.0, pool=5.0))


async def sync_worktree(record: Dict, log) -> None:
    """Copy the source tree's code over a managed deployment's work tree, and link what the copy
    leaves out: .environment (the generated file) and .venv (the Python task runs)."""
    work = registry.work_dir(record)
    script = Path(settings.source_path) / "scripts" / "sync-worktree.sh"
    proc = await asyncio.create_subprocess_exec("bash", str(script), settings.source_path, str(work), stdout=log, stderr=log)
    if await proc.wait() != 0:
        raise RuntimeError(f"syncing the work tree failed (see {registry.run_dir(record['id']) / 'worker.log'})")
    env_link = work / ".environment"
    if not env_link.is_symlink():
        env_link.unlink(missing_ok=True)
        env_link.symlink_to(registry.environment_file(record))
    venv = Path(settings.ops_venv) if settings.ops_venv else Path(settings.source_path) / ".venv"
    link = work / ".venv"
    if venv.exists() and not link.exists():
        link.symlink_to(venv.resolve())


class Worker:
    def __init__(self, deployment_id: str, notify: Callable[[str], None]):
        self.id = deployment_id
        self.notify = notify
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.state = "stopped"
        self.started: Optional[str] = None
        self.restarts = 0
        self.error: Optional[str] = None
        self.stopping = False
        self.task: Optional[asyncio.Task] = None
        self.ready = asyncio.Event()

    def status(self) -> WorkerStatus:
        return WorkerStatus(state=self.state, pid=self.proc.pid if self.proc and self.state != "stopped" else None,
                            started=self.started, restarts=self.restarts, error=self.error)

    def _set(self, state: str, error: Optional[str] = None):
        self.state, self.error = state, error
        if state == "running":
            self.ready.set()
        else:
            self.ready.clear()
        self.notify(self.id)

    def start(self):
        if self.task is None or self.task.done():
            self.stopping = False
            self.task = asyncio.create_task(self._run())

    async def _run(self):
        backoff = 1.0
        while not self.stopping:
            began = time.monotonic()
            try:
                await self._start_once()
                code = await self.proc.wait()
                if self.stopping:
                    break
                self._set("crashed", f"exited with {code}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("Worker %s failed to start", self.id)
                await self._kill()
                self._set("crashed", str(e))
            if time.monotonic() - began > 300:
                backoff = 1.0
            self.restarts += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)
        self._set("stopped")

    async def _start_once(self):
        record = registry.get(self.id)
        if record is None:
            raise RuntimeError("the deployment is gone")
        self._set("starting")
        run = registry.run_dir(self.id)
        sock = registry.socket_path(self.id)
        await asyncio.to_thread(_stop_leftover, run / "worker.pid")
        sock.unlink(missing_ok=True)
        with open(run / "worker.log", "ab") as log:
            if record["kind"] == "managed" and not settings.simulate:
                await sync_worktree(record, log)
            env = worker_env.build(record, await asyncio.to_thread(registry.effective_env, record))
            debug = []
            if settings.worker_debug and settings.worker_debug.partition(":")[0] == self.id:
                debug = ["-m", "debugpy", "--listen", f"127.0.0.1:{settings.worker_debug.partition(':')[2]}"]
            self.proc = await asyncio.create_subprocess_exec(
                settings.worker_python, *debug, "-m", "uvicorn", "main:app", "--uds", str(sock), "--log-level", "info",
                cwd=str(BACKEND), env=env, stdout=log, stderr=log, start_new_session=True, preexec_fn=_die_with_parent)
        (run / "worker.pid").write_text(f"{self.proc.pid}\n")
        self.started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        deadline = time.monotonic() + settings.worker_start_timeout
        async with client(self.id, timeout=2.0) as c:
            while True:
                if self.proc.returncode is not None:
                    raise RuntimeError(f"exited with {self.proc.returncode} while starting (see {run / 'worker.log'})")
                with contextlib.suppress(httpx.HTTPError, OSError):
                    if (await c.get("/health")).status_code == 200:
                        break
                if time.monotonic() > deadline:
                    raise RuntimeError(f"didn't answer /health within {settings.worker_start_timeout:.0f}s")
                await asyncio.sleep(0.25)
        self._set("running")

    async def _kill(self):
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 15)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()

    async def stop(self):
        self.stopping = True
        await self._kill()
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(BaseException):
                await self.task
        self.task = None
        if self.state != "stopped":
            self._set("stopped")

    async def restart(self):
        await self.stop()
        self.start()


class Supervisor:
    def __init__(self):
        self.workers: Dict[str, Worker] = {}
        self.listeners: List[Callable[[str], None]] = []

    def _notify(self, deployment_id: str):
        for listener in list(self.listeners):
            listener(deployment_id)

    def worker(self, deployment_id: str) -> Worker:
        if deployment_id not in self.workers:
            self.workers[deployment_id] = Worker(deployment_id, self._notify)
        return self.workers[deployment_id]

    def start(self, deployment_id: str):
        self.worker(deployment_id).start()

    def start_all(self):
        for record in registry.all_records():
            self.start(record["id"])

    async def stop(self, deployment_id: str):
        worker = self.workers.pop(deployment_id, None)
        if worker is not None:
            await worker.stop()

    async def stop_all(self):
        await asyncio.gather(*(w.stop() for w in self.workers.values()))
        self.workers.clear()

    def status(self, deployment_id: str) -> WorkerStatus:
        worker = self.workers.get(deployment_id)
        return worker.status() if worker else WorkerStatus(state="stopped")

    def running(self, deployment_id: str) -> bool:
        worker = self.workers.get(deployment_id)
        return worker is not None and worker.state == "running"

    async def wait_ready(self, deployment_id: str, timeout: float) -> bool:
        worker = self.workers.get(deployment_id)
        if worker is None:
            return False
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(worker.ready.wait(), timeout)
        return worker.state == "running"


supervisor = Supervisor()


def log_tail(deployment_id: str, lines: int = 40) -> List[str]:
    path = registry.directory(deployment_id) / "run" / "worker.log"
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 16384))
            return f.read().decode(errors="replace").splitlines()[-lines:]
    except OSError:
        return []
