"""GET /api/v1/core/inventory and /api/v1/core/dns (services/inventory.py), and the guest.update
events the state monitor publishes, against the golden tfvars.

Run from grayspace-web/backend: python -m unittest discover -s tests -t .
"""

import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from models.instances import CreatedDomain, CreatedRecord, InstanceRecord, SliceRef
from routers import base as base_router
from services import inventory
from services.slices import base
from tasks.state_monitor import StateMonitor, state_monitor
from tests import AUTH, REPO

GOLDEN = REPO / "tests" / "golden"
FULL = json.loads((GOLDEN / "tfvars-full.json").read_text())
TIER1 = json.loads((GOLDEN / "tfvars-tier1.json").read_text())
LINK = next(i for i in base.links(FULL) if i.startswith("lum-tok:lum-seo") or i.startswith("lum-seo:lum-tok"))


def guests(tfvars):
    return list(inventory.guest_kinds(tfvars))


def instance():
    return InstanceRecord(
        id="ie", slice=SliceRef(slug="information-environment", version="sha256:" + "0" * 64), state="ready", created="t", updated="t",
        domains=[CreatedDomain(domain="dailychronicle.com", hosting_server="dns-cloudflare", tld_server="dns-gtld")],
        records=[CreatedRecord(server="dns-cloudflare", zone="dailychronicle.com", name="www", fqdn="www.dailychronicle.com",
                               type="A", data="198.41.0.2", ttl=3600)],
    )


OVERLAY_DOMAINS = [{
    "domain": "example.org", "hosting_server": "dns-aws", "tld_server": "dns-gtld", "ip_address": "200.38.194.5",
    "mx_records": "10 mail.example.org.", "create_www": True,
    "additional_records": [{"name": "mail", "type": "A", "data": "200.38.194.6"}, {"name": "empty", "type": "A", "data": ""}],
}]
OVERLAY_RECORDS = [
    {"server": "dns-cloudflare", "zone": "dailychronicle.com", "name": "news.dailychronicle.com.", "type": "cname", "data": "www", "ttl": 600},
    {"server": "dns-gtld", "zone": "com", "name": "@", "type": "TXT", "data": "\"hello\"", "ttl": 3600},
]


