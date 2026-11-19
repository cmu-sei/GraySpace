"""services/ca_service.py: the step commands it runs on a CA.

Run from grayspace-web/backend: python -m unittest discover -s tests -t .
"""

import unittest
from unittest.mock import AsyncMock, patch

from services import ca_service


class Revoke(unittest.IsolatedAsyncioTestCase):
    async def revoke(self, returns=(0, "Certificate with Serial Number 42 has been revoked.", "")):
        run = AsyncMock(return_value=returns)
        with patch.object(ca_service.ssh_client, "execute_command", run), \
             patch.object(ca_service.CAService, "_get_management_ip", return_value="10.0.0.9"):
            result = await ca_service.ca_service.revoke_certificate("ca-graytrust", "42")
        return result, run.await_args.args[1]

    async def test_revokes_with_a_revocation_token(self):
        # step ca revoke has no provisioner or password flags (step 0.28): it takes a token
        result, cmd = await self.revoke()
        self.assertTrue(result["success"])
        self.assertIn("step ca token '42' --revoke --provisioner admin --password-file /etc/step-ca/secrets/password.txt", cmd)
        self.assertIn("step ca revoke '42' --token \"$token\"", cmd)
        self.assertNotIn("--provisioner-password-file", cmd)

    async def test_failure_reports_the_output(self):
        result, _ = await self.revoke(returns=(1, "certificate not found", ""))
        self.assertFalse(result["success"])
        self.assertIn("certificate not found", result["message"])


if __name__ == "__main__":
    unittest.main()
