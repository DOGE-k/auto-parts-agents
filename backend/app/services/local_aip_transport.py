"""Delegate a logged-in browser session only to this backend's local AIP RPC.

The token lives in request context, never in a cached client or skill payload.
This transport is used only by the browser coordinator, not ERP/MES or mTLS AIP.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from ipaddress import ip_address

import httpx


@dataclass(frozen=True)
class LocalAipSession:
    token: str = field(repr=False)
    host: str
    port: int


local_aip_session: ContextVar[LocalAipSession | None] = ContextVar("local_aip_session", default=None)
_RPC_PATHS = frozenset(
    f"/aip/{agent}/rpc".encode("ascii")
    for agent in ("quotation", "procurement", "tracking", "quality-document")
)


def session_for_backend(scope, token: str | None) -> LocalAipSession | None:
    """Bind to the actual ASGI listener, never browser Host/forwarded headers."""
    server = scope.get("server")
    if not token or not server or len(server) != 2:
        return None
    try:
        address = ip_address(server[0])
        port = int(server[1])
    except (ValueError, TypeError):
        return None
    if not 1 <= port <= 65535:
        return None
    if address.is_unspecified:
        address = ip_address("127.0.0.1" if address.version == 4 else "::1")
    if not address.is_loopback:
        return None
    return LocalAipSession(token=token, host=str(address), port=port)


class BrowserSessionAipTransport(httpx.AsyncBaseTransport):
    """Add request-local auth only for the pinned local RPC target.

    Literal loopback + the observed listener port prevents a custom self URL
    from receiving browser credentials. No environment proxy is consulted.
    HTTPX's SDK client does not follow redirects; each request is checked again.
    """

    def __init__(self, partner_url: str, *, transport=None):
        self._target = httpx.URL(partner_url)
        self._transport = transport if transport is not None else httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Never reuse an authorization header copied from an earlier request or
        # redirect. A matching request must obtain its own current session.
        request.headers.pop("Authorization", None)
        session = local_aip_session.get()
        url = request.url
        if (
            session is not None
            and request.method == "POST"
            and url == self._target
            and url.scheme == "http"
            and url.host == session.host
            and url.port == session.port
            and not url.userinfo
            and not url.fragment
            and url.raw_path in _RPC_PATHS
        ):
            request.headers["Authorization"] = f"Bearer {session.token}"
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._transport.aclose()
