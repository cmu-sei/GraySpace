"""Platform connections (control/platform.py, platform_api.py): the site file imported as the first
connections, deployments from before connections migrated, environment files from a placement, and
the routes admins and builders use."""

import json
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from control import envfile, platform, registry
from control.models import CreateDeployment, DeploymentOptions
from control.settings import settings
from tests import AUTH
from tests.test_control_access import Base
from tests.test_control_registry import SITE, control_isolate

# Made per run, so they read as what they are: no real passwords
CRUCIBLE_PASSWORD = uuid.uuid4().hex
SSH_PASSWORD = uuid.uuid4().hex

CRUCIBLE_SITE = SITE + """export TF_VAR_configure_crucible=true
export TF_VAR_crucible_username="svc"
export TF_VAR_crucible_password="{crucible}"
export TF_VAR_crucible_token_url="https://c.test/keycloak/token"
export TF_VAR_crucible_auth_url="https://c.test/keycloak/auth"
export TF_VAR_crucible_client_id="tf"
export TF_VAR_crucible_client_scopes='["vm-api","player-api"]'
export TF_VAR_crucible_player_api_url="https://c.test/player/api"
export TF_VAR_crucible_vm_api_url="https://c.test/vm/api"
export TF_VAR_crucible_caster_api_url="https://c.test/caster/api"
export TF_VAR_crucible_user_id="u-1"
""".replace("{crucible}", CRUCIBLE_PASSWORD)


def legacy(deployment_id, proxmox=None, credentials=None, options=None):
    """A deployment record as it was before connections."""
    path = registry.directory(deployment_id)
    path.mkdir(parents=True)
    registry._write(path / "deployment.json", {"id": deployment_id, "name": deployment_id, "kind": "managed", "created": "t",
                                               "proxmox": proxmox or {}, "options": options or {}, "owner": None, "members": []})
    registry._write(path / "secrets.json", {"credentials": credentials} if credentials else {}, 0o600)


class Import(unittest.TestCase):
    def test_the_site_file_becomes_the_site_connection(self):
        tmp = control_isolate(self, site=CRUCIBLE_SITE)
        platform.import_site(lambda *_: None)
        view = platform.view()
        site = view["proxmox"][0]
        self.assertEqual((site["id"], site["host"], site["port"], site["auth"], site["secrets"], view["default_proxmox"]),
                         ("site", "pve-n1.example", 8443, "token", ["api_token"], "site"))
        self.assertEqual(site["nodes"], [{"name": "pve", "blockstore": "local-lvm"}])
        crucible = view["crucible"][0]
        self.assertEqual((crucible["id"], crucible["client_scopes"], crucible["service_user_id"], crucible["secrets"]),
                         ("site", ["vm-api", "player-api"], "u-1", ["password"]))
        # No secret value in the public file or the view; the secrets file is the server's alone
        self.assertNotIn("site@pve!t=1", (tmp / "deployments" / "platform.json").read_text())
        self.assertNotIn(CRUCIBLE_PASSWORD, json.dumps(view))
        self.assertEqual(os.stat(tmp / "deployments" / "platform-secrets.json").st_mode & 0o777, 0o600)
        # Once only
        platform.delete_crucible("site")
        platform.import_site(lambda *_: None)
        self.assertEqual(platform.view()["crucible"], [])

    def test_crucible_is_imported_only_when_it_was_on(self):
        control_isolate(self, site=CRUCIBLE_SITE.replace("TF_VAR_configure_crucible=true", "TF_VAR_configure_crucible=false"))
        platform.import_site(lambda *_: None)
        self.assertEqual(platform.view()["crucible"], [])


