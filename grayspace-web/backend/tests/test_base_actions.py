"""Base changes as actions (services/base_changes.py): the web UI's routes, POST /base/actions and
presets make each change through the same handler, so they check, track and answer alike."""

from unittest.mock import AsyncMock, MagicMock, patch

from models.network import AddDNSRecordResult, CreateDomainResult, SDNApplyResult
from models.presets import PresetAction
from routers import base_actions, dns, interface_zones
from services import base_changes
from services.mutation_tracker import mutation_tracker
from tests import test_slice_api

TFVARS = test_slice_api.TFVARS
ROUTER, OTHER = [h for h, r in TFVARS["routers"].items() if r.get("customer_net")][:2]


class Actions(test_slice_api.Api):
    # Actions' helpers without its tests
    ready, link = test_slice_api.Actions.ready, test_slice_api.Actions.link

    def setUp(self):
        super().setUp()
        for r in (base_actions, dns, interface_zones):
            self.client.app.include_router(r.router)
        self.proxmox = MagicMock()
        self.proxmox.get_container_by_hostname.return_value = {"name": ROUTER, "resource_type": "lxc"}
        for p in (patch("services.topology_service.topology_service.tfvars_data", TFVARS),
                  patch("services.topology_service.topology_service.get_deployed_hostnames", return_value={ROUTER, OTHER}),
                  patch("services.proxmox_client.proxmox_client", self.proxmox),
                  patch("services.base_changes.asyncio.sleep", AsyncMock()),
                  patch.object(mutation_tracker, "traffic_configs", {}),
                  patch.object(mutation_tracker, "zone_changes", {}), patch.object(mutation_tracker, "changes", [])):
            p.start()
            self.addCleanup(p.stop)

    def post(self, action, target=None, **params):
        return self.client.post("/api/v1/core/actions", json={"action": action, "target": target, "params": params})

    def test_changing_a_zone_twice_keeps_the_first_original(self):
        key = f"{ROUTER}:eth1"
        zones = iter(["z0", "z1"])  # what the interface is on before each change

        def info(*_):
            return MagicMock(zone=next(zones), vlan_tag=None)

        with patch("services.network_service.network_service.get_customer_interface_network_info", side_effect=info), \
             patch("services.network_service.network_service.change_customer_interface_zone", return_value=SDNApplyResult(success=True, message="moved")):
            self.post("interface.zone", key, zone_id="z1")
            self.post("interface.zone", key, zone_id="z2")
            self.assertEqual((mutation_tracker.zone_changes[key]["zone_id"], mutation_tracker.zone_changes[key]["original_zone_id"]), ("z2", "z0"))
            zones = iter(["z2"])
            self.post("interface.zone", key, zone_id="z0")
        self.assertNotIn(key, mutation_tracker.zone_changes)

    def test_client_moves_are_gone(self):
        """Base clients were retired (contract 1.18): a client.move is no action, and a stored preset
        that has one names it among its problems."""
        r = self.post("client.move", "lum-svc", router=ROUTER)
        self.assertEqual(r.status_code, 422)
        self.assertIn("isn't a core action", r.json()["detail"])
        found = base_changes.problems([PresetAction(action="client.move", target="lum-svc", params={"router": ROUTER})], TFVARS)
        self.assertEqual(found, ["client.move lum-svc: core clients were retired (contract 1.18)"])

    def test_a_preset_checks_what_the_route_checks(self):
        self.proxmox.get_container_by_hostname.return_value = None
        r = self.post("interface.zone", f"{ROUTER}:eth9", zone_id="z1")
        self.assertEqual((r.status_code, r.json()["detail"]), (404, f"Container {ROUTER} not found or not in this deployment"))

    def test_params_are_checked_as_the_routes_check_bodies(self):
        link = self.link()
        self.assertEqual(self.post("link.condition", link, latency_ms=-1).status_code, 422)
        self.assertEqual(self.post("link.condition", link, colour=1).status_code, 422)
        self.assertEqual(self.post("interface.zone", f"{ROUTER}:eth1", zone_id="z", vlan_tag=5000).status_code, 422)
        self.assertEqual(self.post("host.stop", "x").status_code, 422)
        self.assertEqual(self.post("dns.record.set", None, server="nope", zone="com", name="a", type="A", data="1.2.3.4").status_code, 404)
        dns_server = next(iter(TFVARS["core_dns"]))
        r = self.post("dns.record.set", None, server=dns_server, zone="com", name="a", type="BOGUS", data="x")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.post("dns.domain.create", None, domain="not a domain").status_code, 400)

    def test_links_from_the_route_and_as_actions(self):
        link = self.link()
        tc = AsyncMock(return_value=(True, "applied"))
        with patch("services.traffic_service.traffic_service.apply_link_traffic_config", tc):
            by_route = self.client.put(f"/api/v1/links/{link}/traffic", json={"latency_ms": 80})
            by_action = self.post("link.condition", link, latency_ms=80)
        self.assertEqual(by_route.status_code, 200, by_route.text)
        self.assertEqual(by_action.json(), by_route.json())
        self.assertEqual(mutation_tracker.traffic_configs[link]["latency_ms"], 80)
        with patch("services.traffic_service.traffic_service.clear_link_traffic_config", AsyncMock(return_value=(True, "cleared"))):
            self.assertEqual(self.client.delete(f"/api/v1/links/{link}/traffic").json()["message"], "cleared")
        self.assertEqual(mutation_tracker.traffic_configs, {})
        self.assertEqual(self.post("link.condition", "nope:nada", latency_ms=1).status_code, 404)

    def test_zones_and_dns_answer_with_the_service_result(self):
        info = MagicMock(zone="z0", vlan_tag=None)
        with patch("services.network_service.network_service.get_customer_interface_network_info", return_value=info), \
             patch("services.network_service.network_service.change_customer_interface_zone", return_value=SDNApplyResult(success=True, message="moved")):
            r = self.client.put(f"/api/v1/router/{ROUTER}/interface/eth1/zone", json={"zone_id": "z1", "vlan_tag": 7})
        self.assertEqual(r.json(), {"success": True, "message": "moved", "warnings": []})
        self.assertEqual(mutation_tracker.zone_changes[f"{ROUTER}:eth1"]["original_zone_id"], "z0")
        dns_server = next(iter(TFVARS["core_dns"]))
        with patch("services.dns_service.dns_service.add_record", AsyncMock(return_value=AddDNSRecordResult(success=True, message="added"))) as add, \
             patch("services.dns_service.dns_service.create_domain", AsyncMock(return_value=CreateDomainResult(success=True, message="made"))) as make:
            self.assertEqual(self.client.post(f"/api/v1/dns/{dns_server}/records", json={"zone": "com", "name": "x", "type": "a", "data": "1.2.3.4"}).json()["message"], "added")
            self.assertEqual(self.client.post("/api/v1/dns/domains", json={"domain": "example.com."}).json()["message"], "made")
        self.assertEqual(add.await_args.kwargs["rtype"], "A")
        self.assertEqual(make.await_args.kwargs["domain"], "example.com")


