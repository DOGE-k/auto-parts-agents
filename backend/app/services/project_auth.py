"""项目自身的登录会话与人工身份。

ERPNext/OpenMES 凭据只用于后端连接上游系统；页面操作者使用本项目账号。
会话令牌只在浏览器 Cookie 中出现，数据库只保存其 SHA-256 摘要。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.persistence.database import SessionLocal
from app.persistence.models import ProjectSessionRow, ProjectUserRow, utc_now
from app.services.identity import RealIdentity


PROJECT_SESSION_COOKIE = "project_session"
DEFAULT_PROJECT_ROLES = (
    "sales_manager",
    "purchase_manager",
    "production_manager",
    "quality_manager",
)
_PBKDF2_ITERATIONS = 210_000


class ProjectAuthError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _config() -> dict[str, Any]:
    roles = tuple(
        item.strip()
        for item in os.getenv("PROJECT_AUTH_ROLES", ",".join(DEFAULT_PROJECT_ROLES)).split(",")
        if item.strip()
    )
    try:
        ttl = int(os.getenv("PROJECT_SESSION_TTL_SECONDS", "28800"))
    except ValueError:
        ttl = 28800
    return {
        "username": os.getenv("PROJECT_AUTH_USERNAME", "admin").strip() or "admin",
        "password": os.getenv("PROJECT_AUTH_PASSWORD", "").strip(),
        "display_name": os.getenv("PROJECT_AUTH_DISPLAY_NAME", "项目管理员").strip() or "项目管理员",
        "roles": roles or DEFAULT_PROJECT_ROLES,
        "ttl": max(300, min(ttl, 7 * 24 * 60 * 60)),
    }


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(digest_text + "=" * (-len(digest_text) % 4))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError, UnicodeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def project_identity(user: ProjectUserRow) -> RealIdentity:
    roles = tuple(str(role) for role in (user.roles_json or []) if str(role).strip())
    return RealIdentity(
        subject=user.username,
        display_name=user.display_name,
        roles=roles,
        authority="Project",
        provider="project",
    )


def ensure_bootstrap_project_user() -> bool:
    """Create the first local user when an explicit password is configured.

    Existing users are never overwritten by environment changes. If no password
    is configured, startup remains available but project login reports that the
    account has not been initialized.
    """
    cfg = _config()
    if not cfg["password"]:
        return False
    with SessionLocal() as session:
        existing = session.scalar(select(ProjectUserRow).where(ProjectUserRow.username == cfg["username"]))
        if existing:
            return False
        session.add(
            ProjectUserRow(
                user_id=f"project-user-{secrets.token_hex(8)}",
                username=cfg["username"],
                display_name=cfg["display_name"],
                password_hash=_hash_password(cfg["password"]),
                roles_json=list(cfg["roles"]),
                is_active=True,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        try:
            session.commit()
        except IntegrityError:
            # Multiple application workers can bootstrap concurrently. The
            # unique username constraint makes the losing worker observe the
            # already-created account; do not turn that benign race into a
            # startup failure or overwrite the winner's password/roles.
            session.rollback()
            return False
        return True


def login_project_user(username: str, password: str) -> tuple[str, RealIdentity]:
    username = (username or "").strip()
    if not username or not password:
        raise ProjectAuthError("invalid_login_input", "用户名和密码不能为空", 422)
    with SessionLocal() as session:
        user = session.scalar(select(ProjectUserRow).where(ProjectUserRow.username == username))
        if user is None:
            configured = _config()["password"]
            if not configured:
                raise ProjectAuthError("project_auth_not_initialized", "项目账号尚未初始化，请设置 PROJECT_AUTH_PASSWORD 后重启后端", 503)
            raise ProjectAuthError("invalid_credentials", "项目用户名或密码错误", 401)
        if not user.is_active or not _verify_password(password, user.password_hash):
            raise ProjectAuthError("invalid_credentials", "项目用户名或密码错误", 401)
        raw_token = secrets.token_urlsafe(32)
        now = utc_now()
        session.add(
            ProjectSessionRow(
                session_id=f"project-session-{secrets.token_hex(8)}",
                token_hash=_token_hash(raw_token),
                user_id=user.user_id,
                created_at=now,
                expires_at=now + timedelta(seconds=_config()["ttl"]),
            )
        )
        session.commit()
        return raw_token, project_identity(user)


def resolve_project_token(token: str | None) -> RealIdentity | None:
    token = (token or "").strip()
    if not token:
        return None
    with SessionLocal() as session:
        row = session.scalar(
            select(ProjectSessionRow).where(
                ProjectSessionRow.token_hash == _token_hash(token),
                ProjectSessionRow.revoked_at.is_(None),
            )
        )
        if row is None or _as_utc(row.expires_at) <= datetime.now(timezone.utc):
            return None
        user = session.get(ProjectUserRow, row.user_id)
        if user is None or not user.is_active:
            return None
        return project_identity(user)


def logout_project_token(token: str | None) -> None:
    token = (token or "").strip()
    if not token:
        return
    with SessionLocal() as session:
        row = session.scalar(select(ProjectSessionRow).where(ProjectSessionRow.token_hash == _token_hash(token)))
        if row is not None and row.revoked_at is None:
            row.revoked_at = utc_now()
            session.commit()


def project_session_ttl_seconds() -> int:
    return int(_config()["ttl"])
