"""真实 OpenMES 会话登录。

前端会话面板不再必须手工粘贴令牌：这里按 vendored OpenMES 认证契约
（``POST /api/auth/login``）转发用户名/密码，返回其短时 Sanctum token
（默认 TTL 由 ``openmmes.default_token_ttl_minutes`` 控制，15 分钟）和
解析后的业务身份。

刻意不代理 logout/refresh：

- OpenMES 的 logout 会吊销该上游用户的全部 token，若用户名与服务端
  集成账号（OPENMES_TOKEN）相同，会把部署配置的集成令牌一并吊销；
- refresh 会在上游删除当前 token 并签发新 token，同样可能轮换掉
  集成令牌。本地登出只清除浏览器会话，不触发上游吊销。
"""
from __future__ import annotations

from typing import Any

from app.integrations.errors import IntegrationError, IntegrationNotConfigured
from app.integrations.http import JsonHttpClient
from app.integrations.settings import IntegrationSettings
from app.services.identity import resolve_openmes_identity


async def login_openmes_session(
    settings: IntegrationSettings | None = None,
    *,
    username: str,
    password: str,
    client=None,
) -> dict[str, Any]:
    """用真实 OpenMES 登录契约换取短时会话，并回读解析身份。"""
    settings = settings or IntegrationSettings.from_environment()
    if not settings.openmes_base_url.strip():
        raise IntegrationNotConfigured("OpenMES", ["OPENMES_BASE_URL"])
    user = (username or "").strip()
    if not user or not (password or "").strip():
        raise ValueError("用户名和密码不能为空")

    http = JsonHttpClient(settings.openmes_base_url, client=client)
    try:
        payload = await http.request_json(
            "POST",
            "api/auth/login",
            # Laravel 只在 Accept: application/json 时把校验失败渲染成
            # 422 JSON；否则 302 回登录页 HTML，错误信息就丢了。
            json_body={"username": user, "password": password},
            headers={"Accept": "application/json"},
        )
    finally:
        await http.aclose()

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise IntegrationError(
            "OpenMES 登录响应不符合其 API 契约", code="login_invalid_response"
        )
    data = payload["data"]
    token = str(data.get("token", "")).strip()
    if not token:
        raise IntegrationError(
            "OpenMES 登录响应未返回会话令牌", code="login_invalid_response"
        )

    identity = await resolve_openmes_identity(settings, token=token)
    return {
        "provider": "openmes",
        "token_type": "bearer",
        "access_token": token,
        "force_password_change": bool(data.get("force_password_change")),
        "session_scope": "short_lived_upstream_token",
        "expires_hint": "OpenMES Sanctum token 默认 15 分钟 TTL（openmmes.default_token_ttl_minutes）",
        "identity": identity.as_dict(),
        "authority": "OpenMES",
        "data_source": "openmes_auth_login",
    }
