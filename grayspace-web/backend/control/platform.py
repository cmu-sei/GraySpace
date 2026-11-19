"""Platform connections: the Proxmox clusters and Crucible instances deployments use, defined once
by admins (Admin > Platform, control/platform_api.py; docs/deployments.md, Platform connections).

    <root>/platform.json           the connections, without secrets
    <root>/platform-secrets.json   their passwords and tokens (0600), never returned

A managed deployment's placement names a Proxmox connection and one of its nodes; with
options.player, a Crucible connection too. Its environment file (registry.write_environment)
sources the site's file for everything else, unsets the site's Proxmox and Crucible settings, and
exports the connections': no site credential reaches another cluster.

On first start the site file's Proxmox and Crucible settings become the connections named "site",
and existing deployments get a placement (import_site, migrate_records).
"""
import contextlib
import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from control import envfile
from control.settings import settings
from control.store import Store

logger = logging.getLogger("control")

_platform = Store("platform.json")
_secrets = Store("platform-secrets.json")

PROXMOX_SECRETS = ("api_token", "password", "ssh_password")
PROXMOX_PUBLIC = ("name", "host", "port", "verify_ssl", "auth", "username", "ssh_username", "default_node", "apply_parallelism",
                  "plan_parallelism", "proxy_mode", "proxy", "no_proxy")
# Terraform's -parallelism, read by the jobs (services/slices/environment.parallelism) and task apply
PARALLELISM_VARS = {"apply_parallelism": "GRAYSPACE_APPLY_PARALLELISM", "plan_parallelism": "GRAYSPACE_PLAN_PARALLELISM"}
# The guests' proxy (ansible/inventory/proxy.ini). Not in MANAGED_VARS: with proxy_mode site (the
# default) the site file's apply; none exports them empty, which proxy.ini takes as no proxy
PROXY_VARS = {"proxy": "TF_VAR_proxy", "no_proxy": "TF_VAR_no_proxy"}
NODE_VARS = {"name": "TF_VAR_proxmox_node", "blockstore": "TF_VAR_proxmox_blockstore", "filestore": "TF_VAR_proxmox_filestore",
             "mgmt_bridge": "TF_VAR_proxmox_mgmt_bridge", "vm_bridge": "TF_VAR_proxmox_vm_bridge"}
CRUCIBLE_SECRETS = ("password",)
CRUCIBLE_PUBLIC = ("name", "auth_url", "token_url", "client_id", "client_scopes", "username", "vm_api_url", "player_api_url",
                   "caster_api_url", "service_user_id", "player_url")
# TF_VAR_crucible_<name> for each setting; user_id is the service user
CRUCIBLE_VARS = {"username": "username", "password": "password", "auth_url": "auth_url", "token_url": "token_url", "client_id": "client_id",
                 "client_scopes": "client_scopes", "vm_api_url": "vm_api_url", "player_api_url": "player_api_url",
                 "caster_api_url": "caster_api_url", "user_id": "service_user_id", "player_url": "player_url"}
# What a connection decides: unset after the site's file is sourced, so only the connection's apply
MANAGED_VARS = (["TF_VAR_proxmox_host_addr", "PROXMOX_PORT", "PROXMOX_VE_ENDPOINT", "PROXMOX_VE_API_TOKEN", "PROXMOX_VE_USERNAME",
                 "PROXMOX_VE_PASSWORD", "PROXMOX_VE_SSH_USERNAME", "PROXMOX_VE_SSH_PASSWORD", "TF_VAR_configure_crucible", "GRAYSPACE_PLAYER"]
                + list(NODE_VARS.values()) + list(PARALLELISM_VARS.values()) + [f"TF_VAR_crucible_{k}" for k in CRUCIBLE_VARS])


class PlatformError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


# ----- The store -----

def data() -> Dict:
    """A copy: changing it changes nothing until _save."""
    found = json.loads(json.dumps(_platform.read()))
    return {"version": 1, "proxmox": {}, "crucible": {}, "default_proxmox": None, "imported_from": None, **found}


def secrets() -> Dict:
    return {"proxmox": {}, "crucible": {}, **json.loads(json.dumps(_secrets.read()))}


def _save(public: Dict, secret: Dict) -> None:
    _secrets.write(secret)
    _platform.write(public)


