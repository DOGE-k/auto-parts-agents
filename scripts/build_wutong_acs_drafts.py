"""Build Wutong initial registration ACS files without importing the application.

This script uses only the standard library, reads the trusted local capability
declaration through AST, and writes only registration/wutong/*.json. It never
loads .env, reads a database, registers an agent, or configures a live endpoint.
JSON Schema and SDK-model checks can be run separately; they are not implied by
the local contract checks recorded here.
"""
from __future__ import annotations

import ast
import hashlib
import ipaddress
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "registration" / "wutong"
CATALOG_PATH = ROOT / "backend" / "app" / "services" / "coordinator.py"
SCHEMA_PATH = ROOT / "acps-sdk-src" / "registry-server" / "app" / "agent" / "acsSchema.json"
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
PUBLIC_IP = "129.204.33.138"
WEB_APP_URL = f"http://{PUBLIC_IP}:5173/"
PROVIDER_ORGANIZATION = "广航海燕队"
PROVIDER_CONTACT = "柯树鑫"
PROVIDER_EMAIL = "2698322689@qq.com"
INTERNAL_INPUT_CONVENTION = "TextDataItem.text=技能ID|JSON参数；带参数调用须使用实际业务标识和契约要求的JSON类型"
NO_ARGUMENT_EXAMPLES = {"procurement.inventory_overview", "quality.list_open_issues"}

# Hand-reviewed display text; exact skill IDs are checked against REAL_SKILL_TOOLS.
SKILLS: dict[str, tuple[str, str]] = {
    "quotation.analyze_real": (
        "报价分析",
        "根据客户、物料和数量读取价格、BOM、库存与交期依据，生成并保存本地报价分析方案；不创建ERP订单或完成审批。",
    ),
    "quotation.get_real": (
        "报价内容与状态查询",
        "查询已保存报价的价格、内容、审批状态及关联ERP草稿编号；不修改报价。",
    ),
    "quotation.find_real_by_erp_order": (
        "按销售订单查询报价",
        "根据销售订单号查找已保存的关联报价及最新采购方案摘要；无正式关联时如实返回未找到。",
    ),
    "quotation.assess_cost_impact": (
        "材料成本影响评估",
        "依据已有采购选项和价格记录计算材料成本变化及材料毛利影响；缺少价格时报告缺项，不作全成本利润结论。",
    ),
    "procurement.analyze_real": (
        "缺料与供应方案分析",
        "根据已有报价计算净物料需求、缺料与供应商选项并保存本地方案；缺料可能触发本地协同事件，不发布采购订单。",
    ),
    "procurement.get_real_plan": (
        "采购方案查询",
        "查询已保存采购方案、选定供应商、审批状态及PO草稿编号；不选择供应商或修改审批。",
    ),
    "procurement.find_real_plan_by_quotation": (
        "按报价查询采购方案",
        "根据报价编号查询最新采购方案的状态、已选选项和PO草稿摘要。",
    ),
    "procurement.inventory_overview": (
        "库存总览",
        "查询物料在各仓库的实时余量、预留量、在途量和预计可用量；不进行库存调整。",
    ),
    "procurement.assess_combination": (
        "分单采购组合评估",
        "对同一方案内的多个供应选项计算成本、缺料覆盖和最长交期，提示重复采购风险；不下单。",
    ),
    "tracking.find_real_by_no": (
        "按工单编号查找工单",
        "将用户可见工单编号精确转换为系统工单ID，供进度、质量及门禁查询使用；不猜测ID。",
    ),
    "tracking.lookup_order_link": (
        "销售订单与生产工单关联查询",
        "按正式订单关联字段查找生产工单；产品编码相同的记录不据此视为正式订单关联。",
    ),
    "tracking.track_real": (
        "生产进度与风险查询",
        "查询工单进度、完成率、质量风险和有依据的完工预估；缺少速率时报告缺项，可能产生本地延期或临期协同事件。",
    ),
    "tracking.check_real_ship_gate": (
        "发运条件检查",
        "基于调用方传入且应先核对的审批状态、质量门禁和生产完成率检查发运条件并解释阻塞原因；不执行发运或签发质量放行。",
    ),
    "tracking.assess_delivery_impact": (
        "物料到货与交期影响评估",
        "比较物料预计到货日与工单交期，提示物料维度的延期风险；不将这一比较作为完整排程或剩余产量的完工预测。",
    ),
    "quality.ppap_find_tasks": (
        "PPAP任务检索",
        "按客户、零件号、负责人及状态检索已有PPAP任务；不新建任务，须按返回的数据模式区分正式与测试任务。",
    ),
    "quality.ppap_get_applicability": (
        "PPAP条目适用性查询",
        "查询各条目的提交、保留、按需或不适用要求及确认依据；不代替客户要求确认。",
    ),
    "quality.ppap_get_status": (
        "PPAP编制与审核状态查询",
        "查询任务、条目编制、内部审核、客户处理及覆盖度等状态；不把草稿或内部批准视为客户接受。",
    ),
    "quality.ppap_explain_missing": (
        "PPAP缺项与冲突解释",
        "根据现有输入和证据解释缺项、冲突及责任岗位；不补造资料，不保存检查结果或修改任务。",
    ),
    "quality.ppap_evidence_plan": (
        "PPAP取证计划查询",
        "查询任务可用来源、已确认业务关联和待补人工输入；仅返回计划，不执行远端取证或新增证据。",
    ),
    "quality.ppap_get_versions": (
        "PPAP版本查询",
        "查询产物版本、输入快照、内容哈希及冻结、归档状态；不生成、批准或变更版本。",
    ),
    "quality.get_real_package": (
        "工单质量资料与门禁查询",
        "汇集已有质量问题、工程文档和检验记录，检查资料及问题门禁；不生成检测数据或签发质量放行。",
    ),
    "quality.assess_quality_impact": (
        "质量异常影响分析",
        "交叉分析质量问题、批次、门禁与生产进度，解释交付影响及处理方向；不执行处置、关闭或放行。",
    ),
    "quality.check_issue_closure": (
        "NCR关闭前置检查",
        "检查指定工单的NCR状态、处置、根因、遏制和措施验证条件；只报告前置条件，不关闭问题。",
    ),
    "quality.list_open_issues": (
        "未关闭质量问题查询",
        "查询跨工单的未关闭质量问题及严重度、状态和已报告时长；不改变问题状态。",
    ),
}

