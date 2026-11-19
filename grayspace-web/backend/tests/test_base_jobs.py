"""Base jobs and routes: deploy, configure and destroy with fake
terraform and ansible-playbook (fake_tools.py), job availability, preflight, and the routes'
refusals."""

import asyncio
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import settings
from models.base import BgpHealth, DeployedBase, Preflight, PreflightCheck, RouterHealth
from models.instances import InstanceRecord, SliceRef
from routers import base, jobs
from services import base_jobs, base_preflight, crucible_api
from services.slices import environment, runner, store
from services.topology_service import topology_service
from tests import AUTH, REPO
from tests.fake_tools import FakeCheckout
from tests.isolation import isolate

HEALTHY = DeployedBase(version=None, status="healthy", routers=RouterHealth(expected=2, running=2),
                       bgp=BgpHealth(total=4, established=4))
DOWN = DeployedBase(version=None, status="degraded", routers=RouterHealth(expected=2, running=0, not_running=["a", "b"]),
                    bgp=BgpHealth(total=None, established=None))


def ready(version="grayspace/2026.11", scope="tier1"):
    return Preflight(ready=True, version=version, scope=scope, checks=[PreflightCheck(name="proxmox", status="ok", message="ok")])


def live_instance(iid="mb"):
    now = datetime.now(timezone.utc).isoformat()
    record = InstanceRecord(id=iid, slice=SliceRef(slug="meridian-bank", version="sha256:x"), state="ready", created=now, updated=now)
    store.claim_instance(record)
    return record


class Checkout(unittest.TestCase):
    """A fake checkout with the real scripts/ and bases/, so parse and the guards really run."""

    def setUp(self):
        self.data = isolate(self)
        self.checkout = FakeCheckout()
        self.addCleanup(self.checkout.cleanup)
        for name in ("scripts", "bases"):
            os.symlink(REPO / name, self.checkout.root / name)
        self.tfvars = self.checkout.root / "terraform" / "terraform.tfvars.json"
        self.health = AsyncMock(return_value=HEALTHY)
        for p in (
            patch.object(settings, "codebase_path", str(self.checkout.root)),
            patch.object(settings, "environment_file", None),
            patch.object(settings, "tfvars_path", str(self.tfvars)),
            patch.object(settings, "base_check_timeout", 0),
            patch.dict(os.environ, {"PATH": self.checkout.path, "FAKE_ROOT": str(self.checkout.root),
                                    "GRAYSPACE_LOCK_FILE": str(self.checkout.root / "terraform" / ".grayspace.lock")}),
            patch.object(topology_service, "tfvars_data", {}),
            patch("tasks.state_monitor.state_monitor._load_ip_mapping", AsyncMock()),
            patch("tasks.state_monitor.state_monitor._load_link_topology", MagicMock()),
            patch("services.base_service.get_deployed_base", self.health),
            patch.object(base_preflight, "run", AsyncMock(return_value=ready())),
            patch("services.mutation_tracker.mutation_tracker.save"),
        ):
            p.start()
            self.addCleanup(p.stop)
        environment._load_env.cache_clear()

    def run_job(self, job):
        return asyncio.run(runner.execute(job.id))

    def log(self, job):
        return store.job_log(job.id).read_text()

    def calls(self, tool):
        return [c["args"] for c in self.checkout.calls(tool)]


class Availability(Checkout):
    def test_base_jobs_need_no_deployed_base_but_slice_jobs_do(self):
        (self.checkout.root / ".ssh" / "terraform_key.pub").unlink()
        self.assertEqual(environment.base_jobs_available(), (True, None))
        available, why = environment.jobs_available()
        self.assertFalse(available)
        self.assertIn("deploy the base first", why)

    def test_slice_jobs_become_available_without_a_restart(self):
        key = self.checkout.root / ".ssh" / "terraform_key.pub"
        key.unlink()
        self.assertFalse(environment.jobs_available()[0])
        key.write_text("ssh-rsa AAAA test\n")
        self.assertTrue(environment.jobs_available()[0])

    def test_the_runner_starts_with_nothing_deployed(self):
        (self.checkout.root / ".ssh" / "terraform_key.pub").unlink()
        r = runner.Runner()

        async def start_stop():
            await r.start()
            running = r.running
            await r.stop()
            return running

        self.assertTrue(asyncio.run(start_stop()))