def exists() -> bool:
    return _platform.path().exists()


def proxmox(connection_id: Optional[str]) -> Optional[Dict]:
    """A Proxmox connection with its secrets, or None."""
    found = data()["proxmox"].get(connection_id or "")
    if found is None:
        return None
    return {**found, "id": connection_id, **secrets()["proxmox"].get(connection_id, {})}


def crucible(connection_id: Optional[str]) -> Optional[Dict]:
    found = data()["crucible"].get(connection_id or "")
    if found is None:
        return None
    return {**found, "id": connection_id, **secrets()["crucible"].get(connection_id, {})}


def default_proxmox() -> Optional[str]:
    d = data()
    if d.get("default_proxmox") in d["proxmox"]:
        return d["default_proxmox"]
    return next(iter(sorted(d["proxmox"])), None)


def node(connection: Dict, name: Optional[str]) -> Optional[Dict]:
    name = name or connection.get("default_node") or (connection["nodes"][0]["name"] if connection.get("nodes") else None)
    return next((n for n in connection.get("nodes") or [] if n["name"] == name), None)


def view_users(kind: str, connection_id: str) -> List[str]:
    from control import registry

    out = []
    for record in registry.all_records():
        placement = record.get("placement") or {}
        options = record.get("options") or {}
        if kind == "proxmox" and placement.get("connection") == connection_id:
            out.append(record["id"])
        elif kind == "crucible" and options.get("player") and player_connection(record) == connection_id:
            out.append(record["id"])
    return out


def users_of_node(connection_id: str, node_name: str) -> List[str]:
    from control import registry

    out = []
    for record in registry.all_records():
        placement = record.get("placement") or {}
        if placement.get("connection") == connection_id:
            conn = proxmox(connection_id)
            used = node(conn, placement.get("node")) if conn else None
            if used and used["name"] == node_name:
                out.append(record["id"])
    return out


def view() -> Dict:
    """Both lists for GET /api/v1/platform: no secret values, only which are set."""
    d, s = data(), secrets()
    out_proxmox = []
    for cid, c in sorted(d["proxmox"].items()):
        out_proxmox.append({**c, "id": cid, "name": c.get("name") or cid,
                            "default_node": c.get("default_node") or (c["nodes"][0]["name"] if c.get("nodes") else ""),
                            "secrets": sorted(k for k, v in s["proxmox"].get(cid, {}).items() if v), "deployments": view_users("proxmox", cid)})
    out_crucible = [{**c, "id": cid, "name": c.get("name") or cid, "secrets": sorted(k for k, v in s["crucible"].get(cid, {}).items() if v),
                     "deployments": view_users("crucible", cid)} for cid, c in sorted(d["crucible"].items())]
    return {"proxmox": out_proxmox, "crucible": out_crucible, "default_proxmox": default_proxmox(), "imported_from": d.get("imported_from")}


def _split(body: Dict, public_keys, secret_keys) -> Tuple[Dict, Dict]:
    public = {k: body[k] for k in public_keys if k in body and body[k] is not None}
    secret = {k: body[k] for k in secret_keys if k in body and body[k] is not None}
    return public, secret


def _check_nodes(conn: Dict) -> None:
    names = [n["name"] for n in conn.get("nodes") or []]
    if not names:
        raise PlatformError(422, "A Proxmox connection needs at least one node")
    if len(set(names)) != len(names):
        raise PlatformError(422, "A node is listed twice")
    if conn.get("default_node") and conn["default_node"] not in names:
        raise PlatformError(422, f"The default node '{conn['default_node']}' isn't one of the connection's nodes")


