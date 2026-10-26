"""Browser API boundary; standalone mTLS AIP applications do not install this middleware.

Limits are per process and reset on restart. Deploy the browser backend with one
worker; multi-worker deployments must replace the limiter with shared storage.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from contextvars import ContextVar
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from app.services.project_auth import PROJECT_SESSION_COOKIE, resolve_project_token

browser_actor: ContextVar[str | None] = ContextVar("browser_actor", default=None)
PUBLIC_PATHS = frozenset({"/api/health", "/api/runtime/surface", "/api/auth/login", "/api/auth/me", "/api/auth/logout"})
ASSISTANT_PATHS = frozenset({"/api/real-orders/assistant/ask", "/api/real-orders/assistant/ask/stream"})


def login_required() -> bool:
    return os.getenv("PROJECT_AUTH_REQUIRE_LOGIN", "true").strip().lower() not in {"0", "false", "off", "no"}


def _positive(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


class RequestLimiter:
    """Bounded, thread-safe sliding windows plus active streaming request leases."""
    def __init__(self):
        self._lock = threading.Lock()
        self._windows: dict[tuple[str, str], deque[float]] = {}
        self._active: dict[str, int] = {}
        self._global_active = 0

    def clear(self):
        with self._lock:
            self._windows.clear()
            self._active.clear()
            self._global_active = 0

    def _consume(self, kind: str, key: str, limit: int, seconds: int, now: float) -> bool:
        bucket = self._windows.setdefault((kind, key), deque())
        while bucket and bucket[0] <= now - seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    def enter(self, kind: str, key: str) -> tuple[bool, str]:
        now = time.monotonic()
        with self._lock:
            # Bound attacker-created IP windows without allowing memory growth.
            if len(self._windows) >= 9998:
                stale = [k for k, v in self._windows.items() if not v or v[-1] <= now - (86400 if k[0] == "assistant-day" else 60)]
                for k in stale:
                    self._windows.pop(k, None)
                needed = [(kind, key)] if kind == "login" else [("assistant-minute", key), ("assistant-day", key)]
                if len(self._windows) + sum(k not in self._windows for k in needed) > 10000:
                    return False, "security_capacity_reached"
            if kind == "login":
                ok = self._consume(kind, key, _positive("PROJECT_LOGIN_ATTEMPTS_PER_MINUTE", 5), 60, now)
                return ok, "login_rate_limited"
            if self._active.get(key, 0) >= _positive("PROJECT_ASSISTANT_MAX_CONCURRENT", 1):
                return False, "assistant_concurrency_limited"
            if self._global_active >= _positive("PROJECT_ASSISTANT_GLOBAL_CONCURRENT", 4):
                return False, "assistant_concurrency_limited"
            if not self._consume("assistant-minute", key, _positive("PROJECT_ASSISTANT_REQUESTS_PER_MINUTE", 10), 60, now):
                return False, "assistant_rate_limited"
            if not self._consume("assistant-day", key, _positive("PROJECT_ASSISTANT_REQUESTS_PER_DAY", 100), 86400, now):
                return False, "assistant_daily_limit"
            self._active[key] = self._active.get(key, 0) + 1
            self._global_active += 1
            return True, ""

    def leave(self, key: str):
        with self._lock:
            count = self._active.get(key, 0)
            if count:
                if count == 1:
                    self._active.pop(key, None)
                else:
                    self._active[key] = count - 1
                self._global_active = max(0, self._global_active - 1)


request_limiter = RequestLimiter()


def _origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            return ""
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    except ValueError:
        return ""


def _same_origin(scope, headers) -> bool:
    incoming = headers.get("origin", "")
    if not incoming:
        # Browsers send Origin for cookie-authenticated unsafe fetch requests.
        # Non-browser Bearer clients do not need a CSRF token.
        return PROJECT_SESSION_COOKIE not in headers.get("cookie", "")
    supplied = _origin(incoming)
    expected = _origin(f"{scope.get('scheme', 'http')}://{headers.get('host', '')}")
    allowed = {_origin(v.strip()) for v in os.getenv("PROJECT_AUTH_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174").split(",")}
    allowed.discard("")
    return bool(supplied and (supplied == expected or supplied in allowed))


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
                extra = {"Retry-After": "60"} if status == 429 else {}
                await JSONResponse({"detail": {"code": code, "message": message}}, status_code=status, headers=extra)(scope, receive, send)

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
        if kind == "websocket" or scope.get("method") not in {"GET", "HEAD", "OPTIONS"}:
            if not _same_origin(scope, headers):
                return await reject(403, "cross_origin_request_denied", "请求来源不在允许范围内")
        lease = None
        if path == "/api/auth/login" and scope.get("method") == "POST":
            client = scope.get("client") or ("unknown", 0)
            ok, code = request_limiter.enter("login", str(client[0]))
            if not ok:
                return await reject(429, code, "登录尝试过于频繁，请稍后重试")
        if path in ASSISTANT_PATHS and scope.get("method") == "POST" and identity is not None:
            # Read a bounded body to authorize the requested session before SSE
            # headers are sent. Replay it unchanged to FastAPI's normal parser.
            body = bytearray()
            while True:
                event = await receive()
                if event["type"] == "http.disconnect":
                    return
                body.extend(event.get("body", b""))
                if len(body) > 65536:
                    return await reject(413, "assistant_request_too_large", "问答请求超过大小限制")
                if not event.get("more_body", False):
                    break
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                payload = None
            if isinstance(payload, dict):
                question = payload.get("question", "")
                if isinstance(question, str) and len(question) > 8000:
                    return await reject(422, "assistant_question_too_long", "问题不能超过8000个字符")
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
            ok, code = request_limiter.enter("assistant", identity.actor_id)
            if not ok:
                return await reject(429, code, "问答达到频率、并发或每日额度限制，请稍后重试")
            lease = identity.actor_id
        context = browser_actor.set(identity.actor_id if identity is not None else None)
        scope.setdefault("state", {})["project_identity"] = identity
        try:
            return await self.app(scope, receive, send)
        finally:
            browser_actor.reset(context)
            if lease is not None:
                request_limiter.leave(lease)
