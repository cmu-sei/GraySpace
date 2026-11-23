"""The slice, instance, job, image and CA bundle routes, and runtime actions, through the API."""

import copy
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from models.instances import CreatedDomain, CreatedRecord, InstanceRecord, SliceRef
from routers import ca_bundle, images, instances, jobs, link_traffic, slices
from services import instance_actions
from services.mutation_tracker import mutation_tracker
from services.slices import runner as runner_module
from services.slices import service, stages, store
from tests import AUTH, REPO
from tests.isolation import isolate
from tests.test_slice_plan import MINIMAL
from tests.test_slice_render import allocate

TFVARS = json.loads((REPO / "tests" / "golden" / "tfvars-full.json").read_text())
EXAMPLE = yaml.safe_load((REPO / "examples" / "slices" / "meridian-bank.yaml").read_text())


class Api(unittest.TestCase):
    jobs = True

    def setUp(self):
        self.data = isolate(self)
        app = FastAPI()
        for r in (slices, instances, jobs, images, ca_bundle, link_traffic):
            app.include_router(r.router)
        # The deprecated /slices routes: the tests below use them, so they also check backward compatibility
        app.include_router(slices.legacy_router)
        self.client = TestClient(app, headers=AUTH)
        self.submitted = []
        fake_runner = MagicMock()
        fake_runner.running = self.jobs
        fake_runner.submit.side_effect = self.submitted.append
        for p in (
            patch.object(service, "tfvars", return_value=TFVARS),
            patch("services.capabilities.capacity", AsyncMock(return_value=None)),
            patch.object(slices, "runner", fake_runner),
            patch.object(instances, "runner", fake_runner),
            patch.object(jobs, "runner", fake_runner),
            patch("services.jobs_api.runner", fake_runner),
            # Slice job routes also check, per request, that a base is deployed
            patch("services.jobs_api.environment.jobs_available",
                  return_value=(True, None) if self.jobs else (False, "No GraySpace checkout")),
            patch.object(stages, "statuses", AsyncMock(return_value={"mb-hq-edge": "running"})),
            patch.object(mutation_tracker, "save"),
        ):
            p.start()
            self.addCleanup(p.stop)

    def put(self, spec=None):
        spec = spec or EXAMPLE
        return self.client.put(f"/api/v1/slices/{spec['slug']}", json=spec)


