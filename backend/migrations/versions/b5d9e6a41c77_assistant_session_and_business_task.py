"""assistant_sessions / assistant_messages / business_tasks + real_agent_runs 会话列

Revision ID: b5d9e6a41c77
Revises: 1a1aade41dfd
Create Date: 2026-10-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5d9e6a41c77'
down_revision: Union[str, Sequence[str], None] = '1a1aade41dfd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('assistant_sessions',
    sa.Column('session_id', sa.String(length=60), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('business_task_id', sa.String(length=60), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_active_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('session_id')
    )
    op.create_index(op.f('ix_assistant_sessions_business_task_id'), 'assistant_sessions', ['business_task_id'], unique=False)

    op.create_table('assistant_messages',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('session_id', sa.String(length=60), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_assistant_messages_session_id'), 'assistant_messages', ['session_id'], unique=False)
    op.create_index(op.f('ix_assistant_messages_expires_at'), 'assistant_messages', ['expires_at'], unique=False)

    op.create_table('business_tasks',
    sa.Column('task_id', sa.String(length=60), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=40), nullable=False),
    sa.Column('entity_context', sa.JSON(), nullable=False),
    sa.Column('active_plan', sa.JSON(), nullable=True),
    sa.Column('stale_downstream', sa.JSON(), nullable=False),
    sa.Column('selected_option_id', sa.String(length=40), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('task_id')
    )
    op.create_index(op.f('ix_business_tasks_status'), 'business_tasks', ['status'], unique=False)

    op.add_column('real_agent_runs', sa.Column('session_id', sa.String(length=60), nullable=True))
    op.add_column('real_agent_runs', sa.Column('business_task_id', sa.String(length=60), nullable=True))
    op.add_column('real_agent_runs', sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_real_agent_runs_session_id'), 'real_agent_runs', ['session_id'], unique=False)
    op.create_index(op.f('ix_real_agent_runs_business_task_id'), 'real_agent_runs', ['business_task_id'], unique=False)
    op.create_index(op.f('ix_real_agent_runs_expires_at'), 'real_agent_runs', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_real_agent_runs_expires_at'), table_name='real_agent_runs')
    op.drop_index(op.f('ix_real_agent_runs_business_task_id'), table_name='real_agent_runs')
    op.drop_index(op.f('ix_real_agent_runs_session_id'), table_name='real_agent_runs')
    op.drop_column('real_agent_runs', 'expires_at')
    op.drop_column('real_agent_runs', 'business_task_id')
    op.drop_column('real_agent_runs', 'session_id')
    op.drop_index(op.f('ix_business_tasks_status'), table_name='business_tasks')
    op.drop_table('business_tasks')
    op.drop_index(op.f('ix_assistant_messages_expires_at'), table_name='assistant_messages')
    op.drop_index(op.f('ix_assistant_messages_session_id'), table_name='assistant_messages')
    op.drop_table('assistant_messages')
    op.drop_index(op.f('ix_assistant_sessions_business_task_id'), table_name='assistant_sessions')
    op.drop_table('assistant_sessions')
