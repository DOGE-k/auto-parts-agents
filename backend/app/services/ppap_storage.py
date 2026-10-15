"""PPAP 受控文件存储与配置位置解析。

职责很小且集中：把"文件到底写到哪里、怎么定位、怎么防目录穿越、怎么算内容
指纹"统一在一处，供计算器（读业务规则）、导出器（写工作簿）和 PPAP 服务
（输入快照哈希、产物下载校验）共用。

约束（设计说明 §7 与 AC16）：

- 数据文件不放进源代码目录。默认位置 ``backend/data/ppap_store`` 仅为本地
  开发与测试便利，且已加入 .gitignore；生产部署必须用 ``PPAP_STORAGE_DIR``
  指向仓库外的受控目录。
- 业务规则（MSA/过程能力阈值等）来自 ``acceptance_rules.json``。该文件
  **默认不存在**，此时一律按"规则未确认"处理，绝不回落到内置阈值。
  "文件不存在"与"文件损坏读不出来"必须区分：前者是合法未配置，后者要报错。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ENV_STORAGE_DIR = "PPAP_STORAGE_DIR"
ENV_RULES_FILE = "PPAP_RULES_FILE"
DEFAULT_RULES_FILENAME = "acceptance_rules.json"
DEFAULT_REL_STORAGE = ("data", "ppap_store")

# 任务/产物的路径片段只允许这些字符，禁止路径分隔符与 ".."。
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


def _backend_root() -> Path:
    # backend/app/services/ppap_storage.py -> backend/
    return Path(__file__).resolve().parents[2]


def storage_root() -> Path:
    raw = os.environ.get(ENV_STORAGE_DIR, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (_backend_root().joinpath(*DEFAULT_REL_STORAGE)).resolve()


def rules_file() -> Path:
    raw = os.environ.get(ENV_RULES_FILE, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return storage_root() / DEFAULT_RULES_FILENAME


def ensure_storage_root() -> Path:
    root = storage_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_component(value: str, *, field: str = "path") -> str:
    """校验单个路径片段；不合法直接拒绝，不做静默清洗。"""
    text = str(value or "").strip()
    if not _SAFE_COMPONENT.match(text):
        raise ValueError(f"{field} 含非法字符或为空，拒绝作为存储路径片段：{value!r}")
    return text


def resolve_within(root: Path, rel_path: str) -> Path:
    """把相对路径解析到 root 之内；越界或绝对路径一律拒绝。"""
    candidate_rel = str(rel_path or "").replace("\\", "/").strip()
    if not candidate_rel:
        raise ValueError("存储相对路径为空")
    if candidate_rel.startswith("/") or re.match(r"^[A-Za-z]:", candidate_rel):
        raise ValueError(f"存储相对路径必须是相对路径：{rel_path!r}")
    parts = [p for p in candidate_rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError(f"存储相对路径不得包含 '..'：{rel_path!r}")
    root_resolved = Path(root).resolve()
    target = root_resolved.joinpath(*parts).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise ValueError(f"存储路径越界：{rel_path!r}")
    return target


def write_bytes(rel_path: str, data: bytes, *, root: Path | None = None) -> Path:
    base = Path(root).resolve() if root is not None else ensure_storage_root()
    target = resolve_within(base, rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def read_bytes(rel_path: str, *, root: Path | None = None) -> bytes:
    base = Path(root).resolve() if root is not None else storage_root()
    return resolve_within(base, rel_path).read_bytes()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sha256_json(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def load_rules_document(path: Path | None = None) -> dict[str, Any]:
    """读取业务规则文件，并如实区分"未配置"与"读取失败"。

    返回：``{"configured": bool, "path": str, "document": dict|None,
    "rule_version": str, "error": str|None}``。

    - 文件不存在 → ``configured=False, error=None``（合法未配置）。
    - 文件存在但不可读或不是合法 JSON 对象 → ``configured=False`` 且带
      ``error``，调用方必须把该错误暴露出去，不能当成"没有规则"。
    """
    target = Path(path) if path is not None else rules_file()
    result: dict[str, Any] = {
        "configured": False,
        "path": str(target),
        "document": None,
        "rule_version": "",
        "error": None,
    }
    if not target.exists():
        return result
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        result["error"] = f"业务规则文件无法读取：{exc}"
        return result
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        result["error"] = f"业务规则文件不是合法 JSON：{exc}"
        return result
    if not isinstance(document, dict):
        result["error"] = "业务规则文件顶层必须是 JSON 对象"
        return result
    result["configured"] = True
    result["document"] = document
    declared = str(document.get("rule_version") or "").strip()
    result["rule_version"] = declared or f"sha256:{sha256_text(raw)[:12]}"
    return result
