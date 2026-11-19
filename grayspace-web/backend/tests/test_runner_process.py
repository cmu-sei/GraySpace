"""JobContext.run (services/slices/runner.py): long lines, and no orphans when a job is cancelled."""

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from models.instances import JobRecord
from services.slices import runner, store
from tests.isolation import isolate


def alive(pid: int) -> bool:
    """Running, as opposed to gone or a zombie (an orphan whose reaper, PID 1 in a container,
    may never collect it)."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state != "Z"


class Run(unittest.TestCase):
    def setUp(self):
        isolate(self)
        job = JobRecord(id="j1", kind="plan_slice", target="x", state="running", stages=[], created="t")
        store.save_job(job)
        self.ctx = runner.JobContext("j1")
        self.addCleanup(self.ctx.close)

    def test_long_lines_and_exit_codes(self):
        script = "import sys; print('x' * 200000); print('done'); sys.exit(3)"
        rc, out = asyncio.run(self.ctx.run([sys.executable, "-c", script], Path.cwd(), env=dict(os.environ), check=False))
        self.assertEqual(rc, 3)
        self.assertEqual(out.splitlines(), ["x" * 200000, "done"])
        with self.assertRaises(runner.StageError):
            asyncio.run(self.ctx.run([sys.executable, "-c", script], Path.cwd(), env=dict(os.environ)))

    def test_cancelling_stops_the_process_group(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = Path(d, "child.pid")
            # A parent that starts a child (like terraform and its providers) and ignores nothing
            script = (f"import subprocess, sys, time; c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)']); "
                      f"open('{pidfile}', 'w').write(str(c.pid)); print('started', flush=True); time.sleep(600)")

            async def scenario():
                task = asyncio.create_task(self.ctx.run([sys.executable, "-c", script], Path.cwd(), env=dict(os.environ)))
                for _ in range(100):
                    if pidfile.exists() and pidfile.read_text():
                        break
                    await asyncio.sleep(0.1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            started = time.monotonic()
            asyncio.run(scenario())
            self.assertLess(time.monotonic() - started, 30)
            child = int(pidfile.read_text())
            for _ in range(50):
                if not alive(child):
                    break
                time.sleep(0.1)
            else:
                self.fail("the child kept running after the job was cancelled")

    def test_a_cancel_request_interrupts_the_running_command(self):
        # POST /jobs/{id}/cancel only sets cancel_requested; a Terraform that is hanging (a refresh
        # waiting on a locked guest) must still stop, gracefully, without waiting for it to finish
        with tempfile.TemporaryDirectory() as d:
            pidfile = Path(d, "child.pid")
            script = (f"import signal, subprocess, sys, time\n"
                      f"c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
                      f"open('{pidfile}', 'w').write(str(c.pid))\n"
                      f"def stop(*_):\n    print('Interrupt received', flush=True); sys.exit(1)\n"
                      f"signal.signal(signal.SIGINT, stop)\n"
                      f"print('started', flush=True); time.sleep(600)\n")

            async def scenario():
                task = asyncio.create_task(self.ctx.run([sys.executable, "-c", script], Path.cwd(), env=dict(os.environ), check=False))
                for _ in range(100):
                    if pidfile.exists() and pidfile.read_text():
                        break
                    await asyncio.sleep(0.1)
                with store.update_job("j1") as j:
                    j.cancel_requested = True
                with self.assertRaises(runner.Cancelled):
                    await task

            started = time.monotonic()
            with patch.object(runner, "CANCEL_POLL_SECONDS", 0.2):
                asyncio.run(scenario())
            self.assertLess(time.monotonic() - started, 30)
            log = store.job_log("j1").read_text()
            self.assertIn("Cancel requested: interrupting", log)
            self.assertIn("Interrupt received", log)  # SIGINT first, and its output still reached the log
            child = int(pidfile.read_text())
            for _ in range(50):
                if not alive(child):
                    break
                time.sleep(0.1)
            else:
                self.fail("the child kept running after the cancel request")

    def test_a_command_that_finishes_is_not_cancelled(self):
        with patch.object(runner, "CANCEL_POLL_SECONDS", 0.05):
            rc, out = asyncio.run(self.ctx.run([sys.executable, "-c", "import time; time.sleep(0.3); print('ok')"], Path.cwd(),
                                               env=dict(os.environ)))
        self.assertEqual((rc, out), (0, "ok"))


if __name__ == "__main__":
    unittest.main()
