"""The control plane's registry, environment files and worker settings (control/, docs/deployments.md)."""

import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from control import envfile, platform, registry, worker_env
from control.models import CreateDeployment, DeploymentOptions, Placement, ProxmoxSettings, UpdateDeployment
from control.settings import settings

# A value made per run, so it reads as what it is: no real password
ROUTER_PASSWORD = uuid.uuid4().hex

SITE = """export TF_VAR_proxmox_host_addr="pve-n1.example"
export PROXMOX_PORT="8443"
export PROXMOX_VE_ENDPOINT="https://${TF_VAR_proxmox_host_addr}:${PROXMOX_PORT}/"
export PROXMOX_VE_API_TOKEN="site@pve!t=1"
export TF_VAR_proxmox_node="pve"
export TF_VAR_proxmox_blockstore="local-lvm"
export TF_VAR_environment_id="site"
"""


def control_isolate(test, site: str = SITE, cleanup=None) -> Path:
    """A deployments root and site environment file of the test's own (cleanup: addClassCleanup for a class)."""
    cleanup = cleanup or test.addCleanup
    tmp = Path(tempfile.mkdtemp(prefix="grayspace-control-"))
    cleanup(shutil.rmtree, tmp, True)
    (tmp / "site.env").write_text(site)
    for key, value in {"deployments_path": str(tmp / "deployments"), "site_environment": str(tmp / "site.env"),
                       "simulate": False, "adopt_path": None}.items():
        p = patch.object(settings, key, value)
        p.start()
        cleanup(p.stop)
    return tmp


