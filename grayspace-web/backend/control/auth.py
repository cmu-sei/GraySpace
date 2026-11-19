"""Who is calling: a signed-in user's session token, or the server's service token (docs/deployments.md, Sign-in).

Both arrive as Authorization: Bearer <token> (or ?token= on a websocket, where browsers can't set
headers). GRAYSPACE_API_TOKEN is the service credential: scripts, task, GHOSTS, and the proxy's own
calls to workers; it acts as an admin. A session belongs to a local account (control/users.py).
With no GRAYSPACE_API_TOKEN set, the server refuses with 503 as before: workers need it.
"""
import hmac
import time
from typing import Dict, Iterable, Optional, Tuple
from urllib.parse import parse_qs

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from control import users
from control.access import SERVICE, Principal
from control.settings import settings

bearer = HTTPBearer(auto_error=False, scheme_name="GrayspaceToken",
                    description="A session token (POST /api/v1/auth/login), or the server's GRAYSPACE_API_TOKEN")

PASSWORD_CHANGE_REQUIRED = "Change your password first: POST /api/v1/auth/password"


def resolve(token: Optional[str]) -> Optional[Principal]:
    if not token:
        return None
    if settings.api_token and hmac.compare_digest(token.encode(), settings.api_token.encode()):
        return SERVICE
    session = users.resolve_session(token)
    if session is None:
        return None
    record = users.get(session["user"])
    if record is None or record.get("disabled"):
        return None
    return Principal(username=record["username"], role=record["role"], session_id=session["id"],
                     must_change_password=bool(record.get("must_change_password")))


def problem(token: Optional[str], allow_pending: bool = False) -> Tuple[Optional[Principal], Optional[Tuple[int, str]]]:
    """(principal, None), or (None, (status, detail)) when the token doesn't authorize the request."""
    if not settings.api_token:
        return None, (503, "GRAYSPACE_API_TOKEN is not set on this GraySpace; protected routes are disabled")
    principal = resolve(token)
    if principal is None:
        return None, (401, "Missing or invalid bearer token; sign in (POST /api/v1/auth/login)")
    if principal.must_change_password and not allow_pending:
        return None, (403, PASSWORD_CHANGE_REQUIRED)
    return principal, None


def header_token(headers: Iterable[Tuple[bytes, bytes]]) -> Optional[str]:
    for name, value in headers:
        if name.lower() == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer":
                return token.strip()
    return None


def scope_token(scope) -> Optional[str]:
    """The bearer header's token, else ?token= (websockets)."""
    token = header_token(scope.get("headers") or [])
    if token:
        return token
    query = parse_qs((scope.get("query_string") or b"").decode())
    return (query.get("token") or [None])[0]


def _raise(error: Tuple[int, str]):
    raise HTTPException(status_code=error[0], detail=error[1], headers={"WWW-Authenticate": "Bearer"} if error[0] == 401 else None)


async def current(credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer)) -> Principal:
    principal, error = problem(credentials.credentials if credentials else None)
    if error:
        _raise(error)
    return principal


async def current_pending(credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer)) -> Principal:
    """Also a user who must still change their password: for /me, the password change and sign-out."""
    principal, error = problem(credentials.credentials if credentials else None, allow_pending=True)
    if error:
        _raise(error)
    return principal


def require(permission: str):
    async def check(principal: Principal = Depends(current)) -> Principal:
        if not principal.can(permission):
            raise HTTPException(403, f"You need {permission}")
        return principal
    return check


# Peers that are the server's own reverse proxy (nginx in the container, on a socket or loopback)
LOCAL_PEERS = {None, "", "127.0.0.1", "::1"}


def scope_ip(scope) -> str:
    """The caller's address. Behind the server's own nginx (a local peer) it is the one nginx
    passes on (X-Real-IP, else the first X-Forwarded-For); from anywhere else, the peer itself,
    so a remote caller can't name another address and dodge the sign-in throttle."""
    client = scope.get("client")
    peer = client[0] if client else None
    if peer in LOCAL_PEERS:
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        forwarded = headers.get("x-real-ip") or headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return peer or ""


def client_ip(request: Request) -> str:
    return scope_ip(request.scope)


class Throttle:
    """Failed sign-ins per key (a username, an address): after FREE failures, wait 2^n seconds, up to MAX."""

    FREE = 5
    MAX = 15 * 60

    def __init__(self):
        self._failures: Dict[str, Tuple[int, float]] = {}

    def wait(self, keys: Iterable[str]) -> int:
        t = time.monotonic()
        waits = [until - t for count, until in (self._failures.get(k, (0, 0.0)) for k in keys)]
        return max(0, int(max(waits, default=0) + 0.999))

    def failed(self, keys: Iterable[str]) -> None:
        t = time.monotonic()
        for k in keys:
            count = self._failures.get(k, (0, 0.0))[0] + 1
            delay = min(self.MAX, 2 ** (count - self.FREE)) if count >= self.FREE else 0
            self._failures[k] = (count, t + delay)

    def succeeded(self, keys: Iterable[str]) -> None:
        for k in keys:
            self._failures.pop(k, None)

    def clear(self) -> None:
        self._failures.clear()


throttle = Throttle()
