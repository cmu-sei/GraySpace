"""The Proxmox API as the control plane sees it through a connection: the Platform tab's test and
discovery, and the capacity of nodes no running deployment reports (control/platform_api.py,
accounts.get_nodes). Calls are made per request; nothing is kept."""
import asyncio
import contextlib
from typing import Dict, List, Optional, Set, Tuple

import httpx

Check = Tuple[str, str, str]  # (name, status ok|warning|error, message)


class ProxmoxAPI:
    """One connection's API session: an API token, or a ticket from a username and password."""

    def __init__(self, conn: Dict, timeout: float = 10):
        self.conn = conn
        self.base = f"https://{conn['host']}:{conn.get('port') or 8006}/api2/json"
        self.client = httpx.AsyncClient(timeout=timeout, verify=bool(conn.get("verify_ssl")))
        self.headers: Dict[str, str] = {}

    async def __aenter__(self):
        if self.conn.get("auth", "token") == "token":
            if not self.conn.get("api_token"):
                raise ProbeError("The connection has no API token")
            self.headers["Authorization"] = f"PVEAPIToken={self.conn['api_token']}"
        else:
            if not (self.conn.get("username") and self.conn.get("password")):
                raise ProbeError("The connection has no username and password")
            r = await self.client.post(f"{self.base}/access/ticket", data={"username": self.conn["username"], "password": self.conn["password"]})
            if r.status_code != 200:
                raise ProbeError(f"Proxmox refused the username and password ({r.status_code})")
            ticket = r.json()["data"]
            self.client.cookies.set("PVEAuthCookie", ticket["ticket"])
            self.headers["CSRFPreventionToken"] = ticket["CSRFPreventionToken"]
        return self

    async def __aexit__(self, *exc):
        await self.client.aclose()

    async def get(self, path: str):
        r = await self.client.get(f"{self.base}{path}", headers=self.headers)
        if r.status_code in (401, 403):
            raise ProbeError(f"Proxmox refused the credentials ({r.status_code}) at {path}")
        r.raise_for_status()
        return r.json().get("data")


class ProbeError(Exception):
    pass


async def _ssh_answers(host: str, port: int = 22) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 5)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


async def check_proxmox(conn: Dict) -> List[Check]:
    """The API answers and accepts the credentials, each node exists and has its storage and
    bridges, and SSH answers (Terraform uploads snippets over it)."""
    checks: List[Check] = []
    try:
        async with ProxmoxAPI(conn) as api:
            version = await api.get("/version")
            checks.append(("api", "ok", f"Proxmox VE {version.get('version')} at {conn['host']} accepts the credentials"))
            found = {n["node"]: n for n in await api.get("/nodes") or []}
            # A guest's bridge can be an SDN vnet (e.g. a vlan zone's vnet as the management network);
            # /nodes/{node}/network lists only the host's own bridges
            vnets: Dict[str, str] = {}
            with contextlib.suppress(httpx.HTTPError, ProbeError):
                vnets = {v["vnet"]: v.get("zone") for v in await api.get("/cluster/sdn/vnets") or []}
            for node in conn.get("nodes") or []:
                name = node["name"]
                if name not in found:
                    checks.append((f"node {name}", "error", f"No node {name}; the cluster has {', '.join(sorted(found)) or 'none'}"))
                    continue
                storages = {s["storage"] for s in await api.get(f"/nodes/{name}/storage") or []}
                bridges = {i["iface"] for i in await api.get(f"/nodes/{name}/network") or [] if i.get("type") in ("bridge", "OVSBridge")}
                zones: Set[str] = set()
                if vnets:
                    with contextlib.suppress(httpx.HTTPError, ProbeError):
                        zones = {z["zone"] for z in await api.get(f"/nodes/{name}/sdn/zones") or [] if z.get("status") == "available"}
                missing = [f"storage {node[k]}" for k in ("blockstore", "filestore") if node.get(k) and node[k] not in storages]
                for k in ("mgmt_bridge", "vm_bridge"):
                    bridge = node.get(k)
                    if not bridge or bridge in bridges:
                        continue
                    if bridge not in vnets:
                        missing.append(f"bridge {bridge}")
                    elif vnets[bridge] not in zones:
                        missing.append(f"SDN vnet {bridge} applied (zone {vnets[bridge]} isn't available on it; apply SDN)")
                status = found[name].get("status")
                if missing:
                    checks.append((f"node {name}", "error", f"{name} ({status}) has no " + ", ".join(missing)))
                else:
                    checks.append((f"node {name}", "ok" if status == "online" else "warning", f"{name} is {status}; its storage and bridges are there"))
    except ProbeError as e:
        checks.append(("api", "error", str(e)))
    except (httpx.HTTPError, ValueError, KeyError) as e:
        checks.append(("api", "error", f"The Proxmox API at {conn['host']}:{conn.get('port') or 8006} didn't answer: {e}"))
    if await _ssh_answers(conn["host"]):
        checks.append(("ssh", "ok", f"SSH answers on {conn['host']}"))
    else:
        checks.append(("ssh", "warning", f"SSH doesn't answer on {conn['host']}:22; Terraform uploads snippets over it"))
    return checks


async def discover(conn: Dict) -> Dict:
    """The cluster's nodes with their storage and bridges, and its SDN vnets, to fill the node form."""
    async with ProxmoxAPI(conn) as api:
        version = (await api.get("/version") or {}).get("version")
        nodes = []
        for n in sorted(await api.get("/nodes") or [], key=lambda n: n["node"]):
            storages, bridges = [], []
            if n.get("status") == "online":
                storages = [{"storage": s["storage"], "type": s.get("type"), "content": s.get("content", "")}
                            for s in await api.get(f"/nodes/{n['node']}/storage") or []]
                bridges = sorted(i["iface"] for i in await api.get(f"/nodes/{n['node']}/network") or [] if i.get("type") in ("bridge", "OVSBridge"))
            nodes.append({"name": n["node"], "status": n.get("status"), "storages": storages, "bridges": bridges})
        vnets: List[str] = []
        with contextlib.suppress(httpx.HTTPError, ProbeError):
            vnets = sorted(v["vnet"] for v in await api.get("/cluster/sdn/vnets") or [])
        return {"version": version, "nodes": nodes, "vnets": vnets}


async def capacity(conn: Dict, node: Dict) -> Optional[Dict]:
    """A node's CPU, memory and block storage, as a worker's GET /capabilities reports them."""
    try:
        async with ProxmoxAPI(conn, timeout=8) as api:
            status = await api.get(f"/nodes/{node['name']}/status") or {}
            storage = None
            if node.get("blockstore"):
                with contextlib.suppress(httpx.HTTPError):
                    storage = await api.get(f"/nodes/{node['name']}/storage/{node['blockstore']}/status")
    except (ProbeError, httpx.HTTPError, ValueError, KeyError):
        return None
    memory = status.get("memory") or {}
    return {"cpus": (status.get("cpuinfo") or {}).get("cpus"), "cpu_usage": status.get("cpu"), "memory_total": memory.get("total"),
            "memory_free": memory.get("available", memory.get("free")), "storage": node.get("blockstore"),
            "storage_total": (storage or {}).get("total"), "storage_free": (storage or {}).get("avail")}
