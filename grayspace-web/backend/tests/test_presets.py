"""Presets (services/presets.py): base presets from the tracked changes, applying them after a
reset, and the draft's preset in the rollout. Enclaves have none (contract 1.19)."""

import json
from unittest.mock import AsyncMock, patch

from models.presets import Preset, PresetAction, PresetOwner
from models.rollout import DraftBase, DraftEnclave, DraftUpdate
from routers import changes, presets as presets_router
from services import base_changes, presets, rollout
from services.mutation_tracker import MutationTracker, mutation_tracker
from services.slices import store
from services.topology_service import topology_service
from tests import test_rollout, test_slice_api

# Modules, not their classes: a test class imported here would run again with this module's
VERSION = test_rollout.VERSION
TFVARS = test_slice_api.TFVARS

LINK_PARAMS = {"latency_ms": 80, "jitter_ms": None, "bandwidth_mbps": None, "packet_loss_percent": None}


class Api(test_slice_api.Api):
    # Actions' helpers without its tests
    ready, act, link = test_slice_api.Actions.ready, test_slice_api.Actions.act, test_slice_api.Actions.link

    def setUp(self):
        super().setUp()
        self.client.app.include_router(presets_router.router)
        self.client.app.include_router(changes.router)
        self.client.app.include_router(changes.legacy_router)
        for p in (patch.object(topology_service, "tfvars_data", TFVARS), patch.object(mutation_tracker, "preset", None),
                  patch.object(mutation_tracker, "traffic_configs", {}),
                  patch.object(mutation_tracker, "changes", [])):
            p.start()
            self.addCleanup(p.stop)

    def shape(self, link):
        mutation_tracker.set_traffic_config(link, dict(LINK_PARAMS))


