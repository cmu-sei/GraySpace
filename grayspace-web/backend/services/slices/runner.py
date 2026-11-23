"""The job runner.

All building and teardown happens in jobs. In the backend one worker task runs queued jobs one at
a time; the CLI (slicectl.py) runs a job in its own process. Either way the job holds the node lock
(terraform/.grayspace.lock, the flock task apply, destroy and configure take) while it runs, so
jobs queue behind each other and behind base applies.

Jobs are files (store.py): their state, current stage and attempt, and a log that streams while
they run. Cancellation interrupts the command the job is running (Terraform stops gracefully on
SIGINT, saving its state and releasing its lock), or stops the job at the next stage boundary
when nothing is running. A job that was running when the
backend stopped is marked failed on startup; POST /jobs/{id}/retry resumes it from the stage that
failed, since every stage is idempotent.
"""
import asyncio
import contextlib
import fcntl
import logging
import os
import signal
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Sequence

from models.instances import JobRecord
from services import events
from services.slices import environment, store

logger = logging.getLogger(__name__)

# How often a running command checks its job for a cancel request (the job record is a file, so a
# cancel from the API reaches a job the CLI runs too)
CANCEL_POLL_SECONDS = 2.0


class StageError(Exception):
    """A stage failed; the message says why and what to do."""


class Cancelled(Exception):
    pass


class JobContext:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.log_path = store.job_log(job_id)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.log_path, "a", buffering=1)

    def close(self):
        self._fh.close()

    @property
    def job(self) -> JobRecord:
        return store.get_job(self.job_id)

    def log(self, message: str):
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        for line in str(message).rstrip("\n").split("\n"):
            self._fh.write(f"{stamp} {line}\n")

    def update(self, **fields) -> JobRecord:
        with store.update_job(self.job_id) as job:
            for key, value in fields.items():
                setattr(job, key, value)
        events.publish_job(job)
        return job

    def stage(self, name: str):
        self.check_cancel()
        self.log(f"=== {name} ===")
        self.update(stage=name, attempt=None)

    def attempt(self, text: Optional[str]):
        if text:
            self.log(f"--- {text} ---")
        self.update(attempt=text)

    def check_cancel(self):
        if self.job.cancel_requested:
            raise Cancelled()

    async def run(self, argv: Sequence[str], cwd: Path, env: Optional[Dict[str, str]] = None, check: bool = True,
                  quiet: bool = False) -> tuple:
        """Run a command, streaming its output into the job log. Returns (exit code, output).

        The command runs in its own process group. If the job is cancelled (POST /jobs/{id}/cancel,
        seen within CANCEL_POLL_SECONDS, or the backend stopping) or reading fails, the whole group
        is interrupted, then killed, before this returns, so no Terraform or Ansible keeps running
        after the job has let go of the node lock. A cancel request raises Cancelled.
        """
        name = os.path.basename(argv[0])
        self.log(f"$ {' '.join(argv)}  (in {cwd})")
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), env=env or environment.job_env(), start_new_session=True,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL,
        )
        lines: List[str] = []
        reader = asyncio.ensure_future(self._read(proc, lines, quiet))
        try:
            while True:
                done, _ = await asyncio.wait({reader}, timeout=CANCEL_POLL_SECONDS)
                if done:
                    break
                if self.job.cancel_requested:
                    self.log(f"Cancel requested: interrupting {name}")
                    # The reader keeps draining the output while the command stops, so its last
                    # words reach the log and a full pipe can't block it
                    await stop_process(proc)
                    with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                        await asyncio.wait_for(asyncio.shield(reader), 10.0)
                    self.log(f"Stopped {name}")
                    raise Cancelled()
            reader.result()
            rc = await proc.wait()
        except Cancelled:
            raise
        except BaseException:
            await stop_process(proc)
            self.log(f"Stopped {name}")
            raise
        finally:
            if not reader.done():
                reader.cancel()
                with contextlib.suppress(BaseException):
                    await reader
        output = "\n".join(lines)
        if check and rc != 0:
            tail = "\n".join(lines[-15:])
            raise StageError(f"{os.path.basename(argv[0])} {' '.join(argv[1:3])} exited {rc}: {tail[-1500:]}")
        return rc, output

    async def _read(self, proc, lines: List[str], quiet: bool):
        pending = b""
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            pending += chunk
            *complete, pending = pending.split(b"\n")
            for raw in complete:
                self._line(raw, lines, quiet)
        if pending:
            self._line(pending, lines, quiet)

    def _line(self, raw: bytes, lines: List[str], quiet: bool):
        line = raw.decode(errors="replace")
        lines.append(line)
        if not quiet:
            self._fh.write(line + "\n")


async def stop_process(proc, grace: float = 60.0):
    """Interrupt a process group (Terraform stops gracefully on SIGINT), then kill what remains."""
    if proc.returncode is not None:
        return
    for sig, wait in ((signal.SIGINT, grace), (signal.SIGKILL, 10.0)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), wait)
            return
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            continue


