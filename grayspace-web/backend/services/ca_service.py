"""Certificate Authority service for managing step-ca instances."""
import asyncio
import json
import logging
import random
from typing import Any, Dict, List, Optional

from services.proxmox_client import proxmox_client
from services.ssh_client import ssh_client
from services.topology_service import topology_service

logger = logging.getLogger(__name__)

CA_PASSWORD = "grayspace-ca"
STEPPATH = "/etc/step-ca"
PASSWORD_FILE = f"{STEPPATH}/secrets/password.txt"
ROOT_CERT = f"{STEPPATH}/certs/root_ca.crt"


class CAService:
    """Service for interacting with step-ca instances on CA containers."""

    def _get_ca_url(self) -> str:
        """Get the step-ca API URL for commands running on the CA itself."""
        return "https://localhost"

    def _get_management_ip(self, hostname: str) -> Optional[str]:
        """Get the management IP for SSH access to a CA container."""
        return proxmox_client.get_container_management_ip(hostname)

    def _get_deployed_ca_hostnames(self) -> List[str]:
        """Get list of deployed CA hostnames."""
        return [
            s["hostname"] for s in topology_service.get_ca_servers()
            if s["deployed"]
        ]

    async def get_certificates(self, hostname: str) -> List[Dict[str, Any]]:
        """Get certificates issued by a specific CA via SSH."""
        ip = await asyncio.to_thread(self._get_management_ip, hostname)
        if not ip:
            raise RuntimeError(f"Cannot resolve management IP for '{hostname}'")

        # Use step CLI to inspect the CA database
        # step-ca stores certs in badger DB; we query via the admin API
        cmd = (
            f"STEPPATH={STEPPATH} step ca admin list "
            f"--admin-provisioner admin "
            f"--admin-password-file {PASSWORD_FILE} "
            f"--ca-url {self._get_ca_url()} "
            f"--root {ROOT_CERT} 2>/dev/null || true"
        )
        exit_code, stdout, stderr = await ssh_client.execute_command(ip, cmd, timeout=15)

        # Parse certificates from step output
        # For now, return empty list — step-ca's cert listing requires admin API
        # which we'll query via the web UI API on the CA itself
        try:
            ca_ip = await asyncio.to_thread(self._get_management_ip, hostname)
            cmd = "curl -s http://localhost/api/certificates 2>/dev/null || echo '{\"certificates\":[]}'"
            exit_code, stdout, stderr = await ssh_client.execute_command(ca_ip, cmd, timeout=10)
            data = json.loads(stdout.strip())
            return data.get("certificates", [])
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to get certificates from {hostname}: {e}")
            return []

    async def get_root_certificate(self, hostname: str) -> Optional[str]:
        """A CA's root certificate (PEM), or None when the CA can't be reached."""
        ip = await asyncio.to_thread(self._get_management_ip, hostname)
        if not ip:
            return None
        exit_code, stdout, _ = await ssh_client.execute_command(ip, f"cat {ROOT_CERT}", timeout=15)
        if exit_code != 0 or "BEGIN CERTIFICATE" not in stdout:
            return None
        return stdout.strip() + "\n"

    async def get_ca_bundle(self) -> Dict[str, Optional[str]]:
        """{CA hostname: root PEM or None} for every deployed CA."""
        return {h: await self.get_root_certificate(h) for h in await asyncio.to_thread(self._get_deployed_ca_hostnames)}

    async def issue_certificate(self, domain: str, ca_hostname: Optional[str] = None,
                                sans: Optional[List[str]] = None, not_after: Optional[str] = None) -> Dict[str, Any]:
        """Issue a certificate for a domain using a CA's step-ca instance.

        sans adds names to the certificate; not_after sets its validity (e.g. "720h"), up to the
        CA's maximum. Both need the CA web UI (task configure)."""
        deployed_cas = await asyncio.to_thread(self._get_deployed_ca_hostnames)
        if not deployed_cas:
            return {"success": False, "message": "No CA servers are deployed"}

        if ca_hostname:
            if ca_hostname not in deployed_cas:
                return {"success": False, "message": f"CA '{ca_hostname}' is not deployed"}
        else:
            ca_hostname = random.choice(deployed_cas)

        ip = await asyncio.to_thread(self._get_management_ip, ca_hostname)
        if not ip:
            return {"success": False, "message": f"Cannot resolve management IP for '{ca_hostname}'"}

        body = {"domain": domain}
        if sans:
            body["sans"] = list(sans)
        if not_after:
            body["not_after"] = not_after
        payload = json.dumps(body).replace("'", "'\\''")
        cmd = (
            f"curl -s -X POST http://localhost/api/issue "
            f"-H 'Content-Type: application/json' "
            f"-d '{payload}'"
        )

        exit_code, stdout, stderr = await ssh_client.execute_command(ip, cmd, timeout=30)

        if exit_code != 0:
            error_msg = stderr.strip() or stdout.strip()
            return {"success": False, "message": f"Certificate issuance failed: {error_msg}", "ca_hostname": ca_hostname}

        try:
            result = json.loads(stdout.strip())
        except json.JSONDecodeError:
            return {"success": False, "message": f"Invalid response from CA: {stdout[:200]}", "ca_hostname": ca_hostname}

        if not result.get("success"):
            return {"success": False, "message": result.get("error", "Unknown error"), "ca_hostname": ca_hostname}

        return {
            "success": True,
            "message": f"Certificate issued for {domain} by {ca_hostname}",
            "certificate": result.get("certificate", ""),
            "private_key": result.get("private_key", ""),
            "ca_hostname": ca_hostname,
        }

    async def revoke_certificate(self, hostname: str, serial: str, reason: Optional[str] = None) -> Dict[str, Any]:
        """Revoke a certificate by serial number."""
        ip = await asyncio.to_thread(self._get_management_ip, hostname)
        if not ip:
            return {"success": False, "message": f"Cannot resolve management IP for '{hostname}'"}

        reason_flag = f"--reason '{reason}'" if reason else ""

        # step ca revoke takes no provisioner flags: it needs a revocation token, minted with the
        # admin provisioner's password
        ca = f"--ca-url {self._get_ca_url()} --root {ROOT_CERT}"
        cmd = (
            f"export STEPPATH={STEPPATH}; "
            f"token=$(step ca token '{serial}' --revoke --provisioner admin "
            f"--password-file {PASSWORD_FILE} {ca} 2>/tmp/grayspace-revoke.err) "
            f"|| {{ cat /tmp/grayspace-revoke.err; exit 1; }}; "
            f"step ca revoke '{serial}' --token \"$token\" {ca} {reason_flag} 2>&1"
        )

        exit_code, stdout, stderr = await ssh_client.execute_command(ip, cmd, timeout=15)

        if exit_code != 0:
            error_msg = stderr.strip() or stdout.strip()
            return {"success": False, "message": f"Revocation failed: {error_msg}"}

        return {"success": True, "message": f"Certificate {serial} revoked on {hostname}"}


ca_service = CAService()
