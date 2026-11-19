"""SDN zones and vnets belong to a deployment by exact zone ID, not prefix (services/network_service.py)."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from config import settings
from services.network_service import network_service
from services.proxmox_client import proxmox_client

ZONES = [{"zone": "ab1", "type": "simple"}, {"zone": "ab2", "type": "vlan"}, {"zone": "abc1", "type": "simple"}, {"zone": "abc2", "type": "vlan"}]
VNETS = [{"vnet": "abv100", "zone": "ab1"}, {"vnet": "abcv100", "zone": "abc1"}, {"vnet": "abcs000", "zone": "abc1"}]


class SharedCluster(unittest.TestCase):
    def setUp(self):
        sdn = SimpleNamespace(zones=SimpleNamespace(get=lambda: ZONES), vnets=SimpleNamespace(get=lambda: VNETS))
        api = SimpleNamespace(cluster=SimpleNamespace(sdn=sdn))
        for p in (patch.object(settings, "environment_id", "ab"), patch.object(proxmox_client, "_connect", MagicMock()),
                  patch.object(proxmox_client, "proxmox", api, create=True)):
            p.start()
            self.addCleanup(p.stop)

    def test_a_shorter_id_doesnt_see_a_longer_ones_zones(self):
        self.assertEqual([z.zone_id for z in network_service.get_zones()], ["ab1", "ab2"])
        self.assertEqual([v.vnet_id for v in network_service.get_vnets()], ["abv100"])

    def test_moving_a_vnet_to_another_deployments_zone_is_refused(self):
        result = network_service.change_vnet_zone("abv100", "abc2", vlan_tag=10)
        self.assertFalse(result.success)
        self.assertIn("does not belong", result.message)


if __name__ == "__main__":
    unittest.main()