class Migrate(unittest.TestCase):
    def setUp(self):
        self.tmp = control_isolate(self, site=CRUCIBLE_SITE)
        platform.import_site(lambda *_: None)

    def migrate(self):
        logged = []
        platform.migrate_records(logged.append)
        return logged

    def test_the_site_host_keeps_the_site_connection_and_gains_a_node(self):
        legacy("abc")
        legacy("abd", proxmox={"node": "pve2", "blockstore": "fast"})
        self.migrate()
        self.assertEqual(registry.get("abc")["placement"], {"connection": "site", "node": "pve"})
        self.assertEqual(registry.get("abd")["placement"], {"connection": "site", "node": "pve2"})
        self.assertEqual([n["name"] for n in platform.proxmox("site")["nodes"]], ["pve", "pve2"])
        self.assertNotIn("proxmox", registry.get("abd"))
        self.assertEqual(registry.effective_env(registry.get("abd"))["TF_VAR_proxmox_blockstore"], "fast")

    def test_another_storage_on_the_same_node_gets_a_connection_of_its_own(self):
        legacy("abc", proxmox={"blockstore": "other"})
        self.migrate()
        self.assertEqual(registry.get("abc")["placement"], {"connection": "abc-pve", "node": "pve"})
        self.assertEqual(platform.proxmox("abc-pve")["api_token"], "site@pve!t=1")  # the same cluster, so its credentials
        self.assertEqual(platform.proxmox("site")["nodes"], [{"name": "pve", "blockstore": "local-lvm"}])

    def test_the_site_host_with_credentials_of_its_own_keeps_the_sites_others(self):
        """Its own token over the site's, as its environment file had: the site's SSH user and password stay."""
        site = CRUCIBLE_SITE + f'export PROXMOX_VE_SSH_USERNAME="notroot"\nexport PROXMOX_VE_SSH_PASSWORD="{SSH_PASSWORD}"\n'
        control_isolate(self, site=site)
        platform.import_site(lambda *_: None)
        legacy("own", proxmox={"host_addr": "pve-n1.example", "node": "pve"}, credentials={"api_token": "own@pve!t=9"})
        legacy("same", proxmox={"host_addr": "pve-n1.example"}, credentials={"api_token": "site@pve!t=1"})
        self.migrate()
        self.assertEqual(registry.get("same")["placement"], {"connection": "site", "node": "pve"})  # nothing of its own
        self.assertEqual(registry.get("own")["placement"]["connection"], "own-pve")
        env = registry.effective_env(registry.get("own"))
        self.assertEqual((env["PROXMOX_VE_API_TOKEN"], env["PROXMOX_VE_SSH_USERNAME"], env["PROXMOX_VE_SSH_PASSWORD"]),
                         ("own@pve!t=9", "notroot", SSH_PASSWORD))

    def test_another_cluster_takes_only_its_own_credentials(self):
        legacy("far", proxmox={"host_addr": "pve-b.example", "node": "b1"}, credentials={"api_token": "far@pve!t=2"})
        legacy("bad", proxmox={"host_addr": "pve-c.example", "node": "c1"})
        logged = self.migrate()
        self.assertEqual(platform.proxmox("far-pve")["api_token"], "far@pve!t=2")
        self.assertNotIn("credentials", registry.secrets("far"))
        env = registry.effective_env(registry.get("bad"))
        self.assertNotIn("PROXMOX_VE_API_TOKEN", env)  # the site's stays with the site
        self.assertEqual(env["TF_VAR_proxmox_host_addr"], "pve-c.example")
        self.assertIn("without credentials", registry.get("bad")["migration_warning"])
        self.assertTrue(any("without credentials" in line for line in logged))

    def test_crucible_on_at_the_site_turns_player_on(self):
        legacy("abc")
        self.migrate()
        record = registry.get("abc")
        self.assertTrue(record["options"]["player"])
        env = registry.effective_env(record)
        self.assertEqual((env["GRAYSPACE_PLAYER"], env["TF_VAR_configure_crucible"], env["TF_VAR_crucible_client_scopes"], env["TF_VAR_crucible_user_id"]),
                         ("true", "false", '["vm-api", "player-api"]', "u-1"))
        self.assertEqual(env["TF_VAR_crucible_password"], CRUCIBLE_PASSWORD)

    def test_once(self):
        legacy("abc")
        self.migrate()
        self.assertEqual(self.migrate(), [])


