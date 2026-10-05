from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import delete, select

from app.persistence.database import SessionLocal
from app.persistence.models import ProjectSessionRow, ProjectUserRow
from app.services.project_auth import (
    ProjectAuthError,
    ensure_bootstrap_project_user,
    login_project_user,
    logout_project_token,
    resolve_project_token,
)


class ProjectAuthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.username = f"project-auth-{uuid4().hex}"

    def tearDown(self) -> None:
        with SessionLocal() as session:
            user = session.scalar(
                select(ProjectUserRow).where(ProjectUserRow.username == self.username)
            )
            if user is not None:
                session.execute(
                    delete(ProjectSessionRow).where(ProjectSessionRow.user_id == user.user_id)
                )
                session.delete(user)
                session.commit()

    def _env(self, **overrides: str):
        values = {
            "PROJECT_AUTH_USERNAME": self.username,
            "PROJECT_AUTH_PASSWORD": "initial-secret",
            "PROJECT_AUTH_DISPLAY_NAME": "测试项目用户",
            "PROJECT_AUTH_ROLES": "sales_manager,quality_manager",
        }
        values.update(overrides)
        return patch.dict(os.environ, values, clear=False)

    def test_bootstrap_hashes_password_and_resolves_session(self) -> None:
        with self._env():
            self.assertTrue(ensure_bootstrap_project_user())

            with SessionLocal() as session:
                user = session.scalar(
                    select(ProjectUserRow).where(ProjectUserRow.username == self.username)
                )
                self.assertIsNotNone(user)
                assert user is not None
                self.assertTrue(user.password_hash.startswith("pbkdf2_sha256$"))
                self.assertNotIn("initial-secret", user.password_hash)

            token, identity = login_project_user(self.username, "initial-secret")
            self.assertEqual(identity.subject, self.username)
            self.assertEqual(identity.authority, "Project")
            resolved = resolve_project_token(token)
            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertEqual(resolved.actor_id, self.username)

            with self.assertRaises(ProjectAuthError) as ctx:
                login_project_user(self.username, "wrong-secret")
            self.assertEqual(ctx.exception.code, "invalid_credentials")
            self.assertEqual(ctx.exception.status_code, 401)

    def test_bootstrap_does_not_overwrite_existing_user(self) -> None:
        with self._env():
            self.assertTrue(ensure_bootstrap_project_user())

        with self._env(
            PROJECT_AUTH_PASSWORD="replacement-secret",
            PROJECT_AUTH_DISPLAY_NAME="不应覆盖",
            PROJECT_AUTH_ROLES="purchase_manager",
        ):
            self.assertFalse(ensure_bootstrap_project_user())
            token, identity = login_project_user(self.username, "initial-secret")
            self.assertEqual(identity.display_name, "测试项目用户")
            self.assertEqual(set(identity.roles), {"sales_manager", "quality_manager"})
            self.assertIsNotNone(resolve_project_token(token))
            with self.assertRaises(ProjectAuthError) as ctx:
                login_project_user(self.username, "replacement-secret")
            self.assertEqual(ctx.exception.code, "invalid_credentials")

    def test_expired_session_is_rejected(self) -> None:
        with self._env():
            ensure_bootstrap_project_user()
            token, _ = login_project_user(self.username, "initial-secret")

        with SessionLocal() as session:
            user = session.scalar(
                select(ProjectUserRow).where(ProjectUserRow.username == self.username)
            )
            assert user is not None
            row = session.scalar(
                select(ProjectSessionRow).where(ProjectSessionRow.user_id == user.user_id)
            )
            assert row is not None
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()

        self.assertIsNone(resolve_project_token(token))

    def test_logout_revokes_session(self) -> None:
        with self._env():
            ensure_bootstrap_project_user()
            token, _ = login_project_user(self.username, "initial-secret")
            self.assertIsNotNone(resolve_project_token(token))
            logout_project_token(token)
            self.assertIsNone(resolve_project_token(token))
            # 重复登出应保持幂等，不重新激活会话。
            logout_project_token(token)
            self.assertIsNone(resolve_project_token(token))


if __name__ == "__main__":
    unittest.main()
