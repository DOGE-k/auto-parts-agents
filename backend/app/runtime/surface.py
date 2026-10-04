"""运行时表面开关：真实业务 API 与 Mock 模拟 API 可独立启用。"""
from __future__ import annotations

import os


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def mock_demo_enabled() -> bool:
    """是否暴露 Mock 场景 API。

    显式 ``MOCK_DEMO_ENABLED`` 优先；未配置时 real 适配器默认只暴露真实
    业务表面，避免旧场景路由与真实链共用入口。
    """
    override = os.getenv("MOCK_DEMO_ENABLED")
    if override is not None and override.strip():
        return _truthy(override)
    return os.getenv("APP_ADAPTER_MODE", "auto").strip().lower() != "real"


def public_surface() -> dict[str, object]:
    enabled = mock_demo_enabled()
    return {
        "mock_demo_enabled": enabled,
        "real_business_enabled": True,
        "aip_mock_skills_enabled": enabled,
        "mock_api_prefixes": [
            "/api/projects", "/api/agents", "/api/approvals", "/api/plans",
            "/api/audit", "/api/scenarios", "/api/replay", "/api/quotation",
        ],
    }
