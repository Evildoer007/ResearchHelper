"""事件驱动报告的证据门。

事件本体、产业机制、A 股对象暴露与市场响应是不同层次。行情数据只能回答最后一件；
没有可溯源的事件事实、机制原文、暴露依据及经确认的组合链（或直接传导原文）时，
不能把一次海外公司业绩与 A 股板块表现写成因果关系。

本模块只校验分析师提供或从受控材料整理出的证据，不调用 LLM，也不推断事实。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from . import genres
from .fetcher import FieldValue

BRANCH_PRIMARY = "主方向"
BRANCH_SECONDARY = "备选方向"
BRANCH_AUDIT_ONLY = "仅审计"
BRANCH_ROLES = (BRANCH_PRIMARY, BRANCH_SECONDARY, BRANCH_AUDIT_ONLY)


@dataclass(frozen=True)
class EvidenceItem:
    content: str
    source: str
    link: str = ""
    relation: str = ""
    acquisition: str = ""
    evidence_id: str = ""
    translation: str = ""

    def display(self) -> str:
        prefix = f"[{self.evidence_id}] " if self.evidence_id else ""
        suffix = f"；来源：{self.source}"
        if self.link:
            suffix += f"；链接：{self.link}"
        if self.relation:
            return f"{prefix}{self.relation}：{self.content}{suffix}"
        return f"{prefix}{self.content}{suffix}"

    def public_display(self) -> str:
        """客户报告写作输入：保留原文与来源，隐藏内部证据编号。"""
        suffix = f"；来源：{self.source}"
        if self.link:
            suffix += f"；链接：{self.link}"
        return f"{self.relation + '：' if self.relation else ''}{self.content}{suffix}"


@dataclass(frozen=True)
class EvidenceChain:
    fact_ids: tuple[str, ...]
    mechanism_ids: tuple[str, ...]
    exposure_ids: tuple[str, ...]
    conclusion: str
    direction: str = "不确定"
    confidence: str = "低"
    reason: str = ""
    branch_name: str = ""
    branch_role: str = BRANCH_PRIMARY

    def display(self) -> str:
        refs = "+".join((*self.fact_ids, *self.mechanism_ids, *self.exposure_ids))
        suffix = f"分支：{self.branch_name or '未命名'}；用途：{self.branch_role}；方向：{self.direction}；置信度：{self.confidence}"
        if self.reason:
            suffix += f"；边界：{self.reason}"
        return f"[{refs}] {self.conclusion}（{suffix}）"

    def public_display(self) -> str:
        """客户报告写作输入：不暴露内部编号、分支角色及置信度标签。"""
        suffix = f"方向：{self.direction}"
        if self.reason:
            suffix += f"；适用边界：{self.reason}"
        return f"{self.conclusion}（{suffix}）"


@dataclass(frozen=True)
class EventClaim:
    """一条可进入正文主轴选择器的、已经分析师确认的事件传导论点。

    它不是研报观点，也不是 LLM 新推断。``source_text`` 把该结论引用的事实、
    产业机制、A 股暴露原文和组合边界放在一起，供 planner/writer 原样引用。
    """

    id: str
    viewpoint: str
    direction: str
    source_text: str
    source: str = "分析师确认的事件证据组合"
    category: str = "事件/催化类"
    evidence_scope: str = "事件传导链"
    evidence_subject: str = ""
    branch_name: str = ""
    branch_role: str = BRANCH_PRIMARY


@dataclass
class EventEvidence:
    event_facts: list[EvidenceItem] = field(default_factory=list)
    industry_mechanisms: list[EvidenceItem] = field(default_factory=list)
    ashare_exposures: list[EvidenceItem] = field(default_factory=list)
    inference_chains: list[EvidenceChain] = field(default_factory=list)
    # 兼容旧版：若一条原文已经直接说明“事件为何影响本次 A 股对象”，可走捷径。
    transmission_links: list[EvidenceItem] = field(default_factory=list)
    # 搜索服务、检索词、命中/读取、淘汰原因及额度；只用于内部审计，不进入客户正文。
    search_audit: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        composed = bool(self.industry_mechanisms and self.ashare_exposures and selected_chains(self))
        return not self.errors and bool(self.event_facts) and bool(self.transmission_links or composed)


def _branch_role(value: object, *, fallback: str = BRANCH_PRIMARY) -> str:
    text = str(value or "").strip()
    aliases = {"主分支": BRANCH_PRIMARY, "主轴": BRANCH_PRIMARY,
               "备选": BRANCH_SECONDARY, "次方向": BRANCH_SECONDARY,
               "不进入正文": BRANCH_AUDIT_ONLY, "未选": BRANCH_AUDIT_ONLY}
    if text in BRANCH_ROLES:
        return text
    return aliases.get(text, fallback)


def selected_chains(evidence: EventEvidence) -> list[EvidenceChain]:
    """正文只接收一个主方向和至多一个备选方向，其余分支仅留在内部审计。"""
    primary = next((item for item in evidence.inference_chains
                    if item.branch_role == BRANCH_PRIMARY), None)
    secondary = next((item for item in evidence.inference_chains
                      if item.branch_role == BRANCH_SECONDARY), None)
    out: list[EvidenceChain] = []
    if primary is not None:
        out.append(primary)
    if secondary is not None and secondary is not primary:
        out.append(secondary)
    return out


def _report_direction(value: str) -> str:
    """把证据链方向统一成报告候选使用的方向词。"""
    text = str(value or "").strip()
    if any(word in text for word in ("正向", "利好", "上涨", "看涨")):
        return "看涨"
    if any(word in text for word in ("负向", "利空", "下跌", "看跌")):
        return "看跌"
    if "双向" in text:
        return "双向"
    return text or "不确定"


def claims(evidence: EventEvidence, *, subject: str = "") -> list[EventClaim]:
    """将已确认证据链转换为候选观点；不生成证据中不存在的新结论。"""
    if not evidence.complete:
        return []
    known = {
        item.evidence_id: item
        for values in (evidence.event_facts, evidence.industry_mechanisms,
                       evidence.ashare_exposures, evidence.transmission_links)
        for item in values
    }
    out: list[EventClaim] = []
    for index, chain in enumerate(selected_chains(evidence), 1):
        refs = (*chain.fact_ids, *chain.mechanism_ids, *chain.exposure_ids)
        quoted = [known[value].public_display() for value in refs if value in known]
        quoted.append("分析师确认的组合结论：" + chain.public_display())
        out.append(EventClaim(
            id=f"event_chain_{index}", viewpoint=chain.conclusion,
            direction=_report_direction(chain.direction),
            source_text="\n".join(quoted), evidence_subject=subject,
            branch_name=chain.branch_name, branch_role=chain.branch_role,
        ))
    # 兼容旧版直接传导原文：没有组合链时，只取第一条直链作为主方向，避免一次
    # 检索得到多条近义原文后把一页通候选全部占满。
    direct_links = [] if selected_chains(evidence) else evidence.transmission_links[:1]
    for index, link in enumerate(direct_links, 1):
        facts = "\n".join(item.public_display() for item in evidence.event_facts)
        text = "\n".join(value for value in (facts, link.public_display()) if value)
        out.append(EventClaim(
            id=f"event_direct_{index}",
            viewpoint=f"{link.relation}：{link.content}", direction="不确定",
            source_text=text, source=link.source,
            evidence_scope="直接传导原文", evidence_subject=subject,
        ))
    return out


@dataclass(frozen=True)
class GateResult:
    required: bool
    ready: bool
    missing: tuple[str, ...] = ()

    def message(self, entity_name: str = "该事件主体") -> str:
        if not self.required or self.ready:
            return ""
        return (f"无法生成事件影响报告：{entity_name}的事件证据链尚未齐备。"
                f"缺少：{'；'.join(self.missing)}。")


def required_for(brief) -> bool:
    """仅对明确外部事件或分析师显式覆盖启用原文证据硬门。

    市场状态触发和普通板块报告不应因没有公司公告而被挡住；明确外部事件则不能
    把“事件发生”和“A 股影响”写成没有原文依据的联想。
    """
    # 分析师显式指定事件型时，即使境外证券代码暂未被数据源验证，也必须保留
    # 证据硬门；否则“强制事件型”反而会因代码覆盖不足静默退化成普通行业报告。
    if bool(getattr(brief, "分析师强制事件驱动", False)):
        return True
    event_path = genres.normalize_event_path(
        getattr(brief, "事件路径", ""),
        topic_type=str(getattr(brief, "主导类型", "") or ""),
        has_entity=bool(getattr(getattr(brief, "触发实体", None), "可用", False)),
    )
    # “科技回调→消费轮动”等市场状态型问题仍可采用事件叙事，但证据来自行情、
    # 资金、估值及历史区间，不应被公司公告式 F/M/E/C 硬门拦住。
    return event_path == genres.EVENT_PATH_EXTERNAL


def subject_for(brief) -> str:
    """返回事件证据在候选与底稿中的展示主体。

    自动识别到证券实体时优先显示名称与代码；分析师强制事件型但解析器没有形成
    可验证证券代码时，退回已识别的事件描述或研究主题，不能因此丢弃整包证据。
    """
    entity = getattr(brief, "触发实体", None)
    if entity is not None:
        value = " ".join(filter(None, (
            str(getattr(entity, "名称", "") or "").strip(),
            str(getattr(entity, "代码", "") or "").strip(),
        ))).strip()
        if value:
            return value
    for name in ("触发事件", "研究主题", "主题"):
        value = str(getattr(brief, name, "") or "").strip()
        if value:
            return value
    return "本次事件"


def assess(brief, evidence: EventEvidence) -> GateResult:
    required = required_for(brief)
    if not required:
        return GateResult(False, True)
    missing: list[str] = []
    if evidence.errors:
        missing.extend(evidence.errors)
    if not evidence.event_facts:
        missing.append("至少 1 条事件事实（业绩实际值、指引或其他已披露事实，且必须注明来源）")
    if not evidence.transmission_links:
        if not evidence.industry_mechanisms:
            missing.append("至少 1 条产业机制原文（事件变量如何影响相关产业环节）")
        if not evidence.ashare_exposures:
            missing.append("至少 1 条 A 股暴露原文（公司业务、指数编制或ETF成分依据）")
        if evidence.industry_mechanisms and evidence.ashare_exposures and not selected_chains(evidence):
            missing.append("至少 1 条设为主方向的组合传导链（事件事实＋产业机制＋A股暴露）")
    return GateResult(True, not missing, tuple(dict.fromkeys(missing)))


def parse(raw: object) -> EventEvidence:
    """读取 overrides 顶层的 ``事件证据``；字段不合格不能悄悄当作有效。"""
    out = EventEvidence()
    if raw in (None, ""):
        return out
    if not isinstance(raw, Mapping):
        out.errors.append("「事件证据」必须是对象，含事件事实及直接传导或三段式证据链")
        return out

    def items(key: str, *, relation_required: bool, prefix: str) -> list[EvidenceItem]:
        value = raw.get(key) or []
        if not isinstance(value, list):
            out.errors.append(f"「事件证据.{key}」必须是列表")
            return []
        result: list[EvidenceItem] = []
        for index, item in enumerate(value, 1):
            if not isinstance(item, Mapping):
                out.errors.append(f"「事件证据.{key}」第 {index} 项必须是对象")
                continue
            content = str(item.get("内容") or item.get("事实") or item.get("说明") or "").strip()
            source = str(item.get("来源") or "").strip()
            link = str(item.get("链接") or "").strip()
            relation = str(item.get("关系") or "").strip()
            acquisition = str(item.get("取得方式") or "").strip()
            evidence_id = str(item.get("证据ID") or item.get("id") or "").strip()
            translation = str(item.get("译文") or "").strip()
            if not content or not source or (relation_required and not relation):
                required_text = "内容、来源、关系" if relation_required else "内容、来源"
                out.errors.append(f"「事件证据.{key}」第 {index} 项必须填写{required_text}")
                continue
            result.append(EvidenceItem(
                content=content, source=source, link=link, relation=relation,
                acquisition=acquisition, evidence_id=evidence_id or f"{prefix}{index}",
                translation=translation,
            ))
        return result

    out.event_facts = items("事件事实", relation_required=False, prefix="F")
    out.industry_mechanisms = items("产业机制", relation_required=False, prefix="M")
    out.ashare_exposures = items("A股暴露", relation_required=False, prefix="E")
    out.transmission_links = items("传导关系", relation_required=True, prefix="D")
    raw_audit = raw.get("检索审计") or []
    if isinstance(raw_audit, list):
        out.search_audit = [dict(item) for item in raw_audit if isinstance(item, Mapping)]
    else:
        out.errors.append("「事件证据.检索审计」必须是列表")

    known = {
        item.evidence_id: kind
        for kind, values in (
            ("事件事实", out.event_facts), ("产业机制", out.industry_mechanisms),
            ("A股暴露", out.ashare_exposures), ("传导关系", out.transmission_links),
        )
        for item in values
    }
    raw_chains = raw.get("组合传导链") or raw.get("传导链") or []
    if not isinstance(raw_chains, list):
        out.errors.append("「事件证据.组合传导链」必须是列表")
        raw_chains = []
    for index, item in enumerate(raw_chains, 1):
        if not isinstance(item, Mapping):
            out.errors.append(f"「事件证据.组合传导链」第 {index} 项必须是对象")
            continue
        fact_ids = tuple(str(value).strip() for value in (item.get("事实证据ID") or item.get("fact_ids") or []) if str(value).strip())
        mechanism_ids = tuple(str(value).strip() for value in (item.get("机制证据ID") or item.get("mechanism_ids") or []) if str(value).strip())
        exposure_ids = tuple(str(value).strip() for value in (item.get("暴露证据ID") or item.get("exposure_ids") or []) if str(value).strip())
        conclusion = str(item.get("结论") or item.get("conclusion") or "").strip()
        if not (fact_ids and mechanism_ids and exposure_ids and conclusion):
            out.errors.append(f"「事件证据.组合传导链」第 {index} 项必须填写三类证据ID和结论")
            continue
        typed = ((fact_ids, "事件事实"), (mechanism_ids, "产业机制"), (exposure_ids, "A股暴露"))
        invalid = [value for values, expected in typed for value in values if known.get(value) != expected]
        if invalid:
            out.errors.append(f"「事件证据.组合传导链」第 {index} 项引用了不存在或类型不符的ID：{', '.join(invalid)}")
            continue
        # 旧 JSON 没有“分支用途”时，第一条作为主方向、第二条作为备选，其余只留审计；
        # 这样旧证据包继续可用，同时不会把所有可能受影响行业都写进一页通。
        fallback_role = (BRANCH_PRIMARY if index == 1 else
                         BRANCH_SECONDARY if index == 2 else BRANCH_AUDIT_ONLY)
        out.inference_chains.append(EvidenceChain(
            fact_ids=fact_ids, mechanism_ids=mechanism_ids, exposure_ids=exposure_ids,
            conclusion=conclusion,
            direction=str(item.get("方向") or item.get("direction") or "不确定").strip(),
            confidence=str(item.get("置信度") or item.get("confidence") or "低").strip(),
            reason=str(item.get("边界") or item.get("reason") or "").strip(),
            branch_name=str(item.get("影响分支") or item.get("branch_name") or "").strip(),
            branch_role=_branch_role(item.get("分支用途") or item.get("branch_role"), fallback=fallback_role),
        ))
    return out


def template() -> dict[str, Any]:
    """可直接放进覆盖文件的最小证据模板。"""
    return {
        "事件证据": {
            "事件事实": [
                {
                    "证据ID": "F1",
                    "内容": "例如：SK海力士本季 DRAM/HBM 相关业绩或管理层指引的已披露事实",
                    "来源": "公司 IR 业绩公告 / 已上传研报名称及页码",
                    "链接": "可选：官方公告或材料链接",
                }
            ],
            "产业机制": [{
                "证据ID": "M1",
                "内容": "例如：需求、价格、产能或技术变化如何影响某一产业环节的原文",
                "来源": "产业报告 / 公司披露及页码",
                "链接": "可选",
            }],
            "A股暴露": [{
                "证据ID": "E1",
                "内容": "例如：本次A股公司、指数或ETF为何暴露于该产业环节的原文",
                "来源": "公司年报 / 指数编制方案 / 基金公告",
                "链接": "可选",
            }],
            "组合传导链": [{
                "事实证据ID": ["F1"], "机制证据ID": ["M1"], "暴露证据ID": ["E1"],
                "结论": "仅由上述原文组合出的传导结论", "方向": "正向/负向/双向/不确定",
                "置信度": "高/中/低", "影响分支": "例如：市场流动性或存储产业链",
                "分支用途": "主方向", "边界": "可选：证据限制",
            }],
            "传导关系": [
                {
                    "关系": "直接竞争 / 供应链 / 客户需求 / 技术替代 / 估值情绪映射",
                    "内容": "例如：该事实为何会影响本次确认的 A 股存储芯片行业或芯片ETF",
                    "来源": "公司披露、产业链研报或分析师可核验材料",
                    "链接": "可选：材料链接",
                }
            ],
        }
    }


def to_dict(evidence: EventEvidence) -> dict[str, Any]:
    def item(value: EvidenceItem) -> dict[str, Any]:
        out = {"证据ID": value.evidence_id, "内容": value.content, "来源": value.source}
        if value.link:
            out["链接"] = value.link
        if value.relation:
            out["关系"] = value.relation
        if value.acquisition:
            out["取得方式"] = value.acquisition
        if value.translation:
            out["译文"] = value.translation
        return out

    return {
        "事件事实": [item(value) for value in evidence.event_facts],
        "产业机制": [item(value) for value in evidence.industry_mechanisms],
        "A股暴露": [item(value) for value in evidence.ashare_exposures],
        "组合传导链": [{
            "事实证据ID": list(value.fact_ids), "机制证据ID": list(value.mechanism_ids),
            "暴露证据ID": list(value.exposure_ids), "结论": value.conclusion,
            "方向": value.direction, "置信度": value.confidence, "边界": value.reason,
            "影响分支": value.branch_name, "分支用途": value.branch_role,
        } for value in evidence.inference_chains],
        "传导关系": [item(value) for value in evidence.transmission_links],
        "检索审计": list(evidence.search_audit),
    }


def attach_to_analysis(ma, brief, evidence: EventEvidence) -> None:
    """将已核验的事件证据作为可溯源字段交给 writer。

    事件事实使用 ``触发标的_`` 前缀，确保写作层把它与板块数据分开；机制、暴露、
    组合链和直接传导分别单列，避免把“同属半导体”这类联想写成因果。
    """
    if not evidence.complete:
        return
    subject = subject_for(brief)
    selected = selected_chains(evidence)
    referenced_ids = {
        value for chain in selected
        for value in (*chain.fact_ids, *chain.mechanism_ids, *chain.exposure_ids)
    }
    def public_items(values):
        return [item for item in values if not selected or item.evidence_id in referenced_ids]

    for index, item in enumerate(public_items(evidence.event_facts), 1):
        note = ("自动检索原文并经分析师确认的事件本体事实"
                if item.acquisition else "分析师提供的事件本体事实")
        ma.field_values[f"触发标的_事件事实{index}"] = FieldValue(
            field=f"触发标的_事件事实{index}", value=item.public_display(), ok=True,
            source=item.source, status="ok",
            note=note + "；不得当作板块数据", display=item.public_display(),
        )
    for index, item in enumerate([] if selected else evidence.transmission_links[:1], 1):
        note = ("自动检索原文并经分析师确认的事件到研究口径传导依据"
                if item.acquisition else "分析师提供的事件到本次研究口径的传导依据")
        ma.field_values[f"事件传导证据{index}"] = FieldValue(
            field=f"事件传导证据{index}", value=item.public_display(), ok=True,
            source=item.source, status="ok",
            note=note, display=item.public_display(),
        )
    for label, values in (("事件产业机制", evidence.industry_mechanisms),
                          ("A股暴露依据", evidence.ashare_exposures)):
        for index, item in enumerate(public_items(values), 1):
            ma.field_values[f"{label}{index}"] = FieldValue(
                field=f"{label}{index}", value=item.public_display(), ok=True,
                source=item.source, status="ok",
                note="原文证据，经分析师确认；不得脱离组合传导链单独推导因果",
                display=item.public_display(),
            )
    for index, chain in enumerate(selected, 1):
        ma.field_values[f"事件组合传导链{index}"] = FieldValue(
            field=f"事件组合传导链{index}", value=chain.public_display(), ok=True,
            source="分析师确认的受控证据组合", status="ok",
            note=("正文采用分支；" if chain in selected else "仅内部审计，不进入正文；")
                 + "结论只允许引用所附原文及边界；不是新的外部事实来源",
            display=chain.public_display(),
        )
    ma.事件证据 = {
        "事件主体": subject,
        "事件事实": [item.display() for item in evidence.event_facts],
        "产业机制": [item.display() for item in evidence.industry_mechanisms],
        "A股暴露": [item.display() for item in evidence.ashare_exposures],
        "组合传导链": [item.display() for item in evidence.inference_chains],
        "正文采用分支": [item.display() for item in selected],
        "传导关系": [item.display() for item in evidence.transmission_links],
    }
    # 只给客户署名真正采用的证据，未选分支与检索淘汰材料仍只留在内部底稿。
    source_items = [
        item for values in (evidence.event_facts, evidence.industry_mechanisms, evidence.ashare_exposures)
        for item in values if item.evidence_id in referenced_ids
    ]
    if not selected:
        source_items = [*evidence.event_facts, *evidence.transmission_links[:1]]
    sources = []
    for item in source_items:
        source = {"来源": item.source, "链接": item.link}
        if source not in sources:
            sources.append(source)
    ma.事件证据["引用来源"] = sources
