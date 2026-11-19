"""GET /api/v1/base/versions[/{name}]: shipped base versions and their snapshots.

Runs against the repository's real bases/ directory.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import settings
from routers import base
from services import base_versions
from tests import AUTH, REPO

SHIPPED = "grayspace/2026.11"
SHA = base_versions.topology().csv_sha256(REPO / "bases" / SHIPPED)


def client():
    app = FastAPI()
    app.include_router(base.router)
    return TestClient(app, headers=AUTH)


class ListVersions(unittest.TestCase):
    def test_lists_shipped_version(self):
        with patch.object(base_versions.topology_service, "tfvars_data", {}):
            body = client().get("/api/v1/base/versions").json()
        version = next(v for v in body if v["name"] == SHIPPED)
        self.assertEqual(version["csv_sha256"], SHA)
        self.assertFalse(version["deployed"])
        self.assertEqual(version["problems"], [])
        self.assertEqual(
            version["summary"],
            {"isps": 15, "locations": 76, "routers": 134, "links": 218, "core_services": 22, "clients": 0},
        )

    def test_marks_deployed_version(self):
        tfvars = {"base": {"name": SHIPPED, "csv_sha256": SHA, "scope": "full"}}
        with patch.object(base_versions.topology_service, "tfvars_data", tfvars):
            body = client().get("/api/v1/base/versions").json()
        self.assertTrue(next(v for v in body if v["name"] == SHIPPED)["deployed"])

    def test_invalid_version_is_listed_with_problems(self):
        bases = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, bases)
        shutil.copytree(REPO / "bases" / SHIPPED, bases / "local" / "broken")
        (bases / "local" / "broken" / "hosted_by.csv").unlink()
        with patch.object(settings, "bases_path", str(bases)):
            body = client().get("/api/v1/base/versions").json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["name"], "local/broken")
        self.assertIsNone(body[0]["summary"])
        self.assertEqual(len(body[0]["problems"]), 1)
        self.assertIn("missing hosted_by.csv", body[0]["problems"][0])


class GetVersion(unittest.TestCase):
    def get(self, name):
        with patch.object(base_versions.topology_service, "tfvars_data", {}):
            return client().get(f"/api/v1/base/versions/{name}")

    def test_snapshot(self):
        body = self.get(SHIPPED).json()
        self.assertEqual(len(body["routers"]), 134)
        self.assertEqual(len(body["links"]), 218)
        lumen = next(i for i in body["isps"] if i["name"] == "Lumen")
        self.assertEqual(lumen, {"name": "Lumen", "asn": 3356, "public_block": "6.0.0.0/16"})
        sfr = next(r for r in body["routers"] if r["hostname"] == "lum-sfr")
        self.assertEqual((sfr["isp"], sfr["location"]), ("Lumen", "San Francisco"))
        # The CSVs' "0,10" array quirk is split for the API.
        self.assertEqual(sfr["ospf_areas"], ["0", "10"])
        self.assertEqual({link["type"] for link in body["links"]}, {"INTERNAL_LINK", "PEERS_WITH", "PROVIDES_TRANSIT_TO"})

    def test_unknown_version_is_404(self):
        self.assertEqual(self.get("grayspace/1999.01").status_code, 404)

    def test_path_outside_bases_is_404(self):
        self.assertEqual(self.get("../scripts").status_code, 404)
        self.assertEqual(self.get("..%2F..%2Fetc").status_code, 404)


if __name__ == "__main__":
    unittest.main()
