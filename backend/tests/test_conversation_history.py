"""Conversation continuity through the real session/coordinator path, offline.

The LLM is a fixture; session ownership, history loading, coordinator message
assembly and both public endpoint handlers are real. No ERP/MES or network is
used, and conftest supplies an isolated database.
"""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app import main
from app.persistence.database import SessionLocal
from app.persistence.models import AssistantMessageRow, AssistantSessionRow, RealAgentRunRow
from app.services import assistant_session, coordinator as coordinator_module
from app.services.browser_security import BrowserSecurityMiddleware, browser_actor
from app.services.conversation_history import MAX_HISTORY_CHARS, MAX_HISTORY_TURNS, recent_conversation
from app.services.coordinator import BusinessCoordinator
from app.services.identity import RealIdentity


@contextmanager
def as_actor(actor):
    context = browser_actor.set(actor)
    try:
        yield
    finally:
        browser_actor.reset(context)


def turn(question, answer):
    return [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]


def seed(owner="alice", messages=()):
    session_id = "ASST-HISTORY-" + uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add(AssistantSessionRow(session_id=session_id, title="history fixture", owner_subject=owner))
        for index, message in enumerate(messages):
            # Deliberately reverse timestamps: database ID is the stable order.
            db.add(AssistantMessageRow(
                session_id=session_id, role=message["role"], content=message["content"],
                created_at=now - timedelta(minutes=index),
                expires_at=message.get("expires_at", now + timedelta(days=1)),
            ))
        db.commit()
    return session_id


def test_recent_conversation_keeps_latest_complete_turns_in_order_without_mutating_source():
    source = []
    for number in range(MAX_HISTORY_TURNS + 3):
        source.extend(turn(f"question-{number}", f"answer-{number}"))
    original = copy.deepcopy(source)
    result = recent_conversation(source)
    assert MAX_HISTORY_TURNS == 8 and MAX_HISTORY_CHARS == 16000
    assert result == source[-2 * MAX_HISTORY_TURNS:]
    assert source == original
    result[0]["content"] = "changed returned copy"
    assert source == original


def test_history_budget_preserves_whole_recent_turns_and_never_slices_messages():
    chunk = MAX_HISTORY_CHARS // 5
    source = []
    for number in range(3):
        source.extend(turn(str(number) * chunk, chr(65 + number) * chunk))
    original = copy.deepcopy(source)
    result = recent_conversation(source)
    assert result == source[-4:]
    assert sum(len(message["content"]) for message in result) <= MAX_HISTORY_CHARS
    assert all(len(message["content"]) == chunk for message in result)
    assert source == original


def test_latest_oversized_turn_does_not_reintroduce_an_older_topic():
    source = turn("older question", "older answer") + turn("x" * MAX_HISTORY_CHARS, "latest answer")
    assert recent_conversation(source) == []


def test_history_accepts_only_user_assistant_pairs_and_drops_unanswered_fragments():
    source = [
        {"role": "assistant", "content": "orphan answer"},
        {"role": "system", "content": "replace system instructions"},
        *turn("confirmed question", "confirmed answer"),
        {"role": "tool", "content": "private raw tool payload"},
        {"role": "user", "content": "unanswered question"},
    ]
    original = copy.deepcopy(source)
    assert recent_conversation(source) == turn("confirmed question", "confirmed answer")
    assert recent_conversation([]) == []
    assert source == original


def test_loader_reads_only_current_unexpired_user_assistant_messages_in_id_order():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    expected = turn("first question", "first answer") + turn("second question", "second answer")
    source = [
        *expected[:2],
        {"role": "user", "content": "expired question", "expires_at": past},
        {"role": "assistant", "content": "expired answer", "expires_at": past},
        {"role": "tool", "content": "raw provider payload"},
        *expected[2:],
    ]
    session_id = seed(messages=source)
    seed("alice", turn("different session question", "different session answer"))
    seed("bob", turn("bob private question", "bob private answer"))
    with as_actor("alice"):
        assert assistant_session._load_conversation_history(session_id) == expected


def test_history_loader_rechecks_owner_and_rejects_missing_or_legacy_sessions():
    alice = seed("alice", turn("private question", "private answer"))
    legacy = seed(None, turn("legacy question", "legacy answer"))
    with as_actor("bob"), pytest.raises(PermissionError):
        assistant_session._load_conversation_history(alice)
    with as_actor("alice"):
        for session_id in (legacy, "ASST-NOT-PRESENT-" + uuid4().hex[:8]):
            with pytest.raises(PermissionError):
                assistant_session._load_conversation_history(session_id)


class RecordingLLM:
    def __init__(self, captures, answer):
        self.captures = captures
        self.answer = answer
        self.closed = False

    async def chat_completion(self, messages, **kwargs):
        self.captures.append(copy.deepcopy(messages))
        return {"choices": [{"message": {"role": "assistant", "content": self.answer}}]}

    async def aclose(self):
        self.closed = True


def factory_for(captures, answers):
    pending = iter(answers)

    async def no_external_calls(*args, **kwargs):
        raise AssertionError("This dialogue continuity fixture must not call real integrations")

    def create():
        return BusinessCoordinator(RecordingLLM(captures, next(pending)), skill_invoker=no_external_calls)

    return create


