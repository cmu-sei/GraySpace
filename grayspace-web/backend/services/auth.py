"""Bearer-token auth for the Terrain API and every write route.

The token is GRAYSPACE_API_TOKEN. Contract routers depend on require_token, so the published spec
declares the scheme. TokenAuthMiddleware covers the rest: every write method on any route, and the
console and desktop websockets, which give shell and screen access. With no token configured,
protected routes refuse with 503 rather than run open.
"""
import hmac
import json
from typing import Optional
from urllib.parse import parse_qs

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import settings

bearer = HTTPBearer(auto_error=False, scheme_name="GrayspaceToken", description="The GRAYSPACE_API_TOKEN of the GraySpace deployment")

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PROTECTED_WEBSOCKETS = ("/ws/console/", "/ws/desktop/")


def problem(token: Optional[str]):
    """(status, detail) when the token doesn't authorize the request, or None when it does."""
    if not settings.api_token:
        return 503, "GRAYSPACE_API_TOKEN is not set on this GraySpace; protected routes are disabled"
    if not token or not hmac.compare_digest(token.encode(), settings.api_token.encode()):
        return 401, "Missing or invalid bearer token"
    return None


async def require_token(credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer)):
    error = problem(credentials.credentials if credentials else None)
    if error:
        raise HTTPException(status_code=error[0], detail=error[1], headers={"WWW-Authenticate": "Bearer"} if error[0] == 401 else None)


def _header_token(scope) -> Optional[str]:
    for name, value in scope.get("headers") or []:
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer":
                return token.strip()
    return None


def websocket_token(scope) -> Optional[str]:
    """Browsers can't set headers on a websocket, so the token may also come as ?token=."""
    token = _header_token(scope)
    if token:
        return token
    query = parse_qs((scope.get("query_string") or b"").decode())
    return (query.get("token") or [None])[0]


class TokenAuthMiddleware:
    """Requires the token on write methods and protected websockets, for every route."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] in WRITE_METHODS:
            error = problem(_header_token(scope))
            if error:
                body = json.dumps({"detail": error[1]}).encode()
                headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
                if error[0] == 401:
                    headers.append((b"www-authenticate", b"Bearer"))
                await send({"type": "http.response.start", "status": error[0], "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return
        elif scope["type"] == "websocket" and scope["path"].startswith(PROTECTED_WEBSOCKETS):
            if problem(websocket_token(scope)):
                # Closing before accepting makes the server answer the handshake with 403.
                await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)
