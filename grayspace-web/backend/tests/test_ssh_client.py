"""The SSH key is read again after a reset (a deploy writes a new one; services/base_jobs.refresh)."""

import unittest
from unittest.mock import patch

from services import ssh_client as module


class KeyReload(unittest.TestCase):
    def test_reset_reads_the_key_again(self):
        client = module.SSHClient()
        with patch.object(module.paramiko.RSAKey, "from_private_key_file", side_effect=["old", "new"]) as read:
            self.assertEqual(client._load_key(), "old")
            self.assertEqual(client._load_key(), "old")
            client.reset()
            self.assertEqual(client._load_key(), "new")
        self.assertEqual(read.call_count, 2)


if __name__ == "__main__":
    unittest.main()
