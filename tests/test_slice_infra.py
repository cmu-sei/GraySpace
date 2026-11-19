"""The base guard (scripts/check_slice_attachments.py), slice attachments in the dynamic inventory,
and the slice Ansible: templates rendered from the golden vars, and syntax checks."""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_slice_attachments as guard  # noqa: E402

TFVARS = json.loads((ROOT / "tests" / "golden" / "tfvars-full.json").read_text())
GOLDEN = ROOT / "tests" / "golden" / "slices"


def instance(state="ready", attachments=()):
    """An instance record shaped as grayspace-web writes it: a NAT site on lum-chi, a published
    host, an internet host on lum-mex and a routed /24."""
    return {
        "id": "mb",
        "state": state,
        "allocation": {
            "sites": [{"name": "hq", "wans": [{"router": "lum-chi", "customer_net": "209.151.130.0/24", "bridge": "v332", "ip": "209.151.130.2"}]}],
            "hosts": [{"name": "news", "site": None, "router": "lum-mex", "bridge": "v333", "ip": "200.38.194.2"}],
            "static_addresses": [
                {"router": "lum-chi", "ip": "209.151.130.2", "use": "mb-hq-edge WAN"},
                {"router": "lum-chi", "ip": "209.151.130.3", "use": "mb-web01 published"},
                {"router": "lum-mex", "ip": "200.38.194.2", "use": "mb-news"},
            ],
            "public_prefixes": ["6.0.2.0/24"],
        },
        "attachments": list(attachments),
    }


class Guard(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        (self.dir / "instances").mkdir()

    def run_guard(self, mode, tfvars=TFVARS, records=()):
        for r in records:
            (self.dir / "instances" / f"{r['id']}.json").write_text(json.dumps(r))
        path = self.dir / "tfvars.json"
        path.write_text(json.dumps(tfvars))
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "check_slice_attachments.py"), mode,
                               f"--tfvars={path}", f"--data-dir={self.dir}"], capture_output=True, text=True)

    def test_no_instances(self):
        self.assertEqual(self.run_guard("destroy").returncode, 0)
        self.assertEqual(self.run_guard("apply").returncode, 0)

    def test_destroy_refused_while_instances_live(self):
        r = self.run_guard("destroy", records=[instance()])
        self.assertEqual(r.returncode, 1)
        self.assertIn("mb (ready)", r.stdout)
        gone = instance(state="destroyed")
        (self.dir / "instances" / "mb.json").write_text(json.dumps(gone))
        self.assertEqual(self.run_guard("destroy").returncode, 0)

    def test_unchanged_base_is_fine(self):
        r = self.run_guard("apply", records=[instance()])
        self.assertEqual(r.returncode, 0, r.stdout)

    def problems(self, tfvars):
        return guard.check_apply(tfvars, [instance()])

    def test_router_loses_customer_net(self):
        t = copy.deepcopy(TFVARS)
        t["routers"]["lum-chi"]["customer_net"] = None
        self.assertIn("lose the customer network", " ".join(self.problems(t)))

    def test_router_removed(self):
        t = copy.deepcopy(TFVARS)
        del t["routers"]["lum-mex"]
        self.assertIn("doesn't have", " ".join(self.problems(t)))

    def test_vnet_renamed(self):
        t = copy.deepcopy(TFVARS)
        for iface in t["routers"]["lum-chi"]["interfaces"]:
            if iface["bridge"] == "v332":
                iface["bridge"] = "v333"
        self.assertIn("from vnet v332 to v333", " ".join(self.problems(t)))

    def test_base_address_collision(self):
        t = copy.deepcopy(TFVARS)
        dns = next(iter(t["core_dns"]))
        t["core_dns"][dns]["ip_address"] = "200.38.194.2/24"
        self.assertIn(f"give 200.38.194.2 on lum-mex to {dns}", " ".join(self.problems(t)))

    def test_dhcp_range_moved_under_an_address(self):
        t = copy.deepcopy(TFVARS)
        t["customer_dhcp_start"] = 3
        self.assertIn("DHCP range", " ".join(self.problems(t)))

    def test_routed_prefix_taken_by_the_base(self):
        t = copy.deepcopy(TFVARS)
        t["networks"]["v999"] = {"subnet": "6.0.2.0/30", "description": "new link"}
        self.assertIn("inside the routed site prefix", " ".join(self.problems(t)))

    def test_cli_reports_and_refuses(self):
        t = copy.deepcopy(TFVARS)
        t["routers"]["lum-chi"]["customer_net"] = None
        r = self.run_guard("apply", tfvars=t, records=[instance()])
        self.assertEqual(r.returncode, 1)
        self.assertIn("Refusing to apply", r.stdout)