class Deploy(Checkout):
    def deploy(self, **request):
        job = base_jobs.submit("deploy_base", {"version": "grayspace/2026.11", "scope": "tier1", "configure": True, **request})
        return job, self.run_job(job)

    def test_init_upgrades_only_an_outdated_lock_file(self):
        # A provider constraint changed in the code: the work tree's lock file still pins the old version
        self.checkout.fail("lock", 1)
        job, done = self.deploy()
        self.assertEqual(done.state, "succeeded", self.log(job))
        inits = [a for a in self.calls("terraform") if a[0] == "init"]
        self.assertEqual(inits[:2], [["init", "-input=false"], ["init", "-input=false", "-upgrade"]])
        self.assertIn("terraform init -upgrade", self.log(job))
        # The next deploy finds the lock up to date: no -upgrade
        job, done = self.deploy()
        self.assertEqual(done.state, "succeeded", self.log(job))
        self.assertNotIn("-upgrade", [a for a in self.calls("terraform") if a[0] == "init"][-1])

    def test_deploy_runs_every_stage_in_order(self):
        job, done = self.deploy(mgmt_gateway="10.0.0.1")
        self.assertEqual(done.state, "succeeded", self.log(job))
        tfvars = json.loads(self.tfvars.read_text())
        self.assertEqual(tfvars["base"]["scope"], "tier1")
        self.assertEqual(topology_service.tfvars_data["base"]["name"], "grayspace/2026.11")
        terraform = [a[0] for a in self.calls("terraform")]
        self.assertEqual(terraform[0:4], ["init", "plan", "apply", "plan"])  # the guard reads no Terraform state
        # Creates contend for the storage lock; the plan only reads
        self.assertIn("-parallelism=30", self.calls("terraform")[1])
        self.assertIn("-parallelism=10", self.calls("terraform")[2])
        self.assertEqual(self.calls("ansible-playbook"), [["main.yml", "-e", "mgmt_gateway=10.0.0.1"]])
        self.assertEqual(done.result["health"]["status"], "healthy")
        for stage in base_jobs.DEPLOY_STAGES:
            self.assertIn(f"=== {stage} ===", self.log(job))

    def test_deploying_again_changes_nothing(self):
        self.deploy()
        before = len(self.calls("terraform"))
        job, done = self.deploy()
        self.assertEqual(done.state, "succeeded", self.log(job))
        self.assertIn("Converged after 0 applies", self.log(job))
        self.assertEqual([a[0] for a in self.calls("terraform")[before:]], ["init", "plan"])

    def test_without_configure_stops_after_terraform(self):
        job, done = self.deploy(configure=False)
        self.assertEqual(done.state, "succeeded", self.log(job))
        self.assertEqual(done.stages, ["preflight", "parse", "guard", "init", "apply", "refresh"])
        self.assertEqual(self.calls("ansible-playbook"), [])

    def test_a_failed_configure_resumes_there(self):
        self.checkout.fail("ansible")
        job, done = self.deploy()
        self.assertEqual((done.state, done.stage), ("failed", "configure"))
        applies = len(self.calls("terraform"))
        retry = runner.new_job(job.kind, job.target, job.stages, resume_from=done.stage, retry_of=job.id, result=done.result)
        again = self.run_job(retry)
        self.assertEqual(again.state, "succeeded", self.log(retry))
        self.assertEqual(len(self.calls("terraform")), applies)  # no second apply
        self.assertNotIn("=== apply ===", self.log(retry))

    def test_a_guard_refusal_stops_before_terraform(self):
        state_before = len(self.calls("terraform"))
        with patch.object(base_jobs, "stage_guard", AsyncMock(side_effect=runner.StageError("guard says no"))):
            _, done = self.deploy()
        self.assertEqual((done.state, done.stage), ("failed", "guard"))
        self.assertEqual(len(self.calls("terraform")), state_before)

    def test_no_router_running_fails_the_check(self):
        self.health.return_value = DOWN
        _, done = self.deploy()
        self.assertEqual((done.state, done.stage), ("failed", "check"))
        self.assertIn("No router is running", done.error)

    def test_a_degraded_base_succeeds_with_a_warning(self):
        self.health.return_value = DeployedBase(version=None, status="degraded", routers=RouterHealth(expected=2, running=2),
                                                bgp=BgpHealth(total=4, established=3))
        job, done = self.deploy()
        self.assertEqual(done.state, "succeeded")
        self.assertIn("WARNING: the base is degraded", self.log(job))

    def test_preflight_errors_fail_the_job(self):
        failing = Preflight(ready=False, checks=[PreflightCheck(name="bridges", status="error", message="Bridge 'vmbr9' isn't on the node")])
        with patch.object(base_preflight, "run", AsyncMock(return_value=failing)):
            _, done = self.deploy()
        self.assertEqual((done.state, done.stage), ("failed", "preflight"))
        self.assertIn("vmbr9", done.error)
        self.assertEqual(self.calls("terraform"), [])

    def test_refresh_reads_prometheus_from_the_inventory(self):
        inventory = self.checkout.root / "ansible" / "inventory"
        inventory.mkdir(parents=True)
        (inventory / "ungrouped_inventory.ini").write_text("prometheus ansible_host=10.0.5.200 ansible_user=root\n")
        from services.prometheus_client import prometheus_client

        with patch.object(settings, "prometheus_from_inventory", True), patch.object(settings, "prometheus_url", settings.prometheus_url), \
                patch.object(prometheus_client, "base_url", prometheus_client.base_url):
            self.deploy()
            self.assertEqual(prometheus_client.base_url, "http://10.0.5.200:9090")


