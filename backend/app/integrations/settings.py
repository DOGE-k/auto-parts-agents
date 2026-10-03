from __future__ import annotations

import os
from dataclasses import dataclass


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class IntegrationSettings:
    deepseek_base_url: str
    deepseek_model: str
    deepseek_api_key: str
    erpnext_base_url: str
    erpnext_api_key: str
    erpnext_api_secret: str
    erpnext_draft_writes_enabled: bool
    openmes_base_url: str
    openmes_user_token: str
    openmes_erp_api_key: str
    # 审批身份来源。默认优先读取 ERPNext 当前登录用户，再回退 OpenMES。
    # 角色映射是 JSON 字符串，密钥可以是外部用户名或 "*"。
    real_identity_provider: str = "auto"
    real_identity_role_map: str = ""
    # Optional ACPs/Wutong discovery endpoints. Discovery is read-only here;
    # registration remains disabled until the deployment supplies its auth
    # contract and tenant policy.
    wutong_registry_url: str = ""
    wutong_discovery_url: str = ""
    wutong_tenant: str = ""

    @classmethod
    def from_environment(cls) -> "IntegrationSettings":
        return cls(
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
            deepseek_model=os.getenv("LLM_MODEL", "deepseek-flash").strip(),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            erpnext_base_url=os.getenv("ERPNEXT_BASE_URL", "").strip(),
            erpnext_api_key=os.getenv("ERPNEXT_API_KEY", "").strip(),
            erpnext_api_secret=os.getenv("ERPNEXT_API_SECRET", "").strip(),
            erpnext_draft_writes_enabled=_truthy(os.getenv("ERPNEXT_DRAFT_WRITES_ENABLED")),
            openmes_base_url=os.getenv("OPENMES_BASE_URL", "").strip(),
            openmes_user_token=os.getenv("OPENMES_TOKEN", "").strip(),
            openmes_erp_api_key=os.getenv("OPENMES_ERP_API_KEY", "").strip(),
            real_identity_provider=os.getenv("REAL_IDENTITY_PROVIDER", "auto").strip().lower() or "auto",
            real_identity_role_map=os.getenv("REAL_IDENTITY_ROLE_MAP", "").strip(),
            wutong_registry_url=os.getenv("WUTONG_REGISTRY_URL", "").strip(),
            wutong_discovery_url=os.getenv("WUTONG_DISCOVERY_URL", "").strip(),
            wutong_tenant=os.getenv("WUTONG_TENANT", "").strip(),
        )

    def public_status(self) -> dict[str, dict[str, bool | str]]:
        return {
            "deepseek": {
                "configured": bool(self.deepseek_api_key),
                "model": self.deepseek_model,
            },
            "erpnext": {
                "configured": bool(self.erpnext_base_url and self.erpnext_api_key and self.erpnext_api_secret),
                "draft_writes_enabled": self.erpnext_draft_writes_enabled,
            },
            "openmes": {
                "configured": bool(self.openmes_base_url and self.openmes_user_token),
                "erp_read_api_configured": bool(self.openmes_base_url and self.openmes_erp_api_key),
            },
            "identity": {
                "provider": self.real_identity_provider,
                "role_map_configured": bool(self.real_identity_role_map),
            },
            "wutong": {
                "registry_configured": bool(self.wutong_registry_url),
                "discovery_configured": bool(self.wutong_discovery_url),
                "tenant_configured": bool(self.wutong_tenant),
            },
        }
