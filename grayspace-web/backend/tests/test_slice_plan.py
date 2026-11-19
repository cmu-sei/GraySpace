"""The slice spec and the resolver (services/slices/plan.py): findings and allocation, against the
golden full-scope tfvars of grayspace/2026.11."""

import copy
import ipaddress
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from models.capabilities import Image
from models.instances import CreatedDomain, CreatedRecord, InstanceRecord, LinkConditionApplied, SliceRef
from models.slices import SliceSpec
from services.slices import base, catalog, plan
from tests import REPO

TFVARS = json.loads((REPO / "tests" / "golden" / "tfvars-full.json").read_text())
CTX = base.from_tfvars(TFVARS)
IMAGES = catalog.images()
ROLES = catalog.roles()

MINIMAL = {
    "apiVersion": "grayspace/v1",
    "kind": "Slice",
    "slug": "test-bank",
    "prefix": "tb",
    "sites": [{"name": "hq", "attach": {"router": "lum-chi"}, "segments": [{"name": "lan"}]}],
    "hosts": [{"name": "ws", "segment": "lan", "image": "grayspace-ubuntu-lxc"}],
}


def spec(**changes):
    data = copy.deepcopy(MINIMAL)
    for key, value in changes.items():
        data[key] = value
    return SliceSpec.model_validate(data)


def resolve(s, taken=None, settings=None, images=None, prefix=None):
    return plan.resolve(s, CTX, images or IMAGES, ROLES, taken or plan.Taken(), settings, prefix)


def codes(findings, severity="error"):
    return [f.code for f in findings if f.severity == severity]


def instance(allocation, instance_id="other", **extra):
    return InstanceRecord(id=instance_id, slice=SliceRef(slug="x", version="sha256:" + "0" * 64), state="ready",
                          created="t", updated="t", allocation=allocation, **extra)


