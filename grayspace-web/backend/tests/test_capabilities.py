"""GET /api/v1/capabilities and /attachment-points, against the real bases/ directory."""

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import capabilities as capabilities_router
from services import base_versions, capabilities
from tests import AUTH, REPO

SHIPPED = "grayspace/2026.11"
SHA = base_versions.topology().csv_sha256(REPO / "bases" / SHIPPED)
NODE = {"cpu": 0.25, "cpuinfo": {"cpus": 64}, "memory": {"total": 512, "free": 256}}
STORAGE = {"total": 4000, "used": 1000, "avail": 3000}


def deployed(scope):
    return {"routers": {}, "base": {"name": SHIPPED, "csv_sha256": SHA, "scope": scope}}


class Api(unittest.TestCase):
    """The checkout's own .environment (a deployed GraySpace) must not leak in: no environment file."""

    def get(self, path, tfvars=None, node=NODE, blockstore=None, storage=STORAGE):
        app = FastAPI()
        app.include_router(capabilities_router.router)
        tfvars = deployed("full") if tfvars is None else tfvars
        with patch.object(base_versions.topology_service, "tfvars_data", tfvars), \
             patch.object(capabilities_router.topology_service, "tfvars_data", tfvars), \
             patch.object(capabilities.settings, "proxmox_blockstore", blockstore), \
             patch.object(capabilities.environment, "environment_file", return_value=None), \
             patch.object(capabilities.proxmox_client, "get_storage_status", return_value=storage), \
             patch.object(capabilities.proxmox_client, "get_node_status", return_value=node):
            return TestClient(app, headers=AUTH).get(path)


class GetCapabilities(Api):
    def test_memory_free_counts_reclaimable_cache(self):
        node = {**NODE, "memory": {"total": 512, "free": 64, "available": 400}}
        self.assertEqual(self.get("/api/v1/capabilities", node=node).json()["capacity"]["memory_free"], 400)

    def test_deployed_full(self):
        body = self.get("/api/v1/capabilities").json()
        self.assertEqual(body["base"], {"name": SHIPPED, "csv_sha256": SHA, "scope": "full"})
        self.assertEqual({i["name"] for i in body["images"]}, {"grayspace-alpine", "grayspace-desktop", "grayspace-ubuntu-vm", "grayspace-ubuntu-lxc", "ghosts-pandora"})
        self.assertEqual(len(body["dns"]["tlds"]), 16)
        self.assertIn("com", body["dns"]["tlds"])
        self.assertNotIn(".", body["dns"]["tlds"])
        self.assertIn({"hostname": "dns-cloudflare", "ip": "108.162.192.9", "router": "are-sfr", "isp": "Arelion"}, body["dns"]["hosting"])
        self.assertIn({"hostname": "dns-google", "ip": "8.8.8.8", "router": "lum-sfr", "isp": "Lumen"}, body["dns"]["resolvers"])
        self.assertIn("63.140.38.10", {ca["ip"] for ca in body["cas"]})
        self.assertEqual(len(body["dns"]["hosting"]), 5)
        self.assertEqual(len(body["dns"]["resolvers"]), 4)
        self.assertEqual({ca["name"] for ca in body["cas"]}, {"GrayTrust", "SecureSign", "NetCert", "CyberLock"})
        self.assertEqual(len(body["isps"]), 15)
        self.assertEqual(body["capacity"], {"node": "pve", "cpus": 64, "cpu_usage": 0.25, "memory_total": 512, "memory_free": 256,
                                            "storage": None, "storage_total": None, "storage_free": None})

    def test_slice_capabilities(self):
        body = self.get("/api/v1/capabilities").json()
        self.assertEqual(body["slice_features"], ["nat-site", "published-host", "internet-host", "routed-site", "dual-homed-site",
                                                  "oci-host", "vm-host", "link-conditions", "crucible-player"])
        self.assertEqual({r["name"] for r in body["roles"]}, {"web", "ghosts-client", "mail", "dns", "file", "ad-dc"})
        ghosts = next(r for r in body["roles"] if r["name"] == "ghosts-client")
        self.assertTrue(ghosts["variables"]["api_url"]["required"])
        self.assertIn("windows", ghosts["os_families"])
        pandora = next(i for i in body["images"] if i["name"] == "ghosts-pandora")
        self.assertEqual(pandora["features"], ["tls-kestrel"])
        self.assertEqual(body["management_subnet"], "10.0.0.0/16")
        self.assertEqual(body["slice_pool"], "192.168.0.0/16")
        self.assertIs(body["jobs"], False)
        self.assertIs(body["crucible"], False)  # no base state, so no Player view
        self.assertEqual(body["enclave_features"], body["slice_features"])  # 1.5: enclave names beside the deprecated ones
        self.assertEqual(body["enclave_pool"], body["slice_pool"])
        self.assertTrue(body["jobs_unavailable_reason"])

    def test_storage_capacity(self):
        body = self.get("/api/v1/capabilities", blockstore="local-lvm").json()
        self.assertEqual(body["capacity"]["storage"], "local-lvm")
        self.assertEqual(body["capacity"]["storage_total"], 4000)
        self.assertEqual(body["capacity"]["storage_free"], 3000)

    def test_tier1_scope_hides_undeployed_isps(self):
        body = self.get("/api/v1/capabilities", tfvars=deployed("tier1")).json()
        self.assertEqual({i["name"] for i in body["isps"]}, {"Lumen", "NTT", "Arelion"})
        self.assertEqual(body["base"]["scope"], "tier1")
        self.assertEqual(len(body["cas"]), 1)
        for server in body["dns"]["hosting"] + body["dns"]["resolvers"] + body["cas"]:
            self.assertIn(server["isp"], {"Lumen", "NTT", "Arelion"})

    def test_proxmox_unavailable(self):
        body = self.get("/api/v1/capabilities", node=None).json()
        self.assertIsNone(body["capacity"])

    def test_named_version_defaults_to_full(self):
        body = self.get(f"/api/v1/capabilities?version={SHIPPED}", tfvars=deployed("tier1")).json()
        self.assertEqual(body["base"]["scope"], "full")

    def test_unknown_deployed_version_is_409(self):
        self.assertEqual(self.get("/api/v1/capabilities", tfvars={"routers": {}}).status_code, 409)

    def test_unknown_named_version_is_404(self):
        self.assertEqual(self.get("/api/v1/capabilities?version=grayspace/1999.01").status_code, 404)


