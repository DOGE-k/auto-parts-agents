"""PPAP 条目模板契约（项目自定义模板，明确非客户模板）。

本模块只描述"PPAP 资料包应包含哪些条目、每个条目用什么方式编制、需要哪些
数据集与证据、缺项由谁补"。它不产生任何业务结论、不写数据库、不调用接口。

事实约束（依据 docs/PPAP编制业务设计与实施说明.md §5.1/§6.1 与
docs/PPAP阶段A确认清单.md）：

1. 用户提供的客户模板样例在当前机器上不可读，仓库内也没有副本。因此本阶段
   **没有客户模板映射**；下面这套 22 条目是项目自定义条目模板，只能以
   ``TEMPLATE_SOURCE = "project_defined"`` 使用，不能在文档或界面中声称为
   客户模板。设计说明 §5.1 要求"条目目录必须可配置"，本模块即该目录的
   单一来源，客户模板接入时替换 ``ELEMENTS`` 与 ``TEMPLATE_ID`` 即可。
2. 条目的"提交 / 保留 / 按要求提供 / 不适用"适用规则属于客户业务规则
   （设计说明 §14.1，尚未确认）。因此 ``suggested_requirement`` 只是公开
   PPAP 惯例下的**建议默认值**，必须由任务显式确认后才生效；未确认时条目
   状态为 ``APPLICABILITY_UNCONFIRMED``，一致性检查 ``PPAP-CHK-REQ-001``
   按阻断项处理（设计说明 §8.1：适用性未满足时不允许标记为批准）。
3. 只读证据来源只允许已核对的真实接口（清单 §1.2）：``quality_package``、
   ``track_order``、``find_work_order_by_no``、``lookup_order_link``、
   ``find_latest_plan_by_quotation``、``get_quotation`` / ``find_quotation_by_erp_order``、
   ``quality_todo_list`` / ``get_quality_issue_workflow_states`` /
   ``assess_quality_issue_closure``、``ship_gate_check``。这里不发明任何新字段。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

# ----------------------------------------------------------------------
# 模板标识
# ----------------------------------------------------------------------

TEMPLATE_ID = "internal-default-ppap-v1"
TEMPLATE_VERSION = "1.0.0"
TEMPLATE_SOURCE = "project_defined"
TEMPLATE_IS_CUSTOMER_TEMPLATE = False
RULE_VERSION = "ppap-rules-2026.10.13"
TEMPLATE_DISPLAY_NAME = "项目自定义 PPAP 条目模板"
TEMPLATE_NOTICE = (
    "本模板为项目自定义条目模板，不是任何客户的正式 PPAP 模板。"
    "客户模板与条目适用规则尚未确认（《PPAP阶段A确认清单》§9 问题 1），"
    "取得客户模板前不得声称按客户模板编制。"
)
# 导出工作表里显示的抬头，避免把项目自定义模板误当客户模板使用。
SHEET_BANNER = "项目自定义模板（非客户模板）"

# ----------------------------------------------------------------------
# 枚举值（与 ORM 列宽/默认值保持一致）
# ----------------------------------------------------------------------

# 条目适用性（PpapElementRow.requirement）
REQ_SUBMIT = "submit"                 # 需提交给客户；未满足时阻断内部批准
REQ_RETAIN = "retain"                 # 留存备查；未满足不阻断
REQ_ON_REQUEST = "on_request"         # 客户要求时提供；未满足不阻断
REQ_NOT_APPLICABLE = "not_applicable"  # 不适用（必须填写依据）
REQ_UNCONFIRMED = ""                  # 尚未确认
REQUIREMENT_VALUES = (REQ_SUBMIT, REQ_RETAIN, REQ_ON_REQUEST, REQ_NOT_APPLICABLE)

# 条目文档状态（PpapElementRow.status，设计说明 §8.1 第二类状态）
EL_WAITING_DATA = "WAITING_DATA"
EL_READY = "READY"
EL_DRAFTED = "DRAFTED"
EL_CONFLICT = "CONFLICT"
EL_PENDING_REVIEW = "PENDING_REVIEW"
EL_REVIEWED = "REVIEWED"
EL_NOT_APPLICABLE = "NOT_APPLICABLE"
EL_BLOCKED_MISSING_DATA = "BLOCKED_MISSING_DATA"
EL_APPLICABILITY_UNCONFIRMED = "APPLICABILITY_UNCONFIRMED"

UNMET_ELEMENT_STATES = (
    EL_WAITING_DATA,
    EL_BLOCKED_MISSING_DATA,
    EL_CONFLICT,
    EL_APPLICABILITY_UNCONFIRMED,
)

# 编制方式（设计说明 §5.2 的方法表）
METHOD_TEMPLATE_FILL = "template_fill"                 # 统一基础信息 + 有来源业务记录填充
METHOD_ENGINEERING_DRAFT = "engineering_draft"         # 复用批准基准 + 模型辅助结构化草稿
METHOD_DETERMINISTIC_CALC = "deterministic_calc"       # 经确认方法 + 原始数据确定性计算
METHOD_SOURCE_DATA_TABLE = "source_data_table"         # 原始检验/试验数据填表
METHOD_CONFIRMED_INPUT_TABLE = "confirmed_input_table"  # 人工确认的结构化输入填表
METHOD_REFERENCE_DOCUMENT = "reference_document"       # 只引用原始文件，不自行生成
METHOD_PHYSICAL_RECORD = "physical_record"             # 实物条目登记，生成表格 ≠ 已提供实物
METHOD_VALUES = (
    METHOD_TEMPLATE_FILL,
    METHOD_ENGINEERING_DRAFT,
    METHOD_DETERMINISTIC_CALC,
    METHOD_SOURCE_DATA_TABLE,
    METHOD_CONFIRMED_INPUT_TABLE,
    METHOD_REFERENCE_DOCUMENT,
    METHOD_PHYSICAL_RECORD,
)

# 条目大类（PpapElementRow.category）
CAT_SUBMISSION = "submission_document"
CAT_ENGINEERING = "engineering_document"
CAT_MEASUREMENT = "measurement_record"
CAT_REFERENCE = "reference_document"
CAT_PHYSICAL = "physical_record"

# ----------------------------------------------------------------------
# 输入数据集（设计说明 §7"结构化工程与检验输入"）
# ----------------------------------------------------------------------

DS_PRODUCT_BASE = "product_base"
DS_PROCESS_OPERATION = "process_operation"
DS_PROCESS_RELATION = "process_relation"
DS_CHARACTERISTIC = "characteristic"
DS_INSPECTION_VALUE = "inspection_value"
DS_MSA_STUDY = "msa_study"
DS_PROCESS_STUDY = "process_study"
DS_MATERIAL_EVIDENCE = "material_evidence"
DS_PACKAGING_EQUIPMENT = "packaging_equipment"
DS_CUSTOMER_REQUIREMENT = "customer_requirement"
DS_CHANGE_RECORD = "change_record"
DS_PHYSICAL_RECORD = "physical_record"
DS_CUSTOMER_HANDLING = "customer_handling"

# 会随左右件产生不同数值的数据集：这些数据集的记录必须显式标注 part_side，
# 不允许用 NONE 混过左右件隔离检查（AC01）。
SIDE_SENSITIVE_DATASETS = (
    DS_CHARACTERISTIC,
    DS_INSPECTION_VALUE,
    DS_MSA_STUDY,
    DS_PROCESS_STUDY,
    DS_MATERIAL_EVIDENCE,
    DS_PROCESS_OPERATION,
)
ALL_DATASETS = (
    DS_PRODUCT_BASE,
    DS_PROCESS_OPERATION,
    DS_PROCESS_RELATION,
    DS_CHARACTERISTIC,
    DS_INSPECTION_VALUE,
    DS_MSA_STUDY,
    DS_PROCESS_STUDY,
    DS_MATERIAL_EVIDENCE,
    DS_PACKAGING_EQUIPMENT,
    DS_CUSTOMER_REQUIREMENT,
    DS_CHANGE_RECORD,
    DS_PHYSICAL_RECORD,
    DS_CUSTOMER_HANDLING,
)

# ----------------------------------------------------------------------
# 证据来源类型（只对应上面已核对的真实接口或受控导入）
# ----------------------------------------------------------------------

EV_OPENMES_WORK_ORDER = "openmes_work_order"                 # track_order / find_work_order_by_no
EV_OPENMES_QUALITY_PACKAGE = "openmes_quality_package"        # quality_package
EV_OPENMES_ENGINEERING_DOC = "openmes_engineering_document"   # quality_package -> documents
EV_OPENMES_INSPECTION = "openmes_inspection_record"           # quality_package -> inspections
EV_ERP_SALES_ORDER = "erp_sales_order"                        # get_quotation / find_quotation_by_erp_order
EV_PROCUREMENT_PLAN = "procurement_plan"                      # find_latest_plan_by_quotation
EV_QUALITY_ISSUE_WORKFLOW = "quality_issue_workflow"          # quality_todo_list / get_quality_issue_workflow_states
EV_SHIP_GATE = "ship_gate"                                    # ship_gate_check
EV_USER_UPLOAD = "user_upload"                                # 受控文件登记（人工上传原件）
EV_USER_CONFIRMATION = "user_confirmation"                    # 人工确认的结构化输入
EV_INTERNAL_PACKAGE = "internal_ppap_package"                 # 引用的既有内部批准包版本
EVIDENCE_SOURCE_TYPES = (
    EV_OPENMES_WORK_ORDER,
    EV_OPENMES_QUALITY_PACKAGE,
    EV_OPENMES_ENGINEERING_DOC,
    EV_OPENMES_INSPECTION,
    EV_ERP_SALES_ORDER,
    EV_PROCUREMENT_PLAN,
    EV_QUALITY_ISSUE_WORKFLOW,
    EV_SHIP_GATE,
    EV_USER_UPLOAD,
    EV_USER_CONFIRMATION,
    EV_INTERNAL_PACKAGE,
)

# ----------------------------------------------------------------------
# 条目定义
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class PpapElementSpec:
    """一个 PPAP 条目的静态契约。

    只描述需求，不含任何零件实际值。``suggested_requirement`` 是公开 PPAP
    惯例下的建议值，任务必须显式确认后才生效。
    """

    element_id: str
    title: str
    sheet_name: str
    category: str
    compilation_method: str
    suggested_requirement: str
    required_inputs: tuple[str, ...]
    evidence_types: tuple[str, ...]
    side_sensitive: bool
    generates_content: bool
    requires_engineering_input: bool
    responsible_role: str
    applicability_condition: str
    notes: str

    @property
    def blocks_approval_when_unmet(self) -> bool:
        """建议值为 submit 的条目未满足时阻断内部批准（设计说明 §8.1）。"""
        return self.suggested_requirement == REQ_SUBMIT

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["blocks_approval_when_unmet"] = self.blocks_approval_when_unmet
        return data


def _el(
    element_id: str,
    title: str,
    sheet_name: str,
    category: str,
    method: str,
    suggested: str,
    *,
    inputs: tuple[str, ...] = (),
    evidence: tuple[str, ...] = (),
    side_sensitive: bool = False,
    generates: bool = True,
    engineering_input: bool = False,
    role: str = "quality_manager",
    condition: str = "",
    notes: str = "",
) -> PpapElementSpec:
    return PpapElementSpec(
        element_id=element_id,
        title=title,
        sheet_name=sheet_name,
        category=category,
        compilation_method=method,
        suggested_requirement=suggested,
        required_inputs=inputs,
        evidence_types=evidence,
        side_sensitive=side_sensitive,
        generates_content=generates,
        requires_engineering_input=engineering_input,
        responsible_role=role,
        applicability_condition=condition,
        notes=notes,
    )


# 22 条：AIAG 18 项 + PSW + 内部提交清单 + 附件目录 + 客户扩展（测点布局图）。
ELEMENTS: tuple[PpapElementSpec, ...] = (
    _el(
        "submission_checklist", "提交清单", "提交清单", CAT_SUBMISSION, METHOD_TEMPLATE_FILL,
        REQ_RETAIN,
        notes="内部工作文档：由其余条目的适用性与状态汇总生成，不复制旧样例的公司/零件/日期/签字。",
    ),
    _el(
        "design_records", "设计记录（零件图纸/技术规范）", "设计记录", CAT_REFERENCE,
        METHOD_REFERENCE_DOCUMENT, REQ_RETAIN,
        evidence=(EV_USER_UPLOAD, EV_ERP_SALES_ORDER),
        generates=False, engineering_input=True,
        condition="仅当设计责任在本厂或客户要求提交设计记录时适用",
        notes="只归集原始文件与有效版本，不自行生成工程图纸或规范。",
    ),
    _el(
        "engineering_change_documents", "工程变更文件", "工程变更文件", CAT_SUBMISSION,
        METHOD_CONFIRMED_INPUT_TABLE, REQ_SUBMIT,
        inputs=(DS_CHANGE_RECORD,), evidence=(EV_USER_UPLOAD,),
        engineering_input=True,
        condition="本次提交存在工程变更时适用；无变更应确认不适用并填写依据",
        notes="变更履历来自人工确认的变更记录；不复制旧样例历史值。",
    ),
    _el(
        "customer_engineering_approval", "客户工程批准", "客户工程批准", CAT_REFERENCE,
        METHOD_REFERENCE_DOCUMENT, REQ_SUBMIT,
        evidence=(EV_USER_UPLOAD,),
        generates=False, engineering_input=True,
        condition="客户要求提交工程批准记录时适用",
        notes="签名或人名不等于客户批准；只登记实际取得的原件。",
    ),
    _el(
        "dfmea", "设计 FMEA", "设计FMEA", CAT_REFERENCE, METHOD_REFERENCE_DOCUMENT, REQ_RETAIN,
        evidence=(EV_USER_UPLOAD,),
        generates=False, engineering_input=True,
        condition="设计责任方提供 DFMEA 时适用",
        notes="设计责任归属未确认（清单 §9 问题 2），本阶段不生成 DFMEA 内容。",
    ),
    _el(
        "process_flow_diagram", "过程流程图", "过程流程图", CAT_ENGINEERING,
        METHOD_ENGINEERING_DRAFT, REQ_SUBMIT,
        inputs=(DS_PROCESS_OPERATION, DS_PROCESS_RELATION),
        evidence=(EV_OPENMES_WORK_ORDER, EV_OPENMES_ENGINEERING_DOC),
        side_sensitive=True, engineering_input=True, role="production_manager",
        notes="工序来自工单工艺快照与人工确认的工艺关系；工序缺失时标待补，不从产品照片猜工艺。",
    ),
    _el(
        "pfmea", "过程 FMEA", "过程FMEA", CAT_ENGINEERING, METHOD_ENGINEERING_DRAFT, REQ_SUBMIT,
        inputs=(DS_PROCESS_OPERATION, DS_CHARACTERISTIC),
        evidence=(EV_OPENMES_ENGINEERING_DOC,),
        side_sensitive=True, engineering_input=True,
        notes="评分方法、评级与控制措施须有依据并标注待审核；不套用其他零件的 RPN。",
    ),
    _el(
        "control_plan", "控制计划", "控制计划", CAT_ENGINEERING, METHOD_ENGINEERING_DRAFT,
        REQ_SUBMIT,
        inputs=(DS_PROCESS_OPERATION, DS_CHARACTERISTIC, DS_INSPECTION_VALUE),
        evidence=(EV_OPENMES_ENGINEERING_DOC,),
        side_sensitive=True, engineering_input=True,
        notes="复用批准的工程基准与过程知识；测点编号须与流程图/PFMEA/检验表一致。",
    ),
    _el(
        "msa", "测量系统分析", "MSA", CAT_MEASUREMENT, METHOD_DETERMINISTIC_CALC, REQ_SUBMIT,
        inputs=(DS_MSA_STUDY, DS_CHARACTERISTIC),
        side_sensitive=True,
        condition="控制计划中列出的测量系统需要 MSA 时适用",
        notes="算法、单位、样本布局与适用条件须经确认；模型不生成测量数值。",
    ),
    _el(
        "dimensional_results", "尺寸结果", "尺寸结果", CAT_MEASUREMENT,
        METHOD_SOURCE_DATA_TABLE, REQ_SUBMIT,
        inputs=(DS_INSPECTION_VALUE, DS_CHARACTERISTIC),
        evidence=(EV_OPENMES_INSPECTION, EV_USER_UPLOAD),
        side_sensitive=True,
        notes="逐测点取自原始检验记录；空值、缺报告与不适用必须区分，禁止填“合格”补缺口。",
    ),
    _el(
        "material_performance_results", "材料/性能试验结果", "材料性能结果", CAT_MEASUREMENT,
        METHOD_SOURCE_DATA_TABLE, REQ_SUBMIT,
        inputs=(DS_INSPECTION_VALUE, DS_MATERIAL_EVIDENCE),
        evidence=(EV_USER_UPLOAD,),
        side_sensitive=True, role="purchase_manager",
        notes="需要供应商试验报告或材料证明原件；供应商名称不能当作试验报告。",
    ),
    _el(
        "initial_process_study", "初始过程研究", "初始过程研究", CAT_MEASUREMENT,
        METHOD_DETERMINISTIC_CALC, REQ_SUBMIT,
        inputs=(DS_PROCESS_STUDY, DS_CHARACTERISTIC),
        side_sensitive=True,
        condition="控制计划中列为特殊特性的特性需要过程能力研究时适用",
        notes="只用原始样本值（样本标准差 n-1 / 总体标准差）；汇总值与公式缓存结果不得混入。",
    ),
    _el(
        "qualified_laboratory_documentation", "合格实验室文件", "合格实验室文件", CAT_REFERENCE,
        METHOD_REFERENCE_DOCUMENT, REQ_SUBMIT,
        evidence=(EV_USER_UPLOAD,),
        generates=False, engineering_input=True,
        condition="使用外部或内部实验室出具试验结果时适用",
        notes="只登记实际取得的资质/范围文件，不自行生成资质或签名。",
    ),
    _el(
        "appearance_approval_report", "外观批准报告（AAR）", "外观批准报告", CAT_SUBMISSION,
        METHOD_REFERENCE_DOCUMENT, REQ_SUBMIT,
        evidence=(EV_USER_UPLOAD,),
        generates=False, engineering_input=True,
        condition="零件为外观件且客户要求外观批准时适用",
        notes="客户判定的外观结论只登记原件；本阶段不生成外观合格结论。",
    ),
    _el(
        "sample_production_part", "生产件样品", "生产件样品", CAT_PHYSICAL,
        METHOD_PHYSICAL_RECORD, REQ_SUBMIT,
        inputs=(DS_PHYSICAL_RECORD,),
        evidence=(EV_USER_UPLOAD,),
        generates=False, role="production_manager",
        notes="实物条目：登记实际留存/送样的样品记录，生成一张表不等于已提供实物。",
    ),
    _el(
        "master_sample", "标准样件", "标准样件", CAT_PHYSICAL, METHOD_PHYSICAL_RECORD, REQ_RETAIN,
        inputs=(DS_PHYSICAL_RECORD,),
        evidence=(EV_USER_UPLOAD,),
        generates=False, role="production_manager",
        condition="客户要求保留标准样件时适用",
        notes="登记签样状态与存放位置；不代替客户批准。",
    ),
    _el(
        "checking_aid", "检具/辅具", "检具辅具", CAT_PHYSICAL, METHOD_PHYSICAL_RECORD, REQ_SUBMIT,
        inputs=(DS_PHYSICAL_RECORD,),
        evidence=(EV_USER_UPLOAD,),
        generates=False, role="production_manager",
        condition="控制计划要求使用专用检具/辅具时适用",
        notes="登记检具清单与校准状态来源；不生成合格结论。",
    ),
    _el(
        "customer_specific_requirements", "客户特殊要求", "客户特殊要求", CAT_SUBMISSION,
        METHOD_CONFIRMED_INPUT_TABLE, REQ_SUBMIT,
        inputs=(DS_CUSTOMER_REQUIREMENT,), evidence=(EV_USER_UPLOAD,),
        engineering_input=True, role="sales_manager",
        notes="逐条来自人工确认的客户要求清单，标注来源与确认人；不从报价金额推断要求。",
    ),
    _el(
        "psw", "零件提交保证书（PSW）", "PSW", CAT_SUBMISSION, METHOD_TEMPLATE_FILL, REQ_SUBMIT,
        notes="从统一基础信息填充；不复制旧样例的公司、零件、日期、人员、签字及结论。",
    ),
    _el(
        "attachment_index", "附件索引", "附件索引", CAT_SUBMISSION, METHOD_TEMPLATE_FILL,
        REQ_RETAIN,
        notes="由证据记录与产物版本自动汇总，含来源系统/记录 ID/哈希，便于追溯。",
    ),
    _el(
        "equipment_packaging", "生产设备与包装规范", "设备与包装", CAT_SUBMISSION,
        METHOD_TEMPLATE_FILL, REQ_SUBMIT,
        inputs=(DS_PACKAGING_EQUIPMENT,),
        condition="客户要求提交设备与包装信息时适用",
        notes="设备与包装信息来自人工确认输入；缺数据时标待补，不填默认值。",
    ),
    _el(
        "dimensional_layout", "测点布局图（客户扩展）", "测点布局图", CAT_REFERENCE,
        METHOD_REFERENCE_DOCUMENT, REQ_ON_REQUEST,
        evidence=(EV_USER_UPLOAD,),
        side_sensitive=True, generates=False, engineering_input=True,
        condition="客户要求标记测点布局时适用",
        notes="客户扩展条目示例：条目目录可配置，不硬编码为固定 18 张表。",
    ),
)

ELEMENT_BY_ID: dict[str, PpapElementSpec] = {e.element_id: e for e in ELEMENTS}
ELEMENT_IDS: tuple[str, ...] = tuple(e.element_id for e in ELEMENTS)


# ----------------------------------------------------------------------
# 查询辅助
# ----------------------------------------------------------------------


def element_spec(element_id: str) -> PpapElementSpec | None:
    return ELEMENT_BY_ID.get(element_id)


def element_specs() -> tuple[PpapElementSpec, ...]:
    return ELEMENTS


def elements_using_input(dataset: str) -> tuple[PpapElementSpec, ...]:
    """哪些条目的编制依赖该数据集；用于源数据变化后的影响分析（AC04/阶段 D）。"""
    return tuple(e for e in ELEMENTS if dataset in e.required_inputs)


def elements_using_evidence(source_type: str) -> tuple[PpapElementSpec, ...]:
    return tuple(e for e in ELEMENTS if source_type in e.evidence_types)


def suggested_applicability() -> list[dict[str, Any]]:
    """建议适用性清单（未确认）。前端据此生成待确认表格。"""
    return [
        {
            "element_id": e.element_id,
            "title": e.title,
            "suggested_requirement": e.suggested_requirement,
            "condition": e.applicability_condition,
            "confirmed": False,
        }
        for e in ELEMENTS
    ]


def requirement_blocks_approval(requirement: str) -> bool:
    return requirement == REQ_SUBMIT


def validate_requirement(requirement: str) -> str | None:
    """返回错误说明；合法时返回 None。"""
    if requirement in REQUIREMENT_VALUES:
        return None
    if requirement == REQ_UNCONFIRMED:
        return None
    return f"未知的适用性取值：{requirement!r}，允许 {REQUIREMENT_VALUES}"


def template_descriptor() -> dict[str, Any]:
    """模板描述（供 /api/real-orders/ppap/templates 与 AIP 只读技能使用）。"""
    return {
        "template_id": TEMPLATE_ID,
        "template_version": TEMPLATE_VERSION,
        "template_source": TEMPLATE_SOURCE,
        "is_customer_template": TEMPLATE_IS_CUSTOMER_TEMPLATE,
        "display_name": TEMPLATE_DISPLAY_NAME,
        "notice": TEMPLATE_NOTICE,
        "rule_version": RULE_VERSION,
        "element_count": len(ELEMENTS),
        "elements": [e.to_dict() for e in ELEMENTS],
        "datasets": list(ALL_DATASETS),
        "side_sensitive_datasets": list(SIDE_SENSITIVE_DATASETS),
        "evidence_source_types": list(EVIDENCE_SOURCE_TYPES),
        "applicability_note": (
            "suggested_requirement 为公开 PPAP 惯例下的建议值，不是客户规则；"
            "任务必须显式确认后才生效（清单 §9 问题 1）。"
        ),
    }


def iter_methods(elements: Iterable[PpapElementSpec] | None = None) -> list[str]:
    seen: list[str] = []
    for e in elements or ELEMENTS:
        if e.compilation_method not in seen:
            seen.append(e.compilation_method)
    return seen