def put_proxmox(connection_id: str, body: Dict, create: bool) -> Dict:
    """Create or change a connection. body: the request's fields (None: unchanged; secrets "": cleared)."""
    with _platform.locked():
        d, s = data(), secrets()
        if create and (connection_id in d["proxmox"] or connection_id in d["crucible"]):
            raise PlatformError(409, f"A connection '{connection_id}' already exists")
        if not create and connection_id not in d["proxmox"]:
            raise PlatformError(404, f"No Proxmox connection '{connection_id}'")
        public, secret = _split(body, PROXMOX_PUBLIC, PROXMOX_SECRETS)
        if body.get("nodes") is not None:
            public["nodes"] = [{k: v for k, v in n.items() if v not in (None, "")} for n in body["nodes"]]
        conn = {"port": 8006, "verify_ssl": False, "auth": "token", **d["proxmox"].get(connection_id, {}), **public}
        for key in PARALLELISM_VARS:
            if conn.get(key) == 0:  # cleared: the server's default
                del conn[key]
        if conn.get("proxy_mode") == "site":  # the default
            del conn["proxy_mode"]
        for key in PROXY_VARS:
            if conn.get(key) == "":  # cleared
                del conn[key]
        if conn.get("proxy_mode") == "custom" and not conn.get("proxy"):
            raise PlatformError(422, "proxy_mode custom needs a proxy (http://host:port)")
        _check_nodes(conn)
        d["proxmox"][connection_id] = conn
        s["proxmox"][connection_id] = {k: v for k, v in {**s["proxmox"].get(connection_id, {}), **secret}.items() if v}
        if d.get("default_proxmox") is None:
            d["default_proxmox"] = connection_id
        _save(d, s)
        return conn


def delete_proxmox(connection_id: str) -> None:
    with _platform.locked():
        d, s = data(), secrets()
        if connection_id not in d["proxmox"]:
            raise PlatformError(404, f"No Proxmox connection '{connection_id}'")
        used = view_users("proxmox", connection_id)
        if used:
            raise PlatformError(409, f"Deployments run on '{connection_id}': {', '.join(used)}")
        del d["proxmox"][connection_id]
        s["proxmox"].pop(connection_id, None)
        if d.get("default_proxmox") == connection_id:
            d["default_proxmox"] = next(iter(sorted(d["proxmox"])), None)
        _save(d, s)


def put_crucible(connection_id: str, body: Dict, create: bool) -> Dict:
    with _platform.locked():
        d, s = data(), secrets()
        if create and (connection_id in d["crucible"] or connection_id in d["proxmox"]):
            raise PlatformError(409, f"A connection '{connection_id}' already exists")
        if not create and connection_id not in d["crucible"]:
            raise PlatformError(404, f"No Crucible connection '{connection_id}'")
        public, secret = _split(body, CRUCIBLE_PUBLIC, CRUCIBLE_SECRETS)
        conn = {**d["crucible"].get(connection_id, {}), **public}
        for key in ("token_url", "player_api_url"):
            if not conn.get(key):
                raise PlatformError(422, f"A Crucible connection needs {key}")
        d["crucible"][connection_id] = conn
        s["crucible"][connection_id] = {k: v for k, v in {**s["crucible"].get(connection_id, {}), **secret}.items() if v}
        _save(d, s)
        return conn


def delete_crucible(connection_id: str) -> None:
    with _platform.locked():
        d, s = data(), secrets()
        if connection_id not in d["crucible"]:
            raise PlatformError(404, f"No Crucible connection '{connection_id}'")
        used = view_users("crucible", connection_id)
        if used:
            raise PlatformError(409, f"Deployments have a Player view through '{connection_id}': {', '.join(used)}")
        del d["crucible"][connection_id]
        s["crucible"].pop(connection_id, None)
        _save(d, s)


def set_default(connection_id: Optional[str]) -> None:
    with _platform.locked():
        d, s = data(), secrets()
        if connection_id is not None and connection_id not in d["proxmox"]:
            raise PlatformError(404, f"No Proxmox connection '{connection_id}'")
        d["default_proxmox"] = connection_id
        _save(d, s)


# ----- Deployments -----

def player_connection(record: Dict) -> Optional[str]:
    """The deployment's Crucible connection: its own choice, else the only one."""
    chosen = (record.get("options") or {}).get("player_connection")
    if chosen:
        return chosen
    found = list(data()["crucible"])
    return found[0] if len(found) == 1 else None


