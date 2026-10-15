"""ppap task evidence input compilation artifact review

Revision ID: f7b3c81d5e42
Revises: e4f1a9c2d7b0
Create Date: 2026-10-13 10:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7b3c81d5e42"
down_revision: Union[str, Sequence[str], None] = "e4f1a9c2d7b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ppap_tasks",
        sa.Column("task_id", sa.String(length=60), nullable=False),
        sa.Column("customer_id", sa.String(length=120), nullable=False),
        sa.Column("customer_name", sa.String(length=200), nullable=False),
        sa.Column("part_no", sa.String(length=120), nullable=False),
        sa.Column("part_name", sa.String(length=200), nullable=False),
        sa.Column("part_side", sa.String(length=10), nullable=False),
        sa.Column("drawing_version", sa.String(length=60), nullable=False),
        sa.Column("engineering_change_level", sa.String(length=60), nullable=False),
        sa.Column("submission_reason", sa.String(length=120), nullable=False),
        sa.Column("submission_level", sa.String(length=60), nullable=False),
        sa.Column("template_id", sa.String(length=80), nullable=False),
        sa.Column("template_version", sa.String(length=40), nullable=False),
        sa.Column("template_source", sa.String(length=40), nullable=False),
        sa.Column("owner", sa.String(length=120), nullable=False),
        sa.Column("reviewer", sa.String(length=120), nullable=False),
        sa.Column("due_date", sa.String(length=40), nullable=False),
        sa.Column("run_state", sa.String(length=30), nullable=False),
        sa.Column("package_state", sa.String(length=30), nullable=False),
        sa.Column("customer_state", sa.String(length=30), nullable=False),
        sa.Column("business_object_json", sa.JSON(), nullable=False),
        sa.Column("coverage_json", sa.JSON(), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("authorized_by", sa.String(length=120), nullable=False),
        sa.Column("authorization_ref", sa.String(length=120), nullable=False),
        sa.Column("state_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index(op.f("ix_ppap_tasks_customer_id"), "ppap_tasks", ["customer_id"], unique=False)
    op.create_index(op.f("ix_ppap_tasks_part_no"), "ppap_tasks", ["part_no"], unique=False)
    op.create_index(op.f("ix_ppap_tasks_part_side"), "ppap_tasks", ["part_side"], unique=False)
    op.create_index(op.f("ix_ppap_tasks_run_state"), "ppap_tasks", ["run_state"], unique=False)
    op.create_index(op.f("ix_ppap_tasks_package_state"), "ppap_tasks", ["package_state"], unique=False)
    op.create_index(op.f("ix_ppap_tasks_customer_state"), "ppap_tasks", ["customer_state"], unique=False)
    op.create_index(op.f("ix_ppap_tasks_created_at"), "ppap_tasks", ["created_at"], unique=False)

    op.create_table(
        "ppap_elements",
        sa.Column("element_row_id", sa.String(length=60), nullable=False),
        sa.Column("task_id", sa.String(length=60), nullable=False),
        sa.Column("element_id", sa.String(length=80), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("requirement", sa.String(length=30), nullable=False),
        sa.Column("applicability_basis", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("compilation_method", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("source_refs_json", sa.JSON(), nullable=False),
        sa.Column("check_result_json", sa.JSON(), nullable=False),
        sa.Column("drafted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["ppap_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("element_row_id"),
        sa.UniqueConstraint("task_id", "element_id", name="uq_ppap_elements_task_element"),
    )
    op.create_index(op.f("ix_ppap_elements_task_id"), "ppap_elements", ["task_id"], unique=False)
    op.create_index(op.f("ix_ppap_elements_element_id"), "ppap_elements", ["element_id"], unique=False)
    op.create_index(op.f("ix_ppap_elements_status"), "ppap_elements", ["status"], unique=False)

    op.create_table(
        "ppap_evidence",
        sa.Column("evidence_id", sa.String(length=60), nullable=False),
        sa.Column("task_id", sa.String(length=60), nullable=False),
        sa.Column("dedup_key", sa.String(length=200), nullable=False),
        sa.Column("source_system", sa.String(length=60), nullable=False),
        sa.Column("source_type", sa.String(length=60), nullable=False),
        sa.Column("source_id", sa.String(length=160), nullable=False),
        sa.Column("source_version", sa.String(length=60), nullable=False),
        sa.Column("locator", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("read_status", sa.String(length=20), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("review_state", sa.String(length=30), nullable=False),
        sa.Column("collected_by", sa.String(length=120), nullable=False),
        sa.Column("element_ids_json", sa.JSON(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["ppap_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("evidence_id"),
        sa.UniqueConstraint("task_id", "dedup_key", name="uq_ppap_evidence_task_dedup"),
    )
    op.create_index(op.f("ix_ppap_evidence_task_id"), "ppap_evidence", ["task_id"], unique=False)
    op.create_index(op.f("ix_ppap_evidence_source_type"), "ppap_evidence", ["source_type"], unique=False)
    op.create_index(op.f("ix_ppap_evidence_read_status"), "ppap_evidence", ["read_status"], unique=False)
    op.create_index(op.f("ix_ppap_evidence_review_state"), "ppap_evidence", ["review_state"], unique=False)
    op.create_index(op.f("ix_ppap_evidence_collected_at"), "ppap_evidence", ["collected_at"], unique=False)

    op.create_table(
        "ppap_inputs",
        sa.Column("input_id", sa.String(length=60), nullable=False),
        sa.Column("task_id", sa.String(length=60), nullable=False),
        sa.Column("dataset", sa.String(length=60), nullable=False),
        sa.Column("record_key", sa.String(length=160), nullable=False),
        sa.Column("part_side", sa.String(length=10), nullable=False),
        sa.Column("element_id", sa.String(length=80), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("source_type", sa.String(length=60), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=False),
        sa.Column("confirmed_by", sa.String(length=120), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["ppap_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("input_id"),
        sa.UniqueConstraint(
            "task_id", "dataset", "record_key", "part_side", name="uq_ppap_inputs_record"
        ),
    )
    op.create_index(op.f("ix_ppap_inputs_task_id"), "ppap_inputs", ["task_id"], unique=False)
    op.create_index(op.f("ix_ppap_inputs_dataset"), "ppap_inputs", ["dataset"], unique=False)

    op.create_table(
        "ppap_compile_runs",
        sa.Column("run_id", sa.String(length=60), nullable=False),
        sa.Column("task_id", sa.String(length=60), nullable=False),
        sa.Column("trigger", sa.String(length=40), nullable=False),
        sa.Column("request_key", sa.String(length=120), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("template_id", sa.String(length=80), nullable=False),
        sa.Column("template_version", sa.String(length=40), nullable=False),
        sa.Column("rule_version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("steps_json", sa.JSON(), nullable=False),
        sa.Column("call_chain_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("idempotent_replay", sa.Boolean(), nullable=False),
        sa.Column("requested_by", sa.String(length=120), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["ppap_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("task_id", "request_key", name="uq_ppap_compile_runs_request_key"),
    )
    op.create_index(op.f("ix_ppap_compile_runs_task_id"), "ppap_compile_runs", ["task_id"], unique=False)
    op.create_index(
        op.f("ix_ppap_compile_runs_input_snapshot_hash"),
        "ppap_compile_runs",
        ["input_snapshot_hash"],
        unique=False,
    )
    op.create_index(op.f("ix_ppap_compile_runs_status"), "ppap_compile_runs", ["status"], unique=False)
    op.create_index(op.f("ix_ppap_compile_runs_started_at"), "ppap_compile_runs", ["started_at"], unique=False)

    op.create_table(
        "ppap_artifacts",
        sa.Column("artifact_id", sa.String(length=60), nullable=False),
        sa.Column("task_id", sa.String(length=60), nullable=False),
        sa.Column("run_id", sa.String(length=60), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("element_id", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("file_name", sa.String(length=240), nullable=False),
        sa.Column("storage_rel_path", sa.String(length=400), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("template_id", sa.String(length=80), nullable=False),
        sa.Column("template_version", sa.String(length=40), nullable=False),
        sa.Column("issue_count_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["ppap_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("artifact_id"),
        sa.UniqueConstraint(
            "task_id", "scope", "element_id", "version", name="uq_ppap_artifacts_version"
        ),
    )
    op.create_index(op.f("ix_ppap_artifacts_task_id"), "ppap_artifacts", ["task_id"], unique=False)
    op.create_index(op.f("ix_ppap_artifacts_scope"), "ppap_artifacts", ["scope"], unique=False)
    op.create_index(op.f("ix_ppap_artifacts_status"), "ppap_artifacts", ["status"], unique=False)
    op.create_index(op.f("ix_ppap_artifacts_created_at"), "ppap_artifacts", ["created_at"], unique=False)

    op.create_table(
        "ppap_issues",
        sa.Column("issue_id", sa.String(length=60), nullable=False),
        sa.Column("task_id", sa.String(length=60), nullable=False),
        sa.Column("dedup_key", sa.String(length=240), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("check_id", sa.String(length=60), nullable=False),
        sa.Column("element_id", sa.String(length=80), nullable=False),
        sa.Column("field_path", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("required_evidence", sa.Text(), nullable=False),
        sa.Column("responsible_role", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=False),
        sa.Column("resolved_by", sa.String(length=120), nullable=False),
        sa.Column("blocks_approval", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["ppap_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("issue_id"),
        sa.UniqueConstraint("task_id", "dedup_key", name="uq_ppap_issues_dedup"),
    )
    op.create_index(op.f("ix_ppap_issues_task_id"), "ppap_issues", ["task_id"], unique=False)
    op.create_index(op.f("ix_ppap_issues_kind"), "ppap_issues", ["kind"], unique=False)
    op.create_index(op.f("ix_ppap_issues_severity"), "ppap_issues", ["severity"], unique=False)
    op.create_index(op.f("ix_ppap_issues_element_id"), "ppap_issues", ["element_id"], unique=False)
    op.create_index(op.f("ix_ppap_issues_status"), "ppap_issues", ["status"], unique=False)
    op.create_index(op.f("ix_ppap_issues_created_at"), "ppap_issues", ["created_at"], unique=False)

    op.create_table(
        "ppap_reviews",
        sa.Column("review_id", sa.String(length=60), nullable=False),
        sa.Column("task_id", sa.String(length=60), nullable=False),
        sa.Column("artifact_id", sa.String(length=60), nullable=False),
        sa.Column("artifact_version", sa.Integer(), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=30), nullable=False),
        sa.Column("reviewer", sa.String(length=120), nullable=False),
        sa.Column("reviewer_role", sa.String(length=60), nullable=False),
        sa.Column("author", sa.String(length=120), nullable=False),
        sa.Column("separation_warning", sa.String(length=80), nullable=False),
        sa.Column("comments", sa.Text(), nullable=False),
        sa.Column("blocker_summary_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["ppap_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("review_id"),
    )
    op.create_index(op.f("ix_ppap_reviews_task_id"), "ppap_reviews", ["task_id"], unique=False)
    op.create_index(op.f("ix_ppap_reviews_decision"), "ppap_reviews", ["decision"], unique=False)
    op.create_index(op.f("ix_ppap_reviews_created_at"), "ppap_reviews", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ppap_reviews_created_at"), table_name="ppap_reviews")
    op.drop_index(op.f("ix_ppap_reviews_decision"), table_name="ppap_reviews")
    op.drop_index(op.f("ix_ppap_reviews_task_id"), table_name="ppap_reviews")
    op.drop_table("ppap_reviews")

    op.drop_index(op.f("ix_ppap_issues_created_at"), table_name="ppap_issues")
    op.drop_index(op.f("ix_ppap_issues_status"), table_name="ppap_issues")
    op.drop_index(op.f("ix_ppap_issues_element_id"), table_name="ppap_issues")
    op.drop_index(op.f("ix_ppap_issues_severity"), table_name="ppap_issues")
    op.drop_index(op.f("ix_ppap_issues_kind"), table_name="ppap_issues")
    op.drop_index(op.f("ix_ppap_issues_task_id"), table_name="ppap_issues")
    op.drop_table("ppap_issues")

    op.drop_index(op.f("ix_ppap_artifacts_created_at"), table_name="ppap_artifacts")
    op.drop_index(op.f("ix_ppap_artifacts_status"), table_name="ppap_artifacts")
    op.drop_index(op.f("ix_ppap_artifacts_scope"), table_name="ppap_artifacts")
    op.drop_index(op.f("ix_ppap_artifacts_task_id"), table_name="ppap_artifacts")
    op.drop_table("ppap_artifacts")

    op.drop_index(op.f("ix_ppap_compile_runs_started_at"), table_name="ppap_compile_runs")
    op.drop_index(op.f("ix_ppap_compile_runs_status"), table_name="ppap_compile_runs")
    op.drop_index(
        op.f("ix_ppap_compile_runs_input_snapshot_hash"), table_name="ppap_compile_runs"
    )
    op.drop_index(op.f("ix_ppap_compile_runs_task_id"), table_name="ppap_compile_runs")
    op.drop_table("ppap_compile_runs")

    op.drop_index(op.f("ix_ppap_inputs_dataset"), table_name="ppap_inputs")
    op.drop_index(op.f("ix_ppap_inputs_task_id"), table_name="ppap_inputs")
    op.drop_table("ppap_inputs")

    op.drop_index(op.f("ix_ppap_evidence_collected_at"), table_name="ppap_evidence")
    op.drop_index(op.f("ix_ppap_evidence_review_state"), table_name="ppap_evidence")
    op.drop_index(op.f("ix_ppap_evidence_read_status"), table_name="ppap_evidence")
    op.drop_index(op.f("ix_ppap_evidence_source_type"), table_name="ppap_evidence")
    op.drop_index(op.f("ix_ppap_evidence_task_id"), table_name="ppap_evidence")
    op.drop_table("ppap_evidence")

    op.drop_index(op.f("ix_ppap_elements_status"), table_name="ppap_elements")
    op.drop_index(op.f("ix_ppap_elements_element_id"), table_name="ppap_elements")
    op.drop_index(op.f("ix_ppap_elements_task_id"), table_name="ppap_elements")
    op.drop_table("ppap_elements")

    op.drop_index(op.f("ix_ppap_tasks_created_at"), table_name="ppap_tasks")
    op.drop_index(op.f("ix_ppap_tasks_customer_state"), table_name="ppap_tasks")
    op.drop_index(op.f("ix_ppap_tasks_package_state"), table_name="ppap_tasks")
    op.drop_index(op.f("ix_ppap_tasks_run_state"), table_name="ppap_tasks")
    op.drop_index(op.f("ix_ppap_tasks_part_side"), table_name="ppap_tasks")
    op.drop_index(op.f("ix_ppap_tasks_part_no"), table_name="ppap_tasks")
    op.drop_index(op.f("ix_ppap_tasks_customer_id"), table_name="ppap_tasks")
    op.drop_table("ppap_tasks")
