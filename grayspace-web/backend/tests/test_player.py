"""The Player view (services/player.py): who sees what, the draft's checks, the plan, the rollout's
player stage, the Terraform it runs, and the move off the base module's view (modules/crucible)."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from models.instances import InstanceRecord, SliceRef
from models.rollout import Draft, DraftBase, DraftEnclave, DraftPlayer, DraftUpdate, PlayerApplication, PlayerGuest, PlayerRule, PlayerTeam
from routers import player as player_router
from services import player, rollout
from services.slices import environment, stages, store
from tests import AUTH, test_rollout
from tests.test_slice_render import allocate

VERSION = test_rollout.VERSION


def view(**extra) -> DraftPlayer:
    return DraftPlayer(**{"applications": [PlayerApplication(name="VMs", template_id="t-1"), PlayerApplication(name="Chat", url="https://chat")],
                          "teams": [PlayerTeam(name="Blue", applications=["VMs", "Chat"]), PlayerTeam(name="Red", applications=["VMs"])],
                          **extra})


def live(prefix="mb", mark=True):
    """A ready meridian-bank instance; its first site and the counted ws hosts marked crucible: true."""
    spec, alloc = allocate("meridian-bank")
    if mark:
        alloc.sites[0].crucible = True
        for h in alloc.hosts:
            h.crucible = h.spec_name == "ws"
    store.put_slice(spec)
    store.claim_instance(InstanceRecord(id=prefix, slice=SliceRef(slug=spec.slug, version=spec.version()), state="ready", created="t",
                                        updated="t", allocation=alloc))
    return alloc


def checkout(test):
    """A scratch checkout for the workspaces' files: the base, terraform/player, terraform/slice."""
    from config import settings

    root = test.data / "checkout"
    for d in ("terraform/player", "terraform/slice/instances"):
        (root / d).mkdir(parents=True, exist_ok=True)
    p = patch.object(settings, "codebase_path", str(root))
    p.start()
    test.addCleanup(p.stop)
    return root


class FakeCtx:
    def __init__(self, outputs=None):
        self.calls, self.logs, self.outputs = [], [], outputs or {}

    def log(self, message):
        self.logs.append(message)

    async def run(self, argv, cwd=None, env=None, check=True, quiet=False):
        self.calls.append((argv, str(cwd), env))
        if argv[:2] == ["terraform", "output"]:
            return 0, json.dumps(self.outputs)
        return 0, ""