def resolve_placement(placement: Optional[Dict], player: bool, player_choice: Optional[str]) -> Optional[Dict]:
    """A new or changed deployment's placement, checked: the default connection and its default node
    when unset; None when there are no connections at all (the site's file decides, as before)."""
    if placement is None:
        connection_id = default_proxmox()
        if connection_id is None:
            if player:
                raise PlatformError(422, "A Player view needs a Proxmox connection and a Crucible connection (Admin > Platform)")
            return None
        placement = {"connection": connection_id}
    conn = proxmox(placement.get("connection"))
    if conn is None:
        raise PlatformError(422, f"No Proxmox connection '{placement.get('connection')}' (GET /api/v1/platform)")
    chosen = node(conn, placement.get("node"))
    if chosen is None:
        raise PlatformError(422, f"'{placement.get('node')}' isn't a node of {conn['id']}: "
                                 + ", ".join(n["name"] for n in conn.get("nodes") or []))
    if player:
        if player_choice and crucible(player_choice) is None:
            raise PlatformError(422, f"No Crucible connection '{player_choice}'")
        if not player_choice and len(data()["crucible"]) != 1:
            raise PlatformError(422, "Choose a Crucible connection (options.player_connection)" if data()["crucible"]
                                else "A Player view needs a Crucible connection; an admin adds one in Admin > Platform")
    return {"connection": conn["id"], "node": chosen["name"]}


def resolved(record: Dict) -> Dict:
    """ProxmoxSettings as the deployment runs: the connection's host and the node's settings."""
    placement = record.get("placement") or {}
    conn = proxmox(placement.get("connection"))
    if conn is None:
        return {}
    chosen = node(conn, placement.get("node")) or {}
    return {"host_addr": conn["host"], **{k: v for k, v in chosen.items() if k != "name" and v}, "node": chosen.get("name")}


def exports(record: Dict) -> Optional[Tuple[Dict[str, str], List[str]]]:
    """What the deployment's environment file exports from its connections, and what it unsets of
    the site's first. None for a deployment without a placement: the site's file decides."""
    placement = record.get("placement")
    if not placement:
        return None
    conn = proxmox(placement.get("connection"))
    if conn is None:
        raise PlatformError(409, f"Deployment '{record['id']}' runs on '{placement.get('connection')}', which no longer exists")
    chosen = node(conn, placement.get("node")) or {"name": placement.get("node")}
    out = {"TF_VAR_proxmox_host_addr": conn["host"], "PROXMOX_PORT": str(conn.get("port") or 8006),
           "PROXMOX_VE_ENDPOINT": f"https://{conn['host']}:{conn.get('port') or 8006}/"}
    for key, var in NODE_VARS.items():
        if chosen.get(key):
            out[var] = chosen[key]
    for key, var in PARALLELISM_VARS.items():
        if conn.get(key):
            out[var] = str(conn[key])
    if conn.get("proxy_mode") in ("none", "custom"):
        custom = conn["proxy_mode"] == "custom"
        for key, var in PROXY_VARS.items():
            out[var] = (conn.get(key) or "") if custom else ""
    if conn.get("auth", "token") == "token":
        if conn.get("api_token"):
            out["PROXMOX_VE_API_TOKEN"] = conn["api_token"]
    else:
        for key, var in (("username", "PROXMOX_VE_USERNAME"), ("password", "PROXMOX_VE_PASSWORD")):
            if conn.get(key):
                out[var] = conn[key]
    for key, var in (("ssh_username", "PROXMOX_VE_SSH_USERNAME"), ("ssh_password", "PROXMOX_VE_SSH_PASSWORD")):
        if conn.get(key):
            out[var] = conn[key]
    options = record.get("options") or {}
    cru = crucible(player_connection(record)) if options.get("player") else None
    # The view is terraform/player/'s (services/player.py), never the base module's any more
    out["TF_VAR_configure_crucible"] = "false"
    out["GRAYSPACE_PLAYER"] = "true" if cru else "false"
    if cru:
        for name, key in CRUCIBLE_VARS.items():
            value = cru.get(key)
            if value:
                out[f"TF_VAR_crucible_{name}"] = json.dumps(value) if isinstance(value, list) else str(value)
    return out, [v for v in MANAGED_VARS if v not in out]


def placement_for_host(host: Optional[str]) -> Optional[Dict]:
    """An adopted tree's placement, for display: the connection with its host."""
    for cid, c in sorted(data()["proxmox"].items()):
        if host and c.get("host") == host:
            return {"connection": cid, "node": None}
    return None


# ----- First start -----

def _port(env: Dict[str, str]) -> int:
    if env.get("PROXMOX_PORT", "").isdigit():
        return int(env["PROXMOX_PORT"])
    m = re.match(r"https?://[^/:]+:(\d+)", env.get("PROXMOX_VE_ENDPOINT", ""))
    return int(m.group(1)) if m else 8006