class GetAttachmentPoints(Api):
    def test_only_routers_with_customer_networks(self):
        # 105 of the 134 routers have a customer network (C3).
        body = self.get("/api/v1/attachment-points").json()
        self.assertEqual(len(body), 105)
        self.assertTrue(all(p["customer_net"] for p in body))

    def test_tier1_scope(self):
        body = self.get("/api/v1/attachment-points", tfvars=deployed("tier1")).json()
        self.assertEqual(len(body), 53)

    def test_free_static_addresses(self):
        body = {p["router"]: p for p in self.get("/api/v1/attachment-points").json()}
        # .2-.19: 18 addresses, less what the base places there
        self.assertEqual(body["lum-chi"]["free_static_addresses"], 18)
        self.assertEqual(body["lum-ash"]["free_static_addresses"], 17)  # dns-gov is 156.154.100.2
        self.assertEqual(body["are-sfr"]["free_static_addresses"], 17)  # dns-cloudflare is 108.162.192.9

    def test_filter_by_location(self):
        body = self.get("/api/v1/attachment-points?location=Chicago").json()
        self.assertTrue(body)
        self.assertEqual({p["location"] for p in body}, {"Chicago"})
        self.assertTrue({"lum-chi", "are-chi"} <= {p["router"] for p in body})

    def test_filter_by_isp(self):
        body = self.get("/api/v1/attachment-points?isp=Lumen").json()
        self.assertEqual({p["isp"] for p in body}, {"Lumen"})

    def test_address_space(self):
        body = self.get("/api/v1/attachment-points?isp=Lumen").json()
        space = body[0]["address_space"]
        self.assertEqual(space["public_block"], "6.0.0.0/16")
        # Lumen's /16 holds its links and loopbacks, 50 addresses in all.
        self.assertEqual(space["free_addresses"], 65536 - 50)
        self.assertEqual(space["largest_free_prefix"], "6.0.128.0/17")


class FreeSpace(unittest.TestCase):
    def test_overlapping_networks(self):
        import ipaddress

        block = ipaddress.ip_network("10.0.0.0/24")
        used = [ipaddress.ip_network("10.0.0.0/30"), ipaddress.ip_network("10.0.0.0/25"), ipaddress.ip_network("192.168.0.0/16")]
        free = capabilities._free(block, used)
        self.assertEqual(free, [ipaddress.ip_network("10.0.0.128/25")])
        self.assertEqual(capabilities._free(block, [ipaddress.ip_network("10.0.0.0/8")]), [])


if __name__ == "__main__":
    unittest.main()