class Api(unittest.TestCase):
    def get(self, path, tfvars=FULL, statuses=None, link_states=None, domains=(), records=(), instances=()):
        app = FastAPI()
        app.include_router(base_router.router)
        with patch.object(base_router.topology_service, "tfvars_data", tfvars), \
             patch.object(inventory, "proxmox_statuses", AsyncMock(return_value=statuses)), \
             patch.object(state_monitor, "previous_link_states", link_states or {}), \
             patch.object(base_router.mutation_tracker, "dns_domains", list(domains)), \
             patch.object(base_router.mutation_tracker, "dns_records", list(records)), \
             patch.object(base_router.store, "live_instances", return_value=list(instances)):
            response = TestClient(app, headers=AUTH).get(path)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class GetInventory(Api):
    def test_everything_deployed(self):
        body = self.get("/api/v1/core/inventory")
        self.assertEqual(body["version"], FULL["base"])
        self.assertEqual(body["customer_dhcp_start"], 20)
        self.assertEqual({r["hostname"] for r in body["routers"]}, set(FULL["routers"]))
        self.assertEqual({link["id"] for link in body["links"]}, set(base.links(FULL)))
        self.assertEqual(body["clients"], [])  # deprecated (contract 1.18): base clients were retired
        self.assertEqual({s["hostname"] for s in body["core_services"]}, set(FULL["core_dns"]) | set(FULL["core_ca"]))
        self.assertEqual({i["name"] for i in body["isps"]}, {r["isp_name"] for r in FULL["routers"].values()})

    def test_scope_is_what_was_deployed(self):
        body = self.get("/api/v1/core/inventory", tfvars=TIER1)
        self.assertEqual(len(body["routers"]), len(TIER1["routers"]))
        self.assertLess(len(body["routers"]), len(FULL["routers"]))

    def test_router(self):
        router = next(r for r in self.get("/api/v1/core/inventory")["routers"] if r["hostname"] == "lum-seo")
        self.assertEqual(router["isp"], "Lumen")
        self.assertEqual(router["asn"], 3356)
        self.assertEqual(router["location"], "Seoul")
        self.assertEqual(router["loopback_ip"], "6.0.1.26/32")
        self.assertEqual(router["customer_net"], "203.254.80.0/24")
        self.assertEqual(router["roles"], [])
        self.assertIn({"name": "eth0", "bridge": "v213", "ip": "172.16.0.146", "prefix_len": 30, "ospf_area": "90"}, router["interfaces"])
        self.assertIn({"name": "eth1", "bridge": "v318", "ip": "203.254.80.1", "prefix_len": 24, "ospf_area": None}, router["interfaces"])

    def test_link_has_both_ends(self):
        body = self.get("/api/v1/core/inventory", link_states={LINK: {"state": "down", "diagnostics": []}})
        link = next(item for item in body["links"] if item["id"] == LINK)
        self.assertEqual(link["subnet"], "172.16.0.144/30")
        self.assertEqual(link["bridge"], "v213")
        self.assertEqual({link["source_ip"], link["target_ip"]}, {"172.16.0.145", "172.16.0.146"})
        self.assertEqual(link["latency_ms"], 2.875)
        self.assertEqual(link["state"], "down")
        self.assertTrue(all(item["state"] == "unknown" for item in body["links"] if item["id"] != LINK))

    def test_core_services(self):
        services = {s["hostname"]: s for s in self.get("/api/v1/core/inventory")["core_services"]}
        self.assertEqual(services["root-a"]["role"], "root-dns")
        self.assertEqual(services["root-a"]["zones"], ["."])
        self.assertEqual(services["dns-gtld"]["zones"], ["com", "net", "org"])
        self.assertEqual(services["dns-google"]["ip"], "8.8.8.8")
        self.assertEqual(services["dns-google"]["role"], "resolver")
        self.assertEqual(services["ca-graytrust"]["role"], "ca")
        self.assertEqual(services["ca-graytrust"]["ca_name"], "GrayTrust")
        self.assertEqual(services["ca-graytrust"]["ip"], "63.140.38.10")
        self.assertIsNone(services["dns-google"]["ca_name"])

    def test_status(self):
        statuses = {h: "running" for h in guests(FULL)}
        statuses["lum-seo"] = "stopped"
        del statuses["dns-google"]
        body = self.get("/api/v1/core/inventory", statuses=statuses)
        status = {g["hostname"]: g["status"] for key in ("routers", "core_services") for g in body[key]}
        self.assertEqual(status["lum-seo"], "stopped")
        self.assertEqual(status["dns-google"], "missing")
        self.assertEqual(status["root-a"], "running")

    def test_status_without_proxmox_is_null(self):
        body = self.get("/api/v1/core/inventory", statuses=None)
        self.assertTrue(all(g["status"] is None for key in ("routers", "clients", "core_services") for g in body[key]))

    def test_proxmox_listing_nothing_is_unavailable(self):
        self.assertIsNone(inventory.guest_statuses([]))
        self.assertEqual(inventory.guest_statuses([{"name": "lum-seo", "status": "running"}]), {"lum-seo": "running"})


def zones(body):
    return {z["name"]: z for z in body["zones"]}


def records(zone, **match):
    return [(r["name"], r["type"], r["data"]) for r in zone["records"] if all(r[k] == v for k, v in match.items())]


