"""Proxmox API token handling in services/proxmox_client.py."""

import unittest
from unittest.mock import patch

from config import settings
from services import proxmox_client as module


class TokenAuth(unittest.TestCase):
    def connect(self, token):
        client = module.ProxmoxClient()
        with patch.object(settings, "proxmox_api_token", token), patch.object(module, "ProxmoxAPI") as api:
            client._connect()
        return api.call_args.kwargs

    def test_user_and_token_id_are_passed_separately(self):
        # proxmoxer joins user and token_name itself; passing "USER@REALM!TOKENID" as token_name
        # sends "None!USER@REALM!TOKENID", which Proxmox rejects.
        kwargs = self.connect("terraform@pve!grayspace=00000000-0000-0000-0000-000000000000")
        self.assertEqual(kwargs["user"], "terraform@pve")
        self.assertEqual(kwargs["token_name"], "grayspace")
        self.assertEqual(kwargs["token_value"], "00000000-0000-0000-0000-000000000000")

    def test_malformed_token(self):
        for token in ("terraform@pve=uuid", "terraform@pve!id"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                self.connect(token)


class FakeApi:
    """Proxmox's /cluster/resources?type=vm: every guest of the cluster, LXC and QEMU, as type."""

    def __init__(self, lxc, qemu=()):
        self.guests = [{"type": "lxc", "node": "pve", **g} for g in lxc] + [{"type": "qemu", "node": "pve", **g} for g in qemu]
        self.calls = 0
        self.fail = False
        api = self

        class Resources:
            def get(self, type=None):
                api.calls += 1
                if api.fail:
                    raise ConnectionError("Proxmox is down")
                assert type == "vm"
                # The cluster also lists storages and nodes; type=vm leaves them out
                return [dict(g) for g in api.guests]

        self.cluster = type("C", (), {"resources": Resources()})()


class TagMembership(unittest.TestCase):
    """Guests are part of the deployment when environment_id is one of their tags (C7)."""

    def client(self, lxc, qemu=()):
        client = module.ProxmoxClient()
        client.proxmox = FakeApi(lxc, qemu)
        client._connected = True
        return client

    def test_has_tag(self):
        self.assertTrue(module.has_tag("test", "test"))
        self.assertTrue(module.has_tag("slice-mb;test", "test"))
        self.assertTrue(module.has_tag("test,slice-mb", "slice-mb"))
        self.assertFalse(module.has_tag("testing", "test"))
        self.assertFalse(module.has_tag(None, "test"))

    def test_slice_guests_stay_in_the_deployment(self):
        lxc = [
            {"vmid": 100, "name": "lum-chi", "tags": "test"},
            {"vmid": 101, "name": "mb-hq-edge", "tags": "slice-mb;test"},
            {"vmid": 102, "name": "other", "tags": "other;slice-mb"},
        ]
        client = self.client(lxc, qemu=[{"vmid": 200, "name": "mb-wrk", "tags": "test;slice-mb"}])
        with patch.object(settings, "environment_id", "test"):
            self.assertEqual({c["name"] for c in client.get_deployment_containers()}, {"lum-chi", "mb-hq-edge", "mb-wrk"})
            self.assertEqual({c["name"] for c in client.get_instance_containers("mb")}, {"mb-hq-edge", "mb-wrk"})
            types = {c["name"]: c["resource_type"] for c in client.get_deployment_containers()}
            self.assertEqual(types, {"lum-chi": "lxc", "mb-hq-edge": "lxc", "mb-wrk": "qemu"})


class GuestCache(unittest.TestCase):
    """One /cluster/resources call serves every caller for a few seconds (proxmox_guest_cache_seconds)."""

    def setUp(self):
        self.client = module.ProxmoxClient()
        self.api = FakeApi([{"vmid": 100, "name": "lum-chi", "tags": "test", "status": "running"}])
        self.client.proxmox = self.api
        self.client._connected = True
        for p in (patch.object(settings, "environment_id", "test"), patch.object(settings, "proxmox_guest_cache_seconds", 60)):
            p.start()
            self.addCleanup(p.stop)

    def test_callers_share_one_list(self):
        self.client.get_deployment_containers()
        self.assertEqual(self.client.get_container_by_hostname("lum-chi")["status"], "running")
        self.client.get_instance_containers("mb")
        self.assertEqual(self.api.calls, 1)

    def test_a_caller_cant_change_the_cached_list(self):
        self.client.get_deployment_containers()[0]["status"] = "changed"
        self.assertEqual(self.client.get_container_by_hostname("lum-chi")["status"], "running")

    def test_it_expires(self):
        self.client.get_deployment_containers()
        with patch.object(settings, "proxmox_guest_cache_seconds", 0):
            self.client.get_deployment_containers()
        self.assertEqual(self.api.calls, 2)

    def test_a_state_change_forgets_it(self):
        node = type("N", (), {})()
        posted = []
        status = type("S", (), {"start": type("P", (), {"post": staticmethod(lambda: posted.append("start"))})(),
                                "stop": type("P", (), {"post": staticmethod(lambda: posted.append("stop"))})()})()
        node.status = status
        with patch.object(self.client, "_resource_api", return_value=node):
            self.client.get_deployment_containers()
            self.assertTrue(self.client.stop_container("lum-chi"))
            self.client.get_deployment_containers()
        self.assertEqual((posted, self.api.calls), (["stop"], 2))

    def test_a_failure_isnt_cached(self):
        self.api.fail = True
        self.assertEqual(self.client.get_deployment_containers(), [])
        self.api.fail = False
        self.client._connected = True
        self.assertEqual(len(self.client.get_deployment_containers()), 1)
        self.assertEqual(self.api.calls, 2)


class LockedGuests(unittest.TestCase):
    """A create that dies part way leaves an untagged CT<vmid> holding the create lock."""

    def test_every_locked_guest_tagged_or_not(self):
        client = module.ProxmoxClient()
        client.proxmox = FakeApi(
            [{"vmid": 100, "name": "lum-chi", "tags": "test", "status": "running"},
             {"vmid": 16769, "name": "CT16769", "status": "stopped", "lock": "create"}],
            qemu=[{"vmid": 200, "name": "ghosts", "tags": "test", "status": "running", "lock": "backup"}])
        client._connected = True
        self.assertEqual(client.get_locked_guests(), [
            {"vmid": 16769, "name": "CT16769", "node": "pve", "status": "stopped", "lock": "create", "resource_type": "lxc"},
            {"vmid": 200, "name": "ghosts", "node": "pve", "status": "running", "lock": "backup", "resource_type": "qemu"},
        ])
        client.proxmox.fail = True
        self.assertIsNone(client.get_locked_guests())


if __name__ == "__main__":
    unittest.main()
