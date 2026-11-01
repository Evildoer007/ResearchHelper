"""Synthetic acceptance fixtures only. Never selected from a real user's keywords."""

from copy import deepcopy
from .contracts import fingerprint

CUTOFF = "2026-07-20T18:00:00+08:00"


def evidence(key, subject, text, value=None, unit="不适用", nature="合成披露"):
    raw = {"subject": subject, "text": text, "value": value, "unit": unit}
    return {"id": key, "object": subject, "source": "合成验收原件", "locator": f"材料{key}第1段",
            "snapshot": raw, "snapshot_hash": fingerprint(raw), "published_at": "2026-07-19T09:00:00+08:00",
            "obtained_at": "2026-07-20T10:00:00+08:00", "period": "2026年7月验收期", "nature": nature,
            "unit": unit, "value": value, "text": text, "source_check": "通过", "applicability_check": "通过"}


def claim(title, judgment, support, counter=(), meaning="", risk="", tracking=None):
    return {"title": title, "judgment": judgment, "support": list(support), "counter": list(counter),
            "investment_meaning": meaning, "limitation": risk, "tracking": tracking}


CASES = {
    "mechanism": {"title": "铜价上涨为何未改善冶炼利润", "entry": "产业链", "intent": "解释变化",
        "question": "解释验收铜冶炼企业A在铜价上涨时利润未改善的机制，不做估值或证券推荐。",
        "objects": ["验收铜冶炼企业A"], "questions": ["企业业务环节是否已确认", "铜价、采购与加工费如何传导", "套保和产量是否抵消影响"],
        "evidence": [evidence("m1", "验收铜冶炼企业A", "主营精矿冶炼，以加工费而非铜价绝对涨幅获取收益。"),
            evidence("m2", "验收铜冶炼企业A", "同期铜价上涨10%，采购按市场价结算。", 10, "%"),
            evidence("m3", "验收铜冶炼企业A", "加工费从80美元/吨降至30美元/吨，采购涨价未增加加工收益。", 30, "美元/吨"),
            evidence("m4", "验收铜冶炼企业A", "产量持平，价格风险经套保对冲；期间加工利润下降。")],
        "claims": [claim("加工费下降解释利润承压，铜价上涨不是独立利好", "支持", ["m1", "m3", "m4"], ["m2"],
            "研究应分环节核验资源收益与加工收益，不推出全产业链受益或做空机会。", "未核验其他企业，结论仅适用于该合成样本。",
            {"indicator": "加工费、产量及套保效果", "source": "经营披露", "window": "下一报告期", "condition": "加工费持续回升且未被成本抵消时重新评估"})]},
    "etf": {"title": "同指数ETF的年度费用比较", "entry": "ETF", "intent": "比较对象",
        "question": "比较验收ETF-A与ETF-B同一年度管理费和托管费，不要求跟踪差异或买入建议。",
        "objects": ["验收ETF-A", "验收ETF-B"], "questions": ["两产品身份、基准与适用期间是否可比", "年度管理费和托管费有何差异"],
        "evidence": [evidence("e1", "验收ETF-A", "同一基准、人民币、相同年度规则，管理费0.15%，托管费0.05%。", .20, "%/年"),
            evidence("e2", "验收ETF-B", "同一基准、人民币、相同年度规则，管理费0.50%，托管费0.10%。", .60, "%/年")],
        "calculation": {"method": "fee_difference_v1", "inputs": {"fee_a": "e1", "fee_b": "e2"}, "parameters": {"unit": "%/年"}},
        "claims": [claim("ETF-A年度费率比ETF-B低0.40个百分点", "支持", ["e1", "e2"], meaning="仅比较披露费用，不代表实际持有收益或指数买入价值。",
                         risk="交易成本、跟踪差异未纳入原问题，不能由费率推断收益。")], "tracking_reason": "静态费用比较，规则变化时主动复核。"},
    "value": {"title": "剩余现金流的投资判断", "entry": "个股", "intent": "发现投资机会",
        "question": "验收普通股A的剩余现金流现值与参考价格是否满足本案例10%现值溢价条件？",
        "objects": ["验收普通股A"], "questions": ["收款、债务、费用与分配是否完整", "基准及压力现值是多少", "本案例机会条件与表达条件是否满足"],
        "evidence": [evidence("v1", "验收普通股A", "一年后预计收款120百万元，履约条件及收款风险已核验。", 120, "百万元", "合成条件预测"),
            evidence("v2", "验收普通股A", "同日清偿债务20百万元，无其他优先索偿。", 20, "百万元"),
            evidence("v3", "验收普通股A", "税费及清算费用5百万元，无其他遗漏义务。", 5, "百万元"),
            evidence("v4", "验收普通股A", "股本10百万股，剩余现金全部分配，无其他残余价值。", 10, "百万股"),
            evidence("v5", "验收普通股A", "截止时点参考价格7.50元；合成交易状态、流动性与承载条件通过。", 7.5, "元/股")],
        "calculation": {"method": "residual_present_value_v1", "inputs": {"receipt": "v1", "debt": "v2", "cost": "v3", "shares": "v4", "price": "v5"},
                        "assumptions": {"rate": .10, "years": 1}, "parameters": {"amount_unit": "百万元", "shares_unit": "百万股"}},
        "claims": [claim("基准现值8.6364元/股，较参考价溢价15.15%", "支持", ["v1", "v2", "v3", "v4", "v5"],
            meaning="在本案例收款、分配及10%必要回报率假设下满足机会阈值，不是通用投资规则。",
            risk="压力收款减少20百万元时现值6.8182元/股；不赋予情景概率。",
            tracking={"indicator": "合同收款及优先索偿", "source": "合同履约与财务披露", "window": "一年", "condition": "收款减少或新增优先义务即重新估值"})]},
    "index": {"title": "指数上涨不代表市场全面改善", "entry": "指数", "intent": "综合研究", "question": "研究验收指数I的上涨来源与市场宽度。",
        "objects": ["验收指数I"], "questions": ["权重与样本是否同一时点", "指数表现与市场宽度有何差异"],
        "evidence": [evidence("i1", "验收指数I", "同期指数上涨4%，前五大成分权重50%，多数成分下跌。", 4, "%"),
                     evidence("i2", "验收指数I", "固定成分样本与权重时点核验，涨幅集中于前五大成分。")],
        "claims": [claim("指数上涨主要集中在高权重成分", "支持", ["i1", "i2"], meaning="区分指数暴露与广泛景气，不能推断全部股票改善。", risk="合成固定样本，不代表真实指数。")]},
    "sector": {"title": "行业改善与企业分化", "entry": "行业", "intent": "综合研究", "question": "研究验收消费行业的供需、竞争与企业分化。",
        "objects": ["验收消费行业"], "questions": ["行业需求与竞争是否改善", "样本企业是否同向受益"],
        "evidence": [evidence("s1", "验收消费行业", "合成同口径行业销量增长8%，价格竞争收敛。", 8, "%"),
                     evidence("s2", "验收企业B", "渠道调整导致份额下降，利润同比下滑。")],
        "claims": [claim("行业需求改善不覆盖企业B的经营反证", "支持", ["s1", "s2"], meaning="投资含义在竞争位置与渠道兑现，不需要一致预期才能交付行业研究。", risk="仅覆盖两个合成记录，不宣称全行业最佳。")]},
    "event": {"title": "政策存在与企业资格分开核验", "entry": "事件", "intent": "验证观点", "question": "核验政策P是否直接惠及验收企业C。",
        "objects": ["政策P", "验收企业C"], "questions": ["政策原文、生效与资格是否已核验", "企业C是否满足适用条件"],
        "evidence": [evidence("p1", "政策P", "已生效合成原文，仅适用特定资格群体。"), evidence("p2", "验收企业C", "企业C不满足政策资格。")],
        "claims": [claim("政策直接惠及企业C的假设被反对", "反对", ["p1", "p2"], meaning="终止直接受益分支，不自动推出做空或其他受益名单。", risk="间接需求渠道仍需独立核验。")]},
    "macro": {"title": "降息的相反传导渠道", "entry": "宏观", "intent": "解释变化", "question": "现有合成证据能否识别降息对港股权益的净方向？",
        "objects": ["合成降息事件", "港股权益"], "questions": ["事件与同期因素是否核验", "折现与盈利渠道能否识别净方向"],
        "evidence": [evidence("a1", "合成降息事件", "合成政策原文降息25基点，披露和生效时点已核验。", 25, "基点"),
                     evidence("a2", "港股权益", "同期盈利预期下修与折现率下降并存，现有材料不能排除竞争解释。")],
        "claims": [claim("现有证据不足以识别净方向", "证据不足", ["a1", "a2"], meaning="充分评估后保留条件性判断，无法识别不等于取证未完成。", risk="因果与净效应不可识别，不输出涨跌概率。")]},
}