class GetDns(Api):
    def test_base_zones(self):
        body = self.get("/api/v1/core/dns")
        by_name = zones(body)
        tlds = {z for s in FULL["core_dns"].values() if s["role"] == "tld" for v in s["zones"] for z in v.split(",")}
        self.assertEqual(set(by_name), {"."} | tlds)
        self.assertEqual([z["name"] for z in body["zones"]][0], ".")
        self.assertTrue(all(z["origin"] == "base" and z["instance"] is None for z in body["zones"]))
        self.assertEqual({(r["hostname"], r["ip"]) for r in body["resolvers"]},
                         {("dns-google", "8.8.8.8"), ("dns-quad9", "9.9.9.9"), ("dns-krnic", "118.221.112.10"), ("dns-yandex", "77.88.8.8")})

    def test_root_zone(self):
        root = zones(self.get("/api/v1/core/dns"))["."]
        self.assertEqual(root["kind"], "root")
        self.assertEqual({s["hostname"] for s in root["servers"]}, {"root-a", "root-k", "root-m"})
        self.assertIn((".", "NS", "root-a.root-servers.net."), records(root))
        self.assertIn(("root-a.root-servers.net.", "A", "198.41.0.4"), records(root))
        self.assertIn(("com.", "NS", "ns1.com."), records(root))
        self.assertIn(("ns1.com.", "A", "216.239.34.10"), records(root))
        self.assertTrue(all(r["ttl"] == 86400 and r["origin"] == "base" for r in root["records"]))

    def test_tld_zone(self):
        com = zones(self.get("/api/v1/core/dns"))["com"]
        self.assertEqual(com["kind"], "tld")
        self.assertEqual(com["servers"], [{"hostname": "dns-gtld", "ip": "216.239.34.10"}])
        self.assertEqual(records(com), [("com.", "NS", "ns1.com."), ("ns1.com.", "A", "216.239.34.10")])

    def test_overlay_domain(self):
        by_name = zones(self.get("/api/v1/core/dns", domains=OVERLAY_DOMAINS))
        domain = by_name["example.org"]
        self.assertEqual((domain["kind"], domain["origin"]), ("domain", "overlay"))
        self.assertEqual(domain["servers"], [{"hostname": "dns-aws", "ip": "205.251.192.123"}])
        self.assertEqual(records(domain), [
            ("example.org.", "NS", "ns1.example.org."),
            ("ns1.example.org.", "A", "205.251.192.123"),
            ("example.org.", "A", "200.38.194.5"),
            ("www.example.org.", "CNAME", "example.org."),
            ("example.org.", "MX", "10 mail.example.org."),
            ("mail.example.org.", "A", "200.38.194.6"),
        ])
        self.assertEqual(records(by_name["org"], origin="overlay"),
                         [("example.org.", "NS", "ns1.example.org."), ("ns1.example.org.", "A", "205.251.192.123")])

    def test_instance_domain_and_records(self):
        by_name = zones(self.get("/api/v1/core/dns", records=OVERLAY_RECORDS, instances=[instance()]))
        domain = by_name["dailychronicle.com"]
        self.assertEqual((domain["origin"], domain["instance"]), ("instance", "ie"))
        self.assertIn(("www.dailychronicle.com.", "A", "198.41.0.2"), records(domain, origin="instance", instance="ie"))
        self.assertEqual(records(domain, origin="overlay"), [("news.dailychronicle.com.", "CNAME", "www")])
        self.assertEqual(records(by_name["com"], instance="ie"),
                         [("dailychronicle.com.", "NS", "ns1.dailychronicle.com."), ("ns1.dailychronicle.com.", "A", "108.162.192.9")])
        self.assertIn(("com.", "TXT", "\"hello\""), records(by_name["com"], origin="overlay"))

    def test_record_in_an_unknown_zone(self):
        stray = [{"server": "dns-ovh", "zone": "Stray.Example.", "name": "host", "type": "A", "data": "1.2.3.4"}]
        zone = zones(self.get("/api/v1/core/dns", records=stray))["stray.example"]
        self.assertEqual(zone["servers"], [{"hostname": "dns-ovh", "ip": "213.186.33.99"}])
        self.assertEqual(zone["records"][0]["name"], "host.stray.example.")
        self.assertEqual(zone["records"][0]["ttl"], 3600)


class Fqdn(unittest.TestCase):
    def test_names(self):
        self.assertEqual(inventory.fqdn("@", "example.com"), "example.com.")
        self.assertEqual(inventory.fqdn("", "example.com."), "example.com.")
        self.assertEqual(inventory.fqdn("www", "example.com"), "www.example.com.")
        self.assertEqual(inventory.fqdn("WWW.Example.com.", "example.com"), "www.example.com.")
        self.assertEqual(inventory.fqdn("@", "."), ".")
        self.assertEqual(inventory.fqdn("com", "."), "com.")


class GuestEvents(unittest.IsolatedAsyncioTestCase):
    async def poll(self, monitor, statuses):
        with patch.object(inventory, "proxmox_statuses", AsyncMock(return_value=statuses)), \
             patch("tasks.state_monitor.topology_service.tfvars_data", FULL), \
             patch("tasks.state_monitor.events.guest_update") as guest, \
             patch("tasks.state_monitor.events.feature_update") as feature:
            await monitor._poll_guests()
        return guest, feature

    async def test_changes_are_published(self):
        monitor = StateMonitor()
        running = {h: "running" for h in guests(FULL)}
        guest, feature = await self.poll(monitor, running)
        guest.assert_not_called()
        changed = {**running, "lum-seo": "stopped", "root-a": "stopped"}
        del changed["dns-google"]
        guest, feature = await self.poll(monitor, changed)
        self.assertEqual(sorted(c.args for c in guest.call_args_list), [
            ("dns-google", "core-service", "missing", "running"),
            ("lum-seo", "router", "stopped", "running"),
            ("root-a", "core-service", "stopped", "running"),
        ])
        feature.assert_called_once_with("lum-seo", "router", {"status": "stopped"})

    async def test_proxmox_unavailable_changes_nothing(self):
        monitor = StateMonitor()
        running = {h: "running" for h in guests(FULL)}
        await self.poll(monitor, running)
        guest, _ = await self.poll(monitor, None)
        guest.assert_not_called()
        self.assertEqual(monitor.previous_guest_statuses, running)


if __name__ == "__main__":
    unittest.main()
