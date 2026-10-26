"""Project login and private browser chat ownership.

Standalone mTLS AIP applications do not install this middleware. Additional
rate, concurrency, request-size and Origin restrictions are deliberately absent
from this demo boundary; original CORS and business-write checks stay separate.
"""
from __future__ import annotations

import json
import os
from contextvars import ContextVar
from http.cookies import SimpleCookie

from starlette.responses import JSONResponse

from app.services.project_auth import PROJECT_SESSION_COOKIE, resolve_project_token

browser_actor: ContextVar[str | None] = ContextVar("browser_actor", default=None)
PUBLIC_PATHS = frozenset({"/api/health", "/api/runtime/surface", "/api/auth/login", "/api/auth/me", "/api/auth/logout"})
ASSISTANT_PATHS = frozenset({"/api/real-orders/assistant/ask", "/api/real-orders/assistant/ask/stream"})


def login_required() -> bool:
    return os.getenv("PROJECT_AUTH_REQUIRE_LOGIN", "true").strip().lower() not in {"0", "false", "off", "no"}


class BrowserSecurityMiddleware:
    def __init__(self, app, token_resolver=resolve_project_token):
        self.app = app
        self.resolve_token = token_resolver

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        path = scope.get("path", "")
        protected = path.startswith(("/api/", "/aip/")) or path in {"/docs", "/redoc", "/openapi.json"} or path.startswith("/docs/")
        if kind not in {"http", "websocket"} or not protected or not login_required() or (kind == "http" and scope.get("method") == "OPTIONS"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}

        async def reject(status, code, message):
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": code})
            else:
                await JSONResponse({"detail": {"code": code, "message": message}}, status_code=status)(scope, receive, send)

        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:
            return await reject(401, "project_login_required", "请先登录项目账号")
        raw_token = cookie.get(PROJECT_SESSION_COOKIE)
        bearer = headers.get("authorization", "").split(" ", 1)
        token = raw_token.value if raw_token else (bearer[1] if len(bearer) == 2 and bearer[0].lower() == "bearer" else None)
        identity = self.resolve_token(token)
        if path not in PUBLIC_PATHS and identity is None:
            return await reject(401, "project_login_required", "请先到系统连接登录项目账号")
        if path in ASSISTANT_PATHS and scope.get("method") == "POST" and identity is not None:
            # Authorize the requested chat before SSE headers are sent, then
            # replay the unchanged body to FastAPI's normal parser. This does
            # not impose an additional demo-specific size or question limit.
            body = bytearray()
            while True:
                event = await receive()
                if event["type"] == "http.disconnect":
                    return
                body.extend(event.get("body", b""))
                if not event.get("more_body", False):
                    break
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                payload = None
            if isinstance(payload, dict):
                session_id = payload.get("session_id")
                if session_id:
                    from app.persistence.database import SessionLocal
                    from app.persistence.models import AssistantSessionRow
                    with SessionLocal() as db:
                        row = db.get(AssistantSessionRow, str(session_id).strip())
                        if row is None or row.owner_subject != identity.actor_id:
                            return await reject(404, "assistant_session_not_found", "会话不存在或无权访问")
            original_receive = receive
            replayed = False

            async def replay_receive():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()

            receive = replay_receive
        context = browser_actor.set(identity.actor_id if identity is not None else None)
        scope.setdefault("state", {})["project_identity"] = identity
        try:
            return await self.app(scope, receive, send)
        finally:
            browser_actor.reset(context)