# Every fixture question has its own answer and original references. These are
# explicit acceptance materials, never selected to answer a real user's input.
CASE_ANSWERS = {
    "mechanism": {
        "q1": {"answer": "A处于精矿冶炼环节，加工费是收益来源，不是铜价绝对涨幅。", "refs": ["m1"]},
        "q2": {"answer": "采购随市场铜价结算，加工费由80降至30美元/吨，铜价上涨未增加加工收益。", "refs": ["m1", "m2", "m3"]},
        "q3": {"answer": "产量持平，价格风险经套保对冲，加工利润仍下降。", "refs": ["m4"]}},
    "etf": {
        "q1": {"answer": "两产品使用同一基准、人民币及同一年度规则，费用口径可比。", "refs": ["e1", "e2"]},
        "q2": {"answer": "A年度费率0.20%，B为0.60%，A低0.40个百分点；这不是实际收益差。", "refs": ["e1", "e2"], "calculation_refs": ["rh_calc_1"]}},
    "value": {
        "q1": {"answer": "收款120百万元、债务20百万元、费用5百万元、股本10百万股；分配和优先索偿范围已核验。", "refs": ["v1", "v2", "v3", "v4"]},
        "q2": {"answer": "基准现值8.6364元/股，收款减少20百万元的压力现值6.8182元/股。", "refs": ["v1", "v2", "v3", "v4", "v5"], "calculation_refs": ["rh_calc_1", "rh_calc_stress"]},
        "q3": {"answer": "基准现值较7.50元参考价溢价15.15%，满足本案例10%阈值；压力情景不满足，且不赋予情景概率。", "refs": ["v1", "v2", "v3", "v4", "v5"], "calculation_refs": ["rh_calc_1", "rh_calc_stress"]}},
    "index": {
        "q1": {"answer": "固定成分样本与权重时点一致，前五大成分权重合计50%。", "refs": ["i1", "i2"]},
        "q2": {"answer": "指数上涨4%但多数成分下跌，涨幅集中于前五大成分，不代表全面改善。", "refs": ["i1", "i2"]}},
    "sector": {
        "q1": {"answer": "同口径行业销量增长8%，价格竞争收敛。", "refs": ["s1"]},
        "q2": {"answer": "企业B因渠道调整失去份额、利润下滑，行业改善不能覆盖公司反证。", "refs": ["s1", "s2"]}},
    "event": {
        "q1": {"answer": "政策已生效，但仅适用于规定资格群体。", "refs": ["p1"]},
        "q2": {"answer": "企业C不满足资格，直接受益假设被反对；这不等于做空结论。", "refs": ["p1", "p2"]}},
    "macro": {
        "q1": {"answer": "政策原文降息25基点，事件时点与同期盈利预期下修均已核验。", "refs": ["a1", "a2"]},
        "q2": {"answer": "折现率下降与盈利预期下修并存，现有证据不能排除竞争解释，净方向不可识别。", "refs": ["a1", "a2"]}},
}


