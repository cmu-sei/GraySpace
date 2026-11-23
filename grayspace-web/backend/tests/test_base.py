"""GET /api/v1/core: the deployed base version and its health.

Run from grayspace-web/backend: python -m unittest discover -s tests -t .
"""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import base
from services import base_service
from tests import AUTH

TFVARS = {
    "routers": {"lum-sfr": {}, "lum-chi": {}},
    "base": {"name": "grayspace/2026.11", "csv_sha256": "ab" * 32, "scope": "full"},
}
RUNNING = [
    {"name": "lum-sfr", "status": "running"},
    {"name": "lum-chi", "status": "running"},
    {"name": "prometheus", "status": "running"},
]
UP = [
    {"router": "lum-sfr", "peer_address": "6.0.0.2", "state": "up"},
    {"router": "lum-chi", "peer_address": "6.0.0.1", "state": "up"},
]


class GetBase(unittest.TestCase):
    def get(self, tfvars=TFVARS, containers=RUNNING, peers=UP):
        app = FastAPI()
        app.include_router(base.router)
        proxmox = containers if callable(containers) else (lambda: containers)
        with patch.object(base_service.topology_service, "tfvars_data", tfvars), \
             patch.object(base_service.proxmox_client, "get_deployment_containers", side_effect=proxmox), \
             patch.object(base_service.prometheus_client, "get_bgp_peer_states", AsyncMock(return_value=peers)):
            response = TestClient(app, headers=AUTH).get("/api/v1/core")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_healthy(self):
        body = self.get()
        self.assertEqual(body["status"], "healthy")
        self.assertEqual(body["version"], TFVARS["base"])
        self.assertEqual(body["routers"], {"expected": 2, "running": 2, "not_running": []})
        self.assertEqual(body["bgp"], {"total": 2, "established": 2, "not_established": []})

    def test_router_down_is_degraded(self):
        body = self.get(containers=[{"name": "lum-sfr", "status": "running"}, {"name": "lum-chi", "status": "stopped"}])
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["routers"], {"expected": 2, "running": 1, "not_running": ["lum-chi"]})

    def test_missing_router_is_degraded(self):
        body = self.get(containers=[{"name": "lum-sfr", "status": "running"}])
        self.assertEqual(body["routers"]["not_running"], ["lum-chi"])
        self.assertEqual(body["status"], "degraded")

    def test_bgp_session_down_is_degraded(self):
        peers = [UP[0], {"router": "lum-chi", "peer_address": "6.0.0.1", "state": "down"}]
        body = self.get(peers=peers)
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["bgp"]["established"], 1)
        self.assertEqual(body["bgp"]["not_established"], [{"router": "lum-chi", "peer_address": "6.0.0.1", "state": "down"}])

    def test_proxmox_unavailable_is_unknown(self):
        def fail():
            raise ConnectionError("no route to host")

        body = self.get(containers=fail)
        self.assertEqual(body["status"], "unknown")
        self.assertIsNone(body["routers"]["running"])

    def test_prometheus_unavailable_is_unknown(self):
        body = self.get(peers=[])
        self.assertEqual(body["status"], "unknown")
        self.assertIsNone(body["bgp"]["total"])

    def test_tfvars_without_version(self):
        body = self.get(tfvars={"routers": TFVARS["routers"]})
        self.assertIsNone(body["version"])
        self.assertEqual(body["status"], "healthy")

    def test_no_tfvars_is_not_deployed(self):
        body = self.get(tfvars={})
        self.assertEqual(body["status"], "not_deployed")
        self.assertEqual(body["routers"]["expected"], 0)

    def test_schema_is_published(self):
        app = FastAPI()
        app.include_router(base.router)
        schema = TestClient(app, headers=AUTH).get("/openapi.json").json()
        ref = schema["paths"]["/api/v1/core"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(ref, {"$ref": "#/components/schemas/DeployedBase"})


if __name__ == "__main__":
    unittest.main()
