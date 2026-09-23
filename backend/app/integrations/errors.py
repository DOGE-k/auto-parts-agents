from __future__ import annotations


class IntegrationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "integration_error",
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class IntegrationNotConfigured(IntegrationError):
    def __init__(self, integration: str, missing: list[str]) -> None:
        super().__init__(
            f"{integration} 尚未配置：{', '.join(missing)}",
            code="not_configured",
            retryable=False,
        )


class IntegrationPermissionDenied(IntegrationError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="permission_denied", retryable=False)
