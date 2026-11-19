"""scripts/converge.sh and scripts/with-node-lock.sh, with a fake terraform on PATH."""

import fcntl
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONVERGE = ROOT / "scripts" / "converge.sh"
LOCK = ROOT / "scripts" / "with-node-lock.sh"

# Records each call in calls.log, with the plan file's path as PLANFILE; `plan` writes the file named
# by -out= and exits with the next code in plan_codes (one per line).
FAKE_TERRAFORM = """#!/bin/bash
planfile=$(printf '%s\\n' "$@" | sed -n 's/^-out=//p')
[ -n "$planfile" ] && echo "$planfile" > "$FAKE_DIR/planfile" && echo plan > "$planfile"
recorded=$(cat "$FAKE_DIR/planfile" 2>/dev/null)
echo "${*//$recorded/PLANFILE}" >> "$FAKE_DIR/calls.log"
if [ "$1" = plan ]; then
    code=$(head -1 "$FAKE_DIR/plan_codes")
    sed -i 1d "$FAKE_DIR/plan_codes"
    exit "$code"
fi
exit "${APPLY_EXIT:-0}"
"""

PLAN = "plan -detailed-exitcode -input=false -var-file=x.json -parallelism=30 -out=PLANFILE"
APPLY = "apply -auto-approve -input=false -parallelism=10 PLANFILE"


class Converge(unittest.TestCase):
    def run_converge(self, plan_codes, max_attempts=6, apply_exit=0, **parallelism):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(d)])
        (d / "terraform").write_text(FAKE_TERRAFORM)
        (d / "terraform").chmod(0o755)
        (d / "plan_codes").write_text("".join(f"{c}\n" for c in plan_codes))
        env = {k: v for k, v in os.environ.items() if not k.endswith("_PARALLELISM")}
        env.update({"PATH": f"{d}:{os.environ['PATH']}", "FAKE_DIR": str(d), "APPLY_EXIT": str(apply_exit), **parallelism})
        result = subprocess.run([str(CONVERGE), str(max_attempts), "-var-file=x.json"], env=env, capture_output=True, text=True)
        calls = (d / "calls.log").read_text().splitlines()
        self.planfile = Path((d / "planfile").read_text().strip())
        return result, calls

    def test_applies_the_plan_until_it_reports_no_changes(self):
        result, calls = self.run_converge([2, 2, 0])
        self.assertEqual(result.returncode, 0)
        self.assertIn("Converged after 2 applies", result.stdout)
        # One refresh per attempt, by the plan; the apply only makes its changes. A saved plan
        # carries the variables, so the apply gets no -var-file
        self.assertEqual(calls, [PLAN, APPLY, PLAN, APPLY, PLAN])

    def test_already_converged_applies_nothing(self):
        result, calls = self.run_converge([0])
        self.assertEqual(result.returncode, 0)
        self.assertIn("Converged after 0 applies", result.stdout)
        self.assertEqual(calls, [PLAN])

    def test_the_plan_file_is_removed(self):
        self.run_converge([2, 0])
        self.assertFalse(self.planfile.exists())
        self.run_converge([2, 2], max_attempts=1)
        self.assertFalse(self.planfile.exists())

    def test_the_environment_sets_the_parallelism(self):
        _, calls = self.run_converge([2, 0], GRAYSPACE_APPLY_PARALLELISM="4", GRAYSPACE_PLAN_PARALLELISM="12")
        self.assertEqual(calls, [PLAN.replace("=30", "=12"), APPLY.replace("=10", "=4"), PLAN.replace("=30", "=12")])

    def test_plan_errors_are_retried(self):
        result, calls = self.run_converge([1, 2, 0])
        self.assertEqual(result.returncode, 0)
        self.assertIn("Plan before attempt 1 failed (exit 1)", result.stdout)
        self.assertEqual(calls, [PLAN, PLAN, APPLY, PLAN])

    def test_gives_up_after_max_attempts(self):
        result, calls = self.run_converge([2, 2, 2], max_attempts=2, apply_exit=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Apply attempt 1 failed", result.stdout)
        self.assertIn("Failed to converge after 2 attempts", result.stdout)
        self.assertEqual(calls, [PLAN, APPLY, PLAN, APPLY, PLAN])


class NodeLock(unittest.TestCase):
    def test_waits_for_the_holder(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(d)])
        lock = d / "node.lock"
        env = {**os.environ, "GRAYSPACE_LOCK_FILE": str(lock)}
        with open(lock, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            started = time.monotonic()
            threading.Timer(1.0, fcntl.flock, args=(held, fcntl.LOCK_UN)).start()
            result = subprocess.run([str(LOCK), "echo", "ran"], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertGreaterEqual(time.monotonic() - started, 0.9)
        self.assertIn("Waiting for the node lock", result.stdout)
        self.assertTrue(result.stdout.endswith("ran\n"))

    def test_passes_the_exit_code(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(d)])
        env = {**os.environ, "GRAYSPACE_LOCK_FILE": str(d / "node.lock")}
        self.assertEqual(subprocess.run([str(LOCK), "false"], env=env).returncode, 1)


if __name__ == "__main__":
    unittest.main()
