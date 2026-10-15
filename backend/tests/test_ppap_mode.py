"""PPAP mode gates and migrations on isolated, generated test records only."""
from __future__ import annotations

import importlib.util
import io
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.persistence.models import Base
from app.services import ppap_mode as modes


def migration(name):
    path = Path(__file__).parents[1] / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mode_defaults_and_invalid_values():
    assert modes.normalize_mode() == "REAL"
    assert modes.normalize_mode("test") == "TEST"
    assert modes.task_mode({}) == "REAL"
    assert modes.is_test_mode({"data_mode": "TEST"})
    with pytest.raises(ValueError):
        modes.normalize_mode("custom")
    assert modes.validate_mode_patch({"data_mode": "REAL"})["code"] == "task_mode_immutable"


@pytest.mark.parametrize("source_type", ["my_custom_source", "openmes_inspection_record", "erp_sales_order", "internal_ppap_package", "user_upload"])
@pytest.mark.parametrize("mode", ["REAL", "TEST"])
def test_manual_input_cannot_impersonate_collector_or_package(source_type, mode):
    assert modes.validate_input_source(mode, source_type, "actual-original", {})["code"] == "unknown_input_source_type"


@pytest.mark.parametrize("source_type,source_ref,payload", [
    ("synthetic_demo_only", "source", {}),
    ("user_confirmation", "TEST-REF-1", {}),
    ("user_confirmation", "DEMO_REF_1", {}),
    ("user_confirmation", "explicit fixture confirmation", {}),
    ("user_confirmation", "actual-source", {"provenance": {"source_ref": "mock original"}}),
    ("user_confirmation", "actual-source", {"provenance": {"data_mode": "TEST"}}),
    ("user_confirmation", "actual-source", {"synthetic_demo_only": True}),
])
def test_real_inputs_reject_explicit_nested_test_provenance(source_type, source_ref, payload):
    assert modes.validate_input_source("REAL", source_type, source_ref, payload)["code"] == "test_data_in_real_task"
    assert modes.validate_input_source("TEST", source_type, source_ref, payload) is None


def test_actual_chinese_testing_and_tooling_prose_are_not_test_provenance():
    payload = {"op_name": "fixture assembly / 检具试验测试", "note": "使用 fixture 定位工装，测试报告来自实际检测", "item": "fixture"}
    assert not modes.has_test_marker(payload)
    assert modes.validate_input_source("REAL", "user_confirmation", "受控检测报告 R-20261014", payload) is None
    assert modes.validate_task_data("REAL", {"part_name": "fixture bracket 检具", "submission_reason": "试验测试报告", "authorization_ref": "实际编制授权 A-1"}) is None


def test_test_mode_blocks_real_business_links_and_customer_status_but_allows_local_sources():
    assert modes.validate_task_data("TEST", {"work_order_id": "WO-ACTUAL-1"})["code"] == "test_business_link_forbidden"
    assert modes.validate_task_data("TEST", {"business_object": {"erp_order_id": "ORDER-ACTUAL-1"}})["code"] == "test_business_link_forbidden"
    assert modes.validate_task_data("TEST", {"design_responsibility": "功能测试输入", "work_order_no": ""}) is None
    assert modes.validate_collect("TEST")["code"] == "test_real_source_forbidden"
    assert modes.validate_customer_state("TEST")["code"] == "test_customer_state_forbidden"
    assert modes.validate_collect("REAL") is None
    assert modes.validate_customer_state("REAL") is None
    assert modes.validate_evidence_source("TEST", "user_upload", "TEST-DOC", "sources/fixture.txt") is None
    assert modes.validate_evidence_source("TEST", "openmes_inspection_record", "WO-ACTUAL", "")["code"] == "test_real_source_forbidden"
    assert modes.validate_evidence_source("REAL", "custom", "source", "")["code"] == "unknown_source_type"


def _required_record(table):
    """Create minimal records from the base migration's real column contract."""
    moment = datetime(2026, 10, 14, tzinfo=timezone.utc)
    return {column.name: {} if isinstance(column.type, sa.JSON) else moment if isinstance(column.type, sa.DateTime)
            else False if isinstance(column.type, sa.Boolean) else 0 if isinstance(column.type, sa.Integer) else ""
            for column in table.columns if not column.nullable}


def test_mode_migration_backfills_only_explicit_test_records_and_preserves_business(tmp_path):
    base = migration("f7b3c81d5e42_ppap_task_and_compilation.py")
    mode = migration("a8c4d12e9f60_ppap_real_test_mode.py")
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'mode_migration.db').as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE existing_business (id INTEGER PRIMARY KEY, value TEXT)"))
            connection.execute(sa.text("INSERT INTO existing_business VALUES (1, 'preserved')"))
            with Operations.context(MigrationContext.configure(connection)):
                base.upgrade()
            metadata = sa.MetaData()
            tasks = sa.Table("ppap_tasks", metadata, autoload_with=connection)
            inputs = sa.Table("ppap_inputs", metadata, autoload_with=connection)
            for task_id, authorization in [("REAL-1", "实际试验测试授权"), ("TEST-PPAP-EXISTING", ""),
                ("PPAP-SEEDED", "TEST-PPAP-SEED-V1:RIGHT"), ("PPAP-SYNTHETIC", ""), ("PPAP-UNRELATED", "testing gauge fixture approval")]:
                connection.execute(tasks.insert().values(**{**_required_record(tasks), "task_id": task_id, "authorization_ref": authorization}))
            connection.execute(inputs.insert().values(**{**_required_record(inputs), "input_id": "SYNTH-IN", "task_id": "PPAP-SYNTHETIC", "source_type": "synthetic_demo_only"}))
            with Operations.context(MigrationContext.configure(connection)):
                mode.upgrade()
            classifications = dict(connection.execute(sa.text("SELECT task_id, data_mode FROM ppap_tasks")).all())
            assert classifications == {"REAL-1": "REAL", "TEST-PPAP-EXISTING": "TEST", "PPAP-SEEDED": "TEST", "PPAP-SYNTHETIC": "TEST", "PPAP-UNRELATED": "REAL"}
            assert {column["name"] for column in sa.inspect(connection).get_columns("ppap_tasks")} == set(Base.metadata.tables["ppap_tasks"].columns.keys())
            assert connection.execute(sa.text("SELECT value FROM existing_business")).scalar() == "preserved"
            assert connection.execute(sa.text("SELECT source_type FROM ppap_inputs")).scalar() == "synthetic_demo_only"
            with Operations.context(MigrationContext.configure(connection)):
                mode.downgrade()
            assert "data_mode" not in {column["name"] for column in sa.inspect(connection).get_columns("ppap_tasks")}
            assert connection.execute(sa.text("SELECT COUNT(*) FROM ppap_tasks")).scalar() == 5
            assert connection.execute(sa.text("SELECT value FROM existing_business")).scalar() == "preserved"
    finally:
        engine.dispose()


def test_mode_migration_compiles_for_postgresql_without_json_dialect_assumptions():
    mode = migration("a8c4d12e9f60_ppap_real_test_mode.py")
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "literal_binds": True, "output_buffer": output})
    with Operations.context(context):
        mode.upgrade()
    sql = output.getvalue()
    assert "DEFAULT 'REAL' NOT NULL" in sql
    assert "EXISTS (SELECT 1" in sql
    assert "synthetic_demo_only" in sql
    assert "json_extract" not in sql