class Environment(unittest.TestCase):
    def test_player_off_unsets_the_sites_crucible(self):
        control_isolate(self, site=CRUCIBLE_SITE)
        platform.import_site(lambda *_: None)
        record = registry.create(CreateDeployment(id="abc", options=DeploymentOptions(player=False)))
        env = registry.effective_env(record)
        self.assertEqual((env["TF_VAR_configure_crucible"], env["GRAYSPACE_PLAYER"]), ("false", "false"))
        self.assertNotIn("TF_VAR_crucible_password", env)
        text = registry.environment_file(record).read_text()
        self.assertLess(text.index("source"), text.index("unset"))

    def test_without_connections_the_site_file_decides(self):
        control_isolate(self, site="")
        record = registry.create(CreateDeployment(id="abc"))
        self.assertIsNone(record["placement"])
        self.assertNotIn("unset", registry.environment_file(record).read_text())

    def test_simulated_deployments_from_before_get_the_simulated_connection(self):
        control_isolate(self, site="")
        legacy("dev")
        with patch.object(settings, "simulate", True):
            platform.seed_simulated()
            platform.migrate_records(lambda *_: None)
        self.assertEqual(registry.get("dev")["placement"], {"connection": "simulated", "node": "pve"})

    def test_simulated_connections(self):
        control_isolate(self, site="")
        with patch.object(settings, "simulate", True):
            platform.seed_simulated()
            record = registry.create(CreateDeployment(id="abc", options=DeploymentOptions(player=True)))
        self.assertEqual(record["placement"], {"connection": "simulated", "node": "pve"})
        self.assertEqual(envfile.load(registry.environment_file(record))["TF_VAR_crucible_player_api_url"], "http://crucible.simulated/player/api")
        self.assertNotIn("TF_VAR_crucible_player_url", envfile.load(registry.environment_file(record)))
        platform.put_crucible("simulated", {"player_url": "https://player.example"}, create=False)
        registry.write_environment(record)
        self.assertEqual(envfile.load(registry.environment_file(record))["TF_VAR_crucible_player_url"], "https://player.example")


