"""OpenMES 服务端用户会话管理器（内存态自动刷新）。

背景：OpenMES 的 /api/v1/* 接口使用 Sanctum 用户 Bearer 令牌，登录签发时
即带 15 分钟 TTL（services/OpenMes Auth::login → now()->addMinutes(
openmmes.default_token_ttl_minutes, 15)）。把登录令牌写进 .env 当长期集
成凭据，过期是必然的——2026-10-01 已实证：/api/mes/work-orders 返回空列
表，根因是令牌过期叠加适配器吞错。

本模块提供后端内存态的会话自愈能力（用户确认的方案，2026-10-01）：

- 令牌只保存在进程内存中，绝不写入 Git、日志、前端或业务数据库；
- 管理凭据只从 ``services/OpenMes/.env`` 读取，仅在登录调用瞬间使用，
  不做成员变量持有；
- 收到 401 时由传输装饰器刷新令牌并自动重试一次（仅一次）；
- 刷新失败抛出明确的认证错误（不含任何凭据），不伪造业务结果，也不要求
  重启服务。

约定：登录（login）不会吊销该用户的其他令牌，只有 logout/refresh 会
（见 session_auth.py 的契约说明），因此自动重登不会波及浏览器短时会话。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from app.integrations.errors import IntegrationError, IntegrationNotConfigured
from app.integrations.http import JsonHttpClient

logger = logging.getLogger(__name__)

# 默认缓存 14 分钟：上游默认 15 分钟 TTL，留 1 分钟时钟余量。
DEFAULT_SESSION_TTL_SECONDS = 14 * 60

# 登录函数签名：(base_url, username, password) -> token 字符串。
LoginFn = Callable[[str, str, str], Awaitable[str]]


def auto_refresh_enabled() -> bool:
    """OPENMES_SESSION_AUTO_REFRESH 默认开启；显式 false/0/off 关闭。"""
    return os.getenv("OPENMES_SESSION_AUTO_REFRESH", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def session_ttl_seconds() -> float:
    """内存会话缓存时长（秒）；OPENMES_SESSION_TTL_SECONDS 可覆盖。"""
    raw = os.getenv("OPENMES_SESSION_TTL_SECONDS", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_SESSION_TTL_SECONDS
    except ValueError:
        value = DEFAULT_SESSION_TTL_SECONDS
    return max(30.0, min(value, 840.0))


@dataclass
class _AdminCredentials:
    username: str
    password: str


def load_openmes_admin_credentials(
    path: Path | None = None,
) -> _AdminCredentials:
    """读取 OpenMES 部署目录 .env 的管理员凭据（调用方不得记录其值）。

    凭据文件不在版本控制内（services/ 被 .gitignore 忽略）；缺文件或缺
    密码时抛出明确的配置错误，不做任何兜底猜测。
    """
    env_path = (
        path
        or Path(__file__).resolve().parents[3] / "services" / "OpenMes" / ".env"
    )
    if not env_path.exists():
        raise IntegrationNotConfigured(
            "OpenMES 自动重新登录", [str(env_path)]
        )
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    username = values.get("ADMIN_USERNAME", "admin").strip()
    password = values.get("ADMIN_PASSWORD", "").strip()
    if not password:
        raise IntegrationNotConfigured(
            "OpenMES 自动重新登录", ["ADMIN_PASSWORD（services/OpenMes/.env）"]
        )
    return _AdminCredentials(username=username, password=password)


async def _login_via_openmes(
    base_url: str, username: str, password: str
) -> str:
    """按 vendored OpenMES 登录契约换取 Sanctum 令牌（只返回令牌本身）。"""
    http = JsonHttpClient(base_url)
    try:
        payload = await http.request_json(
            "POST",
            "api/auth/login",
            json_body={"username": username, "password": password},
            # Laravel 只在 Accept: application/json 时把认证/校验失败渲染成
            # 401/422 JSON，否则 302 回登录页 HTML（见 session_auth.py）。
            headers={"Accept": "application/json"},
        )
    finally:
        await http.aclose()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise IntegrationError(
            "OpenMES 自动重新登录失败：登录响应不符合其 API 契约",
            code="openmes_relogin_failed",
            status_code=401,
        )
    data = payload["data"]
    token = str(data.get("token", "")).strip()
    if not token:
        raise IntegrationError(
            "OpenMES 自动重新登录失败：未返回会话令牌",
            code="openmes_relogin_failed",
            status_code=401,
        )
    if data.get("force_password_change"):
        raise IntegrationError(
            "OpenMES 自动重新登录失败：该账号要求先修改密码，签发的会话无法执行业务调用",
            code="openmes_relogin_failed",
            status_code=401,
        )
    return token


class OpenMESUserSessionManager:
    """一个 OpenMES 用户的短时会话（进程内存态，带锁防并发重登）。

    - ``peek``：同步读取未过期的缓存令牌（无 I/O，供请求头快速构建）；
    - ``get_token``：有缓存用缓存，否则登录（双检锁）；
    - ``refresh``：401 后刷新。传入刚失败的旧令牌，若其他请求已经换新
      则直接复用，避免并发 401 风暴触发多次重登；
    - ``invalidate``：丢弃缓存（供测试与运维）。
    """

    def __init__(
        self,
        base_url: str,
        *,
        ttl_seconds: float | None = None,
        credentials_path: Path | None = None,
        login_fn: LoginFn | None = None,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if not base_url.strip():
            raise IntegrationNotConfigured("OpenMES", ["OPENMES_BASE_URL"])
        self._base_url = base_url.strip().rstrip("/")
        self._ttl = ttl_seconds if ttl_seconds is not None else session_ttl_seconds()
        self._credentials_path = credentials_path
        self._login_fn: LoginFn = login_fn or _login_via_openmes
        self._time_fn = time_fn
        self._token = ""
        self._issued_at = 0.0
        self._lock = asyncio.Lock()

    def peek(self) -> str:
        """未过期的缓存令牌；没有则返回空串（同步、无 I/O）。"""
        if self._token and (self._time_fn() - self._issued_at) < self._ttl:
            return self._token
        return ""

    def invalidate(self) -> None:
        self._token = ""
        self._issued_at = 0.0

    async def get_token(self) -> str:
        token = self.peek()
        if token:
            return token
        async with self._lock:
            token = self.peek()
            if token:
                return token
            return await self._login_locked()

    async def refresh(self, stale_token: str | None = None) -> str:
        """401 后刷新：丢弃指定旧令牌并重新登录。

        若缓存令牌已不是 stale_token（其他请求刚刷新过），直接复用新令牌。
        """
        current = self.peek()
        if stale_token and current and current != stale_token:
            return current
        async with self._lock:
            current = self.peek()
            if stale_token and current and current != stale_token:
                return current
            self.invalidate()
            return await self._login_locked()

    async def _login_locked(self) -> str:
        credentials = load_openmes_admin_credentials(self._credentials_path)
        try:
            token = await self._login_fn(
                self._base_url, credentials.username, credentials.password
            )
        except IntegrationError as exc:
            # 远端认证/服务错误包装成明确的重登失败信息（不含凭据），
            # 让调用方与用户能区分"令牌过期且重登失败"和普通业务错误。
            raise IntegrationError(
                f"OpenMES 自动重新登录失败：{exc}",
                code="openmes_relogin_failed",
                status_code=401,
            ) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise IntegrationError(
                "OpenMES 自动重新登录失败：认证服务不可达",
                code="openmes_relogin_failed",
                status_code=401,
            ) from exc
        # 只在内存中保存令牌本身；凭据对象在此处即可被回收。
        self._token = token
        self._issued_at = self._time_fn()
        logger.info(
            "OpenMES 用户会话已在内存中刷新（TTL %.0f 秒，值不记录）", self._ttl
        )
        return token


class UserTokenRetryTransport:
    """401 时刷新用户令牌并重试一次的传输装饰器。

    包在 ``JsonHttpClient`` 外层供 ``OpenMESClient`` 使用：

    - 仅对带 ``Authorization`` 头的请求重试（X-Api-Key / 匿名请求的 401
      与用户会话无关，直接透传）；
    - 只重试一次：重试后的 401 原样抛出，不循环；
    - 刷新经 ``OpenMESUserSessionManager.refresh``，令牌不经过日志。
    """

    def __init__(self, inner: JsonHttpClient, session: OpenMESUserSessionManager) -> None:
        self._inner = inner
        self._session = session

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json_body: dict[str, Any] | None = None,
        headers: Any = None,
    ) -> Any:
        try:
            return await self._inner.request_json(
                method, path, params=params, json_body=json_body, headers=headers
            )
        except IntegrationError as exc:
            request_headers = dict(headers or {})
            if (
                exc.status_code != 401
                or "Authorization" not in request_headers
            ):
                raise
            stale = request_headers["Authorization"].removeprefix("Bearer ").strip()
            token = await self._session.refresh(stale or None)
            request_headers["Authorization"] = f"Bearer {token}"
            return await self._inner.request_json(
                method,
                path,
                params=params,
                json_body=json_body,
                headers=request_headers,
            )

    async def request_multipart(
        self,
        method: str,
        path: str,
        *,
        data: Any = None,
        files: Any = None,
        headers: Any = None,
    ) -> Any:
        try:
            return await self._inner.request_multipart(
                method, path, data=data, files=files, headers=headers
            )
        except IntegrationError as exc:
            request_headers = dict(headers or {})
            if (
                exc.status_code != 401
                or "Authorization" not in request_headers
            ):
                raise
            stale = request_headers["Authorization"].removeprefix("Bearer ").strip()
            token = await self._session.refresh(stale or None)
            request_headers["Authorization"] = f"Bearer {token}"
            return await self._inner.request_multipart(
                method, path, data=data, files=files, headers=request_headers
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


_managers: dict[str, OpenMESUserSessionManager] = {}


def get_openmes_user_session(base_url: str) -> OpenMESUserSessionManager:
    """按 base_url 复用单例会话管理器（进程内共享缓存）。"""
    key = base_url.strip().rstrip("/")
    if key not in _managers:
        _managers[key] = OpenMESUserSessionManager(key)
    return _managers[key]


def reset_openmes_user_sessions() -> None:
    """清空单例（测试用）。"""
    _managers.clear()


def build_openmes_client(
    settings,
    *,
    user_token: str | None = None,
    erp_api_key: str | None = None,
    timeout_seconds: float = 15.0,
    client: httpx.AsyncClient | None = None,
):
    """构造带内存会话自动刷新的 OpenMES 客户端（真实链路默认入口）。

    自动刷新关闭（OPENMES_SESSION_AUTO_REFRESH=false）时行为与直接构造
    完全一致。凭据缺失不在此处报错——只有真正需要重登（401 或无静态令
    牌）时才抛出明确的认证错误。
    """
    from app.adapters.mes.openmes import OpenMESClient

    session: OpenMESUserSessionManager | None = None
    if auto_refresh_enabled():
        session = get_openmes_user_session(settings.openmes_base_url)
    return OpenMESClient(
        settings.openmes_base_url,
        user_token=user_token,
        erp_api_key=erp_api_key,
        timeout_seconds=timeout_seconds,
        client=client,
        user_session=session,
    )