class Spec(unittest.TestCase):
    def test_version_is_a_content_hash(self):
        a = spec()
        b = SliceSpec.model_validate({**MINIMAL, "description": "", "dns": {"domains": [], "records": []}})
        self.assertEqual(a.version(), b.version())  # defaults spelled out don't change the version
        self.assertTrue(a.version().startswith("sha256:"))
        self.assertNotEqual(a.version(), spec(description="changed").version())

    def test_structural_errors(self):
        for change in (
            {"slug": "Bad Slug"},
            {"slug": "base"},
            {"prefix": "toolongprefix"},
            {"hosts": [{"name": "x", "image": "grayspace-ubuntu-lxc"}]},  # neither segment nor internet
            {"hosts": [{"name": "x", "segment": "lan", "internet": {"isp": "Lumen"}, "image": "i"}]},
            {"hosts": [{"name": "x", "internet": {"isp": "Lumen"}, "image": "i", "publish": True}]},
            {"sites": [{"name": "hq", "attach": {}, "segments": [{"name": "lan"}]}]},
            {"sites": [{"name": "hq", "attach": {"router": "lum-chi"}, "segments": [{"name": "lan", "cidr": "300.1.0.0/24"}]}]},
            {"dns": {"records": [{"name": "www.x.com", "host": "a", "data": "1.2.3.4"}]}},
            {"unknown_field": 1},
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                spec(**change)

    def test_counted_hosts_expand(self):
        s = spec(hosts=[{"name": "ws", "count": 3, "segment": "lan", "image": "grayspace-ubuntu-lxc"}])
        self.assertEqual(s.hosts[0].expanded_names(), ["ws-01", "ws-02", "ws-03"])
        self.assertEqual(spec(hosts=[{"name": "ws", "count": 120, "segment": "lan", "image": "i"}]).hosts[0].expanded_names()[0], "ws-001")


class Allocation(unittest.TestCase):
    def test_nat_site(self):
        alloc, findings = resolve(spec())
        self.assertEqual(codes(findings), [])
        site = alloc.sites[0]
        self.assertEqual(site.edge, "tb-hq-edge")
        self.assertEqual(site.wans[0].router, "lum-chi")
        self.assertEqual(site.wans[0].bridge, "v332")
        self.assertEqual(site.wans[0].ip, "209.151.130.2")
        self.assertEqual(site.wans[0].gateway, "209.151.130.1")
        self.assertEqual(site.segments[0].cidr, "192.168.0.0/24")
        self.assertEqual(site.segments[0].vnet, "s000")
        host = alloc.hosts[0]
        self.assertEqual((host.hostname, host.ip, host.gateway, host.bridge, host.public_ip), ("tb-ws", "192.168.0.10", "192.168.0.1", "s000", None))
        self.assertEqual(host.resolver, "8.8.8.8")
        self.assertEqual(alloc.images, ["grayspace-ubuntu-lxc"])  # edges use the base's copy of theirs

    def test_a_sites_domain_controller_is_its_resolver(self):
        """Hosts of a site with an ad-dc host resolve through it; the DC, other sites and internet hosts keep the base's."""
        ad = {"ad-dc": {"realm": "corp.example.com", "domain": "CORP", "admin_password": "Grayspace1!"}}
        sites = [{"name": "hq", "attach": {"router": "lum-chi"}, "segments": [{"name": "lan"}]},
                 {"name": "branch", "attach": {"router": "lum-chi"}, "segments": [{"name": "br-lan"}]}]
        hosts = [{"name": "ws", "segment": "lan", "image": "grayspace-ubuntu-lxc"},
                 {"name": "dc", "segment": "lan", "image": "grayspace-ubuntu-vm", "roles": ["ad-dc", "dns"]},
                 {"name": "dc2", "segment": "lan", "image": "grayspace-ubuntu-vm", "roles": ["ad-dc"]},
                 {"name": "br", "segment": "br-lan", "image": "grayspace-ubuntu-lxc"},
                 {"name": "news", "internet": {"router": "lum-ash"}, "image": "ghosts-pandora"}]
        alloc, findings = resolve(spec(sites=sites, hosts=hosts, role_vars=ad))
        self.assertEqual(codes(findings), [])
        by = {h.name: h for h in alloc.hosts}
        base = alloc.sites[0].resolver
        self.assertEqual(by["ws"].resolver, by["dc"].ip)
        self.assertEqual(by["dc2"].resolver, by["dc"].ip)  # several DCs are several domains: the first serves
        self.assertEqual(by["dc"].resolver, base)  # its DNS forwards there
        self.assertEqual(by["br"].resolver, alloc.sites[1].resolver)
        self.assertNotEqual(by["news"].resolver, by["dc"].ip)
        self.assertEqual(alloc.sites[0].resolver, base)  # the site's (its edge's) stays the base's

    def test_deterministic(self):
        self.assertEqual(resolve(spec())[0], resolve(spec())[0])

    def test_static_addresses_skip_base_assignments(self):
        # lum-ash hosts dns-gov at .2
        s = spec(hosts=[{"name": "news", "count": 3, "internet": {"router": "lum-ash"}, "image": "ghosts-pandora"}], sites=[])
        alloc, findings = resolve(s)
        self.assertEqual(codes(findings), [])
        self.assertEqual([h.ip.split(".")[-1] for h in alloc.hosts], ["3", "4", "5"])
        self.assertTrue(all(h.public_ip == h.ip and h.gateway == "156.154.100.1" for h in alloc.hosts))

    def test_default_router_has_the_most_free_addresses(self):
        # Ashburn: lum-ash hosts dns-gov (.2), are-ash root-a (.4), ntt-ash dns-aws (.123, outside the range)
        alloc, _ = resolve(spec(sites=[{"name": "hq", "attach": {"location": "Ashburn"}, "segments": [{"name": "lan"}]}]))
        r = alloc.sites[0].wans[0].router
        self.assertEqual(CTX.free_static(r, set()).__len__(), max(len(CTX.free_static(x, set())) for x in ("lum-ash", "are-ash", "ntt-ash", "com-ash")))

    def test_other_instances_addresses_and_vnets_are_taken(self):
        first, _ = resolve(spec())
        taken = plan.Taken.from_instances([instance(first)])
        second, findings = resolve(spec(slug="other", prefix="ot"), taken=taken)
        self.assertEqual(codes(findings), [])
        self.assertEqual(second.sites[0].wans[0].ip, "209.151.130.3")
        self.assertEqual(second.sites[0].segments[0].vnet, "s001")
        # NAT isolates the instances, so private ranges may repeat
        self.assertEqual(second.sites[0].segments[0].cidr, "192.168.0.0/24")

    def test_destroyed_instances_hold_nothing(self):
        first, _ = resolve(spec())
        gone = instance(first)
        gone.state = "destroyed"
        _, findings = resolve(spec(), taken=plan.Taken.from_instances([gone]))
        self.assertEqual(codes(findings), [])

    def test_published_host(self):
        hosts = [{"name": "web", "segment": "lan", "image": "grayspace-ubuntu-lxc", "roles": ["web"], "publish": True}]
        alloc, findings = resolve(spec(hosts=hosts))
        self.assertEqual(codes(findings), [])
        self.assertEqual(alloc.hosts[0].public_ip, "209.151.130.3")
        self.assertEqual([s.use for s in alloc.static_addresses], ["tb-hq-edge WAN", "tb-web published"])

    def test_explicit_cidr_is_honored_and_auto_avoids_it(self):
        sites = [{"name": "hq", "attach": {"router": "lum-chi"}, "segments": [{"name": "a"}, {"name": "b", "cidr": "192.168.0.0/24"}]}]
        alloc, findings = resolve(spec(sites=sites, hosts=[]))
        self.assertEqual(codes(findings), [])
        self.assertEqual([g.cidr for g in alloc.sites[0].segments], ["192.168.1.0/24", "192.168.0.0/24"])

    def test_routed_dual_homed_site(self):
        sites = [{"name": "campus", "routed": True, "attach": [{"router": "lum-chi"}, {"isp": "Arelion", "location": "Chicago"}],
                  "segments": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}]
        alloc, findings = resolve(spec(sites=sites, hosts=[{"name": "www", "segment": "a", "image": "grayspace-ubuntu-lxc"}]))
        self.assertEqual(codes(findings), [])
        site = alloc.sites[0]
        self.assertEqual([w.router for w in site.wans], ["lum-chi", "are-chi"])
        prefix = ipaddress.IPv4Network(site.public_prefix)
        self.assertEqual(prefix.prefixlen, 24)
        self.assertTrue(prefix.subnet_of(ipaddress.IPv4Network("6.0.0.0/16")))  # Lumen's block
        self.assertFalse(any(prefix.overlaps(n) for n in CTX.used_networks))
        self.assertEqual([g.cidr.split("/")[1] for g in site.segments], ["26", "26", "26"])
        self.assertEqual(site.asn, 64512)
        self.assertEqual(alloc.hosts[0].public_ip, alloc.hosts[0].ip)
        # A second routed site gets another /24 and ASN
        taken = plan.Taken.from_instances([instance(alloc)])
        other, _ = resolve(spec(slug="o", prefix="o", sites=sites, hosts=[]), taken=taken)
        self.assertNotEqual(other.sites[0].public_prefix, site.public_prefix)
        self.assertEqual(other.sites[0].asn, 64513)


class Findings(unittest.TestCase):
    def assertFinding(self, s, code, path=None, taken=None, settings=None, severity="error", images=None):
        _, findings = resolve(s, taken=taken, settings=settings, images=images)
        matching = [f for f in findings if f.code == code and f.severity == severity]
        self.assertTrue(matching, f"no {severity} {code} in {[(f.code, f.path, f.message) for f in findings]}")
        if path is not None:
            self.assertIn(path, [f.path for f in matching])
        return matching[0]

    def site(self, attach, **extra):
        return spec(sites=[{"name": "hq", "attach": attach, "segments": [{"name": "lan", **extra}]}])

    def test_unknown_router(self):
        self.assertFinding(self.site({"router": "lum-xyz"}), "UNKNOWN_ROUTER", "sites[0].attach.router")
        self.assertFinding(self.site({"isp": "NoSuchISP"}), "UNKNOWN_ROUTER", "sites[0].attach.isp")
        self.assertFinding(self.site({"location": "Atlantis"}), "UNKNOWN_ROUTER", "sites[0].attach.location")
        f = self.assertFinding(self.site({"isp": "Comcast", "location": "Tokyo"}), "UNKNOWN_ROUTER", "sites[0].attach")
        self.assertIsNotNone(f.suggestion)
        self.assertFinding(self.site({"router": "lum-chi", "location": "Dallas"}), "UNKNOWN_ROUTER")

    def test_no_customer_net_suggests_a_router(self):
        f = self.assertFinding(self.site({"router": "lum-cai"}), "NO_CUSTOMER_NET", "sites[0].attach.router")
        self.assertIn(f.suggestion["router"], CTX.routers)
        self.assertTrue(CTX.routers[f.suggestion["router"]].customer_net)
        self.assertFinding(self.site({"location": "Cairo"}), "NO_CUSTOMER_NET")

    def test_no_static_addresses(self):
        hosts = [{"name": "web", "count": 20, "segment": "lan", "image": "grayspace-ubuntu-lxc", "publish": True}]
        f = self.assertFinding(spec(hosts=hosts), "NO_STATIC_ADDRESSES", "sites[0].attach.router")
        self.assertIn("21 needed", f.message)
        # Taken by other instances
        many = spec(sites=[], hosts=[{"name": "n", "count": 18, "internet": {"router": "lum-chi"}, "image": "ghosts-pandora"}])
        alloc, _ = resolve(many)
        self.assertFinding(spec(), "NO_STATIC_ADDRESSES", taken=plan.Taken.from_instances([instance(alloc)]))

    def test_cidrs(self):
        self.assertFinding(self.site({"router": "lum-chi"}, cidr="10.0.5.0/24"), "CIDR_OVERLAPS_MGMT")
        self.assertFinding(self.site({"router": "lum-chi"}, cidr="172.16.0.0/24"), "CIDR_OVERLAP")  # base links
        self.assertFinding(self.site({"router": "lum-chi"}, cidr="8.8.8.0/24"), "INVALID_CIDR")  # not private
        self.assertFinding(self.site({"router": "lum-chi"}, cidr="192.168.5.0/30"), "INVALID_CIDR")  # too small
        sites = [{"name": "hq", "attach": {"router": "lum-chi"}, "segments": [{"name": "a", "cidr": "192.168.0.0/23"}, {"name": "b", "cidr": "192.168.1.0/24"}]}]
        self.assertFinding(spec(sites=sites, hosts=[]), "CIDR_OVERLAP", "sites[0].segments[1].cidr")
        routed = [{"name": "hq", "routed": True, "attach": {"router": "lum-chi"}, "segments": [{"name": "a", "cidr": "192.168.0.0/24"}]}]
        self.assertFinding(spec(sites=routed, hosts=[]), "INVALID_CIDR")

    def test_pool_overlapping_management(self):
        s = plan.Settings(pool="10.0.0.0/15", mgmt_subnet="10.0.0.0/16")
        alloc, findings = resolve(spec(), settings=s)
        self.assertIn("CIDR_OVERLAPS_MGMT", codes(findings, "warning"))
        self.assertEqual(alloc.sites[0].segments[0].cidr, "10.1.0.0/24")

    def test_images_and_roles(self):
        self.assertFinding(spec(hosts=[{"name": "x", "segment": "lan", "image": "grayspace-ubuntu-vm-lx"}]), "UNKNOWN_IMAGE", "hosts[0].image")
        self.assertFinding(spec(hosts=[{"name": "x", "segment": "lan", "image": "grayspace-ubuntu-lxc", "roles": ["nope"]}]), "UNKNOWN_ROLE", "hosts[0].roles[0]")
        self.assertFinding(spec(hosts=[{"name": "x", "segment": "lan", "image": "grayspace-ubuntu-lxc", "roles": ["edge-nat"]}]), "UNKNOWN_ROLE")
        self.assertFinding(spec(hosts=[{"name": "x", "segment": "lan", "image": "ghosts-pandora", "roles": ["web"]}]), "INCOMPATIBLE_ROLE")
        win = {**IMAGES, "win": Image(name="win", type="vm", os_family="windows", source="template:9000", origin="local")}
        self.assertFinding(spec(hosts=[{"name": "x", "segment": "lan", "image": "win", "roles": ["web"]}]), "INCOMPATIBLE_ROLE", images=win)
        _, findings = resolve(spec(hosts=[{"name": "x", "segment": "lan", "image": "win", "roles": ["ghosts-client"]}],
                                   role_vars={"ghosts-client": {"client_url": "u", "api_url": "a"}}), images=win)
        self.assertEqual(codes(findings), [])

    def test_role_image_types(self):
        ad = {"ad-dc": {"realm": "corp.example.com", "domain": "CORP", "admin_password": "Grayspace1!"}}
        f = self.assertFinding(spec(hosts=[{"name": "dc", "segment": "lan", "image": "grayspace-ubuntu-lxc", "roles": ["ad-dc"]}], role_vars=ad),
                               "INCOMPATIBLE_ROLE", "hosts[0].roles[0]")
        self.assertIn("VM", f.message)
        _, findings = resolve(spec(hosts=[{"name": "dc", "segment": "lan", "image": "grayspace-ubuntu-vm", "roles": ["ad-dc"]}], role_vars=ad))
        self.assertNotIn("INCOMPATIBLE_ROLE", codes(findings))

    def test_missing_role_vars(self):
        hosts = [{"name": "ws", "segment": "lan", "image": "grayspace-ubuntu-lxc", "roles": ["ghosts-client"]}]
        f = self.assertFinding(spec(hosts=hosts), "MISSING_ROLE_VAR")
        self.assertIn(f.path, ("role_vars.ghosts-client.client_url", "role_vars.ghosts-client.api_url"))

    def test_unsupported_features(self):
        dual_nat = [{"name": "hq", "attach": [{"router": "lum-chi"}, {"router": "are-chi"}], "segments": [{"name": "lan"}]}]
        self.assertFinding(spec(sites=dual_nat), "UNSUPPORTED_FEATURE", "sites[0].attach")
        phase_a = plan.Settings(features=["nat-site", "published-host", "internet-host", "oci-host"])
        routed = [{"name": "hq", "routed": True, "attach": {"router": "lum-chi"}, "segments": [{"name": "lan"}]}]
        self.assertFinding(spec(sites=routed), "UNSUPPORTED_FEATURE", "sites[0].routed", settings=phase_a)
        self.assertFinding(spec(hosts=[{"name": "v", "segment": "lan", "image": "grayspace-ubuntu-vm"}]), "UNSUPPORTED_FEATURE", settings=phase_a)
        self.assertFinding(spec(conditions={"links": {"lum-chi:lum-cai": {"latency_ms": 20}}}), "UNSUPPORTED_FEATURE", settings=phase_a)

    def test_names(self):
        self.assertFinding(spec(prefix="lum", hosts=[{"name": "chi", "segment": "lan", "image": "grayspace-ubuntu-lxc"}]), "HOSTNAME_TAKEN")
        self.assertFinding(spec(hosts=[{"name": "hq-edge", "segment": "lan", "image": "grayspace-ubuntu-lxc"}]), "DUPLICATE_NAME")
        twice = [{"name": "hq", "attach": {"router": "lum-chi"}, "segments": [{"name": "lan"}]},
                 {"name": "hq", "attach": {"router": "are-chi"}, "segments": [{"name": "lan2"}]}]
        self.assertFinding(spec(sites=twice), "DUPLICATE_NAME", "sites[1].name")
        self.assertFinding(spec(hosts=[{"name": "x", "segment": "nope", "image": "grayspace-ubuntu-lxc"}]), "UNKNOWN_SEGMENT")
        first, _ = resolve(spec())
        f = self.assertFinding(spec(), "PREFIX_TAKEN", "prefix", taken=plan.Taken.from_instances([instance(first, instance_id="tb")]))
        self.assertEqual(f.suggestion, "tb2")
        self.assertFinding(spec(slug="o", prefix="o"), "HOSTNAME_TAKEN", taken=plan.Taken(hostnames={"o-ws"}))

    def test_segment_full(self):
        sites = [{"name": "hq", "attach": {"router": "lum-chi"}, "segments": [{"name": "lan", "cidr": "192.168.9.0/28"}]}]
        self.assertFinding(spec(sites=sites, hosts=[{"name": "ws", "count": 6, "segment": "lan", "image": "grayspace-ubuntu-lxc"}]), "SEGMENT_FULL")

    def test_dns(self):
        def dns(**d):
            return spec(dns={"domains": [{"domain": "test-bank.com", "hosting_server": "dns-cloudflare"}], "records": [], **d})
        self.assertFinding(spec(dns={"domains": [{"domain": "test.zz", "hosting_server": "dns-cloudflare"}]}), "UNKNOWN_TLD")
        f = self.assertFinding(spec(dns={"domains": [{"domain": "test.com", "hosting_server": "dns-nope"}]}), "UNKNOWN_HOSTING_SERVER")
        self.assertIn(f.suggestion, CTX.hosting)
        self.assertFinding(dns(records=[{"name": "www.other.com", "host": "ws"}]), "UNKNOWN_DOMAIN")
        self.assertFinding(dns(records=[{"name": "www.test-bank.com", "host": "nobody"}]), "UNKNOWN_HOST")
        self.assertFinding(dns(records=[{"name": "www.test-bank.com", "host": "ws"}]), "UNREACHABLE_RECORD", severity="warning")
        held = instance(None, domains=[CreatedDomain(domain="test-bank.com", hosting_server="dns-cloudflare", tld_server="dns-gtld")])
        self.assertFinding(dns(), "DNS_NAME_TAKEN", taken=plan.Taken.from_instances([held]))
        self.assertFinding(dns(), "DNS_NAME_TAKEN", taken=plan.Taken(domains={"test-bank.com"}))
        rec = CreatedRecord(server="dns-cloudflare", zone="x.com", name="www", fqdn="www.test-bank.com", type="A", data="1.2.3.4", ttl=60)
        self.assertFinding(dns(records=[{"name": "www.test-bank.com", "data": "1.2.3.4"}]), "DNS_NAME_TAKEN",
                           taken=plan.Taken.from_instances([instance(None, records=[rec])]))

    def test_one_certificate_entry_per_host(self):
        tls = [{"host": "ws", "names": ["www.test-bank.com"], "ca": "ca-graytrust"}, {"host": "ws", "names": ["api.test-bank.com"], "ca": "ca-graytrust"}]
        self.assertFinding(spec(tls=tls), "DUPLICATE_NAME", "tls[1].host")

    def test_empty_attach_list(self):
        with self.assertRaises(ValidationError):
            spec(sites=[{"name": "hq", "attach": [], "segments": [{"name": "lan"}]}])

    def test_counted_host_names_must_be_expanded(self):
        hosts = [{"name": "ws", "count": 2, "segment": "lan", "image": "grayspace-ubuntu-lxc"}]
        f = self.assertFinding(spec(hosts=hosts, tls=[{"host": "ws", "names": ["www.test-bank.com"], "ca": "ca-graytrust"}]), "UNKNOWN_HOST")
        self.assertEqual(f.suggestion, "ws-01")

    def test_tls(self):
        self.assertFinding(spec(tls=[{"host": "ws", "names": ["a.com"], "ca": "ca-nope"}]), "UNKNOWN_CA")
        no_tls = {**IMAGES, "plain": Image(name="plain", type="oci", source="docker.io/library/nginx")}
        self.assertFinding(spec(hosts=[{"name": "p", "segment": "lan", "image": "plain"}], tls=[{"host": "p", "names": ["a.com"], "ca": "ca-graytrust"}]),
                           "UNSUPPORTED_FEATURE", "tls[0].host", images=no_tls)

    def test_links(self):
        link = next(iter(CTX.links))
        a, b = link.split(":")
        _, findings = resolve(spec(conditions={"links": {f"{b}:{a}": {"latency_ms": 50}}}))
        self.assertEqual(codes(findings), [])  # either order
        self.assertFinding(spec(conditions={"links": {"lum-chi:ntt-tok": {"latency_ms": 50}}}), "UNKNOWN_LINK")
        held = instance(None, conditions=[LinkConditionApplied(link_id=link, latency_ms=5)])
        self.assertFinding(spec(conditions={"links": {link: {"latency_ms": 50}}}), "LINK_OWNED", taken=plan.Taken.from_instances([held]))
        self.assertFinding(spec(conditions={"links": {link: {"latency_ms": 50}}}), "LINK_OWNED", taken=plan.Taken.from_instances([], overlay_links=[link]))

    def test_no_allocation_with_errors(self):
        alloc, _ = resolve(self.site({"router": "lum-xyz"}))
        self.assertIsNone(alloc)


class Capacity(unittest.TestCase):
    def test_required_and_findings(self):
        from models.capabilities import Capacity as Cap

        alloc, _ = resolve(spec(hosts=[{"name": "ws", "count": 4, "segment": "lan", "image": "grayspace-ubuntu-lxc"}]))
        need = plan.required_capacity(alloc)
        self.assertEqual(need, {"guests": 5, "cores": 5, "memory_mib": 4 * 1024 + 256, "disk_gib": 4 * 8 + 4})
        roomy = Cap(node="pve", cpus=64, cpu_usage=0.1, memory_total=2**40, memory_free=2**40, storage="local-lvm", storage_total=2**42, storage_free=2**42)
        self.assertEqual(plan.capacity_findings(alloc, roomy), [])
        tight = roomy.model_copy(update={"memory_free": 2**30, "storage_free": 2**30, "cpus": 1})
        self.assertEqual(sorted(codes(plan.capacity_findings(alloc, tight))), ["CAPACITY", "CAPACITY"])
        self.assertEqual(codes(plan.capacity_findings(alloc, tight), "warning"), ["CAPACITY"])
        self.assertEqual(codes(plan.capacity_findings(alloc, None), "warning"), ["CAPACITY"])


class Examples(unittest.TestCase):
    """examples/slices/ validate against every shipped base version."""

    def test_examples_against_every_shipped_base(self):
        import subprocess
        import sys
        import tempfile

        import yaml

        windows = {**IMAGES, "windows-2022": Image(name="windows-2022", type="vm", os_family="windows", source="template:9000",
                                                   cores=2, memory=4096, disk=40, origin="local")}
        for version in sorted(p for p in (REPO / "bases").glob("*/*") if p.is_dir()):
            with tempfile.NamedTemporaryFile(suffix=".json") as out:
                subprocess.run([sys.executable, str(REPO / "scripts" / "generate_tfvars.py"), "--scope=full",
                                f"--csv-dir={version}", f"--output-file={out.name}"], check=True, capture_output=True)
                ctx = base.from_tfvars(json.loads(Path(out.name).read_text()))
            for example in sorted((REPO / "examples" / "slices").glob("*.yaml")):
                with self.subTest(base=version.name, example=example.name):
                    s = SliceSpec.model_validate(yaml.safe_load(example.read_text()))
                    alloc, findings = plan.resolve(s, ctx, windows, ROLES, plan.Taken())
                    self.assertEqual(codes(findings), [], [(f.code, f.path, f.message) for f in findings])
                    self.assertIsNotNone(alloc)


if __name__ == "__main__":
    unittest.main()