class BasePresets(Api):
    def test_save_list_get_delete(self):
        link = self.link()
        self.shape(link)
        r = self.client.post("/api/v1/base/presets", json={"name": "slow-links", "description": "Transatlantic at 80 ms"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["owner"], {"kind": "base", "ref": VERSION})
        self.assertEqual(r.json()["actions"], [{"action": "link.condition", "target": link, "params": {"latency_ms": 80}}])
        # Saved for every deployment on the version, and now the active preset
        self.assertTrue((self.data / "presets" / "grayspace" / "2026.11" / "slow-links.json").is_file())
        self.assertEqual(mutation_tracker.preset, "slow-links")
        body = self.client.get("/api/v1/changes").json()
        self.assertEqual((body["preset"], body["base"], body["baseline"], body["changes"]), ("slow-links", VERSION, "slow-links", []))
        listed = self.client.get("/api/v1/base/presets").json()
        self.assertEqual([(p["name"], p["actions"], p["kinds"], p["problems"]) for p in listed], [("slow-links", 1, {"link.condition": 1}, [])])
        self.assertEqual(self.client.get(f"/api/v1/base/presets?base={VERSION}").json(), listed)
        self.assertEqual(self.client.get("/api/v1/base/presets?base=other/1").json(), [])
        self.assertEqual(self.client.get("/api/v1/base/presets/slow-links").json()["description"], "Transatlantic at 80 ms")
        self.assertEqual(self.client.delete("/api/v1/base/presets/slow-links").status_code, 204)
        self.assertEqual(self.client.get("/api/v1/base/presets/slow-links").status_code, 404)
        # Its changes stay, now on the base itself
        self.assertIsNone(mutation_tracker.preset)
        self.assertIn(link, mutation_tracker.traffic_configs)

    def test_names_and_versions_are_checked(self):
        self.assertEqual(self.client.post("/api/v1/base/presets", json={"name": "Slow Links"}).status_code, 422)
        self.assertEqual(self.client.get("/api/v1/base/presets?base=../../etc").status_code, 422)
        with patch.object(topology_service, "tfvars_data", {}):
            self.assertEqual(self.client.post("/api/v1/base/presets", json={"name": "x"}).status_code, 409)

    def test_problems_name_what_the_deployment_lacks(self):
        preset = Preset(name="elsewhere", owner=PresetOwner(kind="base", ref=VERSION), created="t",
                        actions=[PresetAction(action="link.condition", target="aaa:bbb", params={"latency_ms": 1}),
                                 PresetAction(action="link.condition", target="lum-chi:ntt-tok", params={"latency_ms": 1}),
                                 PresetAction(action="link.condition", target=self.link(), params={"latency_ms": 1})])
        self.assertEqual(self.client.put("/api/v1/base/presets/elsewhere", json=preset.model_dump()).status_code, 200)
        listed = self.client.get("/api/v1/base/presets").json()
        self.assertEqual(listed[0]["problems"], ["No link aaa:bbb", "No link lum-chi:ntt-tok"])
        for action in ("host.stop", "client.move"):  # an instance action; base clients were retired (contract 1.18)
            bad = {**preset.model_dump(), "actions": [{"action": action, "target": "x"}]}
            self.assertEqual(self.client.put("/api/v1/base/presets/elsewhere", json=bad).status_code, 422)

    def test_apply_resets_then_replays(self):
        link = self.link()
        preset = Preset(name="slow-links", owner=PresetOwner(kind="base", ref=VERSION), created="t",
                        actions=[PresetAction(action="link.condition", target=link, params={"latency_ms": 80})])
        presets.put_base(preset)
        revert = AsyncMock(return_value=["Traffic x:y: unreachable"])
        tc = AsyncMock(return_value=(True, "applied"))
        with patch.object(base_changes, "revert_all", revert), patch("services.traffic_service.traffic_service.apply_link_traffic_config", tc):
            r = self.client.post("/api/v1/base/presets/slow-links/apply")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["applied"], r.json()["warnings"]), ({"link.condition": 1}, ["Reset: Traffic x:y: unreachable"]))
        revert.assert_awaited_once()
        self.assertEqual(tc.await_args.args[1].latency_ms, 80)
        self.assertEqual(mutation_tracker.preset, "slow-links")
        self.assertEqual(mutation_tracker.traffic_configs[link]["latency_ms"], 80)
        self.assertEqual(mutation_tracker.changes, [])  # the preset's changes aren't changes since it

        # Reset changes applies the active preset again; "none" goes back to the base
        with patch.object(base_changes, "revert_all", AsyncMock(return_value=[])), \
             patch("services.traffic_service.traffic_service.apply_link_traffic_config", tc):
            self.assertEqual(self.client.post("/api/v1/changes/discard").json()["message"], "Applied slow-links")
            self.assertEqual(self.client.post("/api/v1/scenarios/discard-changes").json()["message"], "Applied slow-links")
            r = self.client.post("/api/v1/base/presets/none/apply")
        self.assertEqual(r.json()["message"], "Back to the core as deployed")
        self.assertIsNone(mutation_tracker.preset)
        self.assertEqual(self.client.get("/api/v1/scenarios/mutations/count").json()["baseline"], "GraySpace")

    def test_discard_without_the_preset_goes_back_to_the_base(self):
        mutation_tracker.preset = "gone"
        with patch.object(base_changes, "revert_all", AsyncMock(return_value=[])):
            r = self.client.post("/api/v1/changes/discard")
        self.assertEqual(r.json()["message"], "Back to the core as deployed")
        self.assertIsNone(mutation_tracker.preset)

    def test_tracker_as_actions(self):
        with patch.object(mutation_tracker, "zone_changes", {"lum-chi:eth3": {"zone_id": "z1", "vlan_tag": 5, "original_zone_id": "z0"}}), \
             patch.object(mutation_tracker, "dns_records", [{"server": "dns-cloudflare", "zone": "x.com", "name": "www", "type": "A",
                                                             "data": "1.2.3.4", "ttl": 60}]), \
             patch.object(mutation_tracker, "dns_domains", [{"domain": "x.com", "hosting_server": "dns-cloudflare", "tld_server": "dns-gtld"}]):
            self.shape("a:b")
            found = [(a.action, a.target) for a in base_changes.from_tracker()]
            domain = base_changes.from_tracker()[1]
        self.assertEqual(found, [("interface.zone", "lum-chi:eth3"), ("dns.domain.create", None),
                                 ("dns.record.set", None), ("link.condition", "a:b")])
        self.assertEqual(domain.params, {"domain": "x.com", "hosting_server": "dns-cloudflare"})


