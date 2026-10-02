"""协调智能体（Coordinator）：自然语言提问 → 动态调用四个真实智能体 → 汇总回答。

设计对应团队讨论稿的"动态协同"要求：
- 不预写调用顺序：协调者根据问题判断缺少什么信息，从能力目录（真实技能注册表）
  中选择工具，按需调用对应智能体的 AIP 技能，拿到结果后继续判断，直到能回答；
- 同一类问题在不同数据状态下会形成不同的调用链（调用链全程留痕）；
- 工具一律返回真实 ERP/MES 数据（authority/data_source/evidence 随结果携带），
  协调者被明确禁止编造数字；数据不足时如实说明缺失。

写入类操作不在此通道执行：协调者只具备只读查询能力，写真实系统仍走
8 步流程中的人工审批门禁。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable
from uuid import uuid4

from app.aip.aip_agent_client import AipAgentClient
from app.integrations.errors import IntegrationNotConfigured
from app.services.assistant_context import retention_expiry
from app.services.real_order import _save_agent_run, _utc_now

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 12
TOOL_TIMEOUT_SECONDS = 60.0

# 真实技能能力目录：协调者"发现能力"的依据（动态选择，不预写顺序）。
# agent_type/skill_id 与 AIP 服务注册的真实技能一一对应。
REAL_SKILL_TOOLS: list[dict[str, Any]] = [
    {
        "agent_type": "tracking",
        "aip_agent": "tracking",
        "skill_id": "tracking.find_real_by_no",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "按用户提供的 MES 工单编号（如 WO-2026-001）精确查询数字 work_order_id。"
            "用户给出 WO- 开头的工单编号时，必须先调用本工具完成编号到数字 ID 的转换，"
            "再把返回的 work_order_id 传给进度、质量或发运门禁工具；不得猜测数字 ID。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "work_order_no": {
                    "type": "string",
                    "description": "MES 工单编号，如 WO-2026-001",
                },
            },
            "required": ["work_order_no"],
        },
    },
    {
        "agent_type": "tracking",
        "aip_agent": "tracking",
        "skill_id": "tracking.lookup_order_link",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "根据 ERP 销售订单号（如 SAL-ORD-2026-00023）查询正式关联的 MES 工单。"
            "返回 LINKED（含 work_order_id）或 NOT_LINKED。回答任何关于某个订单的生产进度、"
            "交期、质量、发运问题前，必须先调用本工具找到工单，除非已知 work_order_id。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "erp_order_id": {
                    "type": "string",
                    "description": "ERP 销售订单号，如 SAL-ORD-2026-00023",
                },
            },
            "required": ["erp_order_id"],
        },
    },
    {
        "agent_type": "tracking",
        "aip_agent": "tracking",
        "skill_id": "tracking.track_real",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "查询 MES 工单的真实生产进度：状态、完成率、完工数量、计划数量、交期、风险预警。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "work_order_id": {
                    "type": "string",
                    "description": "MES 工单数字 id（由 lookup_order_link 返回）",
                },
            },
            "required": ["work_order_id"],
        },
    },
    {
        "agent_type": "quality",
        "aip_agent": "quality-document",
        "skill_id": "quality.get_real_package",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "查询工单的真实质量资料包：质量问题及状态、SOP/Control Plan 文档、检验记录、"
            "质量门禁是否通过及原因。判断能否发运或质量是否异常时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "work_order_id": {
                    "type": "string",
                    "description": "MES 工单数字 id",
                },
            },
            "required": ["work_order_id"],
        },
    },
    {
        "agent_type": "quality",
        "aip_agent": "quality-document",
        "skill_id": "quality.assess_quality_impact",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "质量异常影响分析：质量问题清单（状态/严重度）、批次关联、质量门禁与生产进度交叉，"
            "输出对发运/交期的影响结论、可执行处理选项与数据缺口。"
            "问“有质量异常吗/影响交付吗/质量怎么办”时优先使用本工具（比 get_real_package 更完整）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "work_order_id": {"type": "string", "description": "MES 工单数字 id"},
            },
            "required": ["work_order_id"],
        },
    },
    {
        "agent_type": "quality",
        "aip_agent": "quality-document",
        "skill_id": "quality.check_issue_closure",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "只读校验指定 NCR 是否满足关闭前置条件：问题已 RESOLVED、已登记 disposition、"
            "根因和遏制措施齐全、所有纠正/预防/遏制措施均为 VERIFIED。"
            "用户问质量问题能否关闭或纠正措施是否完成时使用；本工具不执行写回。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "work_order_id": {"type": "string", "description": "OpenMES 质量问题/NCR id"},
                "issue_id": {"type": "string", "description": "OpenMES 质量问题/NCR id"},
            },
            "required": ["work_order_id", "issue_id"],
        },
    },
    {
        "agent_type": "quality",
        "aip_agent": "quality-document",
        "skill_id": "quality.list_open_issues",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "列出全厂所有未关闭质量问题（跨工单队列：工单号/标题/严重度/状态/处置/已报告天数）。"
            "用户问\"现在有哪些质量问题/最紧急的质量问题/全厂质量状况\"时使用，无需指定工单；"
            "按已报告天数从长到短展示。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "agent_type": "tracking",
        "aip_agent": "tracking",
        "skill_id": "tracking.check_real_ship_gate",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "发运门禁综合判断：报价审批状态 + 质量门禁 + 生产完成率（≥90%）是否全部满足，"
            "返回 can_ship 与阻塞原因。问“能不能发运/为什么不能发运”时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "work_order_id": {"type": "string", "description": "MES 工单数字 id"},
                "quotation_approved": {
                    "type": "boolean",
                    "description": "报价/订单是否已人工审批；不确定时先查报价再传入",
                },
            },
            "required": ["work_order_id", "quotation_approved"],
        },
    },
    {
        "agent_type": "quotation",
        "aip_agent": "quotation",
        "skill_id": "quotation.analyze_real",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "对给定客户/物料/数量做真实报价分析：ERP 真实价格、BOM 成本、库存、"
            "基于 MES 排程的交期参考。问“这个件多少钱/多久能交”或需要报价与缺料分析时使用；"
            "已知 ERP 订单号时必须传入 erp_order_id（声明式上下文，供后续状态查询关联）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "ERP 客户编号/名称"},
                "item_code": {"type": "string", "description": "物料编码，如 BD-2401"},
                "quantity": {"type": "integer", "description": "数量"},
                "delivery_date": {
                    "type": "string",
                    "description": "客户要求交期 YYYY-MM-DD（可选）",
                },
                "erp_order_id": {
                    "type": "string",
                    "description": "相关 ERP 销售订单号（可选；用户问题中提到订单时传入）",
                },
            },
            "required": ["customer_id", "item_code", "quantity"],
        },
    },
    {
        "agent_type": "quotation",
        "aip_agent": "quotation",
        "skill_id": "quotation.get_real",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": "查询已保存报价的状态与内容（价格、审批状态、ERP 草稿号）。",
        "parameters": {
            "type": "object",
            "properties": {
                "quotation_id": {"type": "string", "description": "报价编号，如 QUO-XXXXXXXX"},
            },
            "required": ["quotation_id"],
        },
    },
    {
        "agent_type": "quotation",
        "aip_agent": "quotation",
        "skill_id": "quotation.find_real_by_erp_order",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "按 ERP 销售订单号查找已保存的真实报价（返回 quotation_id/价格/审批状态，"
            "并附带该报价最新采购方案的状态摘要）。"
            "当用户只给了订单号或工单号、而后续工具需要 quotation_id（如采购分析、成本评估），"
            "或用户问“方案批准了吗/PO 生成了吗”等状态问题时，先用本工具反查。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "erp_order_id": {"type": "string", "description": "ERP 销售订单号 SAL-ORD-YYYY-NNNNN"},
            },
            "required": ["erp_order_id"],
        },
    },
    {
        "agent_type": "procurement",
        "aip_agent": "procurement",
        "skill_id": "procurement.find_real_plan_by_quotation",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "按报价编号查找最新采购方案的状态（审批状态/选定选项/PO 草稿号）。"
            "回答“方案批准了吗/PO 草稿生成没有”时，先反查报价再用本工具查方案状态。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "quotation_id": {"type": "string", "description": "报价编号 QUO-XXXXXXXX"},
            },
            "required": ["quotation_id"],
        },
    },
    {
        "agent_type": "quotation",
        "aip_agent": "quotation",
        "skill_id": "quotation.assess_cost_impact",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "成本影响评估：缺料换供应商方案对订单收入/毛利的影响（材料成本口径，真实价格记录）。"
            "输入 plan_id 与 option_id（来自采购分析结果）。问“换供应商要多花多少钱/还赚不赚钱”时使用。"
            "价格数据缺失时返回 DATA_MISSING，如实转告用户，不得估算。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "采购方案编号 PROC-XXXXXXXX"},
                "option_id": {"type": "string", "description": "供应商选项编号，如 OPT-1"},
            },
            "required": ["plan_id", "option_id"],
        },
    },
    {
        "agent_type": "tracking",
        "aip_agent": "tracking",
        "skill_id": "tracking.assess_delivery_impact",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "交期影响评估：物料到货时间（由采购方案 lead_time_days 推算 YYYY-MM-DD）"
            "与工单交期的差值，判断该方案下订单是否延期。问“换供应商还来不来得及/会不会延期”时使用。"
            "工单已完工时返回 not_needed。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "work_order_id": {"type": "string", "description": "MES 工单数字 id"},
                "material_ready_date": {
                    "type": "string",
                    "description": "物料预计到货日 YYYY-MM-DD（今天 + 方案 lead_time_days）",
                },
            },
            "required": ["work_order_id", "material_ready_date"],
        },
    },
        {
        "agent_type": "procurement",
        "aip_agent": "procurement",
        "skill_id": "procurement.inventory_overview",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "查询全厂库存总览：ERPNext Bin 中各物料在各仓库的实时余量"
            "（actual_qty=实时余量、reserved_qty=已预留、ordered_qty=在途、projected_qty=预计可用）。"
            "用户问'所有库存/库存总览/原料还剩多少/仓里有什么'等全仓类问题时使用；"
            "limit 可控制返回条数（默认 50）。按单一物料精确询价或做缺料分析时不要用本工具（走 analyze_real）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回条数上限（默认 50，最大 200）",
                },
            },
            "required": [],
        },
    },
{
        "agent_type": "procurement",
        "aip_agent": "procurement",
        "skill_id": "procurement.analyze_real",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "对已有报价做真实采购分析：BOM 净需求、缺料清单、供应商方案与推荐。"
            "问“缺不缺料/物料什么时候到/选哪家供应商”时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "quotation_id": {"type": "string", "description": "报价编号 QUO-XXXXXXXX"},
            },
            "required": ["quotation_id"],
        },
    },
    {
        "agent_type": "procurement",
        "aip_agent": "procurement",
        "skill_id": "procurement.get_real_plan",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": "查询已保存采购方案的状态（选定供应商、审批状态、PO 草稿号）。",
        "parameters": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "采购方案编号"},
            },
            "required": ["plan_id"],
        },
    },
    {
        "agent_type": "procurement",
        "aip_agent": "procurement",
        "skill_id": "procurement.assess_combination",
        "read_only": True,  # 目录强制显式声明：协调者通道只含免审批只读技能
        "requires_approval": False,
        "description": (
            "分单采购组合的确定性评估：给定多个供应商选项（来自同一采购方案），"
            "计算组合成本（真实价格记录合计）、缺料覆盖并集、最长交期，并对重复覆盖物料告警。"
            "当需要向用户呈现“分单采购/多家供应商组合”方案时，组合的成本与交期"
            "必须用本工具计算，禁止自行相加或估算。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "采购方案编号 PROC-XXXXXXXX"},
                "option_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "组合中包含的供应商选项编号列表，如 [\"OPT-2\", \"OPT-3\"]",
                },
            },
            "required": ["plan_id", "option_ids"],
        },
    },
]

SYSTEM_PROMPT = """你是汽车零部件工厂的协调智能体。用户会用自然语言向你提问（例如"这个订单什么时候能做完"）。

