"""Console session management service."""
import asyncio
import logging
import uuid
from typing import Dict, Optional
from datetime import datetime, timedelta
from services.ssh_client import ssh_client, InteractiveSSHSession
from services.topology_service import topology_service
from services.proxmox_client import proxmox_client
from config import settings

logger = logging.getLogger(__name__)


class ConsoleSession:
    """Represents an active console session."""

    def __init__(self, session_id: str, hostname: str, ssh_session: InteractiveSSHSession):
        self.session_id = session_id
        self.hostname = hostname
        self.ssh_session = ssh_session
        self.created_at = datetime.utcnow()
        self.last_activity = datetime.utcnow()

    def touch(self):
        """Update last activity timestamp."""
        self.last_activity = datetime.utcnow()

    def is_expired(self) -> bool:
        """Check if session has expired due to inactivity."""
        timeout = timedelta(seconds=settings.console_session_timeout)
        return datetime.utcnow() - self.last_activity > timeout

    @property
    def is_connected(self) -> bool:
        """Check if SSH session is still connected."""
        return self.ssh_session.is_connected


class ConsoleManager:
    """Manages console sessions across the application."""

    def __init__(self):
        self._sessions: Dict[str, ConsoleSession] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        """Start the console manager and cleanup task."""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Console manager started")

    async def stop(self):
        """Stop the console manager and close all sessions."""
        self._running = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Close all sessions
        for session in list(self._sessions.values()):
            await self.close_session(session.session_id)

        logger.info("Console manager stopped")

    async def _cleanup_loop(self):
        """Background task to clean up expired sessions."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Check every minute
                await self._cleanup_expired_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

    async def _cleanup_expired_sessions(self):
        """Close expired sessions."""
        expired = [
            session_id for session_id, session in self._sessions.items()
            if session.is_expired() or not session.is_connected
        ]

        for session_id in expired:
            logger.info(f"Cleaning up expired session {session_id}")
            await self.close_session(session_id)

    def _get_container_ip(self, hostname: str) -> Optional[str]:
        """Get the management IP address of a container from Proxmox."""
        # Get management IP directly from Proxmox (10.0.0.0/16 network)
        mgmt_ip = proxmox_client.get_container_management_ip(hostname)
        if mgmt_ip:
            return mgmt_ip

        # Fallback to loopback_ip from topology if Proxmox lookup fails
        logger.warning(f"Falling back to loopback_ip for {hostname}")
        routers = topology_service.get_routers_with_locations()
        for router in routers:
            if router['hostname'] == hostname:
                loopback = router.get('loopback_ip', '')
                if loopback:
                    return loopback.split('/')[0]
        return None

    async def create_session(
        self,
        hostname: str,
        cols: int = 80,
        rows: int = 24
    ) -> Optional[ConsoleSession]:
        """
        Create a new console session for a router.

        Args:
            hostname: Router hostname.
            cols: Terminal columns.
            rows: Terminal rows.

        Returns:
            ConsoleSession if successful, None otherwise.
        """
        ip = await asyncio.to_thread(self._get_container_ip, hostname)
        if not ip:
            logger.error(f"Could not find IP for hostname {hostname}")
            return None

        try:
            # Create interactive SSH session
            ssh_session = ssh_client.create_interactive_session(ip)
            connected = await ssh_session.connect(cols=cols, rows=rows)

            if not connected:
                logger.error(f"Failed to connect SSH session to {hostname}")
                return None

            # Create console session
            session_id = str(uuid.uuid4())
            session = ConsoleSession(session_id, hostname, ssh_session)
            self._sessions[session_id] = session

            logger.info(f"Created console session {session_id} for {hostname}")
            return session
        except Exception as e:
            logger.error(f"Error creating console session for {hostname}: {e}")
            return None

    def get_session(self, session_id: str) -> Optional[ConsoleSession]:
        """Get a console session by ID."""
        session = self._sessions.get(session_id)
        if session and session.is_connected:
            session.touch()
            return session
        return None

    async def close_session(self, session_id: str) -> bool:
        """
        Close a console session.

        Args:
            session_id: Session ID to close.

        Returns:
            True if session was closed.
        """
        session = self._sessions.pop(session_id, None)
        if session:
            await session.ssh_session.close()
            logger.info(f"Closed console session {session_id}")
            return True
        return False

    def get_session_count(self) -> int:
        """Get the number of active sessions."""
        return len(self._sessions)

    def get_sessions_for_hostname(self, hostname: str) -> list:
        """Get all sessions for a specific hostname."""
        return [
            session for session in self._sessions.values()
            if session.hostname == hostname
        ]


# Global console manager instance
console_manager = ConsoleManager()