def _scopes(value: str) -> List[str]:
    with contextlib.suppress(TypeError, ValueError):
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(s) for s in parsed]
    return [s for s in re.split(r"[\s,]+", value or "") if s]


def _site_node(env: Dict[str, str]) -> Dict:
    return {k: env[v] for k, v in NODE_VARS.items() if env.get(v)}


def import_site(log=logger.info) -> None:
    """The site file's Proxmox and Crucible settings as the connections named "site". Once: only
    while there is no platform.json. Not in simulation, whose connections are seed_simulated's."""
    if exists() or settings.simulate:
        return
    site = settings.site_environment_file
    env = {}
    if site is not None:
        with contextlib.suppress(Exception):
            env = envfile.load(site)
    d, s = data(), secrets()
    d["imported_from"] = str(site) if site else None
    if env.get("TF_VAR_proxmox_host_addr"):
        token = env.get("PROXMOX_VE_API_TOKEN")
        d["proxmox"]["site"] = {"name": "Site", "host": env["TF_VAR_proxmox_host_addr"], "port": _port(env), "verify_ssl": False,
                                "auth": "token" if token else "password", "username": env.get("PROXMOX_VE_USERNAME") or None,
                                "ssh_username": env.get("PROXMOX_VE_SSH_USERNAME") or None,
                                "nodes": [_site_node(env)] if env.get("TF_VAR_proxmox_node") else [{"name": "pve"}],
                                **{key: int(env[var]) for key, var in PARALLELISM_VARS.items() if env.get(var, "").isdigit() and int(env[var]) > 0}}
        d["proxmox"]["site"] = {k: v for k, v in d["proxmox"]["site"].items() if v is not None}
        s["proxmox"]["site"] = {k: v for k, v in {"api_token": token, "password": env.get("PROXMOX_VE_PASSWORD"),
                                                  "ssh_password": env.get("PROXMOX_VE_SSH_PASSWORD")}.items() if v}
        d["default_proxmox"] = "site"
        log(f"Imported the Proxmox connection 'site' ({env['TF_VAR_proxmox_host_addr']}) from {site}")
    if env.get("TF_VAR_configure_crucible", "").lower() in ("1", "true", "yes") and env.get("TF_VAR_crucible_player_api_url"):
        d["crucible"]["site"] = {"name": "Site", **{key: (_scopes(env.get(f"TF_VAR_crucible_{name}", "")) if key == "client_scopes"
                                                          else env.get(f"TF_VAR_crucible_{name}", ""))
                                                    for name, key in CRUCIBLE_VARS.items() if name != "password"}}
        s["crucible"]["site"] = {"password": env.get("TF_VAR_crucible_password", "")}
        log(f"Imported the Crucible connection 'site' ({env['TF_VAR_crucible_player_api_url']}) from {site}")
    if site is not None and (d["proxmox"] or d["crucible"]):
        log(f"Managed deployments now take Proxmox and Crucible settings from Admin > Platform; those lines in {site} are ignored for them")
    _save(d, s)


def seed_simulated() -> None:
    """Development: connections for the simulated deployments, when there are none."""
    if not settings.simulate or data()["proxmox"]:
        return
    d, s = data(), secrets()
    d["proxmox"]["simulated"] = {"name": "Simulated", "host": "proxmox.simulated", "port": 8006, "auth": "token",
                                 "nodes": [{"name": "pve", "blockstore": "local-lvm", "filestore": "local", "mgmt_bridge": "vmbr0",
                                            "vm_bridge": "vmbr1"}]}
    s["proxmox"]["simulated"] = {"api_token": "simulated@pve!dev=simulated"}
    d["default_proxmox"] = "simulated"
    if not d["crucible"]:
        d["crucible"]["simulated"] = {"name": "Simulated", "token_url": "http://crucible.simulated/keycloak/token",
                                      "player_api_url": "http://crucible.simulated/player/api", "client_id": "simulated",
                                      "client_scopes": ["player-api", "vm-api"], "username": "simulated"}
        s["crucible"]["simulated"] = {"password": "simulated"}
    _save(d, s)


