"""协同问答会话历史端点测试（2026-10-02 交接文档 §3.5）。

前端刷新后需要按 sessionStorage 的 session_id 恢复当前会话消息：
- GET /api/real-orders/assistant/sessions/{session_id}/messages 返回按时间
  升序的 role/content 原文（不含令牌、凭据或完整工具参数）；
- 会话不存在 / 消息已过期清理时返回空列表（不 404，前端静默降级为空历史）。

使用 conftest 提供的隔离测试库，直接播种消息行，不触真实系统。
"""
from __future__ import annotations

import unittest
from uuid import uuid4

from app.persistence.database import SessionLocal
from app.persistence.models import AssistantMessageRow, AssistantSessionRow


def _seed_session_with_messages() -> tuple[str, list[tuple[str, str]]]:
    session_id = f"ASST-{uuid4().hex[:12].upper()}"
    pairs = [
        ("user", "SAL-ORD-2026-00023 什么时候能做完？"),
        ("assistant", "根据实测速率推算 ETA。"),
        ("user", "那为什么还不能发运？"),
        ("assistant", "唯一阻塞是报价/订单尚未审批。"),
    ]
    with SessionLocal() as db:
        db.add(AssistantSessionRow(session_id=session_id, title="历史恢复测试"))
        for role, content in pairs:
            db.add(AssistantMessageRow(session_id=session_id, role=role, content=content))
        db.commit()
    return session_id, pairs


class AssistantHistoryEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_history_in_chronological_order(self):
        from app.main import real_order_assistant_session_messages

        session_id, pairs = _seed_session_with_messages()
        result = await real_order_assistant_session_messages(session_id)
        self.assertEqual(result["session_id"], session_id)
        messages = result["messages"]
        self.assertEqual(len(messages), len(pairs))
        for message, (role, content) in zip(messages, pairs):
            self.assertEqual(message["role"], role)
            self.assertEqual(message["content"], content)
            self.assertTrue(message["created_at"])
        # 顺序为时间升序（id 递增）
        ids = [m["id"] for m in messages]
        self.assertEqual(ids, sorted(ids))

    async def test_unknown_session_returns_empty_list(self):
        from app.main import real_order_assistant_session_messages

        result = await real_order_assistant_session_messages(f"ASST-{uuid4().hex[:12].upper()}")
        self.assertEqual(result["messages"], [])

    def test_real_app_registers_history_route(self):
        from app.main import app as fastapi_app

        paths = {getattr(r, "path", "") for r in fastapi_app.routes}
        self.assertIn("/api/real-orders/assistant/sessions/{session_id}/messages", paths)


class AssistantSessionListTests(unittest.IsolatedAsyncioTestCase):
    """历史会话列表（"历史会话"切换器）：按最近活跃倒序，只含会话元数据。"""

    async def test_lists_sessions_most_recent_first_without_messages(self):
        from datetime import datetime, timedelta, timezone

        from app.main import real_order_assistant_sessions

        now = datetime.now(timezone.utc)
        ids = []
        with SessionLocal() as db:
            for i, minutes_ago in enumerate((120, 10, 60)):
                sid = f"ASST-{uuid4().hex[:12].upper()}"
                ids.append((sid, minutes_ago))
                db.add(AssistantSessionRow(
                    session_id=sid,
                    title=f"测试会话 {i}",
                    last_active_at=now - timedelta(minutes=minutes_ago),
                ))
            db.commit()

        result = await real_order_assistant_sessions(limit=20)
        listed = {s["session_id"]: s for s in result["sessions"]}
        for sid, _ in ids:
            self.assertIn(sid, listed)
        # 严格断言：10 分钟前的会话比 120 分钟前的靠前（最近活跃倒序）
        order = [s["session_id"] for s in result["sessions"]]
        recent = next(s for s, m in ids if m == 10)
        oldest = next(s for s, m in ids if m == 120)
        self.assertLess(order.index(recent), order.index(oldest))
        # 不泄露消息正文
        sample = listed[recent]
        self.assertIn("title", sample)
        self.assertNotIn("messages", sample)
        self.assertNotIn("content", sample)

    async def test_real_app_registers_sessions_list_route(self):
        from app.main import app as fastapi_app

        paths = {getattr(r, "path", "") for r in fastapi_app.routes}
        self.assertIn("/api/real-orders/assistant/sessions", paths)


if __name__ == "__main__":
    unittest.main()
