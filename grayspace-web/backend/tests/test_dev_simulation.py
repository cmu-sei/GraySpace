"""The simulated deployment for local development (services/dev_simulation.py)."""

import json
import unittest
from unittest.mock import patch

from config import settings
from services import dev_simulation
from services.proxmox_client import ProxmoxClient
from services.slices import environment
from tests import REPO
from tests.isolation import isolate

TFVARS = json.loads((REPO / "tests" / "golden" / "tfvars-full.json").read_text())


class Simulated(unittest.TestCase):
    def setUp(self):
        self.data = isolate(self, dev_simulate_deployment=True, environment_id="test")
        for p in (patch("services.topology_service.topology_service.tfvars_data", TFVARS), patch.object(dev_simulation, "_stopped", None)):
            p.start()
            self.addCleanup(p.stop)
        self.client = ProxmoxClient()

    def test_every_guest_runs(self):
        containers = self.client.get_deployment_containers()
        names = {c["name"] for c in containers}
        self.assertEqual(len([n for n in names if n in TFVARS["routers"]]), 134)
        self.assertTrue({"dns-gtld", "ca-graytrust", "prometheus"} <= names)
        self.assertNotIn("grayspace-web", names)  # the base has no web LXC
        self.assertEqual({c["status"] for c in containers}, {"running"})
        self.assertFalse(self.client._connected)  # never touched Proxmox

    def test_start_and_stop_are_shared_with_the_metrics_simulator(self):
        self.assertTrue(self.client.stop_container("lum-chi"))
        self.assertEqual(self.client.get_container_by_hostname("lum-chi")["status"], "stopped")
        self.assertEqual(json.loads((self.data / "dev-simulation-stopped.json").read_text()), ["lum-chi"])
        self.assertTrue(self.client.start_container("lum-chi"))
        self.assertEqual(json.loads((self.data / "dev-simulation-stopped.json").read_text()), [])
        self.assertFalse(self.client.stop_container("nope"))

    def test_capacity_and_no_management_network(self):
        self.assertEqual(self.client.get_node_status()["cpuinfo"]["cpus"], 64)
        self.assertGreater(self.client.get_storage_status("local-lvm")["avail"], 0)
        self.assertIsNone(self.client.get_container_management_ip("lum-chi"))

    def test_jobs_are_simulated_and_need_a_base(self):
        self.assertEqual(environment.base_jobs_available(), (True, None))
        self.assertEqual(environment.jobs_available(), (True, None))
        with patch("services.topology_service.topology_service.tfvars_data", {}):
            available, why = environment.jobs_available()
        self.assertFalse(available)
        self.assertIn("deploy the core first", why)

    def test_off_by_default(self):
        self.assertFalse(type(settings)(_env_file=None).dev_simulate_deployment)


if __name__ == "__main__":
    unittest.main()
