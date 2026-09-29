"""
适配器工厂：根据配置选择 Mock 或真实适配器。

使用方式：
    from app.adapters.factory import get_erp_adapter, get_mes_adapter

    erp = get_erp_adapter()  # 根据 APP_ADAPTER_MODE 选择 ERPNext 或 Mock
    mes = get_mes_adapter()  # 根据 APP_ADAPTER_MODE 选择 OpenMES 或 Mock

策略：
- mode=mock → 始终返回 Mock，仅用于测试
- mode=real → 返回真实适配器，未配置凭证时抛出 IntegrationNotConfigured
- mode=auto → 配置了凭证则用真实，否则用 Mock（记录警告日志）

环境变量 APP_ADAPTER_MODE：auto / mock / real（默认 auto）
"""
from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable

from app.integrations.errors import IntegrationNotConfigured
from app.integrations.settings import IntegrationSettings

logger = logging.getLogger(__name__)


# 全局单例
_erp_adapter = None
_mes_adapter = None
_adapter_mode = "auto"
_approval_verifier: Callable[[str, str], Awaitable[bool]] | None = None


def set_approval_verifier(verifier: Callable[[str, str], Awaitable[bool]]) -> None:
    """设置审批校验器回调（用于 ERP 草稿写入前的审批验证）。"""
    global _approval_verifier, _erp_adapter
    _approval_verifier = verifier
    # 重置 ERP 适配器单例，下次获取时会用新的 verifier 重建
    _erp_adapter = None


def get_adapter_mode() -> str:
    """获取适配器模式：auto / mock / real。"""
    return os.getenv("APP_ADAPTER_MODE", "auto").strip().lower() or "auto"


def get_erp_adapter() -> Any:
    """
    获取 ERP 适配器。

    - mode=mock → 始终返回 MockERPAdapter
    - mode=real → 尝试返回 ERPNextAdapter，失败抛出异常
    - mode=auto → 配置了 ERPNext 凭证则用真实，否则用 Mock
    """
    global _erp_adapter
    if _erp_adapter is not None:
        return _erp_adapter

    mode = get_adapter_mode()
    settings = IntegrationSettings.from_environment()

    if mode == "mock":
        from app.adapters.erp.mock import get_mock_erp
        _erp_adapter = get_mock_erp()
        return _erp_adapter

    if mode == "real":
        from app.adapters.erp.erpnext_adapter import ERPNextAdapter
        from app.adapters.erp.erpnext import ERPNextClient
        client = ERPNextClient(
            settings.erpnext_base_url,
            settings.erpnext_api_key,
            settings.erpnext_api_secret,
            draft_writes_enabled=settings.erpnext_draft_writes_enabled,
            approval_verifier=_approval_verifier,
        )
        _erp_adapter = ERPNextAdapter(client)
        return _erp_adapter

    # auto 模式：只有凭证完整时才用真实适配器
    erp_configured = bool(
        settings.erpnext_base_url.strip()
        and settings.erpnext_api_key.strip()
        and settings.erpnext_api_secret.strip()
    )
    if erp_configured:
        try:
            from app.adapters.erp.erpnext_adapter import ERPNextAdapter
            from app.adapters.erp.erpnext import ERPNextClient
            client = ERPNextClient(
                settings.erpnext_base_url,
                settings.erpnext_api_key,
                settings.erpnext_api_secret,
                draft_writes_enabled=settings.erpnext_draft_writes_enabled,
                approval_verifier=_approval_verifier,
            )
            _erp_adapter = ERPNextAdapter(client)
        except IntegrationNotConfigured as e:
            logger.warning("auto 模式: ERPNext 凭证不完整，退回 Mock ERP: %s", e)
            from app.adapters.erp.mock import get_mock_erp
            _erp_adapter = get_mock_erp()
    else:
        logger.warning("auto 模式: ERPNext 未配置，使用 Mock ERP")
        from app.adapters.erp.mock import get_mock_erp
        _erp_adapter = get_mock_erp()

    return _erp_adapter


def get_mes_adapter() -> Any:
    """
    获取 MES 适配器。

    - mode=mock → 始终返回 MockMESAdapter
    - mode=real → 尝试返回 OpenMESAdapter，失败抛出异常
    - mode=auto → 配置了 OpenMES 凭证则用真实，否则用 Mock
    """
    global _mes_adapter
    if _mes_adapter is not None:
        return _mes_adapter

    mode = get_adapter_mode()
    settings = IntegrationSettings.from_environment()

    if mode == "mock":
        from app.adapters.mes.mock import get_mock_mes
        _mes_adapter = get_mock_mes()
        return _mes_adapter

    if mode == "real":
        from app.adapters.mes.openmes_adapter import OpenMESAdapter
        from app.adapters.mes.openmes import OpenMESClient
        client = OpenMESClient(
            settings.openmes_base_url,
            user_token=settings.openmes_user_token,
            erp_api_key=settings.openmes_erp_api_key,
        )
        _mes_adapter = OpenMESAdapter(client)
        return _mes_adapter

    # auto 模式：只有 base_url + user_token 都配置时才用真实适配器
    mes_configured = bool(
        settings.openmes_base_url.strip()
        and settings.openmes_user_token.strip()
    )
    if mes_configured:
        try:
            from app.adapters.mes.openmes_adapter import OpenMESAdapter
            from app.adapters.mes.openmes import OpenMESClient
            client = OpenMESClient(
                settings.openmes_base_url,
                user_token=settings.openmes_user_token,
                erp_api_key=settings.openmes_erp_api_key,
            )
            _mes_adapter = OpenMESAdapter(client)
        except IntegrationNotConfigured as e:
            logger.warning("auto 模式: OpenMES 凭证不完整，退回 Mock MES: %s", e)
            from app.adapters.mes.mock import get_mock_mes
            _mes_adapter = get_mock_mes()
    else:
        logger.warning("auto 模式: OpenMES 未配置，使用 Mock MES")
        from app.adapters.mes.mock import get_mock_mes
        _mes_adapter = get_mock_mes()

    return _mes_adapter


def reset_adapters() -> None:
    """重置适配器单例（用于测试或配置变更后）。"""
    global _erp_adapter, _mes_adapter
    _erp_adapter = None
    _mes_adapter = None


def get_adapter_status() -> dict[str, Any]:
    """获取当前适配器状态信息（用于 API 展示）。"""
    mode = get_adapter_mode()
    settings = IntegrationSettings.from_environment()

    erp_mode = "mock"
    erp_configured = bool(settings.erpnext_base_url and settings.erpnext_api_key and settings.erpnext_api_secret)
    if mode == "real" or (mode == "auto" and erp_configured):
        erp_mode = "real"

    mes_mode = "mock"
    mes_configured = bool(settings.openmes_base_url and settings.openmes_user_token)
    if mode == "real" or (mode == "auto" and mes_configured):
        mes_mode = "real"

    return {
        "mode": mode,
        "erp": {
            "active_mode": erp_mode,
            "erpnext_configured": erp_configured,
            "draft_writes_enabled": settings.erpnext_draft_writes_enabled,
        },
        "mes": {
            "active_mode": mes_mode,
            "openmes_configured": mes_configured,
            "erp_api_configured": bool(settings.openmes_base_url and settings.openmes_erp_api_key),
        },
    }
