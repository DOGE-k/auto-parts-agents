"""Immutable PPAP REAL / TEST classification.

Revision ID: a8c4d12e9f60
Revises: f7b3c81d5e42
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a8c4d12e9f60"
down_revision: Union[str, Sequence[str], None] = "f7b3c81d5e42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ppap_tasks", sa.Column("data_mode", sa.String(10), nullable=False, server_default="REAL"))
    op.create_index("ix_ppap_tasks_data_mode", "ppap_tasks", ["data_mode"], unique=False)
    # Portable predicates, including SQLite's default case-insensitive LIKE.
    # Only explicit seed provenance changes existing tasks; other business rows
    # and ambiguous prose remain untouched. Avoid dialect-specific JSON reads.
    tasks = sa.table("ppap_tasks", sa.column("task_id", sa.String),
                     sa.column("part_no", sa.String), sa.column("authorization_ref", sa.String), sa.column("data_mode", sa.String))
    inputs = sa.table("ppap_inputs", sa.column("task_id", sa.String), sa.column("source_type", sa.String))
    explicit_id = sa.func.upper(tasks.c.task_id).like("TEST-PPAP-%")
    part_no = sa.func.upper(sa.func.coalesce(tasks.c.part_no, ""))
    explicit_part = sa.or_(part_no.like("TEST-%"), part_no.like(r"TEST\_%", escape="\\"),
                          part_no.like("DEMO-%"), part_no.like(r"DEMO\_%", escape="\\"),
                          part_no.like("FIXTURE-%"), part_no.like("MOCK-%"))
    authorization = sa.func.upper(sa.func.coalesce(tasks.c.authorization_ref, ""))
    explicit_authorization = sa.or_(
        authorization.like("TEST-PPAP-%"),
        authorization.like(r"SYNTHETIC\_DEMO\_ONLY%", escape="\\"),
        authorization.like(r"%:SYNTHETIC\_DEMO\_ONLY%", escape="\\"),
        authorization.like(r"%；SYNTHETIC\_DEMO\_ONLY%", escape="\\"),
    )
    synthetic_input = sa.exists(sa.select(1).select_from(inputs).where(
        inputs.c.task_id == tasks.c.task_id,
        inputs.c.source_type == "synthetic_demo_only",
    ))
    op.execute(tasks.update().where(sa.or_(explicit_id, explicit_part, explicit_authorization, synthetic_input)).values(data_mode="TEST"))


def downgrade() -> None:
    op.drop_index("ix_ppap_tasks_data_mode", table_name="ppap_tasks")
    # Batch operations support SQLite without assuming DROP COLUMN support.
    with op.batch_alter_table("ppap_tasks") as batch_op:
        batch_op.drop_column("data_mode")
