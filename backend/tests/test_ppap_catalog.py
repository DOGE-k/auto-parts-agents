"""The PPAP question channel is query-only; compilation stays out of AIP."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from app.aip.agents.quality_document_aip import create_quality_document_aip_service
from app.persistence.database import SessionLocal
from app.persistence.models import (
    PpapArtifactRow, PpapCompileRunRow, PpapElementRow, PpapEvidenceRow,
    PpapInputRow, PpapIssueRow, PpapReviewRow, PpapTaskRow,
)
from app.services import ppap
from app.services.capability_catalog import build_capability_catalog
from app.services.coordinator import REAL_SKILL_TOOLS


PPAP_SKILLS = {
    "quality.ppap_find_tasks", "quality.ppap_get_applicability", "quality.ppap_get_status",
    "quality.ppap_explain_missing", "quality.ppap_evidence_plan", "quality.ppap_get_versions",
}


def test_six_ppap_skills_are_registered_with_read_only_metadata():
    service = create_quality_document_aip_service()
    specs = [tool for tool in REAL_SKILL_TOOLS if tool["skill_id"] in PPAP_SKILLS]
    assert len(specs) == 6
    assert PPAP_SKILLS <= set(service.registered_skill_ids())
    assert {"quality.get_real_package", "quality.assess_quality_impact", "quality.check_issue_closure",
            "quality.list_open_issues"} <= set(service.registered_skill_ids())
    catalog = build_capability_catalog(specs, {"quality-document": set(service.registered_skill_ids())})
    assert {entry["skill_id"] for entry in catalog["entries"]} == PPAP_SKILLS
    for item in catalog["entries"]:
        assert item["read_only"] is True
        assert item["requires_approval"] is False
        assert item["aip_agent"] == "quality-document"
    assert not any(item["kind"] in {"invalid_declaration", "declared_not_registered"}
                   for item in catalog["rejections"])


def test_write_operations_cannot_be_advertised_as_ppap_queries():
    spec = next(tool for tool in REAL_SKILL_TOOLS if tool["skill_id"] in PPAP_SKILLS)
    bad = {**spec, "skill_id": "quality.ppap_compile", "read_only": False, "requires_approval": True}
    catalog = build_capability_catalog([bad], None)
    assert catalog["entries"] == []
    assert catalog["rejections"][0]["kind"] == "invalid_declaration"
    declared_ids = {tool["skill_id"] for tool in REAL_SKILL_TOOLS}
    assert {"quality.ppap_compile", "quality.ppap_approve", "quality.ppap_archive"}.isdisjoint(declared_ids)


def test_real_surface_and_acs_only_advertise_allowed_ppap_queries():
    service = create_quality_document_aip_service()
    declared = {tool["skill_id"] for tool in REAL_SKILL_TOOLS if tool["aip_agent"] == "quality-document"}
    service.restrict_skills(declared)
    assert set(service.registered_skill_ids()) == declared
    assert "quality_document.build_package_draft" not in service.registered_skill_ids()
    path = Path(__file__).resolve().parents[1] / "acs" / "quality_document_acs.json"
    acs = json.loads(path.read_text(encoding="utf-8"))
    assert {skill["id"] for skill in acs["skills"]} == declared


def test_all_six_handlers_leave_all_ppap_tables_unchanged():
    # The global pytest database is temporary and independent of business data.
    result = ppap.create_task(customer_name="查询边界测试客户", part_no=f"QUERY-{uuid4().hex}",
                              part_name="查询测试件", part_side="LEFT", owner="query-test", actor="query-test")
    task_id = result["task"]["task_id"]
    service = create_quality_document_aip_service()
    rows = (PpapTaskRow, PpapElementRow, PpapEvidenceRow, PpapInputRow,
            PpapCompileRunRow, PpapArtifactRow, PpapIssueRow, PpapReviewRow)

    def snapshot():
        with SessionLocal() as session:
            return [[{column.name: getattr(row, column.name) for column in cls.__table__.columns}
                     for row in session.query(cls).filter_by(task_id=task_id).all()]
                    for cls in rows]

    before = snapshot()
    try:
        for skill_id in PPAP_SKILLS:
            inputs = {"part_no": result["task"]["part_no"]} if skill_id.endswith("find_tasks") else {"task_id": task_id}
            response = asyncio.run(service._skill_handlers[skill_id](inputs))
            assert isinstance(response, dict)
            assert response.get("ok") is not False
            assert response.get("success") is not False
        assert snapshot() == before
    finally:
        with SessionLocal() as session:
            # Query handlers must leave only the original task and elements.
            session.query(PpapElementRow).filter_by(task_id=task_id).delete()
            session.query(PpapTaskRow).filter_by(task_id=task_id).delete()
            session.commit()