class Routes(Base):
    def setUp(self):
        super().setUp()
        p = patch("control.api._nothing_deployed", AsyncMock())
        self.nothing_deployed = p.start()
        self.addCleanup(p.stop)

    def test_builders_read_admins_change(self):
        bob, alice = self.login("bob"), self.login("alice")
        body = self.client.get("/api/v1/platform", headers=bob).json()
        self.assertEqual([c["id"] for c in body["proxmox"]], ["site"])
        self.assertNotIn("site@pve!t=1", json.dumps(body))
        self.assertEqual(self.client.get("/api/v1/platform", headers=self.login("uma")).status_code, 403)
        new = {"id": "lab", "host": "pve-lab.example", "api_token": "lab@pve!t=3", "nodes": [{"name": "lab1", "vm_bridge": "vmbr9"}]}
        self.assertEqual(self.client.post("/api/v1/platform/proxmox", json=new, headers=bob).status_code, 403)
        r = self.client.post("/api/v1/platform/proxmox", json=new, headers=alice)
        self.assertEqual(r.status_code, 201, r.text)
        lab = next(c for c in r.json()["proxmox"] if c["id"] == "lab")
        self.assertEqual((lab["secrets"], lab["default_node"], lab["port"]), (["api_token"], "lab1", 8006))
        self.assertEqual(self.client.post("/api/v1/platform/proxmox", json=new, headers=alice).status_code, 409)
        self.assertEqual(self.client.post("/api/v1/platform/proxmox", json={**new, "id": "x", "nodes": []}, headers=alice).status_code, 422)

    def test_an_adopted_tree_shows_where_its_own_file_says(self):
        tree = registry.root().parent / "checkout"
        (tree / "terraform").mkdir(parents=True)
        (tree / ".environment").write_text(SITE.replace('"site"', '"abc"'))
        registry.adopt(tree)
        r = self.client.get("/api/v1/deployments/abc", headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["placement"], r.json()["proxmox"]["host_addr"]), ({"connection": "site", "node": None}, "pve-n1.example"))

    def test_a_used_connection_stays(self):
        alice = self.login("alice")
        r = self.client.post("/api/v1/deployments", json={"id": "d1", "placement": {"connection": "site"}}, headers=self.login("bob"))
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual((r.json()["placement"], r.json()["proxmox"]["host_addr"]), ({"connection": "site", "node": "pve"}, "pve-n1.example"))
        self.assertEqual(self.client.delete("/api/v1/platform/proxmox/site", headers=alice).status_code, 409)
        r = self.client.patch("/api/v1/platform/proxmox/site", json={"nodes": [{"name": "other"}]}, headers=alice)
        self.assertEqual(r.status_code, 409)
        self.assertIn("d1", r.json()["detail"])
        self.assertEqual(self.client.post("/api/v1/deployments", json={"id": "d2", "proxmox": {"node": "x"}}, headers=AUTH).status_code, 422)
        self.assertEqual(self.client.patch("/api/v1/deployments/d1", json={"proxmox": {"node": "x"}}, headers=AUTH).status_code, 422)

    def test_new_credentials_reach_the_deployments(self):
        self.client.post("/api/v1/deployments", json={"id": "d1"}, headers=AUTH)
        r = self.client.patch("/api/v1/platform/proxmox/site", json={"api_token": "site@pve!t=new"}, headers=self.login("alice"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(registry.effective_env(registry.get("d1"))["PROXMOX_VE_API_TOKEN"], "site@pve!t=new")
        self.nothing_deployed.assert_not_awaited()  # credentials change any time
        r = self.client.patch("/api/v1/platform/proxmox/site", json={"host": "pve-n2.example"}, headers=self.login("alice"))
        self.assertEqual(r.status_code, 200, r.text)
        self.nothing_deployed.assert_awaited()  # a move needs nothing deployed
        self.assertEqual(registry.effective_env(registry.get("d1"))["TF_VAR_proxmox_host_addr"], "pve-n2.example")
        # A secret sent empty is cleared
        self.client.patch("/api/v1/platform/proxmox/site", json={"api_token": ""}, headers=self.login("alice"))
        self.assertNotIn("PROXMOX_VE_API_TOKEN", registry.effective_env(registry.get("d1")))

    def test_a_clusters_parallelism_reaches_its_deployments(self):
        alice = self.login("alice")
        self.client.post("/api/v1/deployments", json={"id": "d1"}, headers=AUTH)
        env = registry.effective_env(registry.get("d1"))
        self.assertNotIn("GRAYSPACE_APPLY_PARALLELISM", env)  # unset: the server's default
        r = self.client.patch("/api/v1/platform/proxmox/site", json={"apply_parallelism": 5, "plan_parallelism": 40}, headers=alice)
        self.assertEqual(r.status_code, 200, r.text)
        self.nothing_deployed.assert_not_awaited()  # changes any time
        env = registry.effective_env(registry.get("d1"))
        self.assertEqual((env["GRAYSPACE_APPLY_PARALLELISM"], env["GRAYSPACE_PLAN_PARALLELISM"]), ("5", "40"))
        site = self.client.get("/api/v1/platform", headers=alice).json()["proxmox"][0]
        self.assertEqual((site["apply_parallelism"], site["plan_parallelism"]), (5, 40))
        # 0 clears one back to the server's default, and the file unsets any the site's file sets
        self.client.patch("/api/v1/platform/proxmox/site", json={"apply_parallelism": 0}, headers=alice)
        env = registry.effective_env(registry.get("d1"))
        self.assertNotIn("GRAYSPACE_APPLY_PARALLELISM", env)
        self.assertEqual(env["GRAYSPACE_PLAN_PARALLELISM"], "40")
        self.assertIn("GRAYSPACE_APPLY_PARALLELISM", registry.environment_file(registry.get("d1")).read_text())  # unset after the site's
        site = self.client.get("/api/v1/platform", headers=alice).json()["proxmox"][0]
        self.assertIsNone(site["apply_parallelism"])
        for bad in (-1, 101, "many"):
            r = self.client.patch("/api/v1/platform/proxmox/site", json={"apply_parallelism": bad}, headers=alice)
            self.assertEqual(r.status_code, 422, bad)

    def test_a_clusters_proxy_reaches_its_deployments(self):
        alice = self.login("alice")
        self.client.post("/api/v1/deployments", json={"id": "d1"}, headers=AUTH)
        site = self.client.get("/api/v1/platform", headers=alice).json()["proxmox"][0]
        self.assertEqual(site["proxy_mode"], "site")  # the default: the site file's, which isn't unset
        self.assertNotIn("TF_VAR_proxy", registry.environment_file(registry.get("d1")).read_text())
        # none: exported empty, over the site's (inventory/proxy.ini takes empty as no proxy)
        r = self.client.patch("/api/v1/platform/proxmox/site", json={"proxy_mode": "none"}, headers=alice)
        self.assertEqual(r.status_code, 200, r.text)
        self.nothing_deployed.assert_not_awaited()  # changes any time
        env = registry.effective_env(registry.get("d1"))
        self.assertEqual((env["TF_VAR_proxy"], env["TF_VAR_no_proxy"]), ("", ""))
        r = self.client.patch("/api/v1/platform/proxmox/site", json={"proxy_mode": "custom"}, headers=alice)
        self.assertEqual(r.status_code, 422, "custom needs a proxy")
        r = self.client.patch("/api/v1/platform/proxmox/site", json={"proxy_mode": "custom", "proxy": "ftp://x"}, headers=alice)
        self.assertEqual(r.status_code, 422, "an http(s) URL")
        r = self.client.patch("/api/v1/platform/proxmox/site", json={"proxy_mode": "custom", "proxy": "http://px.test:3128",
                                                                     "no_proxy": "10.0.0.0/8"}, headers=alice)
        self.assertEqual(r.status_code, 200, r.text)
        env = registry.effective_env(registry.get("d1"))
        self.assertEqual((env["TF_VAR_proxy"], env["TF_VAR_no_proxy"]), ("http://px.test:3128", "10.0.0.0/8"))
        site = self.client.get("/api/v1/platform", headers=alice).json()["proxmox"][0]
        self.assertEqual((site["proxy_mode"], site["proxy"], site["no_proxy"]), ("custom", "http://px.test:3128", "10.0.0.0/8"))
        # back to the site's: nothing exported, the URL kept for next time
        self.client.patch("/api/v1/platform/proxmox/site", json={"proxy_mode": "site"}, headers=alice)
        self.assertNotIn("TF_VAR_proxy", registry.environment_file(registry.get("d1")).read_text())
        site = self.client.get("/api/v1/platform", headers=alice).json()["proxmox"][0]
        self.assertEqual((site["proxy_mode"], site["proxy"]), ("site", "http://px.test:3128"))

    def test_crucible_connections_and_player(self):
        alice = self.login("alice")
        cru = {"id": "cru", "token_url": "https://c.test/token", "player_api_url": "https://c.test/player/api", "username": "svc",
               "password": CRUCIBLE_PASSWORD, "client_id": "tf"}
        self.assertEqual(self.client.post("/api/v1/platform/crucible", json=cru, headers=alice).status_code, 201)
        r = self.client.post("/api/v1/deployments", json={"id": "d1", "options": {"player": True}}, headers=self.login("bob"))
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(registry.effective_env(registry.get("d1"))["TF_VAR_crucible_password"], CRUCIBLE_PASSWORD)
        self.assertEqual(self.client.get("/api/v1/platform", headers=alice).json()["crucible"][0]["deployments"], ["d1"])
        self.assertEqual(self.client.delete("/api/v1/platform/crucible/cru", headers=alice).status_code, 409)
        with patch("services.crucible_api.check", AsyncMock(return_value=[("ok", "token"), ("ok", "player")])):
            r = self.client.post("/api/v1/platform/crucible/cru/test", headers=alice)
        self.assertEqual((r.json()["ok"], [c["status"] for c in r.json()["checks"]]), (True, ["ok", "ok", "warning"]))  # no service user

    def test_proxmox_test_reports_each_check(self):
        with patch("control.probes.check_proxmox", AsyncMock(return_value=[("api", "ok", "fine"), ("node pve", "error", "No node pve")])):
            r = self.client.post("/api/v1/platform/proxmox/site/test", headers=self.login("alice"))
        self.assertEqual((r.json()["ok"], [c["name"] for c in r.json()["checks"]]), (False, ["api", "node pve"]))
        self.assertEqual(self.client.post("/api/v1/platform/proxmox/nope/test", headers=self.login("alice")).status_code, 404)


class Probes(unittest.TestCase):
    def run_probe(self, conn, routes):
        import asyncio

        import httpx

        from control import probes

        seen = []

        def handler(request):
            seen.append((request.url.path, request.headers.get("authorization")))
            body = routes.get(request.url.path)
            return httpx.Response(404) if body is None else httpx.Response(200, json={"data": body})

        real = httpx.AsyncClient
        with patch.object(probes.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler))), \
             patch.object(probes, "_ssh_answers", AsyncMock(return_value=True)):
            return asyncio.run(probes.check_proxmox(conn)), seen

    def test_each_node_its_storage_and_bridges(self):
        conn = {"host": "pve.test", "auth": "token", "api_token": "a@pve!t=1",
                "nodes": [{"name": "pve", "blockstore": "local-lvm", "vm_bridge": "vmbr1"}, {"name": "gone"}, {"name": "off"}]}
        routes = {"/api2/json/version": {"version": "8.2"}, "/api2/json/nodes": [{"node": "pve", "status": "online"}, {"node": "off", "status": "offline"}],
                  "/api2/json/nodes/pve/storage": [{"storage": "local-lvm"}], "/api2/json/nodes/pve/network": [{"iface": "vmbr0", "type": "bridge"}],
                  "/api2/json/nodes/off/storage": [], "/api2/json/nodes/off/network": []}
        checks, seen = self.run_probe(conn, routes)
        self.assertEqual([(n, s) for n, s, _ in checks], [("api", "ok"), ("node pve", "error"), ("node gone", "error"), ("node off", "warning"),
                                                          ("ssh", "ok")])
        self.assertIn("bridge vmbr1", checks[1][2])
        self.assertEqual(seen[0][1], "PVEAPIToken=a@pve!t=1")

    def test_a_bridge_can_be_an_sdn_vnet_applied_on_the_node(self):
        conn = {"host": "pve.test", "auth": "token", "api_token": "a@pve!t=1",
                "nodes": [{"name": "pve", "mgmt_bridge": "pvebpt", "vm_bridge": "vmbr0"}, {"name": "pve2", "mgmt_bridge": "pvebpt"}]}
        routes = {"/api2/json/version": {"version": "8.2"},
                  "/api2/json/nodes": [{"node": "pve", "status": "online"}, {"node": "pve2", "status": "online"}],
                  "/api2/json/nodes/pve/storage": [], "/api2/json/nodes/pve/network": [{"iface": "vmbr0", "type": "bridge"}],
                  "/api2/json/nodes/pve2/storage": [], "/api2/json/nodes/pve2/network": [],
                  "/api2/json/cluster/sdn/vnets": [{"vnet": "pvebpt", "zone": "mgmt"}],
                  "/api2/json/nodes/pve/sdn/zones": [{"zone": "mgmt", "status": "available"}],
                  "/api2/json/nodes/pve2/sdn/zones": [{"zone": "mgmt", "status": "pending"}]}
        checks, _ = self.run_probe(conn, routes)
        self.assertEqual([(n, s) for n, s, _ in checks], [("api", "ok"), ("node pve", "ok"), ("node pve2", "error"), ("ssh", "ok")])
        self.assertIn("SDN vnet pvebpt applied", checks[2][2])

    def test_no_credentials(self):
        checks, seen = self.run_probe({"host": "pve.test", "auth": "token", "nodes": []}, {})
        self.assertEqual((checks[0][1], seen), ("error", []))


