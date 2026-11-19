"""slicectl.py, the CLI behind task slice:*: offline validation against a shipped base."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import REPO

CLI = REPO / "grayspace-web" / "backend" / "slicectl.py"


def run(*args):
    env = {k: v for k, v in os.environ.items() if k not in ("DATA_PATH", "TFVARS_PATH", "CODEBASE_PATH")}
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, env=env, cwd=REPO, timeout=300)


class Validate(unittest.TestCase):
    def test_example_against_a_shipped_base(self):
        r = run("validate", "examples/slices/meridian-bank.yaml", "--base", "grayspace/2026.11")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("✅ meridian-bank", r.stdout)
        self.assertIn("mb-hq-edge on lum-chi 209.151.130.2", r.stdout)

    def test_findings_fail_the_command(self):
        r = run("validate", "examples/slices/windows-office.yaml", "--base", "grayspace/2026.11")
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNKNOWN_IMAGE at hosts[3].image", r.stdout)

    def test_invalid_specs_report_their_paths(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("apiVersion: grayspace/v1\nkind: Slice\nslug: Bad\nprefix: x\nhosts:\n  - name: h\n    image: i\n")
        self.addCleanup(os.unlink, f.name)
        r = run("validate", f.name, "--base", "grayspace/2026.11")
        self.assertEqual(r.returncode, 1)
        self.assertIn("INVALID_SPEC at slug", r.stdout)
        self.assertIn("INVALID_SPEC at hosts[0]", r.stdout)

    def test_tier1_scope(self):
        # Comcast is not Tier 1, so meridian-bank's branch can't attach on a tier1 deployment.
        r = run("validate", "examples/slices/meridian-bank.yaml", "--base", "grayspace/2026.11", "--scope", "tier1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNKNOWN_ROUTER", r.stdout)

    def test_relative_tfvars(self):
        with tempfile.TemporaryDirectory() as data:
            env_data = {"DATA_PATH": data}
            r = subprocess.run([sys.executable, str(CLI), "validate", "examples/slices/routed-campus.yaml", "--tfvars", "tests/golden/tfvars-full.json"],
                               capture_output=True, text=True, cwd=REPO, timeout=300,
                               env={**{k: v for k, v in os.environ.items() if k not in ("TFVARS_PATH", "CODEBASE_PATH")}, **env_data})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rc-campus-edge on lum-chi", r.stdout)

    def test_base_only_with_validate(self):
        r = run("up", "examples/slices/meridian-bank.yaml", "--base", "grayspace/2026.11")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
