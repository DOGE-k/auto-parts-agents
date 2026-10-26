"""Private browser assistant session ownership; preserve unowned legacy rows.
Revision ID: f31d9a2c6b10
Revises: a8c4d12e9f60
"""
from alembic import op
import sqlalchemy as sa
revision = "f31d9a2c6b10"
down_revision = "a8c4d12e9f60"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("assistant_sessions", sa.Column("owner_subject", sa.String(200), nullable=True))
    op.create_index("ix_assistant_sessions_owner_subject", "assistant_sessions", ["owner_subject"])

def downgrade():
    op.drop_index("ix_assistant_sessions_owner_subject", table_name="assistant_sessions")
    op.drop_column("assistant_sessions", "owner_subject")