class Guests(test_rollout.Simulated):
    deployed = True

    def setUp(self):
        super().setUp()
        p = patch.object(player, "enabled", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test_base_guests_hints_and_overrides(self):
        alloc = live()
        draft = Draft(enclaves=[DraftEnclave(slug="meridian-bank", prefix="mb")],
                      player=view(guests=[PlayerGuest(name="lum-chi", teams=["Blue", "Red"]), PlayerGuest(name="nowhere", teams=["Blue"]),
                                          PlayerGuest(instance="mb", name="web01", teams=["Red"]),
                                          PlayerGuest(instance="mb", name="hq", site=True, teams=[])]))
        found = player.desired(draft.player, draft)
        self.assertEqual(found["lum-chi"], ["Blue", "Red"])
        self.assertNotIn("nowhere", found)  # the base has no such guest
        web = next(h.hostname for h in alloc.hosts if h.spec_name == "web01")
        self.assertEqual(found[web], ["Red"])
        copies = [h.hostname for h in alloc.hosts if h.spec_name == "ws"]
        self.assertGreater(len(copies), 1)
        self.assertTrue(all(found[h] == ["Blue"] for h in copies))  # the hint: the default team, every copy
        self.assertNotIn(alloc.sites[0].edge, found)  # hinted, but the draft leaves it out
        self.assertEqual(player.desired(draft.player, draft, exclude={"mb"}).keys(), {"lum-chi"})

    def test_rules_give_whole_parts_to_teams_and_guests_override_them(self):
        alloc = live()
        draft = Draft(enclaves=[DraftEnclave(slug="meridian-bank", prefix="mb")],
                      player=view(default_team="Red", assign=[PlayerRule(scope="base", teams=["Blue"]), PlayerRule(scope="enclave:mb", teams=["Blue"])],
                                  guests=[PlayerGuest(name="lum-chi", teams=["Red"]), PlayerGuest(instance="mb", name="web01", teams=[])]))
        found = player.desired(draft.player, draft)
        self.assertEqual(found["lum-chi"], ["Red"])  # its own entry
        self.assertEqual(found["prometheus"], ["Blue"])  # every other base guest: the rule
        self.assertNotIn(next(h.hostname for h in alloc.hosts if h.spec_name == "web01"), found)  # left out
        ws = next(h.hostname for h in alloc.hosts if h.spec_name == "ws")
        self.assertEqual(found[ws], ["Blue", "Red"])  # the rule, and the default team for a crucible: true host
        self.assertEqual(found[alloc.sites[1].edge], ["Blue"])

    def test_candidates_group_the_base_and_list_the_enclaves(self):
        live()
        draft = Draft(enclaves=[DraftEnclave(slug="meridian-bank", prefix="mb")])
        found = player.candidates(draft, json.loads(test_rollout.FULL.read_text()))
        groups = {g["group"] for g in found["base"]}
        self.assertEqual(groups, {"routers", "core_services", "management"})
        management = [g["hostname"] for g in found["base"] if g["group"] == "management"]
        self.assertEqual(management, ["prometheus"])  # no GHOSTS VM (contract 1.25)
        self.assertEqual(next(g for g in found["base"] if g["hostname"] == "lum-chi")["isp"], "Lumen")
        enclave = found["enclaves"][0]
        self.assertEqual((enclave["prefix"], [s["name"] for s in enclave["sites"]]), ("mb", ["hq", "branch"]))
        self.assertEqual(next(h for h in enclave["hosts"] if h["name"] == "ws")["count"], 40)


class BeforeTheBase(test_rollout.Simulated):
    def setUp(self):
        super().setUp()
        p = patch.object(player, "enabled", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test_base_guests_are_checked_against_the_drafts_version(self):
        found = player.plan(Draft(base=DraftBase(version=VERSION, scope="full"),
                                  player=view(guests=[PlayerGuest(name="lum-chi", teams=["Blue"]), PlayerGuest(name="nowhere", teams=["Blue"])])),
                            ["base"])
        self.assertEqual((found.warnings, found.guests), (["The base has no nowhere"], 1))

    def test_a_draft_from_before_grafana_the_clients_and_ghosts_were_removed_drops_them_quietly(self):
        old = view(guests=[PlayerGuest(name="grafana", teams=["Blue"]), PlayerGuest(name="lum-fluxbox", teams=["Blue"]),
                           PlayerGuest(name="ghosts", teams=["Blue"]), PlayerGuest(name="lum-chi", teams=["Blue"])])
        store.write_json(rollout._draft_file(), Draft(base=DraftBase(version=VERSION, scope="full"), player=old).model_dump(mode="json"))
        draft = rollout.get_draft()
        self.assertEqual([g.name for g in draft.player.guests], ["lum-chi"])
        self.assertEqual(player.plan(draft, ["base"]).warnings, [])
        saved = rollout.put_draft(DraftUpdate(base=DraftBase(version=VERSION, scope="full"), player=old))
        self.assertEqual([g.name for g in saved.player.guests], ["lum-chi"])

    def test_no_warnings_without_a_base(self):
        found = player.plan(Draft(player=view(guests=[PlayerGuest(name="lum-chi", teams=["Blue"])])), [])
        self.assertEqual(found.warnings, [])


class Checks(test_rollout.Simulated):
    deployed = True

    def setUp(self):
        super().setUp()
        store.put_slice(test_rollout.BANK)

    def test_the_draft_is_checked(self):
        with patch.object(player, "enabled", return_value=False):
            self.assertIn("no Player view option", " ".join(player.problems(Draft(player=view()))))
        with patch.object(player, "enabled", return_value=True):
            bad = view(teams=[PlayerTeam(name="Blue", applications=["Nope"]), PlayerTeam(name="Blue")], default_team="Green",
                       guests=[PlayerGuest(name="lum-chi", teams=["Gold"]), PlayerGuest(instance="xx", name="a", teams=["Blue"]),
                               PlayerGuest(instance="mb", name="nohost", teams=["Blue"]), PlayerGuest(instance="mb", name="web01", site=True, teams=["Blue"])])
            found = player.problems(Draft(enclaves=[DraftEnclave(slug="meridian-bank", prefix="mb")], player=bad))
            self.assertEqual(found, ["A team name is used twice: Blue", "Team Blue has applications the view doesn't: Nope",
                                     "The default team Green isn't one of the teams", "lum-chi: no team Gold",
                                     "a: the draft has no enclave instance xx", "mb: its spec has no host nohost", "mb: its spec has no site web01"])
            with self.assertRaises(rollout.DraftError):
                rollout.put_draft(DraftUpdate(player=bad))
            rules = view(assign=[PlayerRule(scope="base", teams=["Gold"]), PlayerRule(scope="base", teams=[]), PlayerRule(scope="enclave:zz", teams=["Blue"])])
            self.assertEqual(player.problems(Draft(player=rules)), ["Two rules for base", "base: no team Gold", "enclave:zz: the draft has no enclave instance zz"])


class Rollout(test_rollout.Simulated):
    deployed = True

    def setUp(self):
        super().setUp()
        p = patch.object(player, "enabled", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test_a_view_is_made_last_and_removed_first(self):
        store.put_slice(test_rollout.BANK)
        rollout.put_draft(DraftUpdate(base=DraftBase(version=VERSION, scope="full"), enclaves=[DraftEnclave(slug="meridian-bank", prefix="mb")],
                                      player=view(guests=[PlayerGuest(name="lum-chi", teams=["Blue"])])))
        found = rollout.plan()
        self.assertEqual((found.stages, found.player.action, found.player.teams), (["create:mb", "player"], "create", 2))
        job = self.run_job(rollout.submit(found))
        self.assertEqual(job.state, "succeeded", job.error)
        self.assertEqual(job.stage_titles["player"], "Update the Player view")
        applied = player.applied()
        self.assertEqual((applied["view"], applied["team_ids"]), ("test-grayspace", {"Blue": "team-0", "Red": "team-1"}))
        self.assertIn("lum-chi", applied["guests"])
        self.assertFalse(rollout.plan().changes)
        # A new team is an update
        rollout.put_draft(DraftUpdate(base=DraftBase(version=VERSION, scope="full"), enclaves=[DraftEnclave(slug="meridian-bank", prefix="mb")],
                                      player=view(teams=[PlayerTeam(name="Blue")])))
        self.assertEqual((rollout.plan().stages, rollout.plan().player.action), (["player"], "update"))
        # No view any more: it goes before anything else
        rollout.put_draft(DraftUpdate(base=DraftBase(version=VERSION, scope="full")))
        found = rollout.plan()
        self.assertEqual((found.stages, found.player.action), (["player", "destroy:mb"], "destroy"))
        self.assertEqual(self.run_job(rollout.submit(found)).state, "succeeded")
        self.assertIsNone(player.applied())

    def test_without_the_option_a_view_is_a_problem(self):
        rollout.put_draft(DraftUpdate(player=view()))
        with patch.object(player, "enabled", return_value=False):
            found = rollout.plan()
        self.assertIsNone(found.player)
        self.assertIn("no Player option", " ".join(found.problems))

    def test_standalone_instance_jobs_update_the_view(self):
        self.assertEqual(stages.with_player(["a"]), ["a"])  # no view yet
        store.write_json(store.data_dir() / "player.json", {"view_id": "v"})
        self.assertEqual((stages.with_player(["a"]), stages.with_player(["a"], first=True)), (["a", "player"], ["player", "a"]))

    def test_the_base_modules_view_becomes_the_draft(self):
        checkout(self)
        self.assertIsNone(rollout.get_draft().player)
        (environment.base_module() / "terraform.tfstate").write_text(json.dumps({"resources": [{"type": "crucible_player_view", "name": "grayspace"}]}))
        legacy = rollout.get_draft().player
        self.assertEqual(([a.name for a in legacy.applications], [(t.name, t.role) for t in legacy.teams], legacy.default_team),
                         (["VMs"], [("Blue", "View Admin")], "Blue"))
        self.assertIn("lum-chi", {g.name for g in legacy.guests})
        self.assertEqual(rollout.plan().player.action, "create")


class Terraform(test_rollout.Simulated):
    deployed = True

    def setUp(self):
        super().setUp()
        for p in (patch.object(player, "enabled", return_value=True), patch("services.dev_simulation.enabled", return_value=False),
                  patch.object(player, "vm_ids", return_value=({"lum-chi": {"id": 101, "node": "pve", "type": "LXC"}}, ["gone"]))):
            p.start()
            self.addCleanup(p.stop)
        checkout(self)

    def view_state(self, *applications):
        (player.module() / "terraform.tfstate").write_text(json.dumps({"resources": [{"type": "crucible_player_view", "instances": [
            {"attributes": {"application": [{"name": a} for a in applications]}}]}]}))

    def test_apply_writes_the_inputs_and_refreshes(self):
        self.view_state("Chat", "VMs")
        ctx = FakeCtx({"view_id": {"value": "view-9"}, "team_ids": {"value": {"Blue": "t-b"}}})
        draft = Draft(player=view(description="Exercise", guests=[PlayerGuest(name="lum-chi", teams=["Blue"])]))
        asyncio.run(player.apply(ctx, draft))
        data = json.loads(player.tfvars_file().read_text())
        self.assertEqual(data["view"], {"name": "test-grayspace", "description": "Exercise"})
        self.assertEqual(data["vms"], {"lum-chi": {"id": 101, "node": "pve", "type": "LXC", "teams": ["Blue"]}})
        self.assertEqual(data["teams"][0], {"name": "Blue", "permissions": [], "applications": ["VMs", "Chat"]})
        applies = [argv for argv, _, _ in ctx.calls if argv[1] == "apply"]
        self.assertEqual(len(applies), 1)
        # Changes made in Player itself must reach the state: inline mode manages the whole view
        self.assertNotIn("-refresh=false", applies[0])
        self.assertIn("WARNING gone isn't on Proxmox: left out of the view", ctx.logs)
        self.assertEqual((player.applied()["view_id"], player.applied()["team_ids"]), ("view-9", {"Blue": "t-b"}))
        (player.module() / "terraform.tfstate").write_text(json.dumps({"resources": [{"type": "crucible_player_view"}]}))
        asyncio.run(player.destroy(ctx))
        destroy = [argv for argv, _, _ in ctx.calls if argv[1] == "destroy"][0]
        self.assertIn("-refresh=false", destroy)
        self.assertIsNone(player.applied())

    def test_new_applications_are_made_before_the_teams_instances_of_them(self):
        # Provider 2.7 adds an instance of an application made in the same apply with an empty ID
        self.view_state("Chat")
        ctx = FakeCtx()
        asyncio.run(player.apply(ctx, Draft(player=view(guests=[PlayerGuest(name="lum-chi", teams=["Blue"])]))))
        applies = [argv for argv, _, _ in ctx.calls if argv[1] == "apply"]
        self.assertEqual(len(applies), 2)
        self.assertIn('-var=instance_applications=["Chat"]', applies[0])
        self.assertFalse([a for a in applies[1] if a.startswith("-var=instance_applications")])
        self.assertIn("New applications VMs: creating them before the teams' instances of them", ctx.logs)

        ctx = FakeCtx()  # a first view: its applications, then the instances
        (player.module() / "terraform.tfstate").unlink()
        asyncio.run(player.apply(ctx, Draft(player=view(guests=[PlayerGuest(name="lum-chi", teams=["Blue"])]))))
        applies = [argv for argv, _, _ in ctx.calls if argv[1] == "apply"]
        self.assertEqual((len(applies), applies[0][-1]), (2, "-var=instance_applications=[]"))

    def test_the_old_view_is_destroyed_once_through_its_delete_path(self):
        alloc = live(mark=False)
        path = stages.instance_tfvars("mb")
        store.write_json(path, {"prefix": "mb", "crucible_team_id": "team-1", "crucible_guests": ["mb-web01"], "certs": {"x": 1}})
        (environment.base_module() / "terraform.tfstate").write_text(json.dumps({"resources": [{"module": "module.crucible[0]", "type": "crucible_player_view"}]}))
        ctx = FakeCtx()
        asyncio.run(player.migrate_legacy(ctx))
        slice_destroy, base_destroy = [(argv, env) for argv, _, env in ctx.calls]
        self.assertIn("-target=crucible_player_virtual_machine.guest", slice_destroy[0])
        self.assertEqual(slice_destroy[1]["TF_WORKSPACE"], "mb")
        self.assertIn("-target=module.crucible", base_destroy[0])
        self.assertIn("-refresh=false", base_destroy[0])
        self.assertEqual(base_destroy[1]["TF_VAR_configure_crucible"], "true")
        self.assertEqual(json.loads(path.read_text()), {"prefix": "mb", "certs": {"x": 1}})  # the rest of its inputs stay
        self.assertTrue(alloc.hosts)


class Routes(test_rollout.Simulated):
    deployed = True

    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(player_router.router)
        self.client = TestClient(app, headers=AUTH)

    def test_the_view_links_to_player(self):
        with patch.object(player, "enabled", return_value=True), patch.object(player, "applied", return_value={"view": "test-grayspace", "view_id": "v-1"}), \
             patch.dict("os.environ", {"TF_VAR_crucible_player_api_url": "https://crucible/player/api/"}):
            self.assertEqual(self.client.get("/api/v1/player").json()["url"], "https://crucible/player/view/v-1")
            with patch.dict("os.environ", {"TF_VAR_crucible_player_url": "https://player.example/"}):
                self.assertEqual(self.client.get("/api/v1/player").json()["url"], "https://player.example/view/v-1")
        with patch.object(player, "enabled", return_value=True):
            self.assertIsNone(self.client.get("/api/v1/player").json()["url"])  # nothing applied

    def test_off_without_the_option(self):
        with patch.object(player, "enabled", return_value=False):
            self.assertEqual(self.client.get("/api/v1/player/templates").status_code, 503)

    def test_templates_roles_and_guests_in_simulation(self):
        with patch.object(player, "enabled", return_value=True):
            self.assertIn("VMs", [t["name"] for t in self.client.get("/api/v1/player/templates").json()])
            self.assertIn("View Member", [r["name"] for r in self.client.get("/api/v1/player/roles").json()])
            guests = self.client.get("/api/v1/player/guests").json()
            self.assertEqual(self.client.get("/api/v1/player").json()["view_id"], None)
        self.assertIn("lum-chi", [g["hostname"] for g in guests["base"]])

    def test_templates_from_crucible(self):
        with patch.object(player, "enabled", return_value=True), patch("services.dev_simulation.enabled", return_value=False), \
             patch("services.crucible_api.templates", AsyncMock(return_value=[{"id": "t-1", "name": "VMs", "url": "u"}, {"name": "no id"}])):
            r = self.client.get("/api/v1/player/templates")
        self.assertEqual(r.json(), [{"id": "t-1", "name": "VMs", "url": "u", "icon": None, "embeddable": None}])


if __name__ == "__main__":
    unittest.main()
