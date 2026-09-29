"""真实审批身份解析。

审批记录不能再把浏览器提交的 ``approved_by`` 当作身份凭证。这个模块
从已配置的 ERPNext/OpenMES 登录凭据读取当前用户，并把外部角色映射为
本项目的业务角色。当前部署使用的是集成账号，因此返回的身份明确标记
为服务端集成账号；接入真正的 SSO 时只需替换 provider，不改变审批接口。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.adapters.erp.erpnext import ERPNextClient
from app.adapters.mes.openmes import OpenMESClient
from app.integrations.errors import IntegrationError, IntegrationNotConfigured
from app.integrations.settings import IntegrationSettings


ROLE_REQUIREMENTS = {
    "quotation_approval": "sales_manager",
    "sales_order_draft_creation": "sales_manager",
    "procurement_approval": "purchase_manager",
    "po_draft_creation": "purchase_manager",
    "quality_resolution_request": "quality_manager",
    "quality_resolution_approval": "quality_manager",
    "quality_resolution_write": "quality_manager",
    "quality_disposition_write": "quality_manager",
    "quality_close_write": "quality_manager",
}


@dataclass(frozen=True)
class RealIdentity:
    """已由上游身份系统认证的审批主体。"""

    subject: str
    display_name: str
    roles: tuple[str, ...]
    authority: str
    provider: str

    @property
    def actor_id(self) -> str:
        return self.subject

    def has_role(self, required_role: str) -> bool:
        expected = _canonical_role(required_role)
        available = {_canonical_role(role) for role in self.roles}
        # ERPNext 的 Administrator/System Manager 是平台管理员，允许执行
        # 已配置的业务审批，但仍然在审计中保留其真实主体名称。
        return expected in available or bool(available & {"administrator", "system_manager"})

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "actor_id": self.actor_id,
            "display_name": self.display_name,
            "roles": list(self.roles),
            "authority": self.authority,
            "provider": self.provider,
            "authenticated": True,
        }


def _canonical_role(value: str) -> str:
    """让 ERPNext 的 ``Sales Manager`` 与业务角色 snake_case 对齐。"""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")


def _role_names(value: Any) -> set[str]:
    """从 ERPNext/OpenMES 的常见角色字段形状中提取角色名。"""
    result: set[str] = set()
    if isinstance(value, str) and value.strip():
        result.add(value.strip())
    elif isinstance(value, dict):
        # ``id`` is deliberately excluded: OpenMES role payloads may include
        # unrelated numeric/user identifiers alongside the role object.
        for key in ("role", "role_name", "name"):
            if isinstance(value.get(key), str) and value[key].strip():
                result.add(value[key].strip())
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            result.update(_role_names(item))
    return result


def _roles_from_payload(payload: dict[str, Any]) -> set[str]:
    roles: set[str] = set()
    for key in ("roles", "role", "permissions", "groups"):
        roles.update(_role_names(payload.get(key)))
    return roles


def _configured_role_map(settings: IntegrationSettings) -> dict[str, list[str]]:
    if not settings.real_identity_role_map:
        return {}
    try:
        parsed = json.loads(settings.real_identity_role_map)
    except json.JSONDecodeError as exc:
        raise IntegrationError(
            "REAL_IDENTITY_ROLE_MAP 必须是 JSON 对象",
            code="identity_config_invalid",
        ) from exc
    if not isinstance(parsed, dict):
        raise IntegrationError("REAL_IDENTITY_ROLE_MAP 必须是 JSON 对象", code="identity_config_invalid")
    result: dict[str, list[str]] = {}
    for key, value in parsed.items():
        if isinstance(key, str):
            result[key] = sorted(_role_names(value))
    return result


def _apply_role_map(subject: str, roles: set[str], settings: IntegrationSettings) -> set[str]:
    mapping = _configured_role_map(settings)
    for key in (subject, subject.casefold(), "*"):
        roles.update(mapping.get(key, []))
    return roles


async def _from_erpnext(settings: IntegrationSettings) -> RealIdentity:
    client = ERPNextClient(
        settings.erpnext_base_url,
        settings.erpnext_api_key,
        settings.erpnext_api_secret,
    )
    try:
        logged = await client.get_logged_user()
        subject = str(logged.get("user", "")).strip()
        if not subject:
            raise IntegrationError("ERPNext 未返回当前登录用户", code="identity_missing")
        profile: dict[str, Any] = {}
        try:
            profile = await client.get_document("User", subject)
        except (IntegrationError, ValueError):
            # get_logged_user 已完成认证；用户没有 User 详情权限时仍可返回
            # 认证主体，角色由 REAL_IDENTITY_ROLE_MAP 或管理员角色提供。
            profile = {}
        roles = _apply_role_map(subject, _roles_from_payload(profile), settings)
        if subject.casefold() == "administrator":
            roles.add("Administrator")
        return RealIdentity(
            subject=subject,
            display_name=str(profile.get("full_name") or profile.get("first_name") or subject),
            roles=tuple(sorted(roles)),
            authority="ERPNext",
            provider="erpnext",
        )
    finally:
        await client.aclose()


async def _from_openmes(
    settings: IntegrationSettings,
    *,
    request_token: str | None = None,
) -> RealIdentity:
    token = (request_token or settings.openmes_user_token).strip()
    client = OpenMESClient(settings.openmes_base_url, user_token=token)
    try:
        raw_payload = await client.current_user()
        # OpenMES returns the authenticated user in ``data``; accepting the
        # unwrapped shape as well keeps the parser compatible with older API
        # deployments without treating the envelope itself as a user.
        payload: dict[str, Any] = raw_payload
        for _ in range(3):
            candidate = next(
                (payload.get(key) for key in ("data", "user", "current_user")
                 if isinstance(payload.get(key), dict)),
                None,
            )
            if not isinstance(candidate, dict):
                break
            payload = candidate
        subject = str(
            payload.get("username")
            or payload.get("user_name")
            or payload.get("email")
            or payload.get("id")
            or ""
        ).strip()
        if not subject:
            raise IntegrationError("OpenMES 未返回当前登录用户", code="identity_missing")
        roles = _apply_role_map(subject, _roles_from_payload(payload), settings)
        return RealIdentity(
            subject=subject,
            display_name=str(payload.get("full_name") or payload.get("name") or subject),
            roles=tuple(sorted(roles)),
            authority="OpenMES",
            provider="openmes",
        )
    finally:
        await client.aclose()


async def resolve_real_identity(
    settings: IntegrationSettings | None = None,
    *,
    request_bearer_token: str | None = None,
) -> RealIdentity:
    """解析当前主体；请求会话存在时优先使用其上游身份。

    请求 Bearer 会话目前只对 OpenMES/OIDC 兼容身份源开放。携带会话但
    无法解析时直接失败，绝不静默回退到服务端集成账号。
    """
    settings = settings or IntegrationSettings.from_environment()
    provider = settings.real_identity_provider
    if provider not in {"auto", "erpnext", "openmes"}:
        raise IntegrationError(
            f"不支持的 REAL_IDENTITY_PROVIDER: {provider}", code="identity_config_invalid"
        )
    request_token = (request_bearer_token or "").strip()
    if request_token:
        if settings.real_identity_provider not in {"auto", "openmes"} or not settings.openmes_base_url:
            raise IntegrationError(
                "请求 Bearer 会话无法映射到已配置的 OpenMES/OIDC 身份源",
                code="identity_request_token_unsupported",
            )
        try:
            return await _from_openmes(settings, request_token=request_token)
        except Exception as exc:
            raise IntegrationError(
                f"请求 Bearer 会话身份解析失败：{exc}",
                code="identity_request_token_invalid",
            ) from exc

    attempts: list[Exception] = []
    candidates = ["erpnext", "openmes"] if provider == "auto" else [provider]
    for candidate in candidates:
        try:
            if candidate == "erpnext":
                if not (settings.erpnext_base_url and settings.erpnext_api_key and settings.erpnext_api_secret):
                    raise IntegrationNotConfigured("ERPNext", ["ERPNEXT_BASE_URL/API_KEY/API_SECRET"])
                return await _from_erpnext(settings)
            if not (settings.openmes_base_url and settings.openmes_user_token):
                raise IntegrationNotConfigured("OpenMES", ["OPENMES_BASE_URL/OPENMES_TOKEN"])
            return await _from_openmes(settings)
        except Exception as exc:  # auto 模式尝试下一个已配置身份源
            attempts.append(exc)
            if provider != "auto":
                raise
    detail = "; ".join(str(exc) for exc in attempts) or "没有配置身份源"
    raise IntegrationError(f"无法解析真实审批身份：{detail}", code="identity_unavailable")


def ensure_role(identity: RealIdentity, capability: str) -> None:
    required = ROLE_REQUIREMENTS.get(capability)
    if required and not identity.has_role(required):
        raise PermissionError(
            f"身份 {identity.subject} 缺少 {required} 角色，不能执行 {capability}"
        )