class NoEnclavePresets(Api):
    """Enclave presets were removed (contract 1.19): their routes are gone, and an instance is what its spec built."""

    def setUp(self):
        super().setUp()
        self.put()

    def test_the_routes_are_gone(self):
        self.ready()
        self.assertEqual(self.client.post("/api/v1/instances/mb/presets", json={"name": "phish"}).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/instances/mb/presets/phish/apply").status_code, 404)
        self.assertEqual(self.client.delete("/api/v1/enclaves/meridian-bank/presets/phish").status_code, 404)
        self.assertNotIn("preset", self.client.get("/api/v1/instances/mb").json())

    def test_an_enclave_preset_file_is_refused(self):
        bad = Preset(name="phish", owner=PresetOwner(kind="base", ref=VERSION), created="t").model_dump()
        bad["owner"] = {"kind": "enclave", "ref": "meridian-bank"}
        self.assertEqual(self.client.put("/api/v1/base/presets/phish", json=bad).status_code, 422)

    def test_a_site_by_name(self):
        rec = self.ready()
        site = rec.allocation.sites[0]
        container = {"name": site.edge, "tags": "test;slice-mb", "status": "running"}
        with patch("services.proxmox_client.proxmox_client.get_container_by_hostname", return_value=container), \
             patch("services.proxmox_client.proxmox_client.stop_container", return_value=True) as stop:
            self.assertEqual(self.act("host.stop", site.name).status_code, 200)
        stop.assert_called_once_with(site.edge)


class Tracker(Api):
    def test_a_scenario_file_becomes_the_preset(self):
        legacy = self.data / "scenarios" / "active_mutations.json"
        legacy.parent.mkdir()
        legacy.write_text(json.dumps({"traffic_configs": {"a:b": {"latency_ms": 5}}, "baseline_scenario": "Exercise"}))
        with patch("services.mutation_tracker.settings.tfvars_path", str(self.data / "terraform.tfvars.json")):
            tracker = MutationTracker()
            tracker.load()
        self.assertEqual((tracker.preset, tracker.count()), ("Exercise", 1))
        self.assertFalse(legacy.exists())
        self.assertTrue((self.data / "active_mutations.json").exists())
        (self.data / "active_mutations.json").write_text(json.dumps({"baseline_scenario": "GraySpace"}))
        again = MutationTracker()
        again.load()
        self.assertIsNone(again.preset)


class Rollout(test_rollout.Simulated):
    deployed = True

    def setUp(self):
        super().setUp()
        for p in (patch.object(mutation_tracker, "preset", None), patch.object(mutation_tracker, "save"),
                  patch.object(mutation_tracker, "changes", [])):
            p.start()
            self.addCleanup(p.stop)
        presets.put_base(Preset(name="slow", owner=PresetOwner(kind="base", ref=VERSION), created="t",
                                actions=[PresetAction(action="link.condition", target="a:b", params={"latency_ms": 9})]))

    def test_the_drafts_base_preset(self):
        with self.assertRaises(rollout.DraftError):
            rollout.put_draft(DraftUpdate(base=DraftBase(version=VERSION, preset="nope")))
        rollout.put_draft(DraftUpdate(base=DraftBase(version=VERSION, scope="full", preset="slow")))
        found = rollout.plan()
        self.assertEqual((found.base.action, found.base.preset, found.stages), ("none", "slow", ["preset"]))
        apply = AsyncMock()
        with patch.object(base_changes, "revert_all", AsyncMock(return_value=[])), patch.object(base_changes, "apply", apply):
            job = self.run_job(rollout.submit(found))
        self.assertEqual(job.state, "succeeded", job.error)
        self.assertEqual(job.stage_titles, {"preset": "Apply the core preset"})
        self.assertEqual(apply.await_args.args[0].target, "a:b")
        self.assertEqual(mutation_tracker.preset, "slow")
        self.assertFalse(rollout.plan().changes)
        # The adopted draft of what is deployed names it too
        (store.data_dir() / "draft.json").unlink()
        self.assertEqual(rollout.get_draft().base.preset, "slow")

    def test_a_preset_that_fails_fails_the_stage(self):
        rollout.put_draft(DraftUpdate(base=DraftBase(version=VERSION, scope="full", preset="slow")))
        with patch.object(base_changes, "revert_all", AsyncMock(return_value=[])), \
             patch.object(base_changes, "apply", AsyncMock(side_effect=base_changes.ChangeError(502, "unreachable"))):
            job = self.run_job(rollout.submit(rollout.plan()))
        self.assertEqual((job.state, job.stage), ("failed", "preset"))
        self.assertIn("link.condition a:b: unreachable", job.error)

    def test_an_enclave_preset_in_the_draft_is_ignored(self):
        store.put_slice(test_rollout.BANK)
        rollout.put_draft(DraftUpdate(base=DraftBase(version=VERSION, scope="full"), enclaves=[DraftEnclave(slug="meridian-bank", prefix="mb", preset="quiet")]))
        self.assertIsNone(rollout.get_draft().enclaves[0].preset)
        found = rollout.plan()
        self.assertEqual(found.stages, ["create:mb"])
        job = self.run_job(rollout.submit(found))
        self.assertEqual(job.state, "succeeded", job.error)
        self.assertFalse(rollout.plan().changes)
        # A draft saved before 1.19 still loads, without it
        draft = json.loads((store.data_dir() / "draft.json").read_text())
        draft["enclaves"][0]["preset"] = "quiet"
        (store.data_dir() / "draft.json").write_text(json.dumps(draft))
        self.assertIsNone(rollout.get_draft().enclaves[0].preset)

    def test_a_rollout_planned_before_skips_its_enclave_presets(self):
        found = rollout.plan()
        found.stages = ["preset:mb"]
        job = self.run_job(rollout.submit(found))
        self.assertEqual(job.state, "succeeded", job.error)
        self.assertEqual(job.stage_titles["preset:mb"], "Apply the preset to enclave mb (removed)")
