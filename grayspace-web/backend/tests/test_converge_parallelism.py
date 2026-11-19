"""Terraform's -parallelism (services/slices/environment.parallelism, converge): the apply bounds
concurrent creates, the plan only reads; a deployment's environment file (its Proxmox connection)
overrides the server's defaults."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import settings
from models.instances import JobRecord
from services.slices import environment, runner, stages, store
from tests.isolation import isolate


class Parallelism(unittest.TestCase):
    def setUp(self):
        isolate(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env_file = Path(tmp.name) / "environment"
        self.env_file.write_text("")
        for p in (patch.object(settings, "environment_file", str(self.env_file)),
                  patch.dict(os.environ, {}, clear=False)):
            p.start()
            self.addCleanup(p.stop)
        for name in environment.PARALLELISM_VARS.values():
            os.environ.pop(name, None)
        environment._load_env.cache_clear()

    def write(self, text):
        self.env_file.write_text(text)
        environment._load_env.cache_clear()

    def test_the_servers_defaults(self):
        self.assertEqual((environment.parallelism("apply"), environment.parallelism("plan")), (10, 30))
        with patch.object(settings, "terraform_apply_parallelism", 4):
            self.assertEqual(environment.parallelism("apply"), 4)

    def test_the_environment_file_overrides_them(self):
        self.write('export GRAYSPACE_APPLY_PARALLELISM="5"\nexport GRAYSPACE_PLAN_PARALLELISM=40\n')
        self.assertEqual((environment.parallelism("apply"), environment.parallelism("plan")), (5, 40))

    def test_a_bad_value_falls_back(self):
        self.write('export GRAYSPACE_APPLY_PARALLELISM="lots"\nexport GRAYSPACE_PLAN_PARALLELISM=0\n')
        with self.assertLogs(environment.logger, "WARNING"):
            self.assertEqual(environment.parallelism("apply"), 10)
        self.assertEqual(environment.parallelism("plan"), 30)

    def test_converge_applies_and_plans_at_their_own(self):
        self.write('export GRAYSPACE_APPLY_PARALLELISM=6\n')
        store.save_job(JobRecord(id="j1", kind="deploy_base", target="base", state="running", stages=[], created="t"))
        ctx = runner.JobContext("j1")
        self.addCleanup(ctx.close)
        argvs = []

        async def run(argv, cwd, env=None, check=True, quiet=False):
            argvs.append(argv)
            return (2 if argv[1] == "plan" and len(argvs) == 1 else 0, "")

        with patch.object(runner.JobContext, "run", side_effect=run):
            asyncio.run(stages.converge(ctx, Path("."), ["-var-file=x.json"]))
        planfile = argvs[0][-1].split("=", 1)[1]
        self.assertEqual(argvs, [["terraform", "plan", "-detailed-exitcode", "-input=false", "-var-file=x.json", "-parallelism=30", f"-out={planfile}"],
                                 ["terraform", "apply", "-auto-approve", "-input=false", "-parallelism=6", planfile],
                                 ["terraform", "plan", "-detailed-exitcode", "-input=false", "-var-file=x.json", "-parallelism=30", f"-out={planfile}"]])

if __name__ == "__main__":
    unittest.main()