class Create(unittest.TestCase):
    def setUp(self):
        self.tmp = control_isolate(self)
        platform.import_site(lambda *_: None)
        platform.put_proxmox("site", {"nodes": [{"name": "pve", "blockstore": "local-lvm"}, {"name": "pve2", "blockstore": "fast"}]}, False)

    def test_the_environment_file_sources_the_site_and_takes_the_connection(self):
        record = registry.create(CreateDeployment(id="abd", placement=Placement(connection="site", node="pve2"), router_password=ROUTER_PASSWORD,
                                                  options=DeploymentOptions(deploy_ghosts=True)))
        env = registry.effective_env(record)
        self.assertEqual(record["placement"], {"connection": "site", "node": "pve2"})
        self.assertEqual(env["TF_VAR_environment_id"], "abd")
        self.assertEqual(env["TF_VAR_proxmox_node"], "pve2")
        self.assertEqual(env["TF_VAR_proxmox_blockstore"], "fast")  # the node's
        self.assertEqual(env["PROXMOX_VE_API_TOKEN"], "site@pve!t=1")
        self.assertEqual(env["TF_VAR_router_password"], ROUTER_PASSWORD)
        self.assertNotIn("TF_VAR_deploy_ghosts", env)  # deprecated (1.25): accepted, exports nothing
        self.assertEqual(env["TF_VAR_write_home_ssh_key"], "false")
        self.assertEqual(env["GRAYSPACE_LOCK_FILE"], str(self.tmp / "deployments" / "locks" / "pve-n1.example.lock"))
        self.assertEqual(os.stat(registry.environment_file(record)).st_mode & 0o777, 0o600)

    def test_secrets_stay_out_of_the_record(self):
        registry.create(CreateDeployment(id="abd", router_password=ROUTER_PASSWORD))
        self.assertNotIn(ROUTER_PASSWORD, (self.tmp / "deployments" / "abd" / "deployment.json").read_text())
        self.assertEqual(os.stat(self.tmp / "deployments" / "abd" / "secrets.json").st_mode & 0o777, 0o600)

    def test_another_cluster_gets_its_endpoint_lock_and_credentials_only(self):
        platform.put_proxmox("far", {"host": "pve-b.example", "auth": "password", "username": "gs@pve", "password": ROUTER_PASSWORD,
                                     "nodes": [{"name": "b1"}]}, True)
        record = registry.create(CreateDeployment(id="far", placement=Placement(connection="far")))
        env = registry.effective_env(record)
        self.assertEqual(record["placement"], {"connection": "far", "node": "b1"})
        self.assertEqual((env["PROXMOX_VE_ENDPOINT"], env["PROXMOX_VE_USERNAME"], env["PROXMOX_VE_PASSWORD"]),
                         ("https://pve-b.example:8006/", "gs@pve", ROUTER_PASSWORD))
        self.assertTrue(env["GRAYSPACE_LOCK_FILE"].endswith("locks/pve-b.example.lock"))
        # The site's token and its node's storage don't follow it to the other cluster
        self.assertNotIn("PROXMOX_VE_API_TOKEN", env)
        self.assertNotIn("TF_VAR_proxmox_blockstore", env)
        self.assertEqual(env["TF_VAR_configure_crucible"], "false")

    def test_without_a_placement_the_default_connection_and_node(self):
        record = registry.create(CreateDeployment(id="abc"))
        self.assertEqual(record["placement"], {"connection": "site", "node": "pve"})

    def test_settings_of_their_own_are_refused(self):
        for body in (CreateDeployment(id="abc", proxmox=ProxmoxSettings(node="x")), CreateDeployment(id="abc", placement=Placement(connection="nope")),
                     CreateDeployment(id="abc", placement=Placement(connection="site", node="nope")),
                     CreateDeployment(id="abc", options=DeploymentOptions(player=True))):
            with self.subTest(body=body), self.assertRaises(registry.RegistryError) as e:
                registry.create(body)
            self.assertEqual(e.exception.status, 422)
        self.assertFalse((self.tmp / "deployments" / "abc").exists())

    def test_ids_are_unique_and_the_first_is_the_default(self):
        registry.create(CreateDeployment(id="abc"))
        with self.assertRaises(registry.RegistryError) as e:
            registry.create(CreateDeployment(id="abc"))
        self.assertEqual(e.exception.status, 409)
        registry.create(CreateDeployment(id="abd"))
        self.assertEqual(registry.default_id(), "abc")
        self.assertEqual([r["id"] for r in registry.all_records()], ["abc", "abd"])

    def test_bad_ids_are_refused(self):
        for bad in ("", "toolong", "1ab", "AB", "a-b"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                CreateDeployment(id=bad)

    def test_update_and_remove(self):
        registry.create(CreateDeployment(id="abc"))
        registry.create(CreateDeployment(id="abd"))
        record = registry.update("abd", UpdateDeployment(name="Exercise", default=True, placement=Placement(connection="site", node="pve2")), True)
        self.assertEqual((record["name"], registry.default_id()), ("Exercise", "abd"))
        self.assertEqual(registry.effective_env(record)["TF_VAR_proxmox_node"], "pve2")
        registry.remove("abd")
        self.assertFalse((self.tmp / "deployments" / "abd").exists())
        self.assertEqual(registry.default_id(), "abc")  # the only one left


class Adopt(unittest.TestCase):
    def setUp(self):
        self.tmp = control_isolate(self)
        self.tree = self.tmp / "checkout"
        (self.tree / "terraform").mkdir(parents=True)

    def test_an_existing_tree_becomes_the_external_default(self):
        (self.tree / ".environment").write_text(SITE.replace('"site"', '"abc"'))
        record = registry.adopt(self.tree)
        self.assertEqual((record["id"], record["kind"], registry.default_id()), ("abc", "external", "abc"))
        self.assertEqual(registry.work_dir(record), self.tree)
        self.assertEqual(registry.environment_file(record), self.tree / ".environment")
        self.assertIn("GRAYSPACE_LOCK_FILE", " ".join(registry.warnings(record, registry.effective_env(record))))
        with self.assertRaises(registry.RegistryError):
            registry.update("abc", UpdateDeployment(proxmox=ProxmoxSettings(node="x")), True)
        registry.remove("abc")
        self.assertTrue((self.tree / ".environment").exists())  # never deleted

    def test_a_tree_without_an_environment_file_isnt_adopted(self):
        self.assertIsNone(registry.adopt(self.tree))


class WorkerEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = control_isolate(self)

    def test_the_backend_settings_come_from_the_deployment(self):
        record = registry.create(CreateDeployment(id="abd"))
        work = self.tmp / "deployments" / "abd" / "work"
        (work / "ansible" / "inventory").mkdir(parents=True)
        (work / "ansible" / "inventory" / "ungrouped_inventory.ini").write_text("prometheus ansible_host=10.0.0.9\n")
        with patch.dict(os.environ, {"TF_VAR_environment_id": "leak", "PROXMOX_VE_PASSWORD": "leak", "ENVIRONMENT_ID": "leak",
                                     "GRAYSPACE_APPLY_PARALLELISM": "99", "TERRAFORM_APPLY_PARALLELISM": "8"}):
            env = worker_env.build(record, registry.effective_env(record))
        self.assertEqual(env["ENVIRONMENT_ID"], "abd")
        self.assertNotIn("TF_VAR_environment_id", env)
        self.assertNotIn("PROXMOX_VE_PASSWORD", env)
        # A deployment's parallelism is its connection's; the server's default reaches every worker
        self.assertNotIn("GRAYSPACE_APPLY_PARALLELISM", env)
        self.assertEqual(env["TERRAFORM_APPLY_PARALLELISM"], "8")
        self.assertEqual((env["PROXMOX_API_HOST"], env["PROXMOX_NODE"], env["PROXMOX_API_TOKEN"]), ("pve-n1.example", "pve", "site@pve!t=1"))
        self.assertEqual(env["CODEBASE_PATH"], str(work))
        self.assertEqual(env["TFVARS_PATH"], str(work / "terraform" / "terraform.tfvars.json"))
        self.assertEqual(env["DATA_PATH"], str(work / "terraform" / "grayspace-data"))
        self.assertEqual(env["SSH_KEY_PATH"], str(work / ".ssh" / "terraform_key"))
        self.assertEqual(env["ENVIRONMENT_FILE"], str(self.tmp / "deployments" / "abd" / "environment"))
        self.assertEqual(env["PROMETHEUS_URL"], "http://10.0.0.9:9090")
        self.assertTrue(env["GRAYSPACE_LOCK_FILE"].endswith("pve-n1.example.lock"))

    def test_an_adopted_tree_without_the_lock_setting_still_takes_the_clusters(self):
        tree = self.tmp / "checkout"
        tree.mkdir()
        (tree / ".environment").write_text(SITE.replace('"site"', '"abc"'))
        record = registry.adopt(tree)
        env = worker_env.build(record, registry.effective_env(record))
        self.assertTrue(env["GRAYSPACE_LOCK_FILE"].endswith("locks/pve-n1.example.lock"))
        self.assertEqual(env["CODEBASE_PATH"], str(tree))


    def test_a_simulated_worker_gets_the_simulated_jobs_settings(self):
        record = registry.create(CreateDeployment(id="abd"))
        with patch.object(settings, "simulate", True), patch.object(settings, "bootstrap", "abd"), \
                patch.dict(os.environ, {"DEV_SIMULATION_STAGE_SECONDS": "0.1", "DEV_SIMULATION_FAIL": "create_instance:configure"}):
            env = worker_env.build(record, registry.effective_env(record))
        self.assertEqual((env["DEV_SIMULATION_STAGE_SECONDS"], env["DEV_SIMULATION_FAIL"]), ("0.1", "create_instance:configure"))
        self.assertTrue(Path(env["TFVARS_PATH"]).exists())  # a bootstrap deployment starts deployed
        later = registry.create(CreateDeployment(id="abe"))
        with patch.object(settings, "simulate", True), patch.object(settings, "bootstrap", "abd"):
            env = worker_env.build(later, registry.effective_env(later))
        self.assertFalse(Path(env["TFVARS_PATH"]).exists())  # one made later starts empty
        self.assertNotIn("DEV_SIMULATION_FAIL", env)


class EnvFile(unittest.TestCase):
    def test_values_are_quoted(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / "environment"
        envfile.write(path, envfile.render("abc", None, {"TF_VAR_proxy": "it's $HOME `x`"}))
        self.assertEqual(envfile.load(path)["TF_VAR_proxy"], "it's $HOME `x`")


if __name__ == "__main__":
    unittest.main()