AGENTS = (
    ("01-leader.acs.json", "leader", "汽车零部件协同智能体", "面向汽车零部件工厂的跨岗位业务协同，接收业务问题，协调报价、采购、生产跟单和质量文档四个岗位，汇总订单、物料、进度与质量信息，提供处理建议及相关依据。"),
    ("02-quotation.acs.json", "quotation", "汽车零部件报价智能体", "面向汽车零部件工厂的销售与报价业务，基于客户、物料、BOM、价格、库存及交期信息生成报价分析方案，查询报价与销售订单关联状态，评估材料成本变化及其影响。报价审批和订单写入通过项目授权流程处理。"),
    ("03-procurement.acs.json", "procurement", "汽车零部件采购智能体", "面向汽车零部件工厂的物料采购业务，依据报价和库存信息分析物料净需求及缺料情况，比较供应商选项，查询采购方案与关联草稿状态，并评估分单采购组合的成本、覆盖量和交期。采购下单通过项目授权流程处理。"),
    ("04-tracking.acs.json", "tracking", "汽车零部件生产跟单智能体", "面向汽车零部件工厂的生产履约跟踪，查询销售订单与工单关联、生产进度及质量风险，评估物料到货时间对交期的影响，检查生产完成度与发运前置条件，提供阻塞原因和跟进依据。"),
    ("05-quality-document.acs.json", "quality-document", "汽车零部件质量文档智能体", "面向汽车零部件工厂的质量资料管理与PPAP业务，提供PPAP任务检索、条目适用性、编制审核状态、缺项解释、取证计划和版本查询；汇集工单质量资料，分析质量异常影响，并检查NCR关闭前置条件。PPAP编制与审核通过项目任务流程处理。"),
)


def load_catalog() -> list[dict[str, Any]]:
    tree = ast.parse(CATALOG_PATH.read_text(encoding="utf-8"), filename=str(CATALOG_PATH))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "REAL_SKILL_TOOLS":
            tools = ast.literal_eval(node.value)
            break
    else:
        raise ValueError("REAL_SKILL_TOOLS declaration was not found")
    if not isinstance(tools, list) or len(tools) != 24:
        raise ValueError("Reviewed capability count changed; review before generating")
    ids = [tool["skill_id"] for tool in tools]
    if len(ids) != len(set(ids)) or set(ids) != set(SKILLS):
        raise ValueError("Reviewed skill IDs differ from the current capability declaration")
    for tool in tools:
        if tool.get("read_only") is not True or tool.get("requires_approval") is not False:
            raise ValueError(f"Capability permission declaration changed: {tool['skill_id']}")
    return tools


