"""project users and sessions

Revision ID: e4f1a9c2d7b0
Revises: c7e2f9a84d15
Create Date: 2026-10-05 15:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4f1a9c2d7b0"
down_revision: Union[str, Sequence[str], None] = "c7e2f9a84d15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_users",
        sa.Column("user_id", sa.String(length=80), nullable=False),
        sa.Column("username", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("password_hash", sa.String(length=300), nullable=False),
        sa.Column("roles_json", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("username", name="uq_project_users_username"),
    )
    op.create_index(op.f("ix_project_users_username"), "project_users", ["username"], unique=False)
    op.create_index(op.f("ix_project_users_is_active"), "project_users", ["is_active"], unique=False)
    op.create_table(
        "project_sessions",
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["project_users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_project_sessions_token_hash"), "project_sessions", ["token_hash"], unique=True)
    op.create_index(op.f("ix_project_sessions_user_id"), "project_sessions", ["user_id"], unique=False)
    op.create_index(op.f("ix_project_sessions_expires_at"), "project_sessions", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_project_sessions_expires_at"), table_name="project_sessions")
    op.drop_index(op.f("ix_project_sessions_user_id"), table_name="project_sessions")
    op.drop_index(op.f("ix_project_sessions_token_hash"), table_name="project_sessions")
    op.drop_table("project_sessions")
    op.drop_index(op.f("ix_project_users_is_active"), table_name="project_users")
    op.drop_index(op.f("ix_project_users_username"), table_name="project_users")
    op.drop_table("project_users")
