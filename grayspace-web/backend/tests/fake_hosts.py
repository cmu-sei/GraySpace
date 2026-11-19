"""A stand-in for SSH to DNS and CA containers: each host is a directory, and commands run in a
local shell with absolute paths moved under it. chown and rndc succeed without doing anything."""

import asyncio
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


class FakeHosts:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp())
        self.commands = []

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def path(self, host, path):
        return self.root / host / path.lstrip("/")

    def write(self, host, path, content):
        p = self.path(host, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def read(self, host, path):
        p = self.path(host, path)
        return p.read_text() if p.exists() else None

    def _local(self, host, command):
        base = str(self.root / host)
        command = re.sub(r"(?<![\w.$])(?<!INCLUDE )(/(?:var|etc|tmp|usr)/)", lambda m: base + m.group(1), command)
        command = re.sub(r"chown \S+ ", "true ", command)
        command = re.sub(r"rndc reload( \S+)?", "true", command)
        for d in ("var/bind", "etc/bind/zones.d"):
            (self.root / host / d).mkdir(parents=True, exist_ok=True)
        return command

    async def run(self, host, command):
        self.commands.append((host, command))
        result = await asyncio.to_thread(subprocess.run, ["bash", "-c", self._local(host, command)], capture_output=True, text=True)
        return result.returncode, result.stdout, result.stderr

    async def read_file(self, host, path):
        return self.read(host, path)

    async def write_file(self, host, path, content):
        self.write(host, path, content)
        return True