def get_case(key):
    if key == "mixed":
        result = deepcopy(CASES["etf"])
        other = CASES["event"]
        result.update(title="政策核查与ETF费用的并列研究", entry="混合", question="核验政策P适用资格，并独立比较验收ETF-A与ETF-B年度费率。")
        for field in ("objects", "questions", "evidence", "claims"):
            result[field] += deepcopy(other[field])
        result["questions"] = [dict(id=f"q{index+1}", question=text,
            kind="mechanism" if index == 3 else "comparison" if index < 2 else "fact",
            group="fees" if index < 2 else "policy") for index, text in enumerate(result["questions"])]
        result["calculation"]["question_ids"] = ["q1", "q2"]
        result["claims"][0]["question_ids"] = ["q1", "q2"]
        result["claims"][1]["question_ids"] = ["q4"]
        result["answers"] = deepcopy(CASE_ANSWERS["etf"])
        result["answers"].update({"q3": deepcopy(CASE_ANSWERS["event"]["q1"]), "q4": deepcopy(CASE_ANSWERS["event"]["q2"])})
        return result
    if key not in CASES:
        raise ValueError("未知合成验收案例")
    result = deepcopy(CASES[key])
    result["answers"] = deepcopy(CASE_ANSWERS[key])
    return result


def catalog():
    return [{"id": key, "title": get_case(key)["title"], "entry": get_case(key)["entry"], "synthetic": True}
            for key in [*CASES, "mixed"]]
