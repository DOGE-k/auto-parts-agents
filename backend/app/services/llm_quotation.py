"""基于 DeepSeek LLM 的报价信息抽取服务。

当配置了 DEEPSEEK_API_KEY 时，使用 LLM 从 RFQ 描述中提取结构化信息；
否则回退到确定性规则提取，确保离线可用。
"""
from __future__ import annotations

import re
from typing import Any

from app.integrations.deepseek import DeepSeekClient
from app.integrations.errors import IntegrationNotConfigured


RFQ_EXTRACTION_SYSTEM_PROMPT = """你是汽车零部件行业的报价工程师。请从客户询价(RFQ)描述中提取结构化信息。

输出必须是严格的 JSON 对象，不要任何额外文字、Markdown 或解释。
JSON 字段说明：
- product_hint: str | null，产品名称或类型线索
- quantity: number | null，订单数量
- material_spec: str | null，材料规格（如 "45#钢"、"铝合金 6061"）
- surface_treatment: str | null，表面处理要求（如 "镀锌"、"发黑"、"阳极氧化"）
- tolerance_grade: str | null，公差等级（如 "IT7"、"IT8"）
- delivery_days: number | null，要求的交付天数
- quality_standard: str | null，质量标准（如 "IATF16949"、"ISO9001"）
- complexity: "low" | "medium" | "high"，工艺复杂度评估
- key_features: list[str]，关键特征列表（如 ["钻孔", "铣削", "热处理"]）
- risk_notes: list[str]，潜在风险点提示

如果某项信息无法从描述中确定，设为 null。"""


def extract_rfq_deterministic(description: str) -> dict[str, Any]:
    """确定性规则提取（离线回退方案）。"""
    desc = description.lower()

    # 数量提取
    quantity: int | None = None
    qty_match = re.search(r"(\d+)\s*(?:件|个|pcs|pieces|套|台)", desc)
    if qty_match:
        quantity = int(qty_match.group(1))
    else:
        qty_match = re.search(r"数量[：:]\s*(\d+)", desc)
        if qty_match:
            quantity = int(qty_match.group(1))

    # 材料提取
    material_spec: str | None = None
    material_patterns = [
        r"(45#钢|45号钢|45钢)",
        r"(铝合金\s*6061|aluminum\s*6061)",
        r"(不锈钢\s*304|stainless\s*steel\s*304)",
        r"(碳钢|carbon\s*steel)",
        r"(铜|brass|copper)",
    ]
    for pat in material_patterns:
        m = re.search(pat, desc)
        if m:
            material_spec = m.group(1)
            break

    # 表面处理
    surface_treatment: str | None = None
    if "镀锌" in desc or "galvanized" in desc:
        surface_treatment = "镀锌"
    elif "发黑" in desc or "氧化发黑" in desc:
        surface_treatment = "发黑"
    elif "阳极氧化" in desc or "anodized" in desc:
        surface_treatment = "阳极氧化"
    elif "电泳" in desc:
        surface_treatment = "电泳"

    # 交付天数
    delivery_days: int | None = None
    days_match = re.search(r"(\d+)\s*(?:天|日|days)", desc)
    if days_match:
        delivery_days = int(days_match.group(1))

    # 公差
    tolerance_grade: str | None = None
    tol_match = re.search(r"it(\d+)", desc)
    if tol_match:
        tolerance_grade = f"IT{tol_match.group(1)}"

    # 复杂度评估
    complexity = "medium"
    feature_count = 0
    features: list[str] = []
    if any(k in desc for k in ["车削", "车床", "turning"]):
        features.append("车削")
        feature_count += 1
    if any(k in desc for k in ["铣削", "铣床", "milling"]):
        features.append("铣削")
        feature_count += 1
    if any(k in desc for k in ["钻孔", "drilling"]):
        features.append("钻孔")
        feature_count += 1
    if any(k in desc for k in ["热处理", "heat treatment", "淬火", "回火"]):
        features.append("热处理")
        feature_count += 1
    if any(k in desc for k in ["磨削", "grinding"]):
        features.append("磨削")
        feature_count += 1

    if feature_count <= 1:
        complexity = "low"
    elif feature_count >= 4:
        complexity = "high"

    # 质量标准
    quality_standard: str | None = None
    if "iatf16949" in desc or "iatf 16949" in desc:
        quality_standard = "IATF16949"
    elif "iso9001" in desc or "iso 9001" in desc:
        quality_standard = "ISO9001"

    # 产品线索
    product_hint: str | None = None
    if "支架" in desc or "bracket" in desc:
        product_hint = "支架类零件"
    elif "轴" in desc and "承" not in desc and "shaft" in desc:
        product_hint = "轴类零件"
    elif "齿轮" in desc or "gear" in desc:
        product_hint = "齿轮类零件"
    elif "法兰" in desc or "flange" in desc:
        product_hint = "法兰类零件"

    # 风险提示
    risk_notes: list[str] = []
    if feature_count >= 4:
        risk_notes.append("工序较多，需确认产能")
    if tolerance_grade and tolerance_grade in ["IT5", "IT6", "IT7"]:
        risk_notes.append(f"公差要求较高（{tolerance_grade}），需确认加工能力")
    if delivery_days and delivery_days <= 15:
        risk_notes.append("交付周期紧，可能需要加急")

    return {
        "product_hint": product_hint,
        "quantity": quantity,
        "material_spec": material_spec,
        "surface_treatment": surface_treatment,
        "tolerance_grade": tolerance_grade,
        "delivery_days": delivery_days,
        "quality_standard": quality_standard,
        "complexity": complexity,
        "key_features": features,
        "risk_notes": risk_notes,
        "extraction_method": "deterministic",
    }