class Destroy(Checkout):
    def test_destroy_removes_the_base_and_its_tfvars(self):
        deploy = base_jobs.submit("deploy_base", {"version": "grayspace/2026.11", "scope": "tier1", "configure": False})
        self.run_job(deploy)
        job = base_jobs.submit("destroy_base", {})
        done = self.run_job(job)
        self.assertEqual(done.state, "succeeded", self.log(job))
        self.assertIn("destroy", [a[0] for a in self.calls("terraform")])
        self.assertFalse(self.tfvars.exists())
        self.assertEqual(topology_service.tfvars_data, {})

    def test_destroy_inits_first(self):
        # A provider constraint changed since the deploy leaves the lock file outdated, which
        # fails every destroy until terraform init (-upgrade) runs
        done = self.run_job(base_jobs.submit("destroy_base", {}))
        self.assertEqual(done.state, "succeeded")
        commands = [a[0] for a in self.calls("terraform")]
        self.assertLess(commands.index("init"), commands.index("destroy"))

    def test_destroy_skips_the_refresh_with_crucible_resources_in_state(self):
        """The state says, not TF_VAR_configure_crucible: a managed deployment has it false with the
        base module's old view still in state."""
        state = self.checkout.root / "terraform" / "terraform.tfstate"
        state.write_text(json.dumps({"version": 4, "resources": [{"module": "module.crucible[0]", "type": "crucible_player_view", "name": "grayspace"}]}))
        done = self.run_job(base_jobs.submit("destroy_base", {}))
        self.assertEqual(done.state, "succeeded")
        destroys = [a for a in self.calls("terraform") if a[0] == "destroy"]
        self.assertIn("-refresh=false", destroys[0])

    def test_destroy_refreshes_only_when_something_remains(self):
        # The first attempt skips the refresh, which waits on each VM's guest agent for its IPs;
        # a later attempt refreshes, which drops what was deleted outside Terraform
        self.run_job(base_jobs.submit("deploy_base", {"version": "grayspace/2026.11", "scope": "tier1", "configure": False}))
        self.checkout.fail("destroy", 1)
        job = base_jobs.submit("destroy_base", {})
        done = self.run_job(job)
        self.assertEqual(done.state, "succeeded", self.log(job))
        destroys = [a for a in self.calls("terraform") if a[0] == "destroy"]
        self.assertEqual(len(destroys), 2)
        self.assertIn("-refresh=false", destroys[0])
        self.assertNotIn("-refresh=false", destroys[1])
        self.assertIn("Destroyed on attempt 2", self.log(job))

    def test_destroy_is_refused_while_an_instance_is_live(self):
        live_instance()
        job = base_jobs.submit("destroy_base", {})
        done = self.run_job(job)
        self.assertEqual((done.state, done.stage), ("failed", "guard"))
        self.assertNotIn("destroy", [a[0] for a in self.calls("terraform")])