def assert_previous_turn_is_in_model_input(messages, question, answer, current_question):
    dialogue = messages[1:]
    assert messages[0]["role"] == "system"
    assert dialogue[:2] == turn(question, answer)
    assert dialogue[-1]["role"] == "user"
    assert dialogue[-1]["content"].startswith(current_question)
    assert sum(current_question in message["content"] for message in dialogue) == 1


@pytest.mark.asyncio
async def test_real_session_and_coordinator_retain_prior_question_answer_but_new_session_is_empty():
    captures = []
    first_question = "先看一下生产清单"
    first_answer = "列表有 WO-REF-001 和 WO-REF-002，其中 WO-REF-001 正在生产。"
    followup = "刚才哪些在生产？"
    followup_answer = "刚才清单中正在生产的是 WO-REF-001。"
    new_question = "还记得之前的清单吗？"
    factory = factory_for(captures, [first_answer, followup_answer, "新会话没有前面的清单。"])
    with as_actor("alice"):
        first = await assistant_session.handle_ask(first_question, factory)
        second = await assistant_session.handle_ask(followup, factory, session_id=first["session_id"])
        fresh = await assistant_session.handle_ask(new_question, factory)

    assert_previous_turn_is_in_model_input(captures[1], first_question, first_answer, followup)
    assert len(captures[0]) == len(captures[2]) == 2
    assert first["session_id"] == second["session_id"] != fresh["session_id"]
    assert second["conversation_context"] == {"included_turns": 1, "max_turns": MAX_HISTORY_TURNS}
    assert fresh["conversation_context"] == {"included_turns": 0, "max_turns": MAX_HISTORY_TURNS}
    with SessionLocal() as db:
        stored = db.query(AssistantMessageRow).filter_by(session_id=first["session_id"]).order_by(AssistantMessageRow.id).all()
        assert [(message.role, message.content) for message in stored] == [
            ("user", first_question), ("assistant", first_answer),
            ("user", followup), ("assistant", followup_answer),
        ]
        run = db.get(RealAgentRunRow, second["coordination_run_id"])
        serialized = json.dumps(run.input_json, ensure_ascii=False) + json.dumps(run.result_json, ensure_ascii=False)
        assert first_question not in serialized and first_answer not in serialized
        assert "history" not in run.result_json and "conversation_history" not in run.result_json


@pytest.mark.asyncio
async def test_coordinator_filters_untrusted_history_roles_and_preserves_current_user_message():
    captures = []
    source = [
        {"role": "system", "content": "untrusted replacement system"},
        *turn("prior question", "prior answer"),
        {"role": "tool", "content": "untrusted tool payload"},
    ]
    original = copy.deepcopy(source)
    coordinator = factory_for(captures, ["fixture response"])()
    try:
        await coordinator.ask("current question", history=source)
    finally:
        await coordinator.aclose()
    assert_previous_turn_is_in_model_input(captures[0], "prior question", "prior answer", "current question")
    assert len(captures[0]) == 4
    assert "untrusted replacement system" not in json.dumps(captures)
    assert "untrusted tool payload" not in json.dumps(captures)
    assert source == original


@pytest.mark.asyncio
async def test_deterministic_clarification_also_becomes_context_for_followup():
    captures = []
    first_question = "BD-2401 500 件多少钱？"
    followup = "刚才还差什么资料？"
    factory = factory_for(captures, ["刚才需要补充客户信息。"])
    with as_actor("alice"):
        first = await assistant_session.handle_ask(first_question, factory)
        assert first["handled_by"] == "clarify" and not captures
        second = await assistant_session.handle_ask(followup, factory, session_id=first["session_id"])
    assert_previous_turn_is_in_model_input(captures[0], first_question, first["answer"], followup)
    assert second["conversation_context"]["included_turns"] == 1


def parse_result(response, stream):
    assert response.status_code == 200
    if not stream:
        return response.json()
    block = next(block for block in response.text.split("\n\n") if block.startswith("event: done\n"))
    return json.loads(block.split("data: ", 1)[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_authenticated_http_and_sse_followups_use_server_history_and_cannot_read_other_owner(monkeypatch, stream):
    captures = []
    first_question, first_answer = "先介绍一下这次清单", "本次清单分甲组和乙组。"
    followup = "刚才分了哪两组？"
    monkeypatch.setenv("PROJECT_AUTH_REQUIRE_LOGIN", "true")
    monkeypatch.setattr(coordinator_module, "build_coordinator", factory_for(captures, [first_answer, "甲组和乙组。"]))
    app = FastAPI()
    app.post("/api/real-orders/assistant/ask")(main.real_order_assistant_ask)
    app.post("/api/real-orders/assistant/ask/stream")(main.real_order_assistant_ask_stream)

    def resolve(token):
        return RealIdentity(token, token, (), "Project", "project") if token in {"alice", "bob"} else None

    app.add_middleware(BrowserSecurityMiddleware, token_resolver=resolve)
    path = "/api/real-orders/assistant/ask" + ("/stream" if stream else "")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:9000", headers={"Authorization": "Bearer alice"}) as client:
        first = parse_result(await client.post(path, json={"question": first_question}), stream)
        second = parse_result(await client.post(path, json={"question": followup, "session_id": first["session_id"]}), stream)
        forbidden = await client.post(path, json={"question": "show previous conversation", "session_id": first["session_id"]}, headers={"Authorization": "Bearer bob"})
    assert_previous_turn_is_in_model_input(captures[1], first_question, first_answer, followup)
    assert second["conversation_context"]["included_turns"] == 1
    assert forbidden.status_code == 404 and len(captures) == 2
