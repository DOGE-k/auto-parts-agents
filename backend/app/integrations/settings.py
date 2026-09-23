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
        }