class Routes(unittest.TestCase):
    def setUp(self):
        self.data = isolate(self)
        app = FastAPI()
        for r in (base, jobs):
            app.include_router(r.router)
        self.client = TestClient(app, headers=AUTH)
        self.submitted = []
        fake_runner = MagicMock()
        fake_runner.running = True
        fake_runner.submit.side_effect = self.submitted.append
        self.preflight = AsyncMock(return_value=ready())
        for p in (
            patch.object(base, "runner", fake_runner),
            patch.object(jobs, "runner", fake_runner),
            patch("services.jobs_api.runner", fake_runner),
            patch.object(base_preflight, "run", self.preflight),
            patch.object(topology_service, "tfvars_data", {}),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_deploy_returns_a_job(self):
        r = self.client.post("/api/v1/base/deploy", json={"version": "grayspace/2026.11", "scope": "tier1"})
        self.assertEqual(r.status_code, 202, r.text)
        job = r.json()["job"]
        self.assertEqual((job["kind"], job["target"], job["state"]), ("deploy_base", "base", "queued"))
        self.assertEqual(job["stages"], base_jobs.DEPLOY_STAGES)
        self.assertEqual(job["result"]["scope"], "tier1")
        self.assertEqual([j.id for j in self.submitted], [job["id"]])

    def test_one_base_job_at_a_time(self):
        self.client.post("/api/v1/base/deploy", json={"version": "grayspace/2026.11"})
        r = self.client.post("/api/v1/base/deploy", json={"version": "grayspace/2026.11"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.client.delete("/api/v1/base").status_code, 409)

    def test_preflight_errors_are_422_and_instance_conflicts_409(self):
        self.preflight.return_value = Preflight(ready=False, checks=[PreflightCheck(name="ssh", status="error", message="closed")])
        r = self.client.post("/api/v1/base/deploy", json={"version": "grayspace/2026.11"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["detail"]["checks"][0]["name"], "ssh")
        self.preflight.return_value = Preflight(ready=False, checks=[PreflightCheck(name="instances", status="error", message="live")])
        self.assertEqual(self.client.post("/api/v1/base/deploy", json={"version": "grayspace/2026.11"}).status_code, 409)
        self.assertEqual(self.submitted, [])

    def test_a_bad_gateway_is_422(self):
        r = self.client.post("/api/v1/base/deploy", json={"version": "grayspace/2026.11", "mgmt_gateway": "gateway"})
        self.assertEqual(r.status_code, 422)

    def test_configure_needs_a_deployed_base(self):
        self.assertEqual(self.client.post("/api/v1/base/configure", json={}).status_code, 409)
        with patch.object(topology_service, "tfvars_data", {"routers": {"a": {}}}):
            r = self.client.post("/api/v1/base/configure", json={})
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(r.json()["job"]["stages"], base_jobs.CONFIGURE_STAGES)

    def test_destroy_with_live_instances(self):
        live_instance("mb")
        live_instance("ns")
        self.assertEqual(self.client.delete("/api/v1/base").status_code, 409)
        r = self.client.delete("/api/v1/base?destroy_instances=true")
        self.assertEqual(r.status_code, 202, r.text)
        kinds = [(j["kind"], j["target"]) for j in r.json()]
        self.assertEqual(kinds, [("destroy_instance", "mb"), ("destroy_instance", "ns"), ("destroy_base", "base")])
        self.assertEqual([j.kind for j in self.submitted], ["destroy_instance", "destroy_instance", "destroy_base"])

    def test_preflight_route(self):
        r = self.client.get("/api/v1/base/preflight?version=grayspace/2026.11&scope=tier1")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ready"])
        self.preflight.assert_awaited_with("grayspace/2026.11", "tier1")

    def test_retrying_a_base_job_keeps_its_request_and_resumes(self):
        job = base_jobs.submit("deploy_base", {"version": "grayspace/2026.11", "scope": "tier1", "configure": True})
        with store.update_job(job.id) as j:
            j.state, j.stage = "failed", "configure"
        r = self.client.post(f"/api/v1/jobs/{job.id}/retry")
        self.assertEqual(r.status_code, 202, r.text)
        retry = r.json()["job"]
        self.assertEqual((retry["resume_from"], retry["result"]["scope"]), ("configure", "tier1"))


class RealPreflight(unittest.TestCase):
    def setUp(self):
        self.data = isolate(self)
        self.checkout = FakeCheckout()
        self.addCleanup(self.checkout.cleanup)
        for name in ("scripts", "bases"):
            os.symlink(REPO / name, self.checkout.root / name)
        (self.checkout.root / ".environment").write_text(
            "\n".join(f'export {k}="{v}"' for k, v in {
                "TF_VAR_proxmox_node": "pve", "TF_VAR_proxmox_host_addr": "127.0.0.1", "TF_VAR_proxmox_blockstore": "local-lvm",
                "TF_VAR_proxmox_filestore": "local", "TF_VAR_proxmox_mgmt_bridge": "vmbr0", "TF_VAR_proxmox_vm_bridge": "vmbr1",
                "TF_VAR_environment_id": "abc", "TF_VAR_router_password": "x", "PROXMOX_VE_API_TOKEN": "u@pve!t=1"}.items()) + "\n")
        from services.proxmox_client import proxmox_client

        self.storages = [{"storage": "local-lvm", "content": "rootdir,images"}, {"storage": "local", "content": "iso,vztmpl,snippets,backup"}]
        self.networks = [{"iface": "vmbr0"}, {"iface": "vmbr1"}]
        self.vnets = []
        self.zones = []
        self.memory = 64 * 1024 ** 3
        for p in (
            patch.object(settings, "codebase_path", str(self.checkout.root)),
            patch.object(settings, "environment_file", None),
            patch.dict(os.environ, {"PATH": self.checkout.path}),
            patch.object(topology_service, "tfvars_data", {}),
            patch.object(proxmox_client, "get_node_status", MagicMock(side_effect=lambda: {"memory": {"available": self.memory}})),
            patch.object(proxmox_client, "get_storage_status", MagicMock(return_value={"avail": 2 * 1024 ** 4})),
            patch.object(proxmox_client, "get_node_storages", MagicMock(side_effect=lambda: self.storages)),
            patch.object(proxmox_client, "get_node_networks", MagicMock(side_effect=lambda: self.networks)),
            patch.object(proxmox_client, "get_sdn_vnets", MagicMock(side_effect=lambda: self.vnets)),
            patch.object(proxmox_client, "get_sdn_zones", MagicMock(side_effect=lambda: self.zones)),
            patch.object(base_preflight, "_ssh_port", AsyncMock(return_value=True)),
        ):
            p.start()
            self.addCleanup(p.stop)
        environment._load_env.cache_clear()

    def checks(self, version="grayspace/2026.11", scope="tier1"):
        result = asyncio.run(base_preflight.run(version, scope))
        return result, {c.name: c for c in result.checks}

    def test_a_prepared_site_is_ready(self):
        result, checks = self.checks()
        self.assertTrue(result.ready, [c for c in result.checks if c.status == "error"])
        self.assertIn("82 routers", checks["version"].message)
        self.assertEqual(checks["capacity"].status, "ok")
        self.assertNotIn("leak-safeguard", checks)  # not checked yet: not shown
        self.assertNotIn("ghosts-vm", checks)  # the base has no GHOSTS VM to warn about (contract 1.25)

    def test_nothing_deployed_needs_a_version(self):
        result, checks = self.checks(version=None)
        self.assertFalse(result.ready)
        self.assertEqual(checks["version"].status, "error")

    def test_storage_without_snippets_and_a_missing_bridge_are_errors(self):
        self.storages[1]["content"] = "iso,vztmpl"
        self.networks.pop()
        result, _ = self.checks()
        errors = " ".join(c.message for c in result.checks if c.status == "error")
        self.assertFalse(result.ready)
        self.assertIn("snippets", errors)
        self.assertIn("vmbr1", errors)

    def test_a_bridge_may_be_an_sdn_vnet(self):
        self.networks = [{"iface": "vmbr1"}]
        self.vnets = [{"vnet": "vmbr0", "zone": "mgmt"}]
        result, _ = self.checks()
        self.assertTrue(result.ready, [c for c in result.checks if c.status == "error"])
        self.assertIn("SDN vnet", " ".join(c.message for c in result.checks if c.name == "bridges"))

    def test_an_environment_id_another_deployment_uses_is_an_error(self):
        self.zones = [{"zone": "abc1"}, {"zone": "abcd1"}]
        result, checks = self.checks()
        self.assertFalse(result.ready)
        self.assertEqual(checks["environment-id"].status, "error")
        self.assertIn("abc1", checks["environment-id"].message)

    def test_the_deployments_own_zone_and_longer_ids_are_fine(self):
        self.zones = [{"zone": "abcd1"}]
        _, checks = self.checks()
        self.assertEqual(checks["environment-id"].status, "ok")
        self.zones = [{"zone": "abc1"}]
        with patch.object(topology_service, "tfvars_data", {"base": {"name": "grayspace/2026.11", "scope": "tier1"}, "routers": {}}):
            _, checks = self.checks()
        self.assertEqual(checks["environment-id"].status, "ok")

    def test_proxmox_down_is_an_error(self):
        from services.proxmox_client import proxmox_client

        with patch.object(proxmox_client, "get_node_status", MagicMock(return_value=None)):
            _, checks = self.checks()
        self.assertEqual(checks["proxmox"].status, "error")

    def test_too_little_memory_is_a_warning(self):
        self.memory = 1024 ** 3
        result, checks = self.checks()
        self.assertTrue(result.ready)
        self.assertEqual(checks["capacity"].status, "warning")

    def test_changing_version_with_live_instances_is_an_error(self):
        live_instance()
        with patch.object(topology_service, "tfvars_data", {"base": {"name": "grayspace/2026.11", "scope": "full"}, "routers": {}}):
            _, checks = self.checks(scope="tier1")
        self.assertEqual(checks["instances"].status, "error")

    def crucible(self, keycloak=None, player=None, **settings_overrides):
        """Preflight with Crucible on; keycloak and player answer (status, json[, headers]) or raise."""
        crucible = {f"TF_VAR_crucible_{k}": v for k, v in {
            "username": "svc", "password": "pw", "auth_url": "https://kc.test/auth", "token_url": "https://kc.test/token",
            "client_id": "tf", "client_scopes": '["vm-api","player-api"]', "vm_api_url": "https://c.test/vm/api",
            "player_api_url": "https://c.test/player/api", "caster_api_url": "https://c.test/caster/api", "user_id": "u"}.items()}
        crucible.update(settings_overrides)
        env = self.checkout.root / ".environment"
        env.write_text(env.read_text() + "export TF_VAR_configure_crucible=true\n"
                       + "".join(f"export {k}='{v}'\n" for k, v in crucible.items() if v is not None))
        environment._load_env.cache_clear()
        self.requests = []

        def handler(request):
            self.requests.append(request)
            status, body, *headers = keycloak if request.url.host == "kc.test" else player
            if isinstance(status, Exception):
                raise status
            return httpx.Response(status, json=body, headers=headers[0] if headers else None)

        real = httpx.AsyncClient
        with patch.object(crucible_api.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), timeout=5)):
            result = asyncio.run(base_preflight.run("grayspace/2026.11", "tier1"))
        return result, [c for c in result.checks if c.name == "crucible"]

    def test_crucible_off_is_not_checked(self):
        _, checks = self.checks()
        self.assertNotIn("crucible", checks)

    def test_crucible_token_and_player(self):
        result, checks = self.crucible(keycloak=(200, {"access_token": "tok"}), player=(200, []))
        self.assertTrue(result.ready, checks)
        self.assertEqual([c.status for c in checks], ["ok", "ok"])
        token_request, player_request = self.requests[-2:]
        form = dict(p.split("=", 1) for p in token_request.content.decode().split("&"))
        self.assertEqual((form["grant_type"], form["username"], form["scope"]), ("password", "svc", "vm-api+player-api"))
        self.assertEqual(player_request.headers["authorization"], "Bearer tok")
        self.assertEqual(str(player_request.url), "https://c.test/player/api/views")

    def test_crucible_bad_password_is_an_error(self):
        result, checks = self.crucible(keycloak=(401, {"error": "invalid_grant", "error_description": "Invalid user credentials"}))
        self.assertFalse(result.ready)
        self.assertIn("Invalid user credentials", checks[0].message)

    def test_crucible_player_refusing_the_account_is_an_error(self):
        result, checks = self.crucible(keycloak=(200, {"access_token": "tok"}), player=(403, {}))
        self.assertFalse(result.ready)
        self.assertEqual(checks[-1].status, "error")
        self.assertIn("rights to create views", checks[-1].message)

    def test_crucible_player_rejecting_the_token_says_why(self):
        header = 'Bearer error="invalid_token", error_description="The issuer \'https://kc.test/realms/x\' is invalid"'
        result, checks = self.crucible(keycloak=(200, {"access_token": "tok"}), player=(401, {}, {"WWW-Authenticate": header}))
        self.assertFalse(result.ready)
        self.assertIn("The issuer 'https://kc.test/realms/x' is invalid", checks[-1].message)
        self.assertNotIn("rights", checks[-1].message)

    def test_crucible_unreachable_is_an_error(self):
        result, checks = self.crucible(keycloak=(httpx.ConnectError("refused"), None))
        self.assertFalse(result.ready)
        self.assertIn("didn't answer", checks[0].message)

    def test_crucible_missing_settings_are_an_error(self):
        result, checks = self.crucible(TF_VAR_crucible_password=None, TF_VAR_crucible_user_id=None)
        self.assertFalse(result.ready)
        self.assertIn("TF_VAR_crucible_password", checks[0].message)
        self.assertIn("TF_VAR_crucible_user_id", checks[0].message)

    def test_missing_settings_are_errors(self):
        (self.checkout.root / ".environment").write_text('export TF_VAR_environment_id="toolong"\n')
        environment._load_env.cache_clear()
        _, checks = self.checks()
        self.assertEqual(checks["environment"].status, "error")
        self.assertIn("TF_VAR_proxmox_node", checks["environment"].message)


if __name__ == "__main__":
    unittest.main()
