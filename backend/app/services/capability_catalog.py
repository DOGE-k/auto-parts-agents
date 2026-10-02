"""统一能力目录：运行时构建与校验（AI_HANDOFF_PLAN P0-5 遗留收口）。

目标（docs/handoff_2026-10-02_dynamic_capability_catalog.md）：
把协调者工具选择、AIP 真实表面白名单和 ACS 生成的共同来源，从"代码内静态
列表"改为"运行时构建 + 字段校验 + 与 AIP 实际注册交叉验证"的统一目录。

来源分层（不把本地声明冒充外部注册结果）：
- declared_local：REAL_SKILL_TOOLS（本地受信任声明，提供 LLM 所需的
  description/parameters 与权限属性；作为安全回退保留，交接文档 §六 明确允许）；
- aip_runtime：AIP 服务在运行时真实注册的技能 ID（registered_skill_ids）；
- 目录条目 = 声明 ∩ 注册（source=aip_runtime+declared_local）；无运行时上下文
  的场景（如 ACS 生成脚本）使用纯声明目录（source=declared_local）。

安全底线（校验不过的技能一律拒绝并留下可读原因，不吞成空成功）：
- read_only 必须显式 True、requires_approval 必须显式 False；
- 重复 skill_id、未知 aip_agent、缺 parameters（object schema）均拒绝；
- 声明未注册 → 拒绝进入目录（调用会显式失败而非静默）；
- 注册未声明（含 Mock 技能）→ 不进入协调者目录，以 rejections 报告暴露。
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

# AIP 路由键 → RPC 端点（与 init_aip_agents 的注册约定一致，不拼接猜测）
KNOWN_AIP_AGENTS: dict[str, str] = {
    "quotation": "/aip/quotation/rpc",
    "procurement": "/aip/procurement/rpc",
    "tracking": "/aip/tracking/rpc",
    "quality-document": "/aip/quality-document/rpc",
}

# 声明级技能版本：与 generate_acs.py 现行生成值一致；注册信息不含版本字段，
# 不猜测运行时版本
DECLARED_SKILL_VERSION = "1.0.0"

_KIND_INVALID = "invalid_declaration"
_KIND_NOT_REGISTERED = "declared_not_registered"
_KIND_NOT_DECLARED = "registered_not_declared"

_lock = threading.Lock()
_runtime_catalog: dict[str, Any] | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reject(rejections: list, kind: str, skill_id: str, reason: str) -> None:
    rejections.append({"kind": kind, "skill_id": skill_id, "reason": reason})


def _validate_declaration(tool: dict[str, Any]) -> tuple[bool, str]:
    """声明条目的必填字段与权限属性校验，返回 (ok, 拒绝原因)。"""
    skill_id = str(tool.get("skill_id") or "").strip()
    if not skill_id:
        return False, "缺少 skill_id"
    if not str(tool.get("description") or "").strip():
        return False, f"技能 {skill_id} 缺少 description"
    agent_key = str(tool.get("aip_agent") or "").strip()
    if agent_key not in KNOWN_AIP_AGENTS:
        return False, f"技能 {skill_id} 属于未知智能体 {agent_key!r}（已知: {sorted(KNOWN_AIP_AGENTS)}）"
    if not str(tool.get("agent_type") or "").strip():
        return False, f"技能 {skill_id} 缺少 agent_type"
    parameters = tool.get("parameters")
    if not isinstance(parameters, dict) or parameters.get("type") != "object" \
            or not isinstance(parameters.get("properties"), dict):
        return False, f"技能 {skill_id} 缺少有效的 parameters（须为 object JSON Schema）"
    if tool.get("read_only") is not True:
        return False, f"技能 {skill_id} 未显式声明 read_only=True（只读属性必须显式，缺失即拒绝）"
    if tool.get("requires_approval") is not False:
        return False, (
            f"技能 {skill_id} 未显式声明 requires_approval=False"
            "（协调者目录只允许免审批只读技能；写入能力必须走审批端点，不得经目录开放）"
        )
    return True, ""


def build_capability_catalog(
    declared_tools: list[dict[str, Any]],
    registered_by_agent: dict[str, set[str]] | None = None,
    *,
    built_at: str | None = None,
) -> dict[str, Any]:
    """构建统一能力目录。

    registered_by_agent 为 None 时输出纯声明目录（source=declared_local）；
    提供时与 AIP 运行时注册取交集并双向报告差异。
    """
    now = built_at or _utc_now()
    entries: list[dict[str, Any]] = []
    rejections: list[dict[str, str]] = []
    seen: set[str] = set()

    for tool in declared_tools:
        skill_id = str(tool.get("skill_id") or "").strip()
        ok, reason = _validate_declaration(tool)
        if not ok:
            _reject(rejections, _KIND_INVALID, skill_id or "(missing)", reason)
            continue
        if skill_id in seen:
            _reject(rejections, _KIND_INVALID, skill_id, f"技能 {skill_id} 重复声明")
            continue
        agent_key = str(tool["aip_agent"]).strip()
        if registered_by_agent is not None and skill_id not in registered_by_agent.get(agent_key, set()):
            _reject(rejections, _KIND_NOT_REGISTERED, skill_id,
                    f"技能 {skill_id} 已声明但未注册在 {agent_key} 的 AIP 服务上，拒绝进入运行时目录")
            continue
        seen.add(skill_id)
        entries.append({
            "skill_id": skill_id,
            "agent_type": str(tool["agent_type"]),
            "aip_agent": agent_key,
            "description": str(tool["description"]),
            "parameters": tool["parameters"],
            "output_schema": None,  # 注册与声明均无输出 schema，如实标记缺口
            "version": str(tool.get("version") or DECLARED_SKILL_VERSION),
            "read_only": True,
            "requires_approval": False,
            "endpoint": KNOWN_AIP_AGENTS[agent_key],
            "source": "aip_runtime+declared_local" if registered_by_agent is not None else "declared_local",
            "built_at": now,
        })

    if registered_by_agent is not None:
        for agent_key in sorted(KNOWN_AIP_AGENTS):
            registered = registered_by_agent.get(agent_key, set())
            for orphan in sorted(registered - seen):
                _reject(rejections, _KIND_NOT_DECLARED, orphan,
                        f"技能 {orphan} 已注册在 {agent_key} 的 AIP 服务上但未声明目录元数据"
                        "（不进入协调者目录，也不因此获得任何写入权限）")

    return {
        "entries": entries,
        "rejections": rejections,
        "meta": {
            "runtime": registered_by_agent is not None,
            "source": "aip_runtime+declared_local" if registered_by_agent is not None else "declared_local",
            "built_at": now,
            "counts": {
                "entries": len(entries),
                "rejections": len(rejections),
                "declared": len(declared_tools),
            },
        },
    }


def declared_only_catalog() -> dict[str, Any]:
    """无运行时上下文时的声明目录（ACS 生成等脚本场景）。"""
    from app.services.coordinator import REAL_SKILL_TOOLS

    return build_capability_catalog(REAL_SKILL_TOOLS, None)


def store_runtime_catalog(catalog: dict[str, Any]) -> None:
    """缓存运行时已验证目录（init_aip_agents 构建后调用）。"""
    global _runtime_catalog
    with _lock:
        _runtime_catalog = catalog


def get_runtime_catalog() -> dict[str, Any] | None:
    with _lock:
        return _runtime_catalog


def reset_runtime_catalog() -> None:
    global _runtime_catalog
    with _lock:
        _runtime_catalog = None


def catalog_or_declared() -> dict[str, Any]:
    """协调者等使用方的入口：优先运行时目录，未构建时回退声明目录。"""
    current = get_runtime_catalog()
    if current is not None:
        return current
    return declared_only_catalog()


def allowed_skill_ids_by_agent(catalog: dict[str, Any]) -> dict[str, set[str]]:
    """按 AIP 路由键汇总目录技能 ID（真实表面白名单来源）。"""
    allowed: dict[str, set[str]] = {agent: set() for agent in KNOWN_AIP_AGENTS}
    for entry in catalog["entries"]:
        allowed.setdefault(entry["aip_agent"], set()).add(entry["skill_id"])
    return allowed