async def extract_rfq_with_llm(
    description: str,
    client: DeepSeekClient,
) -> dict[str, Any]:
    """使用 DeepSeek LLM 从 RFQ 描述中提取结构化信息。"""
    user_prompt = f"请从以下 RFQ 描述中提取结构化信息：\n\n{description}"

    messages = [
        {"role": "system", "content": RFQ_EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    result = await client.chat_completion(
        messages,
        max_tokens=1024,
    )

    content = result["choices"][0]["message"].get("content", "")

    # 尝试解析 JSON
    try:
        # 清理可能的 Markdown 代码块
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        import json

        extracted = json.loads(cleaned)
    except (json.JSONDecodeError, KeyError, IndexError):
        # LLM 返回格式不对时回退到确定性提取
        extracted = extract_rfq_deterministic(description)
        extracted["extraction_method"] = "deterministic_fallback"
        extracted["llm_raw_response"] = content[:200]
        return extracted

    extracted["extraction_method"] = "llm"
    return extracted


async def extract_rfq(
    description: str,
    llm_client: DeepSeekClient | None = None,
) -> dict[str, Any]:
    """统一入口：优先使用 LLM，失败或未配置时回退到确定性提取。"""
    if llm_client is not None:
        try:
            return await extract_rfq_with_llm(description, llm_client)
        except (IntegrationNotConfigured, Exception):
            # LLM 不可用时静默回退
            pass

    result = extract_rfq_deterministic(description)
    return result


def compute_complexity_factor(complexity: str) -> float:
    """根据复杂度计算价格系数。"""
    factors = {"low": 0.85, "medium": 1.0, "high": 1.25}
    return factors.get(complexity, 1.0)


def compute_quantity_discount(quantity: int | None) -> float:
    """根据数量计算折扣系数。"""
    if quantity is None:
        return 1.0
    if quantity >= 1000:
        return 0.85
    if quantity >= 500:
        return 0.9
    if quantity >= 100:
        return 0.95
    if quantity < 10:
        return 1.15
    return 1.0
