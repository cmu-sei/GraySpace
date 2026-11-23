"""The map layer (services/map_layer.py, GET /api/v1/map): valid RFC 7946 geometry, stable ids,
colors in the base's ISP order, and slice features."""

import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from models.instances import CreatedRecord, InstanceRecord, SliceRef
from routers import map as map_router
from services import base_versions, map_layer
from services.slices import base
from tests import AUTH, REPO
from tests.test_slice_render import allocate

TFVARS = json.loads((REPO / "tests" / "golden" / "tfvars-full.json").read_text())
LINK = next(i for i in base.links(TFVARS) if i.startswith("lum-chi:"))
ISPS = [i.id for i in base_versions.topology().load(REPO / "bases" / "grayspace" / "2026.11").of("ISP")]


def instances():
    out = []
    for example, iid in (("information-environment", "ie"), ("routed-campus", "rc")):
        _, alloc = allocate(example)
        records = [CreatedRecord(server="dns-cloudflare", zone="dailychronicle.com", name="www", fqdn="www.dailychronicle.com",
                                 type="A", data="198.41.0.2", ttl=3600)] if iid == "ie" else []
        out.append(InstanceRecord(id=iid, slice=SliceRef(slug=example, version="sha256:" + "0" * 64), state="ready", created="t",
                                  updated="t", allocation=alloc, records=records))
    return out


def build(**kwargs):
    return map_layer.build(TFVARS, ISPS, {LINK: {"state": "down"}}, {"lum-chi": "running"}, instances(), **kwargs)


def positions(geometry):
    if geometry["type"] == "Point":
        return [geometry["coordinates"]]
    if geometry["type"] == "LineString":
        return geometry["coordinates"]
    return [p for part in geometry["coordinates"] for p in part]


def lines(geometry):
    return [geometry["coordinates"]] if geometry["type"] == "LineString" else geometry["coordinates"]


class Rfc7946(unittest.TestCase):
    def setUp(self):
        self.fc = build()

    def test_structure(self):
        self.assertEqual(self.fc["type"], "FeatureCollection")
        ids = [f["id"] for f in self.fc["features"]]
        self.assertEqual(len(ids), len(set(ids)), "feature ids are unique")
        for f in self.fc["features"]:
            self.assertEqual(f["type"], "Feature")
            self.assertEqual(f["properties"]["id"], f["id"])
            self.assertIn(f["properties"]["kind"], {"router", "link", "site", "internet-host", "attachment"})
            self.assertIn(f["geometry"]["type"], {"Point", "LineString", "MultiLineString"})

    def test_coordinates_in_range(self):
        for f in self.fc["features"]:
            for lon, lat in positions(f["geometry"]):
                self.assertTrue(-180 <= lon <= 180 and -90 <= lat <= 90, (f["id"], lon, lat))

    def test_no_segment_jumps_the_meridian(self):
        crossing = 0
        for f in self.fc["features"]:
            if f["geometry"]["type"] == "Point":
                continue
            for line in lines(f["geometry"]):
                self.assertGreaterEqual(len(line), 2)
                for (a, _), (b, _) in zip(line, line[1:]):
                    self.assertLessEqual(abs(b - a), 180, f["id"])
            if f["geometry"]["type"] == "MultiLineString":
                crossing += 1
                parts = f["geometry"]["coordinates"]
                # Each part ends or starts on the meridian, at the same latitude on both sides
                for left, right in zip(parts, parts[1:]):
                    self.assertEqual(abs(left[-1][0]), 180)
                    self.assertEqual(left[-1][0], -right[0][0])
                    self.assertEqual(left[-1][1], right[0][1])
        self.assertGreater(crossing, 0, "the base has trans-Pacific links that cross the meridian")

    def test_links_are_densified_great_circles(self):
        link = next(f for f in self.fc["features"] if f["properties"]["kind"] == "link" and f["geometry"]["type"] == "LineString"
                    and len(f["geometry"]["coordinates"]) > 10)
        coords = link["geometry"]["coordinates"]
        self.assertGreater(len(coords), 10)

    def test_great_circle_and_split(self):
        line = map_layer.great_circle((170.0, 10.0), (-170.0, 10.0))
        parts = map_layer.split_antimeridian(line)
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0][-1][0], 180.0)
        self.assertEqual(parts[1][0][0], -180.0)
        self.assertGreater(parts[0][-1][1], 10.0)  # the great circle bows toward the pole
        self.assertEqual(map_layer.great_circle((1.0, 1.0), (1.0, 1.0)), [[1.0, 1.0], [1.0, 1.0]])