@contextlib.asynccontextmanager
async def node_lock(ctx: JobContext):
    """Hold the Proxmox node lock, waiting for task apply/destroy/configure or another job."""
    path = environment.lock_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        waited = False
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not waited:
                    ctx.log(f"Waiting for the node lock ({path}): a core apply, destroy, configure or another job holds it")
                    ctx.attempt("waiting for the node lock")
                    waited = True
                ctx.check_cancel()
                await asyncio.sleep(2)
        if waited:
            ctx.attempt(None)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# kind -> (stages, run(ctx, job), on_failure(job, message))
Handler = Callable[[JobContext, JobRecord], Awaitable[None]]
KINDS: Dict[str, Handler] = {}
FAILURE_HOOKS: Dict[str, Callable[[JobRecord, str], None]] = {}


def now() -> str:
    return store.now()


def new_job(kind: str, target: str, stages: List[str], slice_ref=None, resume_from=None, retry_of=None, result=None,
            parent: Optional[str] = None, by: Optional[str] = None) -> JobRecord:
    """by: who submitted it; the request's user (services/request_user.py) unless given."""
    from services import request_user

    job = JobRecord(id=store.new_job_id(), kind=kind, target=target, slice=slice_ref, state="queued", stages=stages,
                    created=store.now(precise=True), resume_from=resume_from, retry_of=retry_of, result=result, parent=parent,
                    by=by if by is not None else request_user.current())
    store.save_job(job)
    return job


async def execute(job_id: str) -> JobRecord:
    """Run a queued job to the end. Used by the backend worker and by the CLI."""
    return await _execute(job_id, lock=True)


async def execute_inline(job_id: str) -> JobRecord:
    """Run a queued job to the end inside another job that already holds the node lock (a rollout's children)."""
    return await _execute(job_id, lock=False)


async def _execute(job_id: str, lock: bool) -> JobRecord:
    job = store.get_job(job_id)
    if job is None or job.state != "queued":
        return job
    ctx = JobContext(job_id)
    try:
        if job.cancel_requested:
            ctx.log("Cancelled before it started")
            return ctx.update(state="cancelled", finished=now())
        ctx.update(state="running", started=now())
        ctx.log(f"Job {job.id}: {job.kind} {job.target}")
        try:
            async with node_lock(ctx) if lock else contextlib.nullcontext():
                await KINDS[job.kind](ctx, store.get_job(job_id))
        except Cancelled:
            ctx.log("Cancelled")
            _fail(job, "Cancelled")
            return ctx.update(state="cancelled", finished=now())
        except StageError as e:
            ctx.log(f"FAILED: {e}")
            _fail(job, str(e))
            return ctx.update(state="failed", error=str(e), finished=now())
        except Exception as e:  # A bug or an unexpected condition: keep the trace in the log.
            ctx.log("FAILED with an unexpected error:\n" + traceback.format_exc())
            _fail(job, f"Unexpected error: {e}")
            return ctx.update(state="failed", error=f"Unexpected error: {e}", finished=now())
        ctx.log("Succeeded")
        return ctx.update(state="succeeded", finished=now(), attempt=None)
    finally:
        ctx.close()


def _fail(job: JobRecord, message: str):
    hook = FAILURE_HOOKS.get(job.kind)
    if hook:
        try:
            hook(store.get_job(job.id), message)
        except Exception:
            logger.exception("Failure hook for job %s failed", job.id)


class Runner:
    """The backend's job worker: one job at a time, in submission order."""

    def __init__(self):
        self.queue: Optional[asyncio.Queue] = None
        self.task: Optional[asyncio.Task] = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def start(self):
        available, why = environment.base_jobs_available()
        if not available:
            logger.info(f"Jobs are unavailable: {why}")
            return
        self.queue = asyncio.Queue()
        for job in store.list_jobs():
            if job.state == "running":
                message = "Interrupted: GraySpace stopped while the job ran. Retry it to resume from this stage"
                with store.update_job(job.id) as j:
                    j.state, j.error, j.finished = "failed", message, now()
                _fail(job, message)
            elif job.state == "queued" and job.parent:
                # A rollout's child runs inside the rollout, never on its own; the rollout's retry makes it again
                with store.update_job(job.id) as j:
                    j.state, j.error, j.finished = "cancelled", "Its rollout stopped before it ran", now()
            elif job.state == "queued":
                self.queue.put_nowait(job.id)
        self.task = asyncio.create_task(self._work())
        logger.info("Job runner started")

    async def stop(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    def submit(self, job: JobRecord):
        if self.queue is None:
            raise RuntimeError("The job runner is not running")
        self.queue.put_nowait(job.id)

    async def _work(self):
        while True:
            job_id = await self.queue.get()
            try:
                await execute(job_id)
            except Exception:
                logger.exception("Job %s crashed the worker loop", job_id)


runner = Runner()