class DynamicInventory(unittest.TestCase):
    def test_attachments_join_the_base_routers_neighbors(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work)
        (work / "terraform").mkdir()
        (work / "ansible").mkdir()
        (work / "terraform" / "terraform.tfvars.json").write_text(json.dumps(TFVARS))
        data = work / "terraform" / "grayspace-data" / "instances"
        data.mkdir(parents=True)
        attached = instance(attachments=[{"router": "lum-chi", "edge": "rc-campus-edge", "ip": "209.151.130.2", "asn": 64512}])
        (data / "rc.json").write_text(json.dumps(attached))
        (data / "old.json").write_text(json.dumps({**attached, "id": "old", "state": "destroyed",
                                                   "attachments": [{"router": "lum-chi", "edge": "x", "ip": "1.1.1.1", "asn": 64999}]}))
        out = subprocess.run([sys.executable, str(ROOT / "ansible" / "inventory" / "dynamic_inventory.py")], cwd=work / "ansible",
                             capture_output=True, text=True, check=True, env={k: v for k, v in os.environ.items() if k != "GRAYSPACE_DATA_PATH"})
        inv = json.loads(out.stdout)
        neighbors = inv["_meta"]["hostvars"]["lum-chi"]["bgp_neighbors"]
        self.assertEqual(neighbors[-1], {"hostname": "rc-campus-edge", "ip": "209.151.130.2", "asn": 64512,
                                         "type": "PROVIDES_TRANSIT_TO", "relationship": "customer"})
        self.assertEqual(len(neighbors), len(TFVARS["routers"]["lum-chi"]["bgp_neighbors"]) + 1)  # the destroyed one is not there
        self.assertEqual(inv["all"]["vars"]["customer_dhcp_start"], 20)


def jinja():
    try:
        import jinja2
    except ImportError:
        raise unittest.SkipTest("jinja2 is not installed")
    return jinja2.Environment(loader=jinja2.FileSystemLoader(str(ROOT / "ansible")), keep_trailing_newline=True,
                              undefined=jinja2.StrictUndefined)


class Templates(unittest.TestCase):
    def vars(self, example):
        return json.loads((GOLDEN / f"{example}.vars.json").read_text())

    def test_nat_rules(self):
        v = self.vars("meridian-bank")
        rules = jinja().get_template("roles/edge-nat/templates/grayspace.rules.j2").render(edge=v["slice_edges"]["mb-hq-edge"], inventory_hostname="mb-hq-edge")
        self.assertIn("-A PREROUTING -d 209.151.130.3/32 -j DNAT --to-destination 192.168.20.10", rules)
        self.assertIn("-A POSTROUTING -s 192.168.20.10/32 -o eth0 -j SNAT --to-source 209.151.130.3", rules)
        self.assertIn("-A POSTROUTING -s 192.168.0.0/24 -o eth0 -j SNAT --to-source 209.151.130.2", rules)
        self.assertIn("-A POSTROUTING -s 192.168.0.0/24 -d 192.168.20.10/32 -j MASQUERADE", rules)  # hairpin
        # The published SNAT comes before the site's, so the host leaves with its own address
        self.assertLess(rules.index("--to-source 209.151.130.3"), rules.index("--to-source 209.151.130.2"))
        self.assertEqual(rules.count("COMMIT"), 2)
        start = jinja().get_template("roles/edge-nat/templates/grayspace-edge.start.j2").render(edge=v["slice_edges"]["mb-hq-edge"])
        self.assertIn("ip addr replace 209.151.130.3/24 dev eth0", start)
        self.assertIn("iptables-restore < /etc/iptables/grayspace.rules", start)
        branch = jinja().get_template("roles/edge-nat/templates/grayspace.rules.j2").render(edge=v["slice_edges"]["mb-branch-edge"], inventory_hostname="x")
        self.assertNotIn("DNAT", branch)

    def test_edge_bgp(self):
        v = self.vars("routed-campus")
        conf = jinja().get_template("roles/edge-bgp/templates/frr.conf.j2").render(edge=v["slice_edges"]["rc-campus-edge"], inventory_hostname="rc-campus-edge")
        self.assertIn("router bgp 64512", conf)
        self.assertIn("neighbor 209.151.130.1 remote-as 3356", conf)
        self.assertIn("neighbor 199.7.83.1 remote-as 1299", conf)
        self.assertIn("network 6.0.2.0/24", conf)
        self.assertIn("ip route 6.0.2.0/24 Null0 254", conf)
        self.assertIn("ip prefix-list PL-SITE seq 5 permit 6.0.2.0/24", conf)

    def test_web_site(self):
        conf = jinja().get_template("roles/web/templates/site.conf.j2")
        self.assertIn("listen 443 ssl", conf.render(web_tls=True))
        self.assertNotIn("443", conf.render(web_tls=False))

    def test_base_router_accepts_the_routed_site(self):
        """frr.conf.j2 on lum-chi with the attachment merged in: the customer filters apply."""
        try:
            import ansible  # noqa: F401
        except ImportError:
            self.skipTest("ansible is not installed")
        router = copy.deepcopy(TFVARS["routers"]["lum-chi"])
        router["bgp_neighbors"].append({"hostname": "rc-campus-edge", "ip": "209.151.130.2", "asn": 64512,
                                        "type": "PROVIDES_TRANSIT_TO", "relationship": "customer"})
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work)
        (work / "vars.json").write_text(json.dumps(router))
        playbook = work / "render.yml"
        playbook.write_text(f"""
- hosts: localhost
  gather_facts: false
  connection: local
  vars:
    inventory_hostname: lum-chi
  tasks:
    - ansible.builtin.template:
        src: {ROOT / 'ansible' / 'templates' / 'frr.conf.j2'}
        dest: {work / 'frr.conf'}
""")
        r = subprocess.run(["ansible-playbook", "-i", "localhost,", "-e", f"@{work / 'vars.json'}", str(playbook)],
                           capture_output=True, text=True, cwd=work)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        conf = (work / "frr.conf").read_text()
        self.assertIn("bgp as-path access-list AS-CUST-64512 seq 5 permit ^64512(_64512)*$", conf)
        self.assertIn("neighbor 209.151.130.2 remote-as 64512", conf)
        self.assertIn("neighbor 209.151.130.2 filter-list AS-CUST-64512 in", conf)
        self.assertIn("neighbor 209.151.130.2 route-map RM-FROM-CUSTOMER in", conf)


