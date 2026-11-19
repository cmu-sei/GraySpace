"""Who made the request, as the control plane says (X-Grayspace-User, control/proxy.py).

The control plane checks the caller and calls the worker with the service token and their name.
The name is only for attribution (tracked changes, job records), never for authorization: a
request straight to the worker with the service token has no user, or whatever header it sends.
"""
from contextvars import ContextVar
from typing import Optional

HEADER = b"x-grayspace-user"

_user: ContextVar[Optional[str]] = ContextVar("grayspace_user", default=None)


def current() -> Optional[str]:
    return _user.get()


class RequestUserMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        name = next((v.decode("latin-1") for k, v in scope.get("headers") or [] if k == HEADER), None)
        token = _user.set(name[:64] if name else None)
        try:
            await self.app(scope, receive, send)
        finally:
            _user.reset(token)
