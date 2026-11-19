"""The development router-metrics simulator (aspire/simulator/router_metrics.py)."""

import json
import re
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "aspire" / "simulator"))

import router_metrics  # noqa: E402

TFVARS = ROOT / "tests" / "golden" / "tfvars-full.json"
SAMPLE = re.compile(r'^(\w+)\{(.*)\} (\S+)$')


def parse(text):
    out = []
    for line in text.splitlines():
        if line.startswith("#") or not line:
            continue
        m = SAMPLE.match(line)
        assert m, line
        labels = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2)))
        out.append((m.group(1), labels, float(m.group(3))))
    return out


class Simulator(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp()) / "stopped.json"
        self.server = router_metrics.serve(str(TFVARS), 0, str(self.state), host="127.0.0.1")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tfvars = json.loads(TFVARS.read_text())

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.read().decode()

    def post(self, path):
        with urllib.request.urlopen(urllib.request.Request(self.base + path, method="POST")) as r:
            return json.loads(r.read())

    def link(self):
        """A link with OSPF (internal) between two routers."""
        return next(i for i, link in self.server.network.links.items() if link["ospf_area"] is not None)

    def test_everything_up(self):
        frr = parse(self.get("/metrics/frr"))
        bgp = [s for s in frr if s[0] == "frr_bgp_peer_state"]
        expected = sum(len(r["bgp_neighbors"]) for r in self.tfvars["routers"].values())
        self.assertEqual(len(bgp), expected)
        self.assertTrue(all(v == 1 for _, _, v in bgp))
        self.assertTrue(all(v == 7 for n, _, v in frr if n == "frr_ospf_neighbor_state"))
        node = parse(self.get("/metrics/node"))
        up = [s for s in node if s[0] == "node_network_up"]
        self.assertTrue(all(v == 1 for _, _, v in up))
        lum = {labels["device"] for n, labels, _ in up if labels["instance"] == "lum-chi"}
        n = len(self.tfvars["routers"]["lum-chi"]["interfaces"])
        self.assertEqual(lum, {f"eth{i}" for i in range(n + 1)} | {"lo"})
        self.assertEqual(len(self.server.network.links), 218)

    def test_link_down(self):
        link_id = self.link()
        a, b = link_id.split(":")
        status = self.post(f"/links/{b}:{a}/down")  # either order
        self.assertEqual(status["links_down"], [link_id])
        (h1, i1, _), (h2, i2, _) = self.server.network.links[link_id]["ends"]
        node = parse(self.get("/metrics/node"))
        state = {(lbl["instance"], lbl["device"]): v for n, lbl, v in node if n == "node_network_up"}
        self.assertEqual((state[(h1, f"eth{i1}")], state[(h2, f"eth{i2}")]), (0, 0))
        ends = {(h1, f"eth{i1}"), (h2, f"eth{i2}")}
        ospf = {(lbl["instance"], lbl["iface"]): v for n, lbl, v in parse(self.get("/metrics/frr")) if n == "frr_ospf_neighbor_state"}
        self.assertEqual({k: ospf[k] for k in ends}, {k: 0 for k in ends})
        self.assertTrue(all(v == 7 for k, v in ospf.items() if k not in ends))
        self.post(f"/links/{link_id}/up")
        self.assertTrue(all(v == 1 for n, _, v in parse(self.get("/metrics/node")) if n == "node_network_up"))

    def test_router_down_and_stopped_in_the_ui(self):
        self.post("/routers/lum-chi/down")
        frr = parse(self.get("/metrics/frr"))
        self.assertFalse([1 for n, lbl, _ in frr if lbl.get("instance") == "lum-chi"])
        lum_ips = {n_["ip"] for r in self.tfvars["routers"].values() for n_ in r["bgp_neighbors"] if n_["hostname"] == "lum-chi"}
        towards = [v for n, lbl, v in frr if n == "frr_bgp_peer_state" and lbl["peer"] in lum_ips]
        self.assertTrue(towards and all(v == 0 for v in towards))
        self.post("/reset")
        self.state.write_text(json.dumps(["are-chi"]))
        self.assertEqual(self.post("/reset")["routers_down"], ["are-chi"])
        self.assertFalse([1 for n, lbl, _ in parse(self.get("/metrics/node")) if lbl.get("instance") == "are-chi"])

    def test_counters_increase(self):
        def rx():
            return {(lbl["instance"], lbl["device"]): v for n, lbl, v in parse(self.get("/metrics/node")) if n == "node_network_receive_bytes_total"}
        first = rx()
        import time

        time.sleep(0.2)
        second = rx()
        self.assertTrue(all(second[k] >= first[k] for k in first))
        self.assertTrue(any(second[k] > first[k] for k in first))

    def test_unknown_targets(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post("/links/nope:nada/down")
        self.assertEqual(e.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
