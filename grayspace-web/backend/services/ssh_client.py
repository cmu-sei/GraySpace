"""SSH client service for executing commands on routers."""
import asyncio
import logging
from typing import Optional, Tuple, AsyncGenerator
import paramiko
from config import settings

logger = logging.getLogger(__name__)


class SSHClient:
    """Async SSH client wrapper using Paramiko."""

    def __init__(self):
        self._key: Optional[paramiko.RSAKey] = None
        self._key_loaded = False

    def _load_key(self) -> paramiko.RSAKey:
        """Load SSH private key from file (lazy initialization)."""
        if not self._key_loaded:
            try:
                self._key = paramiko.RSAKey.from_private_key_file(settings.ssh_key_path)
                self._key_loaded = True
                logger.info(f"Loaded SSH key from {settings.ssh_key_path}")
            except Exception as e:
                logger.error(f"Failed to load SSH key: {e}")
                raise
        return self._key

    def reset(self):
        """Forget the key, so the next use reads it again (a deploy writes a new one)."""
        self._key = None
        self._key_loaded = False

    async def execute_command(
        self,
        hostname: str,
        command: str,
        timeout: int = 30
    ) -> Tuple[int, str, str]:
        """
        Execute a command on a remote host via SSH.

        Args:
            hostname: Target hostname or IP address.
            command: Command to execute.
            timeout: Command timeout in seconds.

        Returns:
            Tuple of (exit_code, stdout, stderr).
        """
        def _execute():
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            try:
                key = self._load_key()
                client.connect(
                    hostname,
                    username=settings.ssh_username,
                    pkey=key,
                    timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False
                )

                stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
                exit_code = stdout.channel.recv_exit_status()
                stdout_str = stdout.read().decode('utf-8', errors='replace')
                stderr_str = stderr.read().decode('utf-8', errors='replace')

                return exit_code, stdout_str, stderr_str
            finally:
                client.close()

        return await asyncio.to_thread(_execute)

    async def execute_command_stream(
        self,
        hostname: str,
        command: str,
        timeout: int = 300
    ) -> AsyncGenerator[str, None]:
        """
        Execute a command and stream output line by line.

        Args:
            hostname: Target hostname or IP address.
            command: Command to execute.
            timeout: Command timeout in seconds.

        Yields:
            Output lines as they become available.
        """
        def _create_connection():
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            key = self._load_key()
            client.connect(
                hostname,
                username=settings.ssh_username,
                pkey=key,
                timeout=timeout,
                look_for_keys=False,
                allow_agent=False
            )
            return client

        client = await asyncio.to_thread(_create_connection)

        try:
            def _exec():
                return client.exec_command(command, timeout=timeout)

            stdin, stdout, stderr = await asyncio.to_thread(_exec)

            # Stream stdout
            while True:
                line = await asyncio.to_thread(stdout.readline)
                if not line:
                    break
                yield line

            # Also yield any stderr at the end
            stderr_output = await asyncio.to_thread(stderr.read)
            if stderr_output:
                for line in stderr_output.decode('utf-8', errors='replace').splitlines(keepends=True):
                    yield f"[stderr] {line}"
        finally:
            await asyncio.to_thread(client.close)

    def create_interactive_session(self, hostname: str) -> 'InteractiveSSHSession':
        """
        Create an interactive SSH session for console access.

        Args:
            hostname: Target hostname or IP address.

        Returns:
            InteractiveSSHSession instance.
        """
        return InteractiveSSHSession(hostname, self._load_key())


class InteractiveSSHSession:
    """Interactive SSH session for console/terminal access."""

    def __init__(self, hostname: str, key: paramiko.RSAKey):
        self.hostname = hostname
        self.key = key
        self.client: Optional[paramiko.SSHClient] = None
        self.channel: Optional[paramiko.Channel] = None
        self._connected = False

    async def connect(self, cols: int = 80, rows: int = 24) -> bool:
        """
        Establish interactive SSH connection.

        Args:
            cols: Terminal columns.
            rows: Terminal rows.

        Returns:
            True if connection successful.
        """
        def _connect():
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.client.connect(
                self.hostname,
                username=settings.ssh_username,
                pkey=self.key,
                timeout=30,
                look_for_keys=False,
                allow_agent=False
            )
            self.channel = self.client.invoke_shell(
                term='xterm-256color',
                width=cols,
                height=rows
            )
            self.channel.settimeout(0.1)  # Non-blocking reads
            return True

        try:
            await asyncio.to_thread(_connect)
            self._connected = True
            logger.info(f"Interactive SSH session established to {self.hostname}")
            return True
        except Exception as e:
            logger.error(f"Failed to establish interactive SSH session to {self.hostname}: {e}")
            return False

    async def read(self, size: int = 4096) -> bytes:
        """
        Read data from the channel.

        Args:
            size: Maximum bytes to read.

        Returns:
            Bytes read from channel, or empty bytes if no data available.
        """
        if not self.channel or not self._connected:
            return b''

        def _read():
            try:
                if self.channel.recv_ready():
                    return self.channel.recv(size)
                return b''
            except Exception:
                return b''

        return await asyncio.to_thread(_read)

    async def write(self, data: bytes) -> bool:
        """
        Write data to the channel.

        Args:
            data: Bytes to send.

        Returns:
            True if write successful.
        """
        if not self.channel or not self._connected:
            return False

        def _write():
            try:
                self.channel.send(data)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_write)

    async def resize(self, cols: int, rows: int) -> bool:
        """
        Resize the terminal.

        Args:
            cols: New terminal columns.
            rows: New terminal rows.

        Returns:
            True if resize successful.
        """
        if not self.channel or not self._connected:
            return False

        def _resize():
            try:
                self.channel.resize_pty(width=cols, height=rows)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_resize)

    async def close(self):
        """Close the SSH session."""
        self._connected = False

        def _close():
            if self.channel:
                try:
                    self.channel.close()
                except Exception:
                    pass
            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass

        await asyncio.to_thread(_close)
        logger.info(f"Interactive SSH session closed for {self.hostname}")

    @property
    def is_connected(self) -> bool:
        """Check if session is still connected."""
        if not self._connected or not self.channel:
            return False
        return not self.channel.closed


# Global SSH client instance
ssh_client = SSHClient()