def build_spec(role: str, name: str, summary: str, tools: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    skills = []
    for tool in tools:
        if tool["aip_agent"] != role:
            continue
        skill_id = tool["skill_id"]
        title, description = SKILLS[skill_id]
        skill = {
            "id": skill_id,
            "name": title,
            "description": description,
            "version": str(tool.get("version") or "1.0.0"),
            "tags": ["汽车零部件", title, "查询与分析"],
            "inputModes": ["text/plain"],
            "outputModes": ["application/json"],
        }
        if skill_id in NO_ARGUMENT_EXAMPLES:
            if tool.get("parameters", {}).get("required"):
                raise ValueError(f"No-argument example now requires parameters: {skill_id}")
            skill["examples"] = [skill_id + "|{}"]
        skills.append(skill)
    return {
        "aic": "",
        "active": False,
        "lastModifiedTime": generated_at,
        "protocolVersion": "02.02",
        "name": name,
        "description": summary,
        "version": "0.1.0",
        "webAppUrl": WEB_APP_URL,
        "provider": {"organization": PROVIDER_ORGANIZATION, "name": PROVIDER_CONTACT, "email": PROVIDER_EMAIL},
        "securitySchemes": {},
        "endPoints": [],
        "capabilities": {"streaming": False, "notification": False, "messageQueue": []},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["application/json"],
        "skills": skills,
        "certificate": {"altNames": {"ip": [PUBLIC_IP]}},
    }


def verify_local_contract(specs: dict[str, dict[str, Any]], tools: list[dict[str, Any]]) -> None:
    if str(ipaddress.ip_address(PUBLIC_IP)) != PUBLIC_IP:
        raise ValueError("Public IP must be an exact address, not a URL or network range")
    for filename, role, _name, _summary in AGENTS:
        spec = specs[filename]
        expected = {tool["skill_id"] for tool in tools if tool["aip_agent"] == role}
        actual = [skill["id"] for skill in spec["skills"]]
        if set(actual) != expected or len(actual) != len(set(actual)):
            raise ValueError(f"Skill-set mismatch: {filename}")
        if spec["aic"] != "" or spec["active"] is not False:
            raise ValueError("Drafts must not invent assigned identity or activation")
        if spec["provider"] != {"organization": PROVIDER_ORGANIZATION, "name": PROVIDER_CONTACT, "email": PROVIDER_EMAIL}:
            raise ValueError("Provider must contain only the user-confirmed team and public contact")
        if spec["endPoints"] or spec["securitySchemes"]:
            raise ValueError("Drafts must not advertise unverified endpoints or authentication")
        web_app = urlparse(spec.get("webAppUrl", ""))
        if spec.get("webAppUrl") != WEB_APP_URL or web_app.scheme not in {"http", "https"} or web_app.hostname != PUBLIC_IP or web_app.port != 5173:
            raise ValueError("Initial applications must include the verified real Web application URL")
        if spec["certificate"] != {"altNames": {"ip": [PUBLIC_IP]}}:
            raise ValueError("Certificate SAN request must match the selected public IP")
        if "entityMeta" in spec:
            raise ValueError("Internal preparation metadata must stay outside public ACS")
        public_text = json.dumps(spec, ensure_ascii=False)
        if any(text in public_text for text in ("本文件", "待确认", "待补充", "尚未实现", "尚未部署", "NOT_READY", "TBD-", "<", ">", "synthetic_demo_only")):
            raise ValueError("Public ACS contains preparation notes or placeholder examples")
        if spec["capabilities"] != {"streaming": False, "notification": False, "messageQueue": []}:
            raise ValueError("Unimplemented capabilities must not be advertised")
        for skill in spec["skills"]:
            for example in skill.get("examples", []):
                skill_id, payload = example.split("|", 1)
                if skill_id != skill["id"] or not isinstance(json.loads(payload), dict):
                    raise ValueError("Example is not a matching skill_id|JSON object")
            if skill["inputModes"] != ["text/plain"] or skill["outputModes"] != ["application/json"]:
                raise ValueError("Input/output capability declaration changed")
        # Round-trip strict JSON; NaN and Infinity are never accepted.
        json.loads(json.dumps(spec, ensure_ascii=False, allow_nan=False))


def build_manifest(specs: dict[str, dict[str, Any]], generated_at: str) -> dict[str, Any]:
    common_gaps = [
        "已选择公网IP方案；实际HTTPS端口与AIP端点尚未部署验证，不从网页端口推定RPC地址",
        "正式mTLS端点、证书部署及AIC身份绑定尚待实施和验证",
        "需验证调用方与任务所有权隔离，不可直接依赖当前共享内存任务字典",
        "普通自然语言调用与StructuredDataItem技能选择适配尚待补齐；当前只记录技能ID|JSON文本约定",
        "提供方团队、联系人和邮箱已由用户提供；平台认证及其他审核材料按实际要求核对",
        "尚未完成梧桐平台注册、审批回执和平台到服务的调用验证",
    ]
    schema_available = SCHEMA_PATH.is_file()
    return {
        "formatVersion": "2.0",
        "generatedAt": generated_at,
        "timezone": "Asia/Shanghai",
        "overallReadiness": "NOT_READY",
        "readinessMeaning": "NOT_READY表示平台调用尚未就绪，不表示禁止尝试在平台注册页面保存草稿",
        "purpose": "五份正式初次注册申请ACS的内部核对记录；本文件不粘贴到平台注册表单",
        "publicContentPolicy": "ACS仅包含对外业务能力、提供方和协议字段；内部准备状态、待办与校验记录只保存在本清单和文档中",
        "admissionFeedback": {
            "source": "user_supplied_platform_rejection_screenshot",
            "submittedAt": "2026-10-16T15:46:58+08:00",
            "processedAt": "2026-10-16T15:50:33+08:00",
            "agentRole": "leader",
            "status": "REJECTED",
            "rule": "registry-admission-2.0-static-format-llm-whole-acs",
            "message": "At least one valid Endpoint or webAppUrl is required",
            "correction": "补充真实、可访问的webAppUrl；该网页入口不冒充AIP端点，未提交重新审核",
            "schemaValidationIsAdmissionApproval": False,
        },
        "webApplication": {
            "url": WEB_APP_URL,
            "applicationScope": "同一项目网页提供Leader协同问答及四个岗位模块，当前没有独立模块URL路由",
            "checkedAt": "2026-10-16T15:52:12+08:00",
            "httpStatus": 200,
            "redirected": False,
            "pageTitle": "汽车零部件智能体协作平台",
            "browserPageRendered": True,
            "browserObservedModules": ["AI协同问答", "销售与订单", "采购与缺料", "生产跟单", "质量中心"],
            "health": {"status": "ok", "database": "connected"},
            "businessActionPerformed": False,
            "note": "仅验证网页可打开及应用健康，页面同时显示MES异常；不作为所有业务或AIP调用正常的证明。生成器不执行网络检查。",
        },
        "inputContract": {
            "convention": INTERNAL_INPUT_CONVENTION,
            "naturalLanguageDirectInvocationImplemented": False,
            "parameterSchemas": {tool["skill_id"]: tool["parameters"] for tool in load_catalog()},
            "examplePolicy": "公开ACS仅保留两个已核对的无参技能文本示例，其他技能省略可选examples，避免伪造业务ID或错误JSON类型",
            "localPersistenceDisclosure": "部分分析保存本地方案、协同事件及运行记录；不据此取得ERP/MES审批写入权限",
        },
        "sourceCatalog": str(CATALOG_PATH.relative_to(ROOT)).replace("\\", "/"),
        "sourceCatalogSha256": hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest(),
        "connectionPlan": {
            "type": "public-ip",
            "publicIp": PUBLIC_IP,
            "authority": "user_selected_existing_server_public_ip",
            "certificateSanRequestedInDraft": {"ip": [PUBLIC_IP]},
            "certificateIssued": False,
            "verifiedHttpsAipEndpoint": None,
            "note": "IP SAN仅是注册资料中的期望证书配置；不代表已申请、签发证书或平台认可IP归属",
            "observedByParentTask": {
                "date": "2026-10-16",
                "checks": [
                    {"url": f"http://{PUBLIC_IP}:5173/api/health", "httpStatus": 200, "response": {"status": "ok", "database": "connected"}},
                    {"url": f"http://{PUBLIC_IP}:9000/openapi.json", "result": "RemoteDisconnected，没有HTTP响应"},
                    {"url": f"http://{PUBLIC_IP}:9000/aip/quotation/health", "result": "RemoteDisconnected，没有HTTP响应"},
                ],
                "conclusion": "应用健康检查可达；未证明公开AIP或HTTPS/mTLS可用，未据连接中断猜测原因；本脚本不发起网络检查",
            },
        },
        "domain": {
            "value": "bgcloud.top",
            "authority": "user_provided",
            "selectedForConnection": False,
            "isVerifiedServiceEndpoint": False,
            "observedByParentTask": {
                "date": "2026-10-16",
                "httpObservation": "http://bgcloud.top:5173/ 跳转至腾讯云网站接入备案拦截页",
                "httpsObservation": "https://bgcloud.top/ 连接失败",
                "conclusion": "当前不能作为已验证可用的RPC入口；本脚本未发起网络复测",
            },
        },
        "provider": {
            "publicationBasis": "项目团队",
            "organization": PROVIDER_ORGANIZATION,
            "organizationConfirmed": True,
            "organizationAuthority": "user_provided_team_name_not_platform_verified",
            "name": PROVIDER_CONTACT,
            "email": PROVIDER_EMAIL,
            "contactConfirmed": True,
            "contactAuthority": "user_provided_public_registration_contact_not_platform_verified",
            "omittedUnconfirmedFields": ["countryCode", "department", "url", "license", "domainRegistrations"],
            "note": "官方AgentProvider各子字段未列为required；不把联系人、邮箱、官网、备案全部设为草稿前置条件，也不编造值",
        },
        "platformAssignedFields": {
            "aic": "首次注册时由平台分配；空值不作为首次注册的前置阻塞",
            "active": "草稿固定false，不代表平台注册状态",
            "lastModifiedTime": "本地生成时间，不是平台注册服务回执时间",
        },
        "drafts": [
            {
                "file": filename,
                "role": role,
                "readiness": "NOT_READY",
                "skillCount": len(specs[filename]["skills"]),
                "verifiedPublicEndpoint": None,
                "webAppUrl": WEB_APP_URL,
                "gaps": (["标准AIP Leader总入口尚未实现，未编造技能ID"] if role == "leader" else []) + common_gaps,
            }
            for filename, role, _name, _summary in AGENTS
        ],
        "localContractValidation": {
            "status": "PASSED",
            "checks": ["严格JSON解析与回读", "四岗技能ID集合与24条当前声明逐项一致且无重复", "全部目录项仍声明read_only=true和requires_approval=false", "空AIC、inactive、空端点、空安全方案", "webAppUrl为已实测HTTP200并在浏览器显示的真实项目入口", "provider仅包含用户确认的团队、联系人和邮箱，不伪造平台认证", "仅申请已选择公网IP的精确SAN，不宣称证书已签发", "只声明text/plain输入和application/json输出", "仅保留两个已核对的无参文本示例，不含虚构业务ID或占位值", "公开ACS不含内部准备状态、待办或entityMeta；持久化及输入契约说明保存在内部清单"],
        },
        "schemaValidation": {
            "schema": str(SCHEMA_PATH.relative_to(ROOT)).replace("\\", "/"),
            "schemaSha256": hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest() if schema_available else None,
            "dialect": "https://json-schema.org/draft/2020-12/schema",
            "status": "NOT_RUN",
            "note": "生成器仅依赖标准库；完整JSON Schema校验需单独执行并补记实际结果，不能由JSON解析或技能一致性代替",
        },
        "onlineValidation": {
            "performed": False,
            "registrationSubmitted": True,
            "registrationSubmissionSource": "user_screenshot_of_prior_leader_application",
            "latestObservedReviewState": "REJECTED",
            "currentFilesSubmitted": False,
            "resubmissionPerformed": False,
            "certificateRequested": False,
            "platformInvocationVerified": False,
        },
        "sensitiveDataHandling": {
            "environmentFilesRead": False,
            "databasesRead": False,
            "credentialFilesRead": False,
            "privateValuesIncluded": False,
            "note": "仅从仓库代码声明与人工已确认公开信息生成；没有密码、Token、Cookie、密钥或数据库连接串字段",
        },
    }


def main() -> None:
    tools = load_catalog()
    generated_at = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    specs = {filename: build_spec(role, name, summary, tools, generated_at) for filename, role, name, summary in AGENTS}
    verify_local_contract(specs, tools)
    manifest = build_manifest(specs, generated_at)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for filename, payload in {**specs, "manifest.json": manifest}.items():
        target = OUTPUT / filename
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
        if json.loads(target.read_text(encoding="utf-8")) != payload:
            raise ValueError(f"Written JSON differs from source: {filename}")
    print("Created five initial registration ACS files; deployment status is recorded separately in manifest")
    for filename, spec in specs.items():
        print(f"{filename}: {len(spec['skills'])} skills, inactive, no advertised endpoint")


if __name__ == "__main__":
    main()
