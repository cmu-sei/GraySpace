"""converge (services/slices/stages.py): applying a saved plan, cancel between plan and apply, and
failing fast on a guest a failed create left locked, which otherwise hangs every refresh."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from models.instances import JobRecord
from services.slices import runner, stages, store
from tests.isolation import isolate


def tfstate(path: Path, resources):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 4, "resources": resources}))


ROUTERS = {
    "mode": "managed", "type": "proxmox_virtual_environment_container", "name": "routers",
    "instances": [
        {"index_key": "lum-chi", "attributes": {"vm_id": 101}},
        {"index_key": "lum-lon", "status": "tainted", "attributes": {"vm_id": 16769}},
    ],
}
GHOSTS = {"mode": "managed", "module": "module.extra", "type": "proxmox_virtual_environment_vm", "name": "ghosts",
          "instances": [{"index_key": 0, "attributes": {"vm_id": 200}}]}
OTHER = {"mode": "data", "type": "proxmox_virtual_environment_container", "name": "x", "instances": [{"attributes": {"vm_id": 7}}]}


class Converge(unittest.TestCase):
    def setUp(self):
        isolate(self)
        store.save_job(JobRecord(id="j1", kind="deploy_base", target="base", state="running", stages=[], created="t"))
        self.ctx = runner.JobContext("j1")
        self.addCleanup(self.ctx.close)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.module = Path(tmp.name)
        self.commands = []
        self.argvs = []
        self.locked = []
        self.plan_codes = [2, 0]  # changes, then converged

        async def run(argv, cwd, env=None, check=True, quiet=False):
            self.commands.append(argv[1])
            self.argvs.append(argv)
            if argv[1] == "plan":
                return (self.plan_codes.pop(0), "")
            if self.on_apply:
                self.on_apply()
            return (0, "")

        self.on_apply = None
        for p in (patch.object(runner.JobContext, "run", side_effect=run, autospec=False),
                  patch("services.proxmox_client.proxmox_client.get_locked_guests", side_effect=lambda: self.locked)):
            p.start()
            self.addCleanup(p.stop)

    def converge(self, workspace=None):
        asyncio.run(stages.converge(self.ctx, self.module, ["-parallelism=7"], workspace=workspace))

    def test_state_guests(self):
        tfstate(self.module / "terraform.tfstate", [ROUTERS, GHOSTS, OTHER])
        self.assertEqual(stages.state_guests(self.module), {
            101: 'proxmox_virtual_environment_container.routers["lum-chi"]',
            16769: 'proxmox_virtual_environment_container.routers["lum-lon"]',
            200: "module.extra.proxmox_virtual_environment_vm.ghosts[0]",
        })
        self.assertEqual(stages.state_guests(self.module, "mb"), {})  # no workspace state
        tfstate(self.module / "terraform.tfstate.d" / "mb" / "terraform.tfstate", [GHOSTS])
        self.assertEqual(list(stages.state_guests(self.module, "mb")), [200])

    def test_a_create_locked_guest_in_the_state_fails_before_terraform_runs(self):
        tfstate(self.module / "terraform.tfstate", [ROUTERS])
        self.locked = [
            {"vmid": 16769, "name": "CT16769", "node": "pve", "status": "stopped", "lock": "create", "resource_type": "lxc"},
            {"vmid": 101, "name": "lum-chi", "node": "pve", "status": "running", "lock": "backup", "resource_type": "lxc"},
            {"vmid": 555, "name": "CT555", "node": "pve", "status": "stopped", "lock": "create", "resource_type": "lxc"},  # not ours
        ]
        with self.assertRaises(runner.StageError) as e:
            self.converge()
        self.assertEqual(self.commands, [])
        message = str(e.exception)
        self.assertIn('routers["lum-lon"] (CT 16769 on pve)', message)
        self.assertIn("pct unlock 16769 && pct destroy 16769 --purge", message)
        self.assertNotIn("555", message)
        self.assertNotIn("101", message)

    def test_a_create_that_fails_during_apply_skips_the_plan(self):
        tfstate(self.module / "terraform.tfstate", [ROUTERS])

        def apply_leaves_a_locked_guest():
            self.locked = [{"vmid": 16769, "name": "CT16769", "node": "pve", "status": "stopped", "lock": "create", "resource_type": "lxc"}]

        self.on_apply = apply_leaves_a_locked_guest
        with self.assertRaises(runner.StageError):
            self.converge()
        self.assertEqual(self.commands, ["plan", "apply"])

    def test_cancel_during_apply_skips_the_plan(self):
        def cancel():
            with store.update_job("j1") as j:
                j.cancel_requested = True

        self.on_apply = cancel
        with self.assertRaises(runner.Cancelled):
            self.converge()
        self.assertEqual(self.commands, ["plan", "apply"])

    def test_without_proxmox_the_check_is_skipped(self):
        tfstate(self.module / "terraform.tfstate", [ROUTERS])
        self.locked = None
        self.converge()
        self.assertEqual(self.commands, ["plan", "apply", "plan"])
        self.assertIn("not checking for failed creates", store.job_log("j1").read_text())

    def planfile(self) -> Path:
        return Path(self.argvs[0][-1].split("=", 1)[1])

    def test_applies_the_plan_it_made(self):
        self.converge()
        planfile = self.planfile()
        self.assertEqual(self.argvs[0][:4], ["terraform", "plan", "-detailed-exitcode", "-input=false"])
        self.assertEqual(self.argvs[1], ["terraform", "apply", "-auto-approve", "-input=false",
                                         f"-parallelism={stages.environment.parallelism('apply')}", str(planfile)])
        self.assertIn("Converged after 1 applies", store.job_log("j1").read_text())
        self.assertFalse(planfile.exists())

    def test_already_converged_applies_nothing(self):
        self.plan_codes = [0]
        self.converge()
        self.assertEqual(self.commands, ["plan"])
        self.assertFalse(self.planfile().exists())

    def test_plan_errors_are_retried(self):
        self.plan_codes = [1, 2, 0]
        self.converge()
        self.assertEqual(self.commands, ["plan", "plan", "apply", "plan"])
        self.assertIn("Plan failed (exit 1)", store.job_log("j1").read_text())

    def test_gives_up_after_the_last_apply_leaves_changes(self):
        self.plan_codes = [2] * 10
        with patch.object(stages.settings, "slice_apply_attempts", 2), self.assertRaises(runner.StageError) as e:
            self.converge()
        self.assertEqual(self.commands, ["plan", "apply", "plan", "apply", "plan"])
        self.assertIn("did not converge after 2 attempts", str(e.exception))
        self.assertFalse(self.planfile().exists())

    def test_the_plan_file_is_removed_on_cancel(self):
        def cancel():
            with store.update_job("j1") as j:
                j.cancel_requested = True

        self.on_apply = cancel
        with self.assertRaises(runner.Cancelled):
            self.converge()
        self.assertFalse(self.planfile().exists())


if __name__ == "__main__":
    unittest.main()