class PlayerOption(Base):
    def setUp(self):
        super().setUp()
        self.client.post("/api/v1/platform/crucible", json={"id": "cru", "token_url": "https://c/t", "player_api_url": "https://c/p"},
                         headers=self.login("alice"))
        self.client.post("/api/v1/deployments", json={"id": "d1"}, headers=AUTH)
        p = patch("control.api._nothing_deployed", AsyncMock(side_effect=AssertionError("not for the Player option")))
        p.start()
        self.addCleanup(p.stop)

    def test_on_any_time_off_only_without_a_view(self):
        r = self.client.patch("/api/v1/deployments/d1", json={"options": {"player": True}}, headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(registry.effective_env(registry.get("d1"))["GRAYSPACE_PLAYER"], "true")
        with patch("control.api.worker_get", AsyncMock(return_value={"view": "d1-grayspace", "view_id": "v-1"})):
            r = self.client.patch("/api/v1/deployments/d1", json={"options": {"player": False}}, headers=AUTH)
        self.assertEqual(r.status_code, 409)
        self.assertIn("deploy its draft without a Player view", r.json()["detail"])
        with patch("control.api.worker_get", AsyncMock(return_value={"view_id": None})):
            self.assertEqual(self.client.patch("/api/v1/deployments/d1", json={"options": {"player": False}}, headers=AUTH).status_code, 200)
        self.assertEqual(registry.effective_env(registry.get("d1"))["GRAYSPACE_PLAYER"], "false")
