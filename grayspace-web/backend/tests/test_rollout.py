"""The draft and the rollout (services/rollout.py), simulated jobs (services/dev_jobs.py), and what a
reset undoes and whose it was (services/mutation_tracker.py, request_user.py, instance_actions reset)."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import settings
from models.instances import InstanceRecord, SliceRef
from models.rollout import DraftBase, DraftEnclave, DraftUpdate
from models.slices import SliceSpec
from services import dev_jobs, dev_simulation, request_user, rollout
from services.mutation_tracker import MutationTracker
from services.slices import runner, store
from services.topology_service import topology_service
from tests import AUTH, REPO
from tests.isolation import isolate

FULL = REPO / "tests" / "golden" / "tfvars-full.json"
VERSION = "grayspace/2026.11"
BANK = SliceSpec.model_validate(yaml.safe_load((REPO / "examples" / "slices" / "meridian-bank.yaml").read_text()))


class Simulated(unittest.TestCase):
    """A simulated deployment whose jobs run at once."""

    deployed = False

    def setUp(self):
        self.data = isolate(self, dev_simulate_deployment=True, environment_id="test", dev_simulation_tfvars=str(FULL),
                            dev_simulation_stage_seconds=0)
        tfvars = self.data / "terraform.tfvars.json"
        if self.deployed:
            tfvars.write_text(FULL.read_text())
        for p in (patch.object(settings, "tfvars_path", str(tfvars)), patch.object(topology_service, "tfvars_data", {}),
                  patch.dict(runner.KINDS), patch.dict(runner.FAILURE_HOOKS), patch.object(dev_simulation, "_stopped", None),
                  patch.dict(os.environ, {"GRAYSPACE_LOCK_FILE": str(self.data / "node.lock")})):
            p.start()
            self.addCleanup(p.stop)
        topology_service._load_tfvars()
        dev_jobs.register()

    def run_job(self, job):
        return asyncio.run(runner.execute(job.id))

    def deploy(self, enclaves=(("meridian-bank", "mb"),), base=True):
        rollout.put_draft(DraftUpdate(base=DraftBase(version=VERSION, scope="full") if base else None,
                                      enclaves=[DraftEnclave(slug=s, prefix=p) for s, p in enclaves]))
        found = rollout.plan()
        self.assertEqual(found.problems, [])
        return self.run_job(rollout.submit(found))


class FromNothing(Simulated):
    def setUp(self):
        super().setUp()
        store.put_slice(BANK)

    def test_a_base_and_an_enclave_in_one_rollout(self):
        rollout.put_draft(DraftUpdate(base=DraftBase(version=VERSION, scope="full"), enclaves=[DraftEnclave(slug="meridian-bank", prefix="mb")]))
        found = rollout.plan()
        self.assertEqual((found.base.action, found.stages, found.changes), ("deploy", ["base", "create:mb"], True))
        job = self.run_job(rollout.submit(found))
        self.assertEqual(job.state, "succeeded", job.error)
        children = [store.get_job(c) for c in job.children]
        self.assertEqual([(c.kind, c.state, c.parent) for c in children],
                         [("deploy_base", "succeeded", job.id), ("create_instance", "succeeded", job.id)])
        self.assertEqual(topology_service.tfvars_data["base"]["name"], VERSION)
        self.assertEqual(store.get_instance("mb").state, "ready")
        self.assertFalse(rollout.plan().changes)
        self.assertEqual(rollout.managed(), {"mb"})
        self.assertEqual(job.stage_titles, {"base": "Deploy the core", "create:mb": "Create enclave mb"})

    def test_enclaves_need_a_base(self):
        rollout.put_draft(DraftUpdate(enclaves=[DraftEnclave(slug="meridian-bank", prefix="mb")]))
        self.assertEqual(rollout.plan().problems, ["Nothing is deployed: choose a core version"])

    def test_the_draft_is_checked(self):
        with self.assertRaises(rollout.DraftError) as e:
            rollout.put_draft(DraftUpdate(base=DraftBase(version="nope/1"), enclaves=[DraftEnclave(slug="nope", prefix="a"),
                                                                                      DraftEnclave(slug="meridian-bank", prefix="a")]))
        self.assertIn("No shipped core version 'nope/1'", e.exception.detail)
        self.assertIn("No enclave 'nope'", e.exception.detail)
        self.assertIn("Prefix 'a' is used twice", e.exception.detail)

    def test_a_retired_version_says_what_replaces_it(self):
        with self.assertRaises(rollout.DraftError) as e:
            rollout.put_draft(DraftUpdate(base=DraftBase(version="grayspace/2026.10")))
        self.assertIn("Core version 'grayspace/2026.10' was retired", e.exception.detail)
        self.assertIn("grayspace/2026.11 replaces it", e.exception.detail)

    def test_a_failed_child_stops_the_rollout_and_its_retry_resumes_there(self):
        with patch.dict(os.environ, {"DEV_SIMULATION_FAIL": "create_instance:configure"}):
            job = self.deploy()
        self.assertEqual((job.state, job.stage), ("failed", "create:mb"))
        self.assertIn("failed at configure", job.error)
        base_child, failed = (store.get_job(c) for c in job.children)
        self.assertEqual((base_child.state, failed.state, store.get_instance("mb").state), ("succeeded", "failed", "failed"))
        retry = runner.new_job(job.kind, job.target, job.stages, resume_from=job.stage, retry_of=job.id, result=job.result)
        with store.update_job(retry.id) as r:
            r.children = list(job.children)
        done = self.run_job(retry)
        self.assertEqual(done.state, "succeeded", done.error)
        last = store.get_job(done.children[-1])
        self.assertEqual((last.retry_of, last.resume_from, len(done.children)), (failed.id, "configure", 3))
        self.assertEqual(store.get_instance("mb").state, "ready")

    def test_a_rollout_resumes_only_while_the_draft_is_the_one_it_deployed(self):
        with patch.dict(os.environ, {"DEV_SIMULATION_FAIL": "create_instance:configure"}):
            job = self.deploy()
        self.assertEqual(job.state, "failed")
        self.assertTrue(rollout.resumable(job))
        self.assertEqual(rollout.plan().resumable_rollout, job.id)
        # Saved again as it is (the Review step does): who and when don't count
        draft = rollout.get_draft()
        rollout.put_draft(DraftUpdate(**draft.model_dump(exclude={"updated", "updated_by", "adopted"})))
        self.assertTrue(rollout.resumable(store.get_job(job.id)))
        # Without the enclave the old plan's create:mb isn't the draft's any more
        rollout.put_draft(DraftUpdate(base=DraftBase(version=VERSION, scope="full")))
        self.assertFalse(rollout.resumable(store.get_job(job.id)))
        self.assertIsNone(rollout.plan().resumable_rollout)

    def test_a_destroyed_instance_is_no_longer_managed(self):
        self.assertEqual(self.deploy().state, "succeeded")
        self.assertEqual(rollout.managed(), {"mb"})
        # Destroyed outside a rollout (the Enclaves page, the API)
        from services.slices import stages

        job = runner.new_job("destroy_instance", "mb", stages.DESTROY_STAGES)
        self.assertEqual(self.run_job(job).state, "succeeded")
        self.assertEqual(rollout.managed(), set())

    def test_a_cancelled_rollout_cancels_its_child(self):
        job = rollout.submit(rollout.plan(rollout.put_draft(DraftUpdate(base=DraftBase(version=VERSION, scope="full")))))
        child = runner.new_job("deploy_base", "base", ["parse"], parent=job.id)
        with store.update_job(job.id) as j:
            j.children = [child.id]
        rollout.cancel_children(store.get_job(job.id))
        self.assertTrue(store.get_job(child.id).cancel_requested)


class OnALiveBase(Simulated):
    deployed = True

    def setUp(self):
        super().setUp()
        store.put_slice(BANK)
        self.assertEqual(self.deploy(base=False).state, "succeeded")

    def test_the_first_draft_is_what_is_deployed(self):
        (self.data / "draft.json").unlink()
        draft = rollout.get_draft()
        self.assertTrue(draft.adopted)
        self.assertEqual([(e.slug, e.prefix) for e in draft.enclaves], [("meridian-bank", "mb")])
        self.assertFalse(rollout.plan(draft).changes)

    def test_removing_an_enclave_destroys_its_instance_but_not_others(self):
        now = store.now()
        store.claim_instance(InstanceRecord(id="gh", slice=SliceRef(slug="meridian-bank", version="v"), state="ready", created=now, updated=now))
        rollout.put_draft(DraftUpdate())
        found = rollout.plan()
        self.assertEqual((found.stages, found.unmanaged), (["destroy:mb"], ["gh"]))
        self.assertEqual(self.run_job(rollout.submit(found)).state, "succeeded")
        self.assertEqual((store.get_instance("mb").state, store.get_instance("gh").state), ("destroyed", "ready"))
        self.assertEqual(rollout.managed(), set())

    def test_a_prefix_made_outside_the_draft_is_a_problem(self):
        now = store.now()
        store.claim_instance(InstanceRecord(id="gh", slice=SliceRef(slug="meridian-bank", version="v"), state="ready", created=now, updated=now))
        rollout.put_draft(DraftUpdate(enclaves=[DraftEnclave(slug="meridian-bank", prefix="mb"), DraftEnclave(slug="meridian-bank", prefix="gh")]))
        self.assertIn("Instance 'gh' (meridian-bank) was made outside the draft", rollout.plan().problems[0])

    def test_a_reset_undoes_the_instances_actions(self):
        from services import instance_actions
        from services.proxmox_client import proxmox_client

        hostname = store.get_instance("mb").allocation.hosts[0].hostname
        with patch.object(instance_actions, "_set_interfaces", AsyncMock()) as interfaces:
            asyncio.run(instance_actions.apply("mb", instance_actions.ActionRequest(action="link.down", target="lum-chi:lum-nyc")))
            asyncio.run(instance_actions.apply("mb", instance_actions.ActionRequest(action="host.stop", target=hostname)))
            self.assertEqual(proxmox_client.get_container_by_hostname(hostname)["status"], "stopped")
            result = asyncio.run(instance_actions.apply("mb", instance_actions.ActionRequest(action="reset")))
        self.assertIn(f"started {hostname}", result.message)
        self.assertEqual(proxmox_client.get_container_by_hostname(hostname)["status"], "running")
        self.assertEqual(store.get_instance("mb").links_down, [])
        self.assertEqual(interfaces.await_args_list[-1].kwargs, {"up": True})


class Routes(Simulated):
    deployed = True

    def setUp(self):
        super().setUp()
        import main

        self.client = TestClient(main.app)
        fake = MagicMock(running=True)
        for p in (patch("services.jobs_api.runner", fake), patch("services.slices.runner.runner", fake)):
            p.start()
            self.addCleanup(p.stop)
        self.submitted = fake.submit

    def test_draft_plan_and_deploy(self):
        store.put_slice(BANK)
        r = self.client.put("/api/v1/draft", json={"enclaves": [{"slug": "nope", "prefix": "x"}]}, headers=AUTH)
        self.assertEqual(r.status_code, 422)
        r = self.client.put("/api/v1/draft", json={"enclaves": [{"slug": "meridian-bank", "prefix": "mb"}]},
                            headers={**AUTH, "X-Grayspace-User": "bob"})
        self.assertEqual((r.status_code, r.json()["updated_by"]), (200, "bob"))
        plan = self.client.get("/api/v1/deploy/plan", headers=AUTH).json()
        self.assertEqual((plan["stages"], plan["capacity"]["cpus"]), (["create:mb"], 64))
        r = self.client.post("/api/v1/deploy", headers={**AUTH, "X-Grayspace-User": "bob"})
        self.assertEqual(r.status_code, 202, r.text)
        job = r.json()["job"]
        self.assertEqual((job["kind"], job["by"], job["stage_titles"]["create:mb"]), ("rollout", "bob", "Create enclave mb"))
        self.submitted.assert_called_once()
        r = self.client.post("/api/v1/deploy", headers=AUTH)
        self.assertEqual(r.status_code, 409)  # the rollout is queued
        listed = self.client.get("/api/v1/jobs?kind=rollout&state=queued", headers=AUTH).json()
        self.assertEqual([j["id"] for j in listed], [job["id"]])
        self.assertEqual(self.client.post("/api/v1/base/configure", headers=AUTH).status_code, 409)

    def test_a_rollout_for_an_earlier_draft_isnt_retried(self):
        store.put_slice(BANK)
        rollout.put_draft(DraftUpdate(enclaves=[DraftEnclave(slug="meridian-bank", prefix="mb")]))
        job = rollout.submit(rollout.plan())
        with store.update_job(job.id) as j:
            j.state, j.stage, j.error = "failed", "create:mb", "failed at configure"
        rollout.put_draft(DraftUpdate())
        r = self.client.post(f"/api/v1/jobs/{job.id}/retry", headers=AUTH)
        self.assertEqual(r.status_code, 409)
        self.assertIn("The draft changed since rollout", r.json()["detail"])
        rollout.put_draft(DraftUpdate(enclaves=[DraftEnclave(slug="meridian-bank", prefix="mb")]))
        with patch("routers.jobs.runner", MagicMock(running=True)):
            self.assertEqual(self.client.post(f"/api/v1/jobs/{job.id}/retry", headers=AUTH).status_code, 202)

    def test_nothing_to_change(self):
        rollout.put_draft(DraftUpdate())
        r = self.client.post("/api/v1/deploy", headers=AUTH)
        self.assertEqual(r.status_code, 409)
        self.assertIn("nothing to change", r.json()["detail"])

    def test_the_summary_tells_of_the_draft(self):
        body = self.client.get("/api/v1/summary", headers=AUTH).json()
        self.assertEqual(body["draft"], {"saved": False, "changes": False})


class Changes(unittest.TestCase):
    """Who changed what, and what Reset changes undoes."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="grayspace-changes-"))
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        p = patch.object(settings, "tfvars_path", str(tmp / "terraform.tfvars.json"))
        p.start()
        self.addCleanup(p.stop)
        self.tracker = MutationTracker()

    def as_user(self, name):
        token = request_user._user.set(name)
        self.addCleanup(request_user._user.reset, token)

    def test_changes_say_who_and_survive_a_restart(self):
        self.as_user("uma")
        self.tracker.set_traffic_config("a:b", {"latency_ms": 50})
        self.tracker.add_certificate_issued("ca-graytrust", "www.example.com", "123")
        # A client move from before base clients were retired (contract 1.18) doesn't survive
        self.tracker.changes.insert(0, {"kind": "client_move", "key": "cli-1", "summary": "cli-1 moved to lum-chi", "by": "uma", "at": "t"})
        self.tracker.save()
        again = MutationTracker()
        again.load()
        self.assertEqual([(c["kind"], c["by"]) for c in again.changes], [("traffic", "uma"), ("certificate_issued", "uma")])
        self.assertEqual(again.count(), 2)
        again.set_preset("exercise")
        self.assertEqual([c["kind"] for c in again.changes], ["certificate_issued"])

    def test_a_reset_revokes_certificates_issued_and_says_revoked_ones_stay(self):
        from services import base_changes

        self.tracker.add_certificate_issued("ca-graytrust", "www.example.com", "123")
        self.tracker.add_certificate_revoked("ca-netcert", "456")
        revoke = AsyncMock(return_value={"success": True, "message": "ok"})
        with patch("services.base_changes.mutation_tracker", self.tracker), patch("services.ca_service.ca_service.revoke_certificate", revoke):
            errors = asyncio.run(base_changes.revert_all())
        revoke.assert_awaited_once_with("ca-graytrust", "123", "superseded")
        self.assertEqual(errors, ["Certificate 456 on ca-netcert stays revoked: a CA can't take a revocation back"])
        self.assertEqual(self.tracker.count(), 0)

    def test_the_header_names_the_user(self):
        app = FastAPI()

        @app.get("/who")
        async def who():
            return {"user": request_user.current()}

        app.add_middleware(request_user.RequestUserMiddleware)
        client = TestClient(app)
        self.assertEqual(client.get("/who", headers={"X-Grayspace-User": "uma"}).json(), {"user": "uma"})
        self.assertEqual(client.get("/who").json(), {"user": None})


if __name__ == "__main__":
    unittest.main()