@unittest.skipUnless(shutil.which("ansible-playbook"), "ansible is not installed")
class RoleFacts(unittest.TestCase):
    """grayspace-host's facts for every guest of every golden example, computed by Ansible itself."""

    def test_default_routes(self):
        for example in ("meridian-bank", "information-environment", "routed-campus", "windows-office"):
            with self.subTest(example=example):
                v = json.loads((GOLDEN / f"{example}.vars.json").read_text())
                work = Path(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, work)
                guests = list(v["slice_edges"]) + list(v["slice_hosts"])
                (work / "inv.ini").write_text("[all]\n" + "\n".join(f"{g} ansible_connection=local" for g in guests) + "\n")
                (work / "vars.json").write_text(json.dumps(v))
                (work / "play.yml").write_text(f"""
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.include_tasks: {ROOT / 'ansible' / 'roles' / 'grayspace-host' / 'tasks' / 'facts.yml'}
    - ansible.builtin.copy:
        dest: "{work}/{{{{ inventory_hostname }}}}.json"
        content: "{{{{ {{'gateway': gs_default_gateway | trim, 'sim': gs_sim_interface, 'family': gs_family, 'edge': gs_edge}} | to_json }}}}"
      delegate_to: localhost
""")
                r = subprocess.run(["ansible-playbook", "-i", str(work / "inv.ini"), "-e", f"@{work / 'vars.json'}", str(work / "play.yml")],
                                   capture_output=True, text=True, env={**os.environ, "ANSIBLE_LOG_PATH": str(work / "a.log")})
                self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                for g in guests:
                    facts = json.loads((work / f"{g}.json").read_text())
                    if g in v["slice_edges"]:
                        edge = v["slice_edges"][g]
                        self.assertEqual(facts["family"], "alpine")
                        self.assertEqual(facts["sim"], "eth0")
                        self.assertEqual(facts["gateway"], "" if edge["routed"] else edge["wans"][0]["gateway"])
                    else:
                        host = v["slice_hosts"][g]
                        self.assertEqual((facts["gateway"], facts["sim"], facts["family"]), (host["gateway"], "eth0", host["os_family"]))


@unittest.skipUnless(shutil.which("ansible-playbook"), "ansible is not installed")
class Playbooks(unittest.TestCase):
    def test_syntax(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work)
        inv = work / "inv.ini"
        inv.write_text("[edges]\ne ansible_host=10.0.0.5\n[hosts]\nh ansible_host=10.0.0.6\n[routers]\nr\n")
        for playbook in ("slice.yml", "slice-attach.yml", "main.yml"):
            with self.subTest(playbook=playbook):
                r = subprocess.run(["ansible-playbook", "-i", str(inv), "--syntax-check", playbook], cwd=ROOT / "ansible",
                                   capture_output=True, text=True, env={**os.environ, "ANSIBLE_LOG_PATH": str(work / "a.log")})
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