def migrate_records(log=logger.info) -> None:
    """Deployments from before connections: their proxmox overrides and credentials become a
    placement. One on the site's host keeps the site connection (with its node added if new);
    another cluster gets a connection of its own, with its own credentials only: the site's are
    never sent to another cluster, as they were."""
    from control import registry

    site_env = {}
    if settings.site_environment_file is not None:
        with contextlib.suppress(Exception):
            site_env = envfile.load(settings.site_environment_file)
    site_crucible = site_env.get("TF_VAR_configure_crucible", "").lower() in ("1", "true", "yes")
    for record in registry.all_records():
        if record["kind"] != "managed" or record.get("schema", 1) >= 2:
            continue
        with _platform.locked():
            d, s = data(), secrets()
            overrides = record.get("proxmox") or {}
            secret = registry.secrets(record["id"])
            credentials = {k: v for k, v in (secret.get("credentials") or {}).items() if v}
            site = d["proxmox"].get("site")
            host = overrides.get("host_addr") or (site or {}).get("host")
            # On the site's host, the node settings it didn't override came from the site's file
            on_site = not overrides.get("host_addr") or overrides["host_addr"] == (site or {}).get("host")
            wanted = {**(_site_node(site_env) if on_site else {}),
                      **{k: overrides[k] for k in ("node", "blockstore", "filestore", "mgmt_bridge", "vm_bridge") if overrides.get(k)}}
            wanted = {("name" if k == "node" else k): v for k, v in wanted.items()}
            wanted.setdefault("name", "pve")
            placement, warning = None, None
            if host and site and host == site.get("host"):
                # The site's cluster: what the deployment had was the site file's credentials with its own over them
                secret_now = {**s["proxmox"].get("site", {}), **{k: credentials[k] for k in PROXMOX_SECRETS if credentials.get(k)}}
                public_now = {**site, **{k: credentials[k] for k in ("username", "ssh_username") if credentials.get(k)}}
                if credentials.get("api_token"):
                    public_now["auth"] = "token"
                elif credentials.get("password"):
                    public_now["auth"] = "password"
                existing = next((n for n in site["nodes"] if n["name"] == wanted["name"]), None)
                if secret_now == s["proxmox"].get("site", {}) and public_now == site and existing in (None, wanted):
                    if existing is None:
                        site["nodes"].append(wanted)
                    placement = {"connection": "site", "node": wanted["name"]}
                else:
                    cid = f"{record['id']}-pve"
                    d["proxmox"][cid] = {**public_now, "name": f"Site ({record['id']})", "nodes": [wanted], "default_node": wanted["name"]}
                    s["proxmox"][cid] = secret_now
                    placement = {"connection": cid, "node": wanted["name"]}
            elif not host and default_proxmox():
                # No cluster of its own nor the site's (simulated deployments): the default connection
                conn = d["proxmox"][default_proxmox()]
                placement = {"connection": default_proxmox(), "node": conn.get("default_node") or conn["nodes"][0]["name"]}
            elif host:
                cid = f"{record['id']}-pve"
                d["proxmox"][cid] = {"name": f"{host} ({record['id']})", "host": host, "port": _port(site_env) if not site else site.get("port", 8006),
                                     "auth": "token" if credentials.get("api_token") or not credentials else "password",
                                     "username": credentials.get("username"), "ssh_username": credentials.get("ssh_username"),
                                     "nodes": [wanted], "default_node": wanted["name"]}
                d["proxmox"][cid] = {k: v for k, v in d["proxmox"][cid].items() if v is not None}
                s["proxmox"][cid] = {k: credentials[k] for k in PROXMOX_SECRETS if credentials.get(k)}
                placement = {"connection": cid, "node": wanted["name"]}
                if not credentials:
                    warning = (f"Moved to the connection '{cid}' without credentials: it used the site's, which aren't sent to "
                               "another cluster. Give the connection its own in Admin > Platform")
            _save(d, s)
        options = dict(record.get("options") or {})
        if options.get("player") is None and site_crucible and "site" in data()["crucible"]:
            options["player"] = True
        record["options"] = options
        record["placement"] = placement
        record.pop("proxmox", None)
        if warning:
            record["migration_warning"] = warning
        record["schema"] = 2
        secret.pop("credentials", None)
        registry.save_migrated(record, secret)
        log(f"Deployment '{record['id']}' now runs on {placement['connection'] + '/' + placement['node'] if placement else 'the site file'}"
            + (f": {warning}" if warning else ""))
