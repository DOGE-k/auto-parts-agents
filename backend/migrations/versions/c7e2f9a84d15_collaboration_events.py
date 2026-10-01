"""collaboration_events（P1 事件协作）

Revision ID: c7e2f9a84d15
Revises: b5d9e6a41c77
Create Date: 2026-10-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7e2f9a84d15'
down_revision: Union[str, Sequence[str], None] = 'b5d9e6a41c77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('collaboration_events',
    sa.Column('event_id', sa.String(length=60), nullable=False),
    sa.Column('event_type', sa.String(length=60), nullable=False),
    sa.Column('dedup_key', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('payload_json', sa.JSON(), nullable=False),
    sa.Column('result_json', sa.JSON(), nullable=True),
    sa.Column('error_json', sa.JSON(), nullable=True),
    sa.Column('failure_count', sa.Integer(), nullable=False),
    sa.Column('max_retries', sa.Integer(), nullable=False),
    sa.Column('taken_over_by', sa.String(length=120), nullable=False),
    sa.Column('taken_over_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('event_id'),
    sa.UniqueConstraint('dedup_key')
    )
    op.create_index(op.f('ix_collaboration_events_event_type'), 'collaboration_events', ['event_type'], unique=False)
    op.create_index(op.f('ix_collaboration_events_status'), 'collaboration_events', ['status'], unique=False)
    op.create_index(op.f('ix_collaboration_events_created_at'), 'collaboration_events', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_collaboration_events_created_at'), table_name='collaboration_events')
    op.drop_index(op.f('ix_collaboration_events_status'), table_name='collaboration_events')
    op.drop_index(op.f('ix_collaboration_events_event_type'), table_name='collaboration_events')
    op.drop_table('collaboration_events')