class Properties(unittest.TestCase):
    def setUp(self):
        self.fc = build()
        self.by_id = {f["id"]: f for f in self.fc["features"]}

    def test_routers(self):
        r = self.by_id["lum-chi"]["properties"]
        self.assertEqual((r["kind"], r["isp"], r["asn"], r["location"], r["status"]), ("router", "Lumen", 3356, "Chicago", "running"))
        self.assertEqual(r["color"], map_layer.ISP_COLORS[ISPS.index("Lumen")])
        self.assertEqual(self.by_id["are-chi"]["properties"]["status"], "missing")
        # Fanned out: the four Chicago routers don't share a point
        chicago = {tuple(self.by_id[h]["geometry"]["coordinates"]) for h in ("lum-chi", "are-chi", "ntt-chi", "com-chi")}
        self.assertEqual(len(chicago), 4)

    def test_links(self):
        down = self.by_id[LINK]["properties"]
        self.assertEqual((down["state"], down["color"], down["type"]), ("down", "#e74c3c", "INTERNAL_LINK"))
        kinds = {f["properties"]["type"] for f in self.fc["features"] if f["properties"]["kind"] == "link"}
        self.assertEqual(kinds, {"INTERNAL_LINK", "PEERS_WITH", "PROVIDES_TRANSIT_TO"})
        self.assertEqual(sum(1 for f in self.fc["features"] if f["properties"]["kind"] == "link"), 218)

    def test_slice_features(self):
        site = self.by_id["rc/campus"]["properties"]
        self.assertEqual((site["kind"], site["instance"], site["router"], site["routers"], site["wan_ip"]),
                         ("site", "rc", "lum-chi", ["lum-chi", "are-chi"], "209.151.130.2"))
        self.assertIn("rc/campus:lum-chi", self.by_id)
        self.assertIn("rc/campus:are-chi", self.by_id)
        news = self.by_id["ie/news"]["properties"]
        self.assertEqual((news["kind"], news["router"], news["fqdns"]), ("internet-host", "are-ash", ["www.dailychronicle.com"]))
        self.assertEqual(self.by_id["ie/news:are-ash"]["properties"]["kind"], "attachment")

    def test_filter_by_instance(self):
        ids = {f["id"] for f in build(instance="ie")["features"]}
        self.assertIn("ie/news", ids)
        self.assertNotIn("rc/campus", ids)
        self.assertIn("lum-chi", ids)

    def test_proxmox_unavailable(self):
        fc = map_layer.build(TFVARS, ISPS, {}, None, [])
        self.assertEqual({f["properties"]["status"] for f in fc["features"] if f["properties"]["kind"] == "router"}, {"unknown"})


class Endpoint(unittest.TestCase):
    def test_get_map(self):
        app = FastAPI()
        app.include_router(map_router.router)
        sha = base_versions.topology().csv_sha256(REPO / "bases" / "grayspace" / "2026.11")
        tfvars = {**TFVARS, "base": {"name": "grayspace/2026.11", "csv_sha256": sha, "scope": "full"}}
        with patch.object(map_router.topology_service, "tfvars_data", tfvars), \
             patch.object(map_router.proxmox_client, "get_deployment_containers", return_value=[{"name": "lum-chi", "status": "running"}]), \
             patch.object(map_router.store, "live_instances", return_value=instances()):
            response = TestClient(app, headers=AUTH).get("/api/v1/map?instance=rc")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["type"], "FeatureCollection")
        self.assertIn("rc/campus", {f["id"] for f in body["features"]})
        self.assertNotIn("ie/news", {f["id"] for f in body["features"]})

    def test_preview_a_version_before_it_is_deployed(self):
        app = FastAPI()
        app.include_router(map_router.router)
        client = TestClient(app, headers=AUTH)
        with patch.object(map_router.store, "live_instances", side_effect=AssertionError("no enclaves in a preview")):
            body = client.get("/api/v1/core/preview?version=grayspace/2026.11&scope=tier1").json()
            full = client.get("/api/v1/core/preview?version=grayspace/2026.11").json()
        routers = [f for f in body["features"] if f["properties"]["kind"] == "router"]
        self.assertTrue(routers)
        self.assertLess(len(routers), len([f for f in full["features"] if f["properties"]["kind"] == "router"]))
        self.assertEqual({f["properties"]["status"] for f in routers}, {"unknown"})
        self.assertEqual(client.get("/api/v1/core/preview?version=nope/1").status_code, 404)


if __name__ == "__main__":
    unittest.main()