class Slices(Api):
    def test_put_get_list_delete(self):
        r = self.put()
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["created"])
        self.assertEqual([f for f in body["findings"] if f["severity"] == "error"], [])
        version = body["version"]
        self.assertFalse(self.put().json()["created"])
        listed = self.client.get("/api/v1/slices").json()
        self.assertEqual([s["slug"] for s in listed], ["base", "meridian-bank"])
        self.assertEqual(listed[1]["latest"], version)
        detail = self.client.get("/api/v1/slices/meridian-bank").json()
        self.assertEqual(detail["spec"]["prefix"], "mb")
        self.assertEqual(self.client.get(f"/api/v1/slices/meridian-bank?version={version}").json()["version"], version)
        self.assertEqual(self.client.get("/api/v1/slices/meridian-bank?version=sha256:" + "0" * 64).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/slices/base").json()["kind"], "base")
        self.assertEqual(self.client.delete("/api/v1/slices/meridian-bank").status_code, 204)
        self.assertEqual(self.client.get("/api/v1/slices/meridian-bank").status_code, 404)
        self.assertEqual(self.client.delete("/api/v1/slices/base").status_code, 409)

    def test_specs_with_errors_are_stored_with_findings(self):
        spec = copy.deepcopy(MINIMAL)
        spec["sites"][0]["attach"] = {"router": "lum-cai"}
        body = self.put(spec).json()
        self.assertIn("NO_CUSTOMER_NET", [f["code"] for f in body["findings"]])

    def test_invalid_specs_are_refused(self):
        self.assertEqual(self.client.put("/api/v1/slices/x", json={"kind": "Slice"}).status_code, 422)
        self.assertEqual(self.put({**MINIMAL, "slug": "other"}).status_code, 200)
        self.assertEqual(self.client.put("/api/v1/slices/test-bank", json={**MINIMAL, "slug": "other"}).status_code, 422)

    def test_validate_against_a_shipped_version_before_anything_is_deployed(self):
        with patch.object(service, "tfvars", return_value={}):
            deployed = self.client.post("/api/v1/enclaves/meridian-bank/validate", json=EXAMPLE).json()
            shipped = self.client.post("/api/v1/enclaves/meridian-bank/validate?base=grayspace/2026.11&scope=full", json=EXAMPLE).json()
            tier1 = self.client.post("/api/v1/enclaves/meridian-bank/validate?base=grayspace/2026.11&scope=tier1", json=EXAMPLE).json()
            missing = self.client.post("/api/v1/enclaves/meridian-bank/validate?base=nope/1", json=EXAMPLE)
        self.assertFalse(deployed["valid"])
        self.assertIn("No core is deployed", deployed["findings"][0]["message"])
        self.assertTrue(shipped["valid"], shipped["findings"])
        self.assertFalse(tier1["valid"])  # Comcast is a Tier 2 ISP
        self.assertEqual(missing.status_code, 404)

    def test_validate_stores_nothing(self):
        body = self.client.post("/api/v1/slices/meridian-bank/validate", json=EXAMPLE).json()
        self.assertTrue(body["valid"])
        self.assertTrue(body["version"].startswith("sha256:"))
        self.assertEqual(self.client.get("/api/v1/slices/meridian-bank").status_code, 404)

    def test_validate_as_the_drafts_own_instance(self):
        """The builder checks a draft's enclave as its instance: the one already built from it holds nothing it needs."""
        Actions.ready(self)  # mb, built from meridian-bank, with its domain
        url = "/api/v1/enclaves/meridian-bank/validate"
        codes = lambda body: {f["code"] for f in body["findings"] if f["severity"] == "error"}
        new = self.client.post(url, json=EXAMPLE).json()
        self.assertIn("PREFIX_TAKEN", codes(new))
        own = self.client.post(f"{url}?instance=mb", json=EXAMPLE).json()
        self.assertTrue(own["valid"], own["findings"])
        shipped = self.client.post(f"{url}?instance=mb&base=grayspace/2026.11&scope=full", json=EXAMPLE).json()
        self.assertTrue(shipped["valid"], shipped["findings"])
        # Another prefix is another instance: the live one's domain is still taken
        self.assertEqual(codes(self.client.post(f"{url}?instance=mb2", json=EXAMPLE).json()), {"DNS_NAME_TAKEN"})
        # Another enclave's instance with the prefix still takes it
        other = {**EXAMPLE, "slug": "other-bank"}
        self.assertIn("PREFIX_TAKEN", codes(self.client.post(f"/api/v1/enclaves/other-bank/validate?instance=mb", json=other).json()))
        self.assertEqual(self.client.post(f"{url}?instance=Bad!", json=EXAMPLE).status_code, 422)

    def test_plan_queues_a_job(self):
        self.put()
        r = self.client.post("/api/v1/slices/meridian-bank/plan", json={"prefix": "mb2"})
        self.assertEqual(r.status_code, 202)
        job = r.json()["job"]
        self.assertEqual((job["kind"], job["state"], job["result"]["prefix"]), ("plan_slice", "queued", "mb2"))
        self.assertEqual([j.id for j in self.submitted], [job["id"]])

    def test_token_required(self):
        self.assertEqual(self.client.get("/api/v1/slices", headers={"Authorization": "Bearer nope"}).status_code, 401)


class Enclaves(Api):
    """The same routes under /api/v1/enclaves (contract 1.5), and the enclave names in bodies."""

    def test_put_get_list_validate_delete(self):
        spec = {**EXAMPLE, "kind": "Enclave"}
        r = self.client.put(f"/api/v1/enclaves/{spec['slug']}", json=spec)
        self.assertEqual(r.status_code, 200, r.text)
        version = r.json()["version"]
        # kind: Enclave and kind: Slice are one spec: one version
        self.assertEqual(self.client.put(f"/api/v1/slices/{EXAMPLE['slug']}", json=EXAMPLE).json()["version"], version)
        listed = self.client.get("/api/v1/enclaves").json()
        self.assertIn(spec["slug"], [s["slug"] for s in listed])
        self.assertEqual(self.client.get(f"/api/v1/enclaves/{spec['slug']}").json()["version"], version)
        self.assertEqual(self.client.post(f"/api/v1/enclaves/{spec['slug']}/validate", json=spec).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/v1/enclaves/{spec['slug']}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/v1/slices/{spec['slug']}").status_code, 404)

    def test_create_with_enclave_and_read_enclave_back(self):
        self.client.put(f"/api/v1/enclaves/{EXAMPLE['slug']}", json=EXAMPLE)
        r = self.client.post("/api/v1/instances", json={"enclave": EXAMPLE["slug"]})
        self.assertEqual(r.status_code, 202, r.text)
        job = r.json()["job"]
        self.assertEqual(job["enclave"], job["slice"])
        summary = self.client.get("/api/v1/instances").json()[0]
        self.assertEqual(summary["enclave"]["slug"], EXAMPLE["slug"])
        self.assertEqual(summary["slice"], summary["enclave"])

    def test_create_needs_a_slug_and_refuses_two(self):
        self.assertEqual(self.client.post("/api/v1/instances", json={}).status_code, 422)
        self.assertEqual(self.client.post("/api/v1/instances", json={"enclave": "a", "slice": "b"}).status_code, 422)


class Instances(Api):
    def test_create(self):
        self.put()
        r = self.client.post("/api/v1/instances", json={"slice": "meridian-bank"})
        self.assertEqual(r.status_code, 202, r.text)
        body = r.json()
        self.assertEqual(body["instance"], "mb")
        self.assertEqual(body["job"]["kind"], "create_instance")
        self.assertEqual(store.get_instance("mb").state, "pending")
        self.assertEqual(store.get_instance("mb").jobs, [body["job"]["id"]])
        self.assertEqual(self.client.post("/api/v1/instances", json={"slice": "meridian-bank"}).status_code, 409)
        self.assertEqual(self.client.post("/api/v1/instances", json={"slice": "meridian-bank", "prefix": "mb2"}).status_code, 202)
        listed = self.client.get("/api/v1/instances").json()
        self.assertEqual([i["id"] for i in listed], ["mb", "mb2"])

    def test_create_refuses_specs_with_errors(self):
        spec = copy.deepcopy(MINIMAL)
        spec["hosts"][0]["image"] = "nope"
        self.put(spec)
        r = self.client.post("/api/v1/instances", json={"slice": "test-bank"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["detail"]["findings"][0]["code"], "UNKNOWN_IMAGE")
        self.assertIsNone(store.get_instance("tb"))

    def test_unknown_slice(self):
        self.assertEqual(self.client.post("/api/v1/instances", json={"slice": "nope"}).status_code, 404)

    def test_view_and_destroy(self):
        self.put()
        created = self.client.post("/api/v1/instances", json={"slice": "meridian-bank"}).json()
        _, alloc = allocate("meridian-bank")
        with store.update_instance("mb") as r:
            r.allocation = alloc
            r.state = "ready"
            r.mgmt_ips = {"mb-hq-edge": "10.0.0.7"}
        view = self.client.get("/api/v1/instances/mb").json()
        self.assertEqual((view["id"], view["state"], view["sites"][0]["router"], view["sites"][0]["wan_ip"]), ("mb", "ready", "lum-chi", "209.151.130.2"))
        ws = next(h for h in view["hosts"] if h["name"] == "ws-01")
        self.assertEqual((ws["hostname"], ws["ip"], ws["labels"]), ("mb-ws-01", "192.168.0.10", {"ghosts.pool": "tellers"}))
        self.assertEqual(view["jobs"], [created["job"]["id"]])
        r = self.client.delete("/api/v1/instances/mb")
        self.assertEqual(r.status_code, 202)
        # The queued create is cancelled; a second destroy is refused
        self.assertTrue(store.get_job(created["job"]["id"]).cancel_requested)
        self.assertEqual(self.client.delete("/api/v1/instances/mb").status_code, 409)
        self.assertEqual(self.client.get("/api/v1/instances/nope").status_code, 404)
        self.assertEqual(self.client.delete("/api/v1/slices/meridian-bank").status_code, 409)  # instance still live


class NoJobs(Api):
    jobs = False

    def test_job_routes_are_unavailable(self):
        self.put()
        r = self.client.post("/api/v1/instances", json={"slice": "meridian-bank"})
        self.assertEqual(r.status_code, 503)
        self.assertIn("can't run jobs", r.json()["detail"])
        self.assertEqual(self.client.post("/api/v1/slices/meridian-bank/plan", json={}).status_code, 503)
        # Storage and validation still work
        self.assertEqual(self.client.post("/api/v1/slices/meridian-bank/validate", json=EXAMPLE).status_code, 200)


class Jobs(Api):
    def job(self, state="failed", kind="create_instance", target="mb"):
        job = runner_module.new_job(kind, target, stages.CREATE_STAGES)
        with store.update_job(job.id) as j:
            j.state, j.stage = state, "configure"
        return job

    def test_get_cancel_retry_log(self):
        self.put()
        self.client.post("/api/v1/instances", json={"slice": "meridian-bank"})
        running = store.get_job(store.get_instance("mb").jobs[0])
        self.assertEqual(self.client.get(f"/api/v1/jobs/{running.id}").json()["state"], "queued")
        self.assertEqual(self.client.post(f"/api/v1/jobs/{running.id}/retry").status_code, 409)
        self.assertTrue(self.client.post(f"/api/v1/jobs/{running.id}/cancel").json()["cancel_requested"])
        with store.update_job(running.id) as j:
            j.state, j.stage = "failed", "configure"
        self.assertEqual(self.client.post(f"/api/v1/jobs/{running.id}/cancel").status_code, 409)
        retry = self.client.post(f"/api/v1/jobs/{running.id}/retry").json()["job"]
        self.assertEqual((retry["resume_from"], retry["retry_of"]), ("configure", running.id))
        self.assertEqual(store.get_instance("mb").jobs[-1], retry["id"])
        self.assertEqual(self.client.post(f"/api/v1/jobs/{running.id}/retry").status_code, 409)  # one at a time
        store.job_log(running.id).write_text("line 1\nline 2\n")
        log = self.client.get(f"/api/v1/jobs/{running.id}/log?offset=7")
        self.assertEqual(log.text, "line 2\n")
        self.assertEqual([j["id"] for j in self.client.get("/api/v1/jobs?target=mb").json()], [retry["id"], running.id])
        self.assertEqual(self.client.get("/api/v1/jobs/nope").status_code, 404)


class Images(Api):
    WINDOWS = {"name": "windows-2022", "type": "vm", "os_family": "windows", "source": "template:9000", "cores": 2, "memory": 4096, "disk": 40}

    def test_local_images(self):
        r = self.client.post("/api/v1/images", json=self.WINDOWS)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["origin"], "local")
        self.assertEqual(self.client.post("/api/v1/images", json=self.WINDOWS).status_code, 409)
        self.assertEqual(self.client.post("/api/v1/images", json={**self.WINDOWS, "name": "grayspace-ubuntu-lxc"}).status_code, 409)
        self.assertEqual(self.client.post("/api/v1/images", json={**self.WINDOWS, "name": "w2", "type": "lxc"}).status_code, 422)
        names = [i["name"] for i in self.client.get("/api/v1/images").json()]
        self.assertIn("windows-2022", names)
        # The windows example validates once the image is registered
        spec = yaml.safe_load((REPO / "examples" / "slices" / "windows-office.yaml").read_text())
        self.assertTrue(self.client.post("/api/v1/slices/windows-office/validate", json=spec).json()["valid"])
        self.assertEqual(self.client.delete("/api/v1/images/grayspace-ubuntu-lxc").status_code, 409)
        self.assertEqual(self.client.delete("/api/v1/images/windows-2022").status_code, 204)
        self.assertEqual(self.client.delete("/api/v1/images/windows-2022").status_code, 404)

    def test_images_in_use_are_kept(self):
        self.client.post("/api/v1/images", json=self.WINDOWS)
        _, alloc = allocate("windows-office")
        store.claim_instance(InstanceRecord(id="wo", slice=SliceRef(slug="windows-office", version="sha256:" + "0" * 64), state="ready",
                                            created="t", updated="t", allocation=alloc))
        r = self.client.delete("/api/v1/images/windows-2022")
        self.assertEqual(r.status_code, 409)
        self.assertIn("wo", r.json()["detail"])

    def test_roles(self):
        roles = {r["name"]: r for r in self.client.get("/api/v1/roles").json()}
        self.assertEqual(set(roles), {"web", "ghosts-client", "mail", "dns", "file", "ad-dc"})


class CaBundle(Api):
    def test_bundle(self):
        with patch.object(ca_bundle.ca_service, "get_ca_bundle", AsyncMock(return_value={"ca-a": "PEM-A\n", "ca-b": None})):
            r = self.client.get("/api/v1/ca/bundle")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text, "PEM-A\n")
        self.assertEqual(r.headers["x-grayspace-missing-cas"], "ca-b")
        with patch.object(ca_bundle.ca_service, "get_ca_bundle", AsyncMock(return_value={"ca-a": None})):
            self.assertEqual(self.client.get("/api/v1/ca/bundle").status_code, 503)


class Actions(Api):
    def ready(self):
        _, alloc = allocate("meridian-bank")
        rec = InstanceRecord(id="mb", slice=SliceRef(slug="meridian-bank", version="sha256:" + "0" * 64), state="ready", created="t",
                             updated="t", allocation=alloc,
                             domains=[CreatedDomain(domain="meridian-bank.com", hosting_server="dns-cloudflare", tld_server="dns-gtld")])
        store.claim_instance(rec)
        return rec

    def act(self, action, target=None, **params):
        return self.client.post("/api/v1/instances/mb/actions", json={"action": action, "target": target, "params": params})

    def link(self):
        return next(iter(service.context().links))

    def test_link_condition_is_owned_by_the_instance(self):
        self.ready()
        link = self.link()
        with patch("services.traffic_service.traffic_service.apply_link_traffic_config", AsyncMock(return_value=(True, "applied"))) as apply:
            r = self.act("link.condition", link, latency_ms=80, packet_loss_percent=5)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(apply.call_args.args[0], link)
        held = store.get_instance("mb").conditions
        self.assertEqual((held[0].link_id, held[0].latency_ms, held[0].origin), (link, 80, "action"))
        self.assertEqual(mutation_tracker.traffic_configs, {})  # not in the overlay
        # The overlay can't take it over
        r = self.client.put(f"/api/v1/links/{link}/traffic", json={"latency_ms": 5})
        self.assertEqual(r.status_code, 409)
        self.assertIn("mb", r.json()["detail"])
        # Nor can another instance
        other = InstanceRecord(id="o2", slice=SliceRef(slug="x", version="sha256:" + "0" * 64), state="ready", created="t", updated="t",
                               allocation=allocate("routed-campus")[1])
        store.claim_instance(other)
        r = self.client.post("/api/v1/instances/o2/actions", json={"action": "link.condition", "target": link, "params": {"latency_ms": 1}})
        self.assertEqual((r.status_code, r.json()["detail"]["code"]), (409, "LINK_OWNED"))
        with patch("services.traffic_service.traffic_service.clear_link_traffic_config", AsyncMock(return_value=(True, "cleared"))):
            self.assertEqual(self.act("link.clear", link).status_code, 200)
        self.assertEqual(store.get_instance("mb").conditions, [])

    def test_ownership_holds_in_either_router_order(self):
        self.ready()
        link = self.link()
        a, b = link.split(":")
        with patch("services.traffic_service.traffic_service.apply_link_traffic_config", AsyncMock(return_value=(True, "applied"))):
            self.assertEqual(self.act("link.condition", f"{b}:{a}", latency_ms=10).status_code, 200)
        self.assertEqual(store.get_instance("mb").conditions[0].link_id, link)  # stored canonical
        self.assertEqual(self.client.put(f"/api/v1/links/{b}:{a}/traffic", json={"latency_ms": 5}).status_code, 409)
        self.assertEqual(self.client.put(f"/api/v1/links/{link}/traffic", json={"latency_ms": 5}).status_code, 409)
        # The overlay holding the reversed ID counts too, for actions and for validation
        with store.update_instance("mb") as r:
            r.conditions = []
        with patch.dict(mutation_tracker.traffic_configs, {f"{b}:{a}": {"latency_ms": 9}}):
            self.assertEqual(self.act("link.condition", link, latency_ms=1).json()["detail"]["code"], "LINK_OWNED")
            self.assertIn(link, service.taken().links)

    def test_base_preset_apply_skips_instance_links(self):
        import asyncio

        from models.presets import Preset, PresetAction, PresetOwner
        from services import base_changes, presets

        self.ready()
        link = self.link()
        with patch("services.traffic_service.traffic_service.apply_link_traffic_config", AsyncMock(return_value=(True, "applied"))):
            self.act("link.condition", link, latency_ms=10)
        preset = Preset(name="s", owner=PresetOwner(kind="base", ref="grayspace/2026.11"), created="t",
                        actions=[PresetAction(action="link.condition", target=link, params={"latency_ms": 99})])
        with patch.object(base_changes, "revert_all", AsyncMock(return_value=[])), patch.object(presets, "get_base", return_value=preset), \
             patch("services.traffic_service.traffic_service.apply_link_traffic_config", AsyncMock(return_value=(True, "applied"))) as apply:
            result = asyncio.run(presets.apply_base("s"))
        apply.assert_not_called()
        self.assertIn("held by slice instance mb", " ".join(result.errors))
        self.assertEqual(mutation_tracker.traffic_configs, {})

    def test_overlay_links_are_refused(self):
        self.ready()
        link = self.link()
        with patch.dict(mutation_tracker.traffic_configs, {link: {"latency_ms": 9}}):
            r = self.act("link.condition", link, latency_ms=10)
        self.assertEqual(r.json()["detail"]["code"], "LINK_OWNED")

    def test_link_down_and_up(self):
        self.ready()
        link = self.link()
        ssh = AsyncMock(return_value=(0, "", ""))
        with patch("services.ssh_client.ssh_client.execute_command", ssh), \
             patch.object(stages, "base_mgmt_ip", AsyncMock(return_value="10.0.8.1")):
            self.assertEqual(self.act("link.down", link).status_code, 200)
            self.assertEqual(store.get_instance("mb").links_down, [link])
            self.assertTrue(all(" down" in c.args[1] for c in ssh.call_args_list))
            self.assertEqual(self.act("link.up", link).status_code, 200)
        self.assertEqual(store.get_instance("mb").links_down, [])

    def test_link_down_rolls_back_when_one_end_fails(self):
        self.ready()
        link = self.link()
        ssh = AsyncMock(side_effect=[(0, "", ""), (1, "", "Operation not permitted"), (0, "", "")])
        with patch("services.ssh_client.ssh_client.execute_command", ssh), \
             patch.object(stages, "base_mgmt_ip", AsyncMock(return_value="10.0.8.1")):
            r = self.act("link.down", link)
        self.assertEqual(r.status_code, 502)
        commands = [c.args[1] for c in ssh.call_args_list]
        self.assertTrue(commands[0].endswith(" down") and commands[1].endswith(" down"))
        self.assertTrue(commands[2].endswith(" up"))  # the first end restored
        self.assertEqual(store.get_instance("mb").links_down, [])

    def test_unknown_link_and_bad_params(self):
        self.ready()
        self.assertEqual(self.act("link.condition", "lum-chi:ntt-tok", latency_ms=1).json()["detail"]["code"], "UNKNOWN_LINK")
        self.assertEqual(self.act("link.condition", self.link(), latency_ms=-1).status_code, 422)
        self.assertEqual(self.act("link.condition", self.link(), colour=1).status_code, 422)

    def test_dns_records(self):
        self.ready()
        added, reverted = [], []

        async def add_record(server, zone, name, rtype, data, ttl=3600, track=True):
            added.append((server, zone, name, rtype, data, track))
            from models.network import AddDNSRecordResult
            return AddDNSRecordResult(success=True, message="ok")

        async def revert_record(record):
            reverted.append(record)
            return True, "removed"

        with patch("services.dns_service.dns_service.add_record", side_effect=add_record), \
             patch("services.dns_service.dns_service.revert_record", side_effect=revert_record):
            self.assertEqual(self.act("dns.record.set", None, name="mail.meridian-bank.com", host="web01").status_code, 200)
            self.assertEqual(added[-1], ("dns-cloudflare", "meridian-bank.com", "mail", "A", "209.151.130.3", False))
            self.assertEqual(self.act("dns.record.set", None, name="mail.meridian-bank.com", data="1.2.3.4").status_code, 200)
            self.assertEqual(len(reverted), 1)  # replaced
            self.assertEqual(self.act("dns.record.set", None, name="www.evil.com", data="1.2.3.4").json()["detail"]["code"], "UNKNOWN_DOMAIN")
            self.assertEqual(self.act("dns.record.delete", None, name="mail.meridian-bank.com").status_code, 200)
            self.assertEqual(self.act("dns.record.delete", None, name="mail.meridian-bank.com").status_code, 404)
        self.assertEqual(store.get_instance("mb").records, [])
        self.assertEqual(mutation_tracker.dns_records, [])

    def test_host_power(self):
        self.ready()
        container = {"name": "mb-ws-01", "tags": "test;slice-mb", "status": "running"}
        with patch("services.proxmox_client.proxmox_client.get_container_by_hostname", return_value=container), \
             patch("services.proxmox_client.proxmox_client.stop_container", return_value=True) as stop:
            self.assertEqual(self.act("host.stop", "ws-01").status_code, 200)
            stop.assert_called_once_with("mb-ws-01")
        with patch("services.proxmox_client.proxmox_client.get_container_by_hostname", return_value={**container, "tags": "test"}):
            self.assertEqual(self.act("host.start", "ws-01").status_code, 404)  # not this instance's
        self.assertEqual(self.act("host.start", "nobody").json()["detail"]["code"], "UNKNOWN_HOST")

    def test_needs_a_ready_instance(self):
        rec = self.ready()
        with store.update_instance(rec.id) as r:
            r.state = "configuring"
        self.assertEqual(self.act("host.stop", "ws-01").json()["detail"]["code"], "NOT_READY")
        self.assertEqual(self.client.post("/api/v1/instances/nope/actions", json={"action": "host.stop"}).status_code, 404)

    def test_quick_reset_leaves_instances_alone(self):
        """Overlay revert touches only active_mutations.json."""
        self.ready()
        with store.update_instance("mb") as r:
            r.records.append(CreatedRecord(server="dns-cloudflare", zone="meridian-bank.com", name="www", fqdn="www.meridian-bank.com",
                                           type="A", data="209.151.130.3", ttl=3600))
        import asyncio

        from services import base_changes

        with patch.object(mutation_tracker, "clear"):
            errors = asyncio.run(base_changes.revert_all())
        self.assertEqual(errors, [])
        self.assertEqual(len(store.get_instance("mb").records), 1)

    def test_release_on_destroy(self):
        self.ready()
        link = self.link()
        with patch("services.traffic_service.traffic_service.apply_link_traffic_config", AsyncMock(return_value=(True, "applied"))):
            self.act("link.condition", link, latency_ms=50)
        with patch("services.traffic_service.traffic_service.clear_link_traffic_config", AsyncMock(return_value=(True, "cleared"))) as clear:
            import asyncio

            asyncio.run(instance_actions.release_links("mb"))
        clear.assert_called_once_with(link)
        self.assertEqual(store.get_instance("mb").conditions, [])


if __name__ == "__main__":
    unittest.main()