你的工作方式：
1. 分析要回答这个问题缺少哪些事实，然后调用提供的工具向对应的职能智能体（报价/采购/跟单/质量文档）获取真实数据。
2. 工具返回的都是真实 ERP（ERPNext）/MES（OpenMES）数据。禁止编造、估算或脑补任何数字；工具没给的数字不得出现在回答里。
3. 一次调用拿到的信息不够时，继续调用其他工具补齐；与问题无关的工具不要调用。
4. 拿到足够信息后，用中文给出结论式回答：先直接回答用户的问题，再列出依据（真实记录编号、来源系统、时间）。
5. 数据缺失或未建立关联时，明确说缺什么、需要谁补录，不要给猜测性答案。用户给出 WO- 开头的工单编号时，先调用 tracking.find_real_by_no 换成数字 work_order_id，再调用其他工单工具；严禁把编号中的数字当成数据库 ID。
6. 你只有只读查询能力；不要承诺任何写入操作（创建订单/审批/发运等），如用户要求，告知需在真实业务页面走审批流程。
7. 回答保持简洁，用短段落或列表。

方案类问题（缺料怎么办/能不能提前交/换供应商值不值）的额外规则：
A. 先用工具拿到缺料清单与全部供应商方案（含各自总价、交期、覆盖范围），再对可行方案逐一调用成本影响评估与交期影响评估。
B. 一旦 procurement.analyze_real 返回 plan_id，后续成本评估、组合评估、交期评估和状态回读必须沿用该 plan_id；不得使用反查结果中的历史方案编号。
C. 输出必须是一组可执行方案（至少尽量给出两个），每个方案包含：选择哪家园供应商、材料成本变化（真实价格记录差值）、对订单交期的影响（天数）、主要风险；并给出推荐及理由，明确"最终由人工确认"。
D. 某物料没有备选供应商或价格数据缺失时，如实说明"无可行替代方案，需补录数据"，绝不编造第三个方案或估算数字。
E. 方案之间的比较只能使用工具返回的真实数字，不得自行折算或取近似值。
F. "分单采购/多供应商组合"方案的成本、覆盖、交期必须调用 procurement.assess_combination 确定性计算，禁止自行相加各选项数字；组合存在重复覆盖或未覆盖缺料时，必须原样转告工具的告警。"""


def _llm_tool_name(skill_id: str) -> str:
    """DeepSeek 工具名只允许 [a-zA-Z0-9_-]，技能 ID 中的点替换为双下划线。"""
    return skill_id.replace(".", "__")


def _active_tools() -> list[dict[str, Any]]:
    """当前生效的能力目录条目（统一目录 → 协调者工具结构）。

    优先运行时已验证目录（AIP 注册 ∩ 声明），未构建时回退声明目录；
    两个来源都不把本地声明冒充外部注册结果（见 capability_catalog）。
    """
    from app.services.capability_catalog import catalog_or_declared

    return [
        {
            "agent_type": entry["agent_type"],
            "aip_agent": entry["aip_agent"],
            "skill_id": entry["skill_id"],
            "description": entry["description"],
            "parameters": entry["parameters"],
        }
        for entry in catalog_or_declared()["entries"]
    ]


def _llm_name_to_skill() -> dict[str, str]:
    return {t["skill_id"].replace(".", "__"): t["skill_id"] for t in _active_tools()}


def _tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": _llm_tool_name(t["skill_id"]),
                "description": f"[{t['agent_type']}智能体] {t['description']}",
                "parameters": t["parameters"],
            },
        }
        for t in _active_tools()
    ]


def _tool_index() -> dict[str, dict[str, Any]]:
    return {_llm_tool_name(t["skill_id"]): t for t in _active_tools()}


def _summarize_result(result: dict[str, Any], limit: int = 400) -> str:
    """给调用链留痕用的结果摘要（不含凭据，截断展示）。"""
    try:
        import json

        text = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        text = str(result)
    return text[:limit]


class BusinessCoordinator:
    """协调智能体：DeepSeek 工具调用循环 + AIP 技能调用。"""

    def __init__(
        self,
        deepseek_client,
        self_base_url: str | None = None,
        skill_invoker=None,
    ):
        self._deepseek = deepseek_client
        self._self_base_url = (
            self_base_url
            or os.getenv("ASSISTANT_SELF_BASE_URL", "http://127.0.0.1:9000")
        ).rstrip("/")
        # skill_invoker(agent_type, skill_id, inputs) -> dict；默认走 AIP RPC。
        # 测试可注入假实现。
        self._skill_invoker = skill_invoker or self._invoke_via_aip
        self._aip_clients: dict[str, AipAgentClient] = {}

    def _aip_client(self, agent_type: str) -> AipAgentClient:
        if agent_type not in self._aip_clients:
            self._aip_clients[agent_type] = AipAgentClient(
                partner_url=f"{self._self_base_url}/aip/{agent_type}/rpc",
                leader_id="local-coordinator-001",
            )
        return self._aip_clients[agent_type]

    async def _invoke_via_aip(self, agent_type: str, skill_id: str, inputs: dict) -> dict:
        client = self._aip_client(agent_type)
        return await client.invoke_skill(
            skill_id, inputs, timeout=TOOL_TIMEOUT_SECONDS
        )

    async def aclose(self) -> None:
        for client in self._aip_clients.values():
            await client.close()
        self._aip_clients.clear()
        if self._deepseek is not None:
            await self._deepseek.aclose()

    async def ask(
        self,
        question: str,
        context: dict[str, Any] | None = None,
        on_step: Callable[[dict[str, Any]], None] | None = None,
        run_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """回答一个业务问题：动态调用真实智能体，返回答案与完整调用链。

        on_step（可选）：每完成一次工具调用即回调当前调用链步骤（SSE 流式
        端点用它把"协调者正在查什么"实时推给前端；同步端点不传，行为不变）。
        run_meta（可选）：session_id/business_task_id，用于会话关联与保留期。
        """
        if not question.strip():
            raise ValueError("问题不能为空")

        context = context or {}
        context_lines = [
            f"- {k}: {v}" for k, v in context.items() if v not in (None, "")
        ]
        user_content = question.strip()
        if context_lines:
            user_content += "\n\n[当前页面上下文]\n" + "\n".join(context_lines)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        call_chain: list[dict[str, Any]] = []
        collected_results: list[tuple[str, dict[str, Any]]] = []
        final_answer = ""
        rounds = 0
        index = _tool_index()

        for rounds in range(1, MAX_TOOL_ROUNDS + 1):
            response = await self._deepseek.chat_completion(
                messages, tools=_tool_specs(), max_tokens=4096
            )
            message = response["choices"][0]["message"]
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                final_answer = (message.get("content") or "").strip()
                break

            # 先把 assistant 的 tool_calls 消息原样回填进对话
            messages.append(message)

            for call in tool_calls:
                function = call.get("function", {})
                llm_name = function.get("name", "")
                skill_id = _llm_name_to_skill().get(llm_name, llm_name)
                try:
                    import json

                    args = json.loads(function.get("arguments") or "{}")
                except ValueError:
                    args = {}
                spec = index.get(llm_name)
                started = time.perf_counter()
                if spec is None:
                    step_result: dict[str, Any] = {
                        "error": f"未知技能 {skill_id}，可用技能见工具列表"
                    }
                else:
                    try:
                        step_result = await self._skill_invoker(
                            spec["aip_agent"], skill_id, args
                        )
                    except Exception as e:  # 工具失败回喂给模型，由它决定下一步
                        step_result = {
                            "error": f"{type(e).__name__}: {str(e)[:300]}",
                            "note": "该智能体调用失败；可尝试其他工具或如实告知用户",
                        }
                elapsed_ms = int((time.perf_counter() - started) * 1000)

                if isinstance(step_result, dict) and (
                    step_result.get("error") is None or step_result.get("status") == "DATA_MISSING"
                ):
                    collected_results.append((skill_id, step_result))

                call_chain.append({
                    "seq": len(call_chain) + 1,
                    "caller": "coordinator",
                    "callee": spec["agent_type"] if spec else "unknown",
                    "skill_id": skill_id,
                    "arguments": args,
                    "result_summary": _summarize_result(step_result),
                    "status": "error" if step_result.get("error") else "ok",
                    "elapsed_ms": elapsed_ms,
                })
                if on_step is not None:
                    on_step(call_chain[-1])
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": _summarize_result(step_result, limit=4000),
                })
        else:
            final_answer = "达到工具调用轮次上限，未能形成最终回答。"

        # 确定性恢复：工具循环结束但模型返回空回答时，做一次无工具的强制总结
        # （只基于以上真实工具结果，不引入新数据；恢复过程透明记录）
        recovered = False
        if not final_answer and collected_results:
            messages.append({
                "role": "user",
                "content": "请基于以上工具返回的真实数据，用中文直接给出最终回答；不要调用任何工具。",
            })
            response = await self._deepseek.chat_completion(messages, max_tokens=4096)
            final_answer = (response["choices"][0]["message"].get("content") or "").strip()
            recovered = bool(final_answer)

        # 方案化协同：把真实工具结果中的方案数据原样汇集为结构化 proposal_options
        # （不做任何二次计算；前端据此渲染方案对比卡片）
        proposal = self._collect_proposal(collected_results)

        result: dict[str, Any] = {
            "question": question.strip(),
            "answer": final_answer,
            "call_chain": call_chain,
            "rounds": rounds,
            "tool_count": len(call_chain),
            "context": context,
            "authority": "ERPNext + OpenMES（各步骤结果含各自 authority/evidence）",
            "assembled_at": _utc_now().isoformat(),
        }
        if proposal:
            result["proposal_options"] = proposal
        if recovered:
            result["empty_answer_recovered"] = True

        # 协调运行留痕（子智能体运行由各自的 @_agent_run 装饰器另行持久化）。
        # 协调者原始问答含中间工具参数，按保留期（默认 90 天）过期清理。
        run_meta = run_meta or {}
        run_id = f"RUN-COORD-{uuid4().hex[:12].upper()}"
        _save_agent_run(
            run_id,
            "coordinator",
            "ask",
            {"args": [question.strip()], "kwargs": {"context": context}},
            "ok",
            result,
            None,
            _utc_now(),
            session_id=run_meta.get("session_id"),
            business_task_id=run_meta.get("business_task_id"),
            expires_at=retention_expiry(),
        )
        result["coordination_run_id"] = run_id
        return result

    @staticmethod
    def _collect_proposal(collected: list[tuple[str, dict[str, Any]]]) -> dict[str, Any] | None:
        """从真实工具结果原样汇集方案数据（缺料/供应商选项/成本影响/交期影响/缺失项）。"""
        proposal: dict[str, Any] | None = None

        def ensure() -> dict[str, Any]:
            nonlocal proposal
            if proposal is None:
                proposal = {}
            return proposal

        # procurement.analyze_real 是本轮刚生成的当前方案；procurement.get_real_plan
        # 可能只是模型为了确认审批/PO 状态而回读历史方案。两者都带有
        # net_requirement/supplier_options 时，不能让状态回读覆盖当前分析结果。
        analysis_results = [
            res for skill_id, res in collected
            if skill_id == "procurement.analyze_real"
            and (res.get("net_requirement") or {}).get("has_shortage")
        ]
        shortage_source = analysis_results[-1] if analysis_results else None
        if shortage_source is None:
            # 兼容只调用 get_real_plan 的状态问答：没有当前分析结果时，才允许
            # 用完整的方案回读作为卡片数据来源。
            shortage_source = next(
                (
                    res for skill_id, res in reversed(collected)
                    if skill_id == "procurement.get_real_plan"
                    and (res.get("net_requirement") or {}).get("has_shortage")
                ),
                None,
            )

        if shortage_source is not None:
            net_req = shortage_source.get("net_requirement") or {}
            p = ensure()
            p["plan_id"] = shortage_source.get("plan_id", "")
            p["quotation_id"] = shortage_source.get("quotation_id", "")
            # 报价审批状态来自报价记录/采购分析结果，不能使用采购方案自身 status。
            p["quotation_status"] = shortage_source.get("quotation_status", "")
            p["shortage"] = {
                "finished_item": net_req.get("finished_item", ""),
                "shortage_count": net_req.get("shortage_count", 0),
                "shortage_items": net_req.get("shortage_items", []),
            }
            option_keys = (
                "option_id", "supplier_id", "supplier_name", "lead_time_days",
                "lead_time_source", "covers_all_shortage_items", "coverage",
                "total_cost", "total_cost_complete", "currency",
                "is_recommended", "recommendation_reason",
            )
            p["supplier_options"] = [
                {k: o.get(k) for k in option_keys}
                for o in shortage_source.get("supplier_options", [])
            ]
            p["recommendation"] = shortage_source.get("recommendation", "")

        def matches_current_plan(res: dict[str, Any]) -> bool:
            """评估结果若声明 plan_id，必须属于当前方案；缺省 plan_id 兼容旧技能返回。"""
            current_plan_id = (proposal or {}).get("plan_id", "")
            result_plan_id = res.get("plan_id", "")
            return not current_plan_id or not result_plan_id or result_plan_id == current_plan_id

        for skill_id, res in collected:
            # 缺料主体已经由当前 analyze_real 结果初始化。状态回读只补充状态，
            # 且必须命中同一个 plan_id；旧方案的完整字段一律跳过。
            if skill_id == "procurement.get_real_plan":
                if proposal and res.get("plan_id") == proposal.get("plan_id"):
                    proposal["plan_status"] = res.get("status", "")
                    proposal["selected_option_id"] = res.get("selected_option_id")
                    proposal["approval_id"] = res.get("approval_id")
                    proposal["po_draft_id"] = res.get("po_draft_id")
                    if not proposal.get("quotation_status"):
                        proposal["quotation_status"] = res.get("quotation_status", "")
                continue
            if skill_id == "procurement.analyze_real":
                continue
            if (
                res.get("status") == "ok"
                and res.get("material_cost_delta") is not None
                and matches_current_plan(res)
            ):
                p = ensure()
                p.setdefault("cost_assessments", []).append({
                    k: res.get(k) for k in (
                        "plan_id", "option_id", "supplier_name", "covers_all_shortage_items",
                        "revenue", "currency", "material_cost_baseline",
                        "material_cost_with_option", "material_cost_delta",
                        "per_unit_surcharge", "material_margin_before", "material_margin_after",
                        "material_margin_rate_before_percent", "material_margin_rate_after_percent",
                        "calculation_basis",
                    )
                })
            if (
                res.get("status") == "ok"
                and res.get("combined_cost") is not None
                and matches_current_plan(res)
            ):
                p = ensure()
                p.setdefault("combination_assessments", []).append({
                    k: res.get(k) for k in (
                        "plan_id", "option_ids", "suppliers", "combined_cost", "currency",
                        "coverage", "overlapping_items", "max_lead_time_days", "warnings",
                    )
                })
            if res.get("verdict") in ("arrival_in_time", "arrival_after_due", "not_needed"):
                p = ensure()
                p.setdefault("delivery_assessments", []).append({
                    k: res.get(k) for k in (
                        "work_order_id", "work_order_no", "due_date", "material_ready_date",
                        "buffer_days", "verdict", "conclusion", "completion_rate",
                    )
                })
            if res.get("status") == "ok" and res.get("impact_conclusions") is not None and res.get("work_order_no"):
                p = ensure()
                p.setdefault("quality_impacts", []).append({
                    k: res.get(k) for k in (
                        "work_order_id", "work_order_no", "quality_records", "open_issues_count",
                        "open_records", "batches", "quality_gate_passed", "missing_documents",
                        "production", "impact_conclusions", "handling_options", "data_gaps",
                    )
                })
            if res.get("status") == "DATA_MISSING":
                p = ensure()
                p.setdefault("data_missing", []).append({
                    "source_skill": skill_id,
                    "missing_fields": res.get("missing_fields", []),
                    "need": res.get("need", ""),
                })
        return proposal


def build_coordinator() -> BusinessCoordinator:
    """从环境构建协调者；DeepSeek 未配置时抛出 IntegrationNotConfigured。"""
    from app.integrations.deepseek import DeepSeekClient
    from app.integrations.settings import IntegrationSettings

    settings = IntegrationSettings.from_environment()
    if not settings.deepseek_api_key:
        raise IntegrationNotConfigured("协调智能体（DeepSeek LLM）", ["DEEPSEEK_API_KEY"])
    return BusinessCoordinator(
        DeepSeekClient(
            settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
        )
    )
