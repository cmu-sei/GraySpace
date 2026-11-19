"""Crucible's APIs as the Terraform provider's account sees them: a token from Keycloak, and the
Player API. Shared by the worker (base preflight) and the control plane (Admin > Platform's test),
so it imports nothing of either's settings.

cfg is a Crucible connection's settings: username, password, auth_url, token_url, client_id,
client_scopes (a list, or a Terraform list as a string), vm_api_url, player_api_url, caster_api_url.
"""
import json
import re
import ssl
from typing import Any, Dict, List, Optional, Tuple

import httpx

Check = Tuple[str, str]  # (status ok|warning|error, message)


def scopes(value: Any) -> str:
    """OAuth wants scopes space-separated; TF_VAR_crucible_client_scopes is a Terraform list (["vm-api","player-api"])."""
    if isinstance(value, list):
        return " ".join(str(s) for s in value)
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return " ".join(str(s) for s in parsed)
    except (TypeError, ValueError):
        pass
    return str(value or "").replace(",", " ")


def _client() -> httpx.AsyncClient:
    # The system trust store, which carries site CAs (the container installs .devcontainer/certs)
    return httpx.AsyncClient(timeout=15, verify=ssl.create_default_context())


async def token(client: httpx.AsyncClient, cfg: Dict) -> Tuple[Optional[str], Check]:
    """A token for the provider's account, or None and why not."""
    try:
        r = await client.post(cfg["token_url"], data={"grant_type": "password", "client_id": cfg["client_id"], "username": cfg["username"],
                                                     "password": cfg["password"], "scope": scopes(cfg.get("client_scopes"))})
    except httpx.HTTPError as e:
        return None, ("error", f"Keycloak at {cfg['token_url']} didn't answer: {e}")
    try:
        body = r.json()
    except ValueError:
        body = {}
    found = body.get("access_token") if r.status_code == 200 and isinstance(body, dict) else None
    if not found:
        detail = (body.get("error_description") or body.get("error")) if isinstance(body, dict) else None
        return None, ("error", f"Keycloak refused the Crucible account ({r.status_code}{': ' + detail if detail else ''}); "
                               "check the username, password, client ID and scopes")
    return found, ("ok", f"Keycloak issued a token for {cfg['username']}")


def _bearer_error(header: str) -> str:
    """error_description (or error) from a WWW-Authenticate: Bearer header, '' when it has neither."""
    found = dict(re.findall(r'(\w+)="([^"]*)"', header))
    return found.get("error_description") or found.get("error") or ""


async def check(cfg: Dict) -> List[Check]:
    """The token, then the Player API accepting it. A wrong password otherwise surfaces only partway through an apply."""
    async with _client() as client:
        found, result = await token(client, cfg)
        if not found:
            return [result]
        checks = [result]
        url = cfg["player_api_url"].rstrip("/")
        try:
            r = await client.get(url + "/views", headers={"Authorization": f"Bearer {found}"})
        except httpx.HTTPError as e:
            return checks + [("error", f"The Player API at {url} didn't answer: {e}")]
        if r.status_code == 401:
            # Not a permission: Player didn't accept the token (often its issuer, when Keycloak is reached under
            # another name than Player's Authority). Its WWW-Authenticate header says why.
            why = _bearer_error(r.headers.get("www-authenticate", ""))
            checks.append(("error", f"The Player API rejected the token (401{': ' + why if why else ''}); "
                                    "check that the token URL uses the Keycloak address Player trusts"))
        elif r.status_code == 403:
            checks.append(("error", "The Player API refused the account (403); it needs rights to create views"))
        elif r.status_code >= 400:
            checks.append(("warning", f"The Player API answered {r.status_code} at {url}/views"))
        else:
            checks.append(("ok", f"The Player API at {url} accepts the token"))
        return checks


async def _get(cfg: Dict, path: str) -> List[Dict]:
    """A Player API list, with the account's token. Raises CrucibleError when it can't be had."""
    async with _client() as client:
        found, (_, why) = await token(client, cfg)
        if not found:
            raise CrucibleError(why)
        url = cfg["player_api_url"].rstrip("/") + path
        try:
            r = await client.get(url, headers={"Authorization": f"Bearer {found}"})
        except httpx.HTTPError as e:
            raise CrucibleError(f"The Player API at {url} didn't answer: {e}") from None
        if r.status_code >= 400:
            raise CrucibleError(f"The Player API answered {r.status_code} at {url}")
        body = r.json()
        return body if isinstance(body, list) else []


class CrucibleError(Exception):
    pass


async def templates(cfg: Dict) -> List[Dict]:
    """Player's application templates: {id, name, url, icon, embeddable, loadInBackground}."""
    return await _get(cfg, "/application-templates")


async def team_roles(cfg: Dict) -> List[Dict]:
    """The roles a team can have: {id, name}."""
    return await _get(cfg, "/team-roles")


def from_env(env: Dict[str, str]) -> Dict:
    """A connection's settings from an environment file's TF_VAR_crucible_* (control/platform.py writes them)."""
    names = ("username", "password", "auth_url", "token_url", "client_id", "client_scopes", "vm_api_url", "player_api_url", "caster_api_url")
    return {name: env.get(f"TF_VAR_crucible_{name}", "") for name in names}
