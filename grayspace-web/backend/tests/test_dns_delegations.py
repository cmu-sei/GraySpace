"""TLD delegations live in /var/bind/delegations.<tld>, pulled in by $INCLUDE, so a domain
created at runtime survives task configure re-templating the TLD zone file."""

import asyncio
import json
import unittest
from unittest.mock import patch

from jinja2 import Template

from services import dns_service as module
from services.mutation_tracker import mutation_tracker
from tests import REPO
from tests.fake_hosts import FakeHosts

TFVARS = json.loads((REPO / "tests" / "golden" / "tfvars-full.json").read_text())


def tld_zone(tld, ip):
    """db.tld.j2 as Ansible renders it."""
    return Template((REPO / "ansible" / "templates" / "db.tld.j2").read_text()).render(item=tld, service_ip=ip) + "\n"


class Delegations(unittest.TestCase):
    def setUp(self):
        self.hosts = FakeHosts()
        self.addCleanup(self.hosts.cleanup)
        service = module.dns_service
        for name, fake in (("_run_command", self.hosts.run), ("_read_file", self.hosts.read_file), ("_write_file", self.hosts.write_file)):
            p = patch.object(service, name, side_effect=fake)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(module.topology_service, "tfvars_data", TFVARS)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(mutation_tracker, "save")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(mutation_tracker.dns_domains.clear)
        self.hosts.write("dns-gtld", "/var/bind/db.com", tld_zone("com", "216.239.34.10"))
        self.hosts.write("dns-gtld", "/var/bind/delegations.com", "; Delegations\n")

    def create(self, domain="meridian-bank.com", track=True):
        return asyncio.run(module.dns_service.create_domain(domain, hosting_server="dns-cloudflare", ip_address="209.151.130.3", track=track))

    def test_delegation_goes_in_the_include_file(self):
        result = self.create()
        self.assertTrue(result.success, result.message)
        self.assertEqual(result.tld_server, "dns-gtld")
        delegations = self.hosts.read("dns-gtld", "/var/bind/delegations.com")
        self.assertIn("meridian-bank.com.\t\tIN\tNS\tns1.meridian-bank.com.", delegations)
        self.assertIn("ns1.meridian-bank.com.\tIN\tA\t108.162.192.9", delegations)
        # The templated zone file is untouched, so re-running the template changes nothing.
        self.assertEqual(self.hosts.read("dns-gtld", "/var/bind/db.com"), tld_zone("com", "216.239.34.10"))
        self.assertIn("$INCLUDE /var/bind/delegations.com", self.hosts.read("dns-gtld", "/var/bind/db.com"))
        self.assertIn('zone "meridian-bank.com"', self.hosts.read("dns-cloudflare", "/etc/bind/zones.d/dynamic.conf"))
        self.assertIn("@\t\t\tIN\tA\t209.151.130.3", self.hosts.read("dns-cloudflare", "/var/bind/db.meridian-bank.com"))

    def test_survives_task_configure(self):
        self.create()
        # task configure: db.tld.j2 again; the delegations file exists, so copy force=false skips it.
        self.hosts.write("dns-gtld", "/var/bind/db.com", tld_zone("com", "216.239.34.10"))
        self.assertIn("NS\tns1.meridian-bank.com.", self.hosts.read("dns-gtld", "/var/bind/delegations.com"))

    def test_zone_files_from_before_the_include_get_it(self):
        self.hosts.write("dns-gtld", "/var/bind/db.com", "$TTL 86400\n@ IN SOA ns1.com. admin.com. ( 2024010101 3600 900 604800 86400 )\n")
        (self.hosts.root / "dns-gtld" / "var/bind/delegations.com").unlink()
        self.assertTrue(self.create().success)
        zone = self.hosts.read("dns-gtld", "/var/bind/db.com")
        self.assertEqual(zone.count("$INCLUDE /var/bind/delegations.com"), 1)
        self.assertTrue(self.create().success)
        self.assertEqual(self.hosts.read("dns-gtld", "/var/bind/db.com").count("$INCLUDE"), 1)

    def test_recreating_replaces_the_delegation(self):
        self.create()
        self.create()
        delegations = self.hosts.read("dns-gtld", "/var/bind/delegations.com")
        self.assertEqual(delegations.count("NS\tns1.meridian-bank.com."), 1)

    def test_revert_removes_only_that_domain(self):
        self.create("meridian-bank.com")
        self.create("dailychronicle.com")
        ok, message = asyncio.run(module.dns_service.revert_domain({"domain": "meridian-bank.com", "hosting_server": "dns-cloudflare", "tld_server": "dns-gtld"}))
        self.assertTrue(ok, message)
        delegations = self.hosts.read("dns-gtld", "/var/bind/delegations.com")
        self.assertNotIn("meridian-bank", delegations)
        self.assertIn("ns1.dailychronicle.com.", delegations)
        self.assertIsNone(self.hosts.read("dns-cloudflare", "/var/bind/db.meridian-bank.com"))

    def test_revert_of_a_legacy_delegation_in_the_zone_file(self):
        zone = tld_zone("com", "216.239.34.10") + "old-site.com.\t\tIN\tNS\tns1.old-site.com.\nns1.old-site.com.\tIN\tA\t198.54.100.10\n"
        self.hosts.write("dns-gtld", "/var/bind/db.com", zone)
        ok, _ = asyncio.run(module.dns_service.revert_domain({"domain": "old-site.com", "tld_server": "dns-gtld"}))
        self.assertTrue(ok)
        self.assertNotIn("old-site", self.hosts.read("dns-gtld", "/var/bind/db.com"))

    def test_revert_matches_exactly(self):
        self.create("bank.com")
        self.create("meridianbank.com")
        ok, _ = asyncio.run(module.dns_service.revert_domain({"domain": "bank.com", "hosting_server": "dns-cloudflare", "tld_server": "dns-gtld"}))
        self.assertTrue(ok)
        dynamic = self.hosts.read("dns-cloudflare", "/etc/bind/zones.d/dynamic.conf")
        self.assertNotIn('zone "bank.com"', dynamic)
        self.assertIn('zone "meridianbank.com"', dynamic)
        self.assertIn("ns1.meridianbank.com.", self.hosts.read("dns-gtld", "/var/bind/delegations.com"))
        # Records: removing "a" keeps "ba"
        for name in ("a", "ba"):
            asyncio.run(module.dns_service.add_record("dns-cloudflare", "meridianbank.com", name, "A", "1.2.3.4", 3600, track=False))
        ok, _ = asyncio.run(module.dns_service.revert_record({"server": "dns-cloudflare", "zone": "meridianbank.com", "name": "a", "type": "A", "data": "1.2.3.4", "ttl": 3600}))
        self.assertTrue(ok)
        zone = self.hosts.read("dns-cloudflare", "/var/bind/db.meridianbank.com")
        self.assertIn("ba\t3600\tIN\tA\t1.2.3.4", zone)
        self.assertNotIn("\na\t3600", zone)

    def test_tracking(self):
        self.create("tracked.com")
        self.create("untracked.com", track=False)
        self.assertEqual([d["domain"] for d in mutation_tracker.dns_domains], ["tracked.com"])


if __name__ == "__main__":
    unittest.main()
