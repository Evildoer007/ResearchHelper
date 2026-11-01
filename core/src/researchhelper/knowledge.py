"""One versioned knowledge authority; indexes are derived, tasks cannot publish."""

import copy

from .contracts import (ContractError, METHOD_REGISTRY, fingerprint, DOCUMENT_CONTENT_INTENT,
                        FINANCIAL_AMOUNT_UNITS, method_definition_for_knowledge, canonical)

GENERAL_DEFINITION_SCOPE = "general_definition"
CATALOGUE_POLICY = 'knowledge_catalogue_revision'
CATALOGUE_REVISION = 3


def catalogue_revision(value=CATALOGUE_REVISION):
    if type(value) is not int or not 1 <= value <= CATALOGUE_REVISION:
        raise ContractError('知识目录版本未登记，不能扩大旧任务可用条目')
    return value


def task_catalogue_revision(task):
    return catalogue_revision(task.get(CATALOGUE_POLICY, 1))

_ETF_MEASURES_V1 = {"id": "rh_k_etf_measures", "version": 1, "type": "概念", "state": "已发布",
     "title": "ETF价格、净值与总回报口径", "scope": ["ETF", "指数", "混合"],
     "knowledge_role": GENERAL_DEFINITION_SCOPE, "aliases": ["净值", "总回报", "价格口径"],
     "content": "市场价格是ETF份额在交易市场的成交价格；单位净值是基金资产减负债后按份额计算的资产价值，二者可能不相等。价格变化只比较交易价格；总回报还包括分配等投资收入，须注明分配与再投资约定。持有人的实际收益还受买卖时点、现金流、费用和税费影响，不等同于基金公布的标准化回报。",
     "conditions": "此处仅解释通用概念，不声明任何基金的净值、分红、持仓或费率。净值比较须取得同份额、同估值时点和币种的数据；总回报及实际收益另需相应现金流和成本依据，不能重复扣除已体现在净值中的费用。",
     "counterexample": "两个未复权收盘价格可以用于相应区间的价格变化，不能单独证明净值收益、总回报、事件因果或下一交易日涨跌概率；公司行为、非同步估值和交易成本可能改变解释。",
     "source": "SEC Investor.gov，Updated Investor Bulletin: Exchange-Traded Funds；FINRA，Key Concepts: Return and Rate of Return",
     "source_reference": [
         {"url": "https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-24", "sections": ["Market Prices versus NAV", "Lower fees", "Bid-ask spread"]},
         {"url": "https://syndication.finra.org/content/key-concepts-return-and-rate-return", "sections": ["What's Return?", "Rate of Return"]}],
     "temporal_scope": "事后通用概念说明，不证明历史时点可得的主体事实或境内基金合同规则。",
     "publication_date": None, "publication_date_status": "不把知识整理时点认作原始公开时点"}

ENTRIES = [
    {"id": "rh_k_management_custody_fee", "version": 1, "type": "方法", "state": "已发布",
     "title": "年度管理托管费率比较", "scope": ["ETF", "混合"], "implementation": "management_custody_fee_comparison_v1",
     "content": "分别读取两基金对应份额的管理费与托管费年费率。以A两项合计减B两项合计得到百分点差；合计是登记计算结果，不是新的原始披露。",
     "conditions": "四项原值、年费率、净资产计费基数、共同适用期间及份额身份均有已核验来源；管理费和托管费不能来自不同份额，费用减免或调整须明确实际适用规则。",
     "counterexample": "销售服务费、交易费用和其他开支未计入，本结果不是基金全部费用率；名义费率差不能直接解释为实际扣费或持有收益差。",
     "source": "ResearchHelper登记数学定义；https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/mutual-fund-and-etf-fees-and-expenses-investor-bulletin",
     "source_sections": ["The Prospectus Fee Table", "Other Expenses", "Mutual Fund Share Classes"],
     "publication_date": "2025-07-23", "source_role": "费用分类与份额核对的通用说明，不是境内基金规则",
     "temporal_scope": "数学定义可用于历史复核；本次合同、份额与适用费率仍由任务证据核验。"},
    {"id": "rh_k_cost_pass", "version": 1, "type": "机制", "state": "已发布", "title": "价格传导与成本抵消",
     "scope": ["个股", "行业", "产业链", "事件"], "content": "售价、采购成本、加工费、销量和套保分别核验。商品上涨不证明所有环节利润同向改善。",
     "source": "ResearchHelper方法规范1.0，合成验收方法说明", "conditions": "已确认业务环节与成本暴露", "counterexample": "加工费下降抵消原料涨价"},
    {"id": "rh_k_fee", "version": 1, "type": "指标", "state": "已发布", "title": "同口径年度费用",
     "scope": ["ETF"], "content": "管理费与托管费相加按年度费率比较；不将费率差直接解释为实际收益差。",
     "source": "ResearchHelper方法规范1.0，合成验收方法说明", "conditions": "共同期间、币种与产品规则可比", "counterexample": "跟踪、交易成本或分红口径不同"},
    {"id": "rh_k_residual", "version": 1, "type": "方法", "state": "已发布", "title": "剩余现金流现值",
     "scope": ["个股"], "content": "核验收款、优先索偿、费用、股本与分配后，用事先指定必要回报率折现。",
     "source": "prd.md第7.5节合成验收规则", "conditions": "无遗漏义务，现金分配规则已核验", "counterexample": "履约风险或存在其他优先权益", "implementation": "residual_present_value_v1"},
    {"id": "rh_k_fact", "version": 1, "type": "概念", "state": "已发布", "title": "披露、预测与因果识别",
     "scope": ["个股", "ETF", "指数", "行业", "产业链", "事件", "宏观", "混合"],
     "content": "事件原文、预测与已实现事实分开。同期上涨不是因果证明；相反渠道充分评估后仍可回答无法识别净方向。",
     "source": "ResearchHelper研究核验规范1.0", "conditions": "证据在截止时点前公开且适用", "counterexample": "缺少原文或同期因素属于执行缺口"},
]

# These are mathematical methods, not foreign-market rules or task facts.
# Existing published entries remain unchanged; indicator and method are distinct.
ENTRIES += [
    {"id": "rh_k_daily_nav_premium", "version": 1, "type": "方法", "state": "已发布", "access": "public",
     "title": "收盘价与同估值日净值偏离率", "scope": ["ETF", "混合"], "implementation": "etf_daily_nav_premium_v1",
     "aliases": ["净值", "收盘折溢价", "估值日", "偏离率"],
     "content": "明确同一基金份额、人民币和估值日，以收盘价除以该估值日官方单位净值减一再乘100，描述日度事后偏离。正值为溢价，负值为折价，不表示两者在同一瞬间可成交。",
     "source": "ResearchHelper明确数学定义；SEC Investor.gov，Updated Investor Bulletin: Exchange-Traded Funds (ETFs)。仅概念依据，不替代境内基金合同或标的原件。",
     "source_reference": {"url": "https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-24",
         "publisher": "SEC Investor.gov", "publication_date": "2023-02-23", "sections": ["How do ETFs work?", "Before you invest in an ETF"],
         "summary_status": "一般概念及本项目明确计算口径，不证明当前数据、境内规则或套利可行性"},
     "conditions": "两项原值的份额、币种及估值日分别有依据；官方净值已经取得并通过相应用途核验，不使用IOPV、昨日或最近净值代替。",
     "counterexample": "同日期不保证跨市场同一时刻；事后公布净值不能证明当时可交易或存在套利，不把缺少时钟信息补成15时。"},
    {"id": "rh_k_fee_difference", "version": 1, "type": "方法", "state": "已发布", "title": "同口径年度费用差",
     "scope": ["ETF", "混合"], "implementation": "fee_difference_v1",
     "content": "比较已核验、同适用期间的两项年度费用率，以费率A减费率B得到百分点差，不预测实际收益。",
     "source": "ResearchHelper登记计算定义；https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/mutual-fund-and-etf-fees-and-expenses-investor-bulletin",
     "conditions": "年度费用定义及期间可比，输入以%/年披露", "counterexample": "一项仅管理费，另一项含其他费用；费用较低不等于收益较高"},
    {"id": "rh_k_price_change", "version": 1, "type": "方法", "state": "已发布", "title": "已观测区间价格变化",
     "scope": ["个股", "ETF", "事件", "行业", "产业链", "混合"], "implementation": "observed_price_return_v1",
     "content": "同一证券未复权收盘价的百分比变化为终点价除以起点价减一再乘100；不包含分红再投资。",
     "source": "ResearchHelper明确数学定义；https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-24",
     "conditions": "同证券、人民币、未复权价格及明确起终交易日，原值和口径有来源；不适用于以点计价的指数",
     "counterexample": "份额拆并引起价格机械变化；未复权价格变化不代表总收益或事件因果"},
    {"id": "rh_k_nav_premium", "version": 1, "type": "方法", "state": "已发布", "title": "同估值时点ETF净值折溢价",
     "scope": ["ETF", "混合"], "implementation": "etf_nav_premium_v1",
     "content": "同类一份ETF的收盘价除以同估值时点官方单位净值减一再乘100，正值为溢价，负值为折价。",
     "source": "ResearchHelper明确数学定义；https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-24",
     "conditions": "同基金份额类别、人民币及估值时点；官方净值已在研究截止前公布", "counterexample": "盘中价格搭配昨日净值、IOPV冒充官方净值；折价不等于可实现套利"},
    {"id": "rh_k_gross_margin", "version": 1, "type": "方法", "state": "已发布", "title": "同口径营业毛利率",
     "scope": ["个股", "行业", "产业链", "事件", "混合"], "implementation": "gross_margin_v1",
     "content": "营业收入减营业成本后除以营业收入再乘100；收入须正，成本非负。允许亏损毛利率，不补零。",
     "source": "ResearchHelper明确数学定义；https://www.sec.gov/about/reports-publications/beginners-guide-financial-statements",
     "conditions": "同主体、同报告期间、同会计准则和合并范围的人民币数据；统一百万元单位", "counterexample": "营业总成本不等于营业成本；母公司口径不与合并口径相减"},
]

# Add only reusable questions with explicit validation paths, not empty sectors.
_DOMAIN_COMMON = {
    "version": 1, "state": "已发布", "access": "public", "knowledge_role": "general_principle",
    "revision_reason": "新增有来源的通用研究核验框架，不修订既有八条定义", "previous_version": None,
    "dependencies": [{"id": "rh_k_fact", "version": 1, "relation": "依赖事实与预测区分"}],
    "temporal_scope": "通用原理可作事后复核框架；不是历史当时规则、当前主体事实或已验证方向。",
    "publication_date": None, "publication_date_status": "来源发布日期未知，不推断首次公开时间",
    "maintenance": "项目维护版本1；正文为项目概括，运行任务不能自动发布或修改。",
}
_SEC = "https://www.sec.gov/about/reports-publications/beginners-guide-financial-statements"
_ECB = "https://www.ecb.europa.eu/mopo/intro/transmission/html/index.en.html"
_INDEX = "https://www.investor.gov/introduction-investing/investing-basics/investment-products/mutual-funds-and-exchange-traded-4"
_DOMAIN_ENTRIES = [
    {"id": "rh_k_cash_profit", "type": "机制", "title": "利润、现金流与再投资约束",
     "scope": ["个股", "行业", "产业链", "事件", "混合"], "domains": ["公司经营指标", "现金流"],
     "aliases": ["营运资金", "资本开支", "现金流", "现金转换"],
     "content": "利润按权责发生口径确认，经营现金流反映经营收付，两者不能互相替代。核查应收、存货、应付与预收的变化及非现金项目，再区分投资和融资现金流。资本开支消耗现金但不等于当期全额费用；经营现金流减资本开支也不是无条件可分配给股东的金额。",
     "conditions": "同主体、同期间、同合并范围；现金流分类与附注一致。资本开支口径明确，区分维持与扩张所需投入；债务偿付、租赁和其他现金义务另核查。金融企业或并购导致分类变化时不能直接套用一般企业现金转换解释。",
     "channels": ["销售形成利润但收款滞后，应收增长可占用现金。", "扩张备货占用现金；去库存或供应商账期延长可能暂时改善现金。", "折旧是非现金费用，但资产更新仍可能需要现金投入。"],
     "counterexample": "经营现金流改善可能来自应付拖延或存货收缩，不证明需求增长；预收款改善现金而收入尚未确认。资本开支上升可能支持未来产能，也可能反映低回报扩张，不能预设净价值方向。",
     "verification_indicators": ["利润与经营现金流调节表及非现金项目", "应收、存货、应付和合同负债的同口径变动", "资本开支、处置收入、并购现金和融资收付", "附注中的现金流分类、受限现金与持续义务"],
     "source": _SEC, "source_sections": ["Financial Statements", "Cash Flow Statements", "Read Footnotes"], "publisher": "U.S. Securities and Exchange Commission"},
    {"id": "rh_k_rate_transmission", "type": "机制", "title": "利率变化的多渠道传导",
     "scope": ["宏观", "个股", "行业", "产业链", "事件", "ETF", "指数", "混合"], "domains": ["宏观传导"],
     "aliases": ["利率", "降息", "加息", "货币政策", "信贷", "融资成本"],
     "content": "政策利率通过市场利率、预期、资产价格、银行贷款和总需求等渠道传导，时滞和强度依赖金融结构及经济状态。融资成本下降可能支持支出，但政策变化也可能反映增长走弱；估值折现、现金流预期和风险溢价须分开分析。",
     "conditions": "先区分政策意外与已定价预期、名义与实际利率、贷款供给与需求；明确市场、币种、期限、固定或浮动利率负债及重定价时点。ECB框架是一般机制来源，不证明其他经济体当期制度或传导参数相同。",
     "channels": ["融资利率及重定价影响利息负担和投资消费。", "预期及风险溢价影响长端利率和资产价格，可能抵消短端变化。", "银行资本、信用风险和借款需求影响贷款扩张，政策宽松不保证信贷增长。"],
     "counterexample": "降息伴随衰退预期恶化时盈利预期下降可超过折现率改善；高信用风险或低融资需求使利率向贷款传导减弱。银行资产负债重定价不同可能压缩净息差。",
     "verification_indicators": ["政策公告与预期差的可得时点", "收益率曲线、实际利率和信用利差", "贷款利率、信贷量及需求调查", "对象债务期限、浮动利率比例和盈利需求暴露"],
     "source": _ECB, "source_sections": ["Transmission mechanism of monetary policy", "Interest rates, expectations, asset prices, lending and demand channels"], "publisher": "European Central Bank"},
    {"id": "rh_k_index_structure", "type": "概念", "title": "指数权重、贡献与市场宽度",
     "scope": ["指数", "ETF", "宏观", "事件", "混合"], "domains": ["指数与ETF规则"],
     "aliases": ["指数", "权重", "成分", "宽度", "市值加权", "等权"],
     "content": "指数汇总结果取决于成分范围、加权与调整规则，不能将指数涨幅等同于多数成分上涨。贡献分析需区间内适用权重和一致收益口径，市场宽度另核查上涨覆盖、分布与样本变化。本条只给核验框架，不登记未实现的贡献算法。",
     "conditions": "取得指数编制机构对应版本的方法与当期成分权重；区分价格和总收益、自由流通调整、换样、权重漂移及公司行为。宽度的分母、缺失、停牌和退市处理须预先说明，不用今天成分回填历史。",
     "channels": ["权重集中可使少数成分主导指数变化。", "等权与市值加权观察角度不同，不能不加说明混比。", "调整与存续样本变化影响区间覆盖，需保存历史适用版本。"],
     "counterexample": "大权重上涨可同时伴随多数成分下跌；期末权重只能描述期末暴露，不等于真实区间贡献。新纳入成分的历史表现不能自动算入当期指数。",
     "verification_indicators": ["编制方法版本和调整公告", "区间适用成分与权重", "同口径成分收益及上涨下跌覆盖", "集中度、停牌缺失与换样解释"],
     "source": _INDEX, "source_sections": ["Index Funds", "Index weighting"], "publisher": "U.S. Securities and Exchange Commission / Investor.gov"},
    {"id": "rh_k_etf_exposure", "type": "机制", "title": "ETF实际暴露与跟踪差异",
     "scope": ["ETF", "指数", "事件", "混合"], "domains": ["指数与ETF规则"],
     "aliases": ["ETF", "etf", "基金", "跟踪", "持仓", "暴露", "复制", "跟踪差异"],
     "content": "ETF名称或主题标签不证明实际持仓与观点暴露。区分基准指数、基金组合、净值收益和交易价格；跟踪差异需在共同期间、币种和分红口径下核验。指数基金可能采用复制或抽样，成本和实施差异影响跟踪，不能只按费率推断表现。",
     "conditions": "核对基金文件、基准方法、份额类别、组合披露时点和复制方式；持仓公开滞后时不冒充实时完整组合。境内外交易时段、汇率、现金仓位与费用口径需一致；没有对应跟踪计算实现时只作定性核验，不临时发明算法。",
     "channels": ["持仓和权重决定实际暴露，基准与组合可能不完全相同。", "费用、现金拖累与复制实施影响净值相对基准表现。", "市场交易价格与净值属于不同观测口径，非同步报价可能造成表观折溢价。"],
     "counterexample": "低费率基金可能因跟踪实施、交易或汇率差异表现不同；主题名称相同的基金可能持仓差异很大。公开旧持仓不能证明当前受益，折价不保证可实现套利。",
     "verification_indicators": ["基准标识、方法版本和基金合同", "持仓披露日期、权重覆盖与抽样复制说明", "共同期间净值与基准收益及分红口径", "费用、现金仓位、汇率和非同步交易限制"],
     "source": _INDEX, "source_sections": ["Index Funds", "Tracking and fund expenses"], "publisher": "U.S. Securities and Exchange Commission / Investor.gov"},
]
for _entry in _DOMAIN_ENTRIES:
    ENTRIES.append(dict(copy.deepcopy(_DOMAIN_COMMON), **_entry,
        source_reference={"url": _entry["source"], "publisher": _entry["publisher"], "sections": _entry["source_sections"],
                          "source_version": "网页版本未标明；按定位章节概括", "publication_date": None,
                          "summary_status": "项目自行概括，非原文引用或当前市场事实"}))

# Retain prior definitions when a maintenance revision is added. A released
# revision must be added here rather than erasing the old content.
ENTRIES += [
    {"id": "rh_k_net_margin", "version": 1, "type": "方法", "state": "已发布", "title": "同口径净利润率",
     "scope": ["个股", "行业", "产业链", "事件", "混合"], "implementation": "net_profit_margin_v1",
     "content": "报表净利润除以同口径营业收入再乘100，收入须正，保留负净利润率。合并净利润包含少数股东份额，不用归母或扣非口径替代。",
     "source": "ResearchHelper明确数学定义；" + _SEC, "source_sections": ["Income Statements", "Read the Footnotes"],
     "conditions": "同主体、期间、合并范围及会计准则的人民币数据，统一百万元。指标名称、数值与口径逐项定位；净利润率不直接证明核心业务盈利。",
     "counterexample": "处置收益可能抬高净利润率而主营经营未改善；季节性、税率与少数股东份额不同不能忽略。行业经营口径不同不作机械排名。",
     "aliases": ["净利润率", "净利率", "净利润", "盈利", "亏损"],
     "dependencies": [{"id": "rh_k_cash_profit", "version": 1, "relation": "利润与现金区分"}]},
    {"id": "rh_k_cash_profit_ratio", "version": 1, "type": "方法", "state": "已发布", "title": "经营现金流净利润比",
     "scope": ["个股", "行业", "产业链", "事件", "混合"], "implementation": "operating_cash_profit_ratio_v1",
     "content": "经营活动产生的现金流量净额除以同口径正净利润再乘100，描述经营现金与利润的关系；经营现金流可负，净利润为零或负时不计算本比率，保留两项原值。",
     "source": "ResearchHelper明确数学定义；" + _SEC, "source_sections": ["Cash Flow Statements", "Operating Activities", "Read the Footnotes"],
     "conditions": "同主体、期间、合并范围和会计准则，人民币百万元；现金流表净额与利润表净利润分别定位。不要使用归母、扣非净利润或现金流入小计。",
     "counterexample": "预收或延迟支付可能暂时提高比例，应收和存货增长可能压低比例。亏损或很小的正利润使比率不可用或极端；超过100%不证明好，低于100%不证明坏。金融行业现金流分类与非金融企业不同。",
     "aliases": ["现金流", "经营现金", "现金兑现", "现金转换", "盈利质量", "净利润"],
     "dependencies": [{"id": "rh_k_cash_profit", "version": 1, "relation": "检查营运资金与再投资约束"}]},
]
# Bank-specific definitions are explanatory knowledge, never bank facts or
# executable sensitivity models. Keep their original source versions explicit.
_BANK_SOURCES = {
    "guidance": "https://www.bis.org/committees/bcbs/basel-framework/standard/srp/98/inforce/2019-12-15/published/2019-12-15",
    "margin": "https://www.federalreserve.gov/publications/2023-may-supervision-and-regulation-report-banking-system-conditions.htm",
}
ENTRIES += [
    dict(copy.deepcopy(_DOMAIN_COMMON), id="rh_k_bank_interest_metrics", type="指标",
         title="银行净利息收入与净息差口径", scope=["个股", "行业", "事件", "宏观", "混合"],
         domains=["公司经营指标", "银行"], aliases=["净息差", "净利息收入", "净利差"],
         content="净利息收入是利息收入减利息支出的金额；净息差通常为年化净利息收入除以平均生息资产。披露中的净利差若定义为生息资产收益率减计息负债成本率，其分母不同，不能与净息差互换。",
         conditions="核对主体、合并范围、币种、期间、平均余额及年化规则，采用对应披露的定义；美国监管统计定义不能代替境内银行附注。期末资产不自动代表平均生息资产。",
         channels=["规模扩大可使净利息收入增长而净息差下降。", "非计息资金和资产负债结构可使净息差与净利差不同。"],
         counterexample="利息收入增长不证明净利息收入增长；净息差改善不证明净利润改善。不能以普通企业营业毛利率替代银行净息差。",
         verification_indicators=["利息收入与利息支出的报表项目及附注", "平均生息资产、计息负债和相应收益率成本率", "披露期间、年化规则及合并范围"],
         source=_BANK_SOURCES["margin"], publisher="Board of Governors of the Federal Reserve System",
         source_sections=["Figure 3. Year-over-year change in net interest margin components", "Figure 3 note: annualized net interest income / average interest-earning assets"],
         source_reference={"url": _BANK_SOURCES["margin"], "publisher": "Board of Governors of the Federal Reserve System",
             "sections": ["Figure 3 and its note"], "source_version": "May 2023 report", "publication_date": None,
             "summary_status": "项目口径说明；未采用报告中的美国银行当期数值"},
         revision_reason="新增银行指标辨析，解释口径，不登记自动计算实现"),
    dict(copy.deepcopy(_DOMAIN_COMMON), id="rh_k_bank_rate_repricing", type="机制",
         title="银行资产负债重定价与利率传导", scope=["个股", "行业", "事件", "宏观", "混合"],
         domains=["宏观传导", "银行"], aliases=["银行", "净息差", "净利息收入", "重定价", "LPR", "贷款", "存款"],
         retrieval_requirements=[["银行", "净息差", "净利息收入"], ["利率", "LPR", "重定价", "贷款", "存款", "净息差", "净利息收入"]],
         content="利率影响取决于资产、负债和表外敞口的重定价期限、基准和客户行为。分别核对贷款收益变化、资金成本变化及套期影响；资产端与负债端变化不必同步，不预设净方向。",
         conditions="先明确对象及观察期限，再核对相关基准、固定浮动结构、重定价窗口、提前还款和存款行为。对LPR事件，实际合同挂钩关系及执行利率须另取证；事件公告不证明银行全部贷款立即同幅变化。BIS框架仅提供机制，不替代境内制度或本期经营事实。",
         channels=["资产更快向下重定价而资金成本暂时不变，可压缩净利息收入。", "资金成本更快下降而资产收益暂时稳定，可形成相反影响。", "基准不同、客户提前还款、存款迁移及套期可改变上述影响；方向与强度依赖本次证据。"],
         counterexample="只有利率公告而无资产负债敞口，不能判断某家银行净息差方向。债券经济价值变化、净利息收入变化与会计利润是不同结果；规模变化亦可使金额与比率不同向。",
         verification_indicators=["贷款与资金的实际利率、基准、金额和重定价期限", "固定浮动结构、存款结构及客户提前还款", "利率敏感性附注的期限、情景、资产负债假设和套期口径", "同期净利息收入、平均生息资产和净息差定义"],
         dependencies=[{"id": "rh_k_fact", "version": 1, "relation": "事件与经营兑现分开"},
                       {"id": "rh_k_bank_interest_metrics", "version": 1, "relation": "金额、比率与分母辨析"}],
         source=_BANK_SOURCES["guidance"], publisher="Basel Committee on Banking Supervision / BIS",
         source_sections=["SRP98.15–98.16", "SRP98.19–98.25", "SRP98.30–98.38"],
         source_reference={"url": _BANK_SOURCES["guidance"], "publisher": "Basel Committee on Banking Supervision / BIS",
             "sections": ["SRP98.15–98.16", "SRP98.19–98.25", "SRP98.30–98.38"],
             "source_version": "2019-12-15 source version; no later shock parameters adopted",
             "publication_date": None, "summary_status": "项目机制概括；非境内监管参数、当前银行事实或原文摘录"},
         revision_reason="针对LPR与银行研究增加有来源的双向重定价核验，不设默认利好或自动敏感性计算"),
]
ENTRIES.append(dict(copy.deepcopy(_DOMAIN_COMMON), id="rh_k_index_change", type="方法",
    title="已观测指数区间变化", scope=["指数", "ETF", "事件", "行业", "宏观", "混合"],
    implementation="observed_index_change_v1", domains=["指数与ETF规则"], aliases=["指数点位", "指数涨跌", "指数区间", "指数收益"],
    content="同版指数终点收盘点位除以起点收盘点位减一，再乘100得到区间变化。价格指数、全收益指数和净全收益指数分别核验，不跨版本相除。",
    conditions="起点为正、终点非负；两端原值、日期、指数身份、计价币种、收益版本与共同基期均有来源。只计算已观测窗口，不补造交易日或指数编制规则。",
    verification_indicators=["指数身份与收益版本", "计价币种及共同基期", "起止观测日、收盘点位及原始单位", "原件公开时间和已实现事实性质"],
    counterexample="两个点位来自不同基期或币种不能相除；价格指数变化不等于含分红收益，指数变化不等于ETF实际收益。基期重设须有已衔接序列，本方法不自行复原。",
    source="ResearchHelper明确数学定义；https://www.msci.com/indexes/index-education/how-is-an-index-constructed",
    publisher="MSCI", source_sections=["Calculating index value and returns"],
    source_reference={"url": "https://www.msci.com/indexes/index-education/how-is-an-index-constructed", "publisher": "MSCI",
        "sections": ["Calculating index value and returns"], "source_version": "网页版本未标明，按定位章节核对",
        "publication_date": None, "summary_status": "项目数学定义及收益版本辨析，不复制当前指数值或编制参数"},
    revision_reason="为指数点位登记独立已观测变化方法，不复用人民币股价口径"))
# A concept is not its arithmetic implementation. Keep the old method and
# adopted versions unchanged, and retain the existing ETF catalogue position.
ENTRIES.append(dict(copy.deepcopy(_DOMAIN_COMMON), id='rh_k_price_return_measures', type='概念',
    knowledge_role=GENERAL_DEFINITION_SCOPE, title='价格变化与总收益口径',
    scope=['个股', 'ETF', '指数', '行业', '产业链', '事件', '宏观', '混合'],
    aliases=['未复权', '含分红', '总收益', '分红再投资'],
    content='未复权价格变化比较相应证券的两个交易价格，不包含期间分红等现金分配。总收益还计入投资期间的分配等收入；若采用分红再投资口径，须明确再投资时间、价格和份额处理。价格涨跌与含分红总收益不等同，税费和交易成本对实际持有收益另行核验。',
    conditions='本条只解释通用概念，不提供任何证券、基金或指数的实际数值。计算需另取同一对象、可比币种、明确交易日和公司行为口径的原值；指数还须核验价格、全收益或净全收益版本，不能由证券价格公式替代指数规则。',
    counterexample='只有未复权起止收盘价，不能据此确认总收益、没有公司行为、投资价值或事件因果；缺少分红资料不等于没有分红。',
    verification_indicators=['价格与收入组成分别核查', '公司行为、分配和再投资约定', '费用、税费与实际现金流另核查'],
    source='FINRA，Key Concepts: Return and Rate of Return；ResearchHelper通用口径概括',
    publisher='FINRA', source_sections=["What’s Return?", 'Rate of Return'],
    source_reference={'url': 'https://syndication.finra.org/content/key-concepts-return-and-rate-return',
        'publisher': 'FINRA', 'sections': ["What’s Return?", 'Rate of Return'],
        'summary_status': '通用概念概括，不是境内产品合同或任何当前市场事实'},
    revision_reason='将通用收益概念与既有价格计算方法分开，禁止方法冒充定义或批准主体事实'))
ENTRIES.append(dict(copy.deepcopy(_DOMAIN_COMMON), id='rh_k_etf_premium_measures', type='概念',
    knowledge_role=GENERAL_DEFINITION_SCOPE, title='ETF折溢价与套利条件', scope=['ETF', '混合'],
    aliases=['折溢价', '套利', '收盘折溢价', 'IOPV'],
    content='ETF市场价格与基金份额净值可能不同。收盘折溢价是相应收盘价格与可比官方单位净值的事后比较，不是已经实现的套利收益。盘中份额参考净值IOPV与官方单位净值分开使用，不能互相替换。是否能执行套利，还需核验实际申赎清单、最小申赎单位、申赎额度、现金替代规则、交易费用、冲击成本及各环节能否成交。',
    conditions='仅解释通用口径与待核验条件，不声明任何基金的价格、净值、份额类别或申赎状态。实际比较仍须核验同份额、币种、估值基准和时间可比性；公布日期与净值所属日分别记录，不以事后公布净值证明当时可交易。境外投教资料只说明概念，不替代境内产品合同和现行规则。',
    counterexample='即使存在价格偏离，也不能省略成本、申赎和成交条件，直接认定套利利润或买入机会。旧净值、跨市场非同步价格、IOPV和未知份额类别不能作为同口径官方净值比较的替代。',
    verification_indicators=['交易价格、官方净值与IOPV分别识别', '份额、币种、估值基准及所属时点核对',
        '申赎清单、额度、现金替代及成交条件另取证', '费用与冲击成本不省略'],
    source='上海证券交易所，ETF瞬时套利策略；SEC Investor.gov，Updated Investor Bulletin: Exchange-Traded Funds (ETFs)。ResearchHelper通用概念概括。',
    publisher='上海证券交易所', source_sections=['ETF瞬时套利策略'],
    source_reference={'url': 'https://etf.sse.com.cn/fund/learning/strategy/c/5704303.shtml',
        'publisher': '上海证券交易所', 'sections': ['ETF瞬时套利策略'], 'publication_date': '2022-06-23',
        'summary_status': '投教概念及核验问题概括，不作为当前产品规则或任何标的事实'},
    additional_source_references=[{'url': 'https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-24',
        'publisher': 'SEC Investor.gov', 'sections': ['How are ETFs different from mutual funds?', 'Before you invest in an ETF'],
        'publication_date': '2023-02-23', 'summary_status': '仅一般概念，不据此确认境内申赎资格或规则'}],
    revision_reason='单独发布折溢价解释概念，与既有净值计算方法分开；不把来源说明或价格偏离当作套利批准'))
ENTRIES.append(copy.deepcopy(_ETF_MEASURES_V1))
ENTRIES.append({
    'id': 'rh_k_pmi_diffusion', 'version': 1, 'type': '概念', 'state': '已发布', 'access': 'public',
    'catalogue_introduced': 2, 'knowledge_role': GENERAL_DEFINITION_SCOPE,
    'title': 'PMI扩散指数与供需解释边界', 'scope': ['宏观', '行业', '混合'],
    'aliases': ['PMI', '采购经理指数', '扩散指数'],
    'content': '采购经理调查的分类指数按企业回答方向计算：正向回答的企业占比，加上回答不变占比的一半。'
        '制造业PMI由新订单、生产、从业人员、供应商配送时间和原材料库存分类指数加权合成，配送时间在合成时反向处理。'
        '生产和新订单指数反映被调查企业相对上月的变化方向分布，不是实际产量或订单金额的增长率。',
    'conditions': '用于解释国家统计局采购经理调查的通用计量含义。实际月份、调查范围、季调、分类和原值另按本任务原件核验；'
        '不将此说明直接套用于其他机构或未核实的调查版本。',
    'counterexample': '生产指数高于新订单指数不能直接量化实际供需缺口，也不足以断言库存已累积、企业盈利改善、证券收益或事件因果。'
        '可以在明确假设下提出待验证的风险，不将情景写成实际结果。'
        '阈值、合成权重或指数变动如需在正文列数字，仍须按本次实际依据或已登记计算绑定，不能把概念说明当作主体原值。',
    'reasoning_checks': [
        '指数水平、原文披露的变化和研究者推断分开，不能将扩散指数当成产量或订单金额增长率。',
        '供需与风险解释保持条件性，比较指数不能独立证明实际缺口、库存累积或排他因果。',
        '本任务未采用某条依据，不等于文件、机构或全渠道未披露；核对已取得原件及实际阅读覆盖。'],
    'source': '国家统计局，2026年9月中国采购经理指数运行情况，附注计算方法。通用概念整理，不是本次指标原值。',
    'source_reference': {'url': 'https://www.stats.gov.cn/sj/zxfbhjd/202609/t20260930_1965449.html',
        'publisher': '国家统计局', 'sections': ['4.计算方法', '（1）分类指数的计算方法', '（2）制造业PMI指数的计算方法'],
        'snapshot_sha256': 'ccfc89f7b260073868a4fa8dce49004378d443bd8bb25d641c9f07a208647d2e',
        'source_role': '已留存官方原件的通用方法说明，不确认历史首次公开或任意主体事实'},
    'temporal_scope': '事后通用概念说明，不证明历史版本或当时信息集；当次调查与观测仍由任务证据核验。',
})
RETAINED_VERSIONS = {(item["id"], item["version"]): copy.deepcopy(item) for item in ENTRIES}

# A maintained concept revision, not a task-derived entity fact. Retain v1.
_tracking = next(item for item in ENTRIES if item['id'] == 'rh_k_etf_exposure')
_tracking.update(version=2, previous_version=1,
    revision_reason='区分业绩比较参照与实际跟踪目标，并为取证阶段提供核验要点',
    content=_tracking['content']+'业绩比较基准用于绩效参照，不单独证明基金实际跟踪的标的指数。',
    extraction_checks=[
        '核对跟踪指数时，选择投资范围、投资目标或策略中明确标的指数名称的原文；不能只用业绩比较基准、基金名称或代码替代。',
        '其他同主体已读文件可以提供直接陈述，但分别保留文件日期与适用范围，不推定旧规则一直未变；缺少代码不从记忆补造。',
        '按实际问题选择相应段落。知识只解释概念，不能代替本基金原件、金额、持仓或历史可得性。'],
    additional_source_reference=dict(
        url='https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/updated-investor-bulletin-how-read-mutual-fund-or-etf-shareholder-report',
        publisher='U.S. Securities and Exchange Commission / Investor.gov',
        sections=['Performance information'], publication_date='2024-01-19',
        summary_status='结合指数基金跟踪定义整理的概念核验规则，不是境内合同规则或主体事实'))
RETAINED_VERSIONS[(_tracking['id'], 2)] = copy.deepcopy(_tracking)
for _knowledge_id in ('rh_k_gross_margin', 'rh_k_net_margin', 'rh_k_cash_profit_ratio'):
    _current_method = next(item for item in ENTRIES if item['id'] == _knowledge_id)
    _current_method.update(version=2, previous_version=1,
        revision_reason='登记已核验原件同单位金额表示，公式与金融含义不变，旧任务保留锁定版本',
        amount_units=['元', '万元', '百万元'])
    _current_method['conditions'] = (_current_method['conditions'].replace('统一百万元单位', '使用相同的已核验金额单位')
        .replace('统一百万元', '使用相同的已核验金额单位').replace('人民币百万元', '同单位人民币金额')
        + '可显式选择元、万元或百万元，全部输入须与amount_unit一致；不跨单位相减或相除，不在方法中转换金额。')
    RETAINED_VERSIONS[(_knowledge_id, 2)] = copy.deepcopy(_current_method)

# Classifications and attribution targets are different questions. Maintain a
# general revision, not issuer-specific facts learned from a research task.
_cash_profit = next(item for item in ENTRIES if item['id'] == 'rh_k_cash_profit')
_cash_profit.update(version=2, previous_version=1,
    revision_reason='区分现金流构成、同比变动和同期间利润调节，核对金融资产负债与合同负债，不改变计算或历史知识包。',
    content=_cash_profit['content'] +
        '解释现金流时先明确对象：本期现金流的收付构成、现金流相对上期的变动、同期间净利润到经营现金流的调节是三种不同分析。'
        '直接法收付明细与间接法调节项目可能描述同一经济变动，不能把两者重复相加。同比原因也不能直接当作本期现金流高于利润的贡献。'
        '在金融企业报表中，存放同业款项属于资产，吸收存款以及同业及其他金融机构存放款项属于负债，须辨清资金方向和实际科目。'
        '应收款项是收取对价的权利；合同负债是已收或应收客户对价而应转让商品的义务，不是经营性应收项目。',
    conditions=_cash_profit['conditions'] +
        '归因须与实际披露的期间、比较基准及调节对象一致。仅期初期末余额不能排除非现金变动、重分类或合并范围变化，不能直接等同现金流。'
        '金融子公司业务不属于集团主要产品销售，不等于其披露的经营活动现金流应改列筹资活动；按实际报表与适用准则核对。',
    extraction_checks=[
        '按原问题选择直接法收付明细、净利润调节表或同比说明，逐项保留各自期间和比较对象；不同分析口径不能互相替代。',
        '金融子公司存放同业与吸收存款分别核对资产、负债方向和现金流原分类，不仅凭存款二字归为同类。',
        '应收、存货、应付、合同负债分别定位；没有科目流转或重分类依据，不把合同负债下降解释成经营性应收减少的来源。'],
    reasoning_checks=[
        '判断经营现金流高于利润的原因，应依据同期间净利润调节表；同比增长说明只解释其实际同比对象，不自动证明现金利润差额的主要贡献。',
        '收付构成与间接法调节有重叠时不能重复计入；没有登记计算，不自行相加、相减或推导贡献率。',
        '余额下降、收付方向和经济原因分开。未取得组成或科目流转依据时保留未知，不能列出与科目性质冲突的可能解释。',
        '分别评估来源披露、口径解释和主体结论；高现金流比率不自动说明主营回款改善、盈利持续或投资价值。'],
    source='财政部，企业会计准则第31号、2018年度金融企业财务报表格式及会计司收入准则列报答复；ResearchHelper通用机制概括。',
    publisher='中华人民共和国财政部',
    source_sections=['企业会计准则第31号第五条、第八至九条、第十六条'],
    source_reference=dict(
        url='https://kjs.mof.gov.cn/zt/kjzzss/kuaijizhunzeshishi/200806/t20080618_46250.htm',
        publisher='中华人民共和国财政部', sections=['第五条', '第八至九条', '第十六条'],
        publication_date=None, summary_status='通用现金流列报与调节框架，不证明主体事实、当前准则适用性或历史当时可得。'),
    additional_source_references=[dict(
        url='https://kjs.mof.gov.cn/gongzuotongzhi/201812/P020181227598151630019.pdf',
        publisher='中华人民共和国财政部', sections=['资产负债表资产与负债列', '合同资产和合同负债项目说明'],
        publication_date=None, summary_status='2018年度金融企业格式中的科目性质示例，不据此确认当前主体适用格式。'), dict(
        url='https://www.mof.gov.cn/bd/mhwzzxly/hjs/202301/t20230115_3863166.htm',
        publisher='中华人民共和国财政部会计司', sections=['收入准则第四十一条及同一合同列报答复'],
        publication_date=None, summary_status='区分应收权利、合同资产和合同负债；留言日期不作为答复首次公开时点。')])
RETAINED_VERSIONS[(_cash_profit['id'], 2)] = copy.deepcopy(_cash_profit)
for _knowledge_id in ('rh_k_net_margin', 'rh_k_cash_profit_ratio'):
    _current_method = next(item for item in ENTRIES if item['id'] == _knowledge_id)
    _current_method.update(version=3, previous_version=2,
        revision_reason='采用现金流机制v2依赖，金额单位、计算实现与适用条件不变。')
    for _dependency in _current_method['dependencies']:
        if _dependency['id'] == 'rh_k_cash_profit':
            _dependency['version'] = 2
    RETAINED_VERSIONS[(_knowledge_id, 3)] = copy.deepcopy(_current_method)

# Keep version 1 byte-for-byte available to adopted historical knowledge packs.
for _knowledge_id in ('rh_k_gross_margin', 'rh_k_net_margin', 'rh_k_cash_profit_ratio'):
    _current_method = next(item for item in ENTRIES if item['id'] == _knowledge_id)
    _prior_version = _current_method['version']
    _current_method.update(version=_prior_version+1, previous_version=_prior_version,
        revision_reason='补齐原件明确披露的千元、千万元和亿元；只允许同单位输入，不转换金额，算术实现不变。',
        amount_unit_contract_version=2, amount_units=list(FINANCIAL_AMOUNT_UNITS))
    _current_method['conditions'] = _current_method['conditions'].replace(
        '可显式选择元、万元或百万元', '可显式选择元、千元、万元、百万元、千万元或亿元')
    RETAINED_VERSIONS[(_knowledge_id, _current_method['version'])] = copy.deepcopy(_current_method)

# Keep version 1 byte-for-byte available to adopted historical knowledge packs.
# The arithmetic implementation is unchanged; version 2 clarifies permissible
# source representations and still requires independently anchored annual rates.
_fee_entry = next(item for item in ENTRIES if item['id'] == 'rh_k_management_custody_fee')
_fee_entry.update(version=2, previous_version=1,
    revision_reason='支持原件以%披露且年度频率已有独立核验的费率，不改写原值、单位或年化。',
    conditions=_fee_entry['conditions'] + '原件单位可为%/年或%；%不自动表示年度，须另核验年费率标签，不接收月费率、收益率或比例值。')
RETAINED_VERSIONS[(_fee_entry['id'], 2)] = copy.deepcopy(_fee_entry)

# A distinct method entry leaves adopted difference-only versions immutable.
_fee_summary = copy.deepcopy(_fee_entry)
for _field in ('previous_version', 'revision_reason'):
    _fee_summary.pop(_field, None)
_fee_summary.update(id='rh_k_management_custody_fee_summary', version=1,
    title='年度管理托管费率合计及比较', implementation='management_custody_fee_summary_v1',
    aliases=['管理费', '托管费', '费率合计'],
    content='分别核验两基金同份额、同适用期间的管理及托管年费率，登记计算两边两项合计，再计算A合计减B合计。两边合计单位为%/年，差值单位为百分点/年。合计与差都是计算输出，不作为原件披露值，不代表全部费用率或持有收益。')
ENTRIES.append(_fee_summary)
RETAINED_VERSIONS[(_fee_summary['id'], 1)] = copy.deepcopy(_fee_summary)

_single_fee = copy.deepcopy(next(item for item in ENTRIES if item['id'] == 'rh_k_fee_difference'))
_single_fee.update(id='rh_k_annual_fee_difference', version=1, implementation='fee_difference_v2',
    title='同口径单项年度费率差',
    content='同类单项费率A减费率B，输出百分点/年。管理费与托管费分别比较；不把分项相加、费率差或费用较低解释成实际持有收益。',
    conditions='分别核验基金、份额、共同适用期间、净资产计费基数及年度频率；原文单位为%时原样保留，不因年费率标签改写单位或年化。fee_kind明确选择两条原值实际一致的管理费或托管费。',
    counterexample='管理费与托管费互减；以年化换算替代原文年度频率；不同基数或期间；年度规则变化未核对。')
ENTRIES.append(_single_fee)
RETAINED_VERSIONS[(_single_fee['id'], 1)] = copy.deepcopy(_single_fee)

# Complete the public metadata in a new version, keeping adopted v1 packs
# readable. Unknown publication time never becomes a historical permission.
_etf_measures = next(item for item in ENTRIES if item['id'] == 'rh_k_etf_measures')
_etf_sources = copy.deepcopy(_etf_measures['source_reference'])
_etf_measures.update(version=2, previous_version=1, access='public',
    revision_reason='补齐来源定位、核验指标和依赖元数据，正文及金融用途边界不变。',
    publication_date_status='来源首次公开时点未知，不把知识整理时点认作原始公开时点',
    source_reference=dict(_etf_sources[0], publisher='SEC Investor.gov'),
    additional_source_references=[dict(_etf_sources[1], publisher='FINRA')],
    verification_indicators=['交易价格与官方单位净值的字段分别定位',
        '份额、币种及估值时点可比', '总回报的分配与再投资约定有依据', '实际持有收益的现金流、费用及税费另核验'],
    dependencies=[{'id': 'rh_k_fact', 'version': 1, 'relation': '区分通用定义与主体事实'}],
    temporal_scope='事后通用概念说明，不是历史当时规则，不证明历史时点可得的主体事实或境内基金合同规则。')
RETAINED_VERSIONS[(_etf_measures['id'], 2)] = copy.deepcopy(_etf_measures)



# Complete metadata in a new revision; adopted v1 concepts remain immutable.
_pmi = next(item for item in ENTRIES if item['id'] == 'rh_k_pmi_diffusion')
_pmi.update(version=2, previous_version=1,
    revision_reason='补齐首次公开时点状态、核验指标与依赖；概念正文及原有来源定位不变。',
    publication_date=None,
    publication_date_status='来源首次公开时点未知，不把知识整理时点认作原始公开时点',
    verification_indicators=copy.deepcopy(_pmi['reasoning_checks']),
    dependencies=[{'id':'rh_k_fact','version':1,'relation':'区分调查定义、实际观测与因果推断'}],
    temporal_scope='事后通用概念说明，不是历史当时规则，不证明历史版本或当时信息集；当次调查与观测仍由任务证据核验。')
RETAINED_VERSIONS[(_pmi['id'],2)] = copy.deepcopy(_pmi)


# New entries are confined to catalogue 3; adopted older catalogues stay closed.
_CPI_COMPILATION = 'https://www.stats.gov.cn/zs/tjws/zytjzbqs/jmxxggzs/202411/t20241127_1957589.html'
_CPI_CARRYOVER = 'https://www.stats.gov.cn/zs/tjws/tjbk/202301/t20230101_1912942.html'
_INFLATION = 'https://www.ecb.europa.eu/ecb-and-you/explainers/tell-me-more/html/what_is_inflation.en.html'
_MACRO_READING_ENTRIES = [
    dict(copy.deepcopy(_DOMAIN_COMMON), id='rh_k_cpi_reading', catalogue_introduced=3, type='指标', knowledge_role=GENERAL_DEFINITION_SCOPE,
         title='CPI读数、分项与比较基期', scope=['宏观','行业','事件','个股','ETF','指数','混合'],
         domains=['宏观传导'], aliases=['CPI','居民消费价格指数'],
         content='CPI反映消费商品和服务价格的综合变化；同比比较上年同月，环比比较上月。'
             '同比变化可能包含比较基期和本期价格变化的共同影响；分项影响取决于适用权数，不能只按涨跌幅大小排序贡献。',
         conditions='本条解释国家统计局CPI的一般概念，不提供本次权数、分解或任何月份原值；核对当期统计版本、地域、频率和比较基期。'
             '只有同比序列时仍可描述走势与提出假设，不定量拆分翘尾、新涨价或分项贡献。',
         channels=['同比分项分化可提示不同价格环节的研究方向，不自动识别需求或供给冲击。',
                   '判断趋势要核对整个区间；同比回升不必然伴随当月价格上涨。'],
         counterexample='食品同比走弱不证明全部消费价格下降；缺少分解时不能认定主导贡献，也不能因此否定一切价格传导。'
             '不同分类可能重叠，不能把食品、消费品等标签直接当成可相加的独立部分。',
         verification_indicators=['统计口径、月份与完整路径','适用分类及权数','原件明确的环比或贡献分解，按问题选用'],
         source='国家统计局，CPI的编制方法；什么是翘尾因素。项目概念概括。', publisher='国家统计局',
         source_reference=dict(url=_CPI_COMPILATION,publisher='国家统计局',sections=['二、篮子和权数的确定','六、CPI的计算过程'],
             publication_date=None,summary_status='已查阅的方法说明，未采用文中历史篮子和样本数量作为当前规则'),
         additional_source_references=[dict(url=_CPI_CARRYOVER,publisher='国家统计局',sections=['一、什么是翘尾因素','二、新涨价因素'],
             publication_date=None,summary_status='仅概念，不登记新的分解计算实现')],
         temporal_scope='事后通用概念说明，不是历史当时规则，不证明任意月份原值或历史信息集。'),
    dict(copy.deepcopy(_DOMAIN_COMMON), id='rh_k_inflation_transmission', catalogue_introduced=3, type='机制',
         title='物价变化的利率、利润与购买力传导', scope=['宏观','行业','产业链','事件','个股','ETF','指数','混合'],
         domains=['宏观传导'], aliases=['CPI','居民消费价格','通胀','物价','inflation'],
         retrieval_requirements=[['CPI','居民消费价格','通胀','物价','inflation'],
             ['投资','策略','资产','债券','股票','利润','消费','传导','investment','asset','bond','profit']],
         content='将价格观察分别连接利率定价、企业利润和居民购买力。以下是待验证的竞争路径，'
             '不是从CPI直接推导的资产涨跌结论；只选与原问题有关的路径，不机械凑齐三条。',
         conditions='先用实际价格证据描述变化，再写清对象、期限和成立条件。'
             'ECB只提供通用机制，不把欧元区目标或政策反应函数套入其他经济体；SEC仅提供利润表关系，不证明境内企业当期事实。',
         channels=['利率路径：若价格压力温和且政策、融资条件相应转松，折现与利率渠道可能改善；增长、信用和风险溢价变化可形成相反作用。',
                   '利润路径：若相关企业售价改善且销量、投入成本和费用没有抵消，利润可受益；消费价格不是企业收入、出厂价或投入成本的替代数据。',
                   '购买力路径：若居民名义收入稳定、实际消费篮子对应且预算约束有效，价格负担缓解可支持其他消费；收入走弱、储蓄倾向或其他成本上涨可抵消。'],
         counterexample='同一低通胀读数可同时伴随融资环境改善与需求走弱，不能预设资产净方向；'
             '单一分项价格下降可能影响买卖双方不同环节，不能宣布整个行业受益。',
         verification_indicators=['利率与预期、信用和风险溢价','对象售价、销量、成本及费用','居民收入、消费结构与支出反应'],
         dependencies=[dict(id='rh_k_fact',version=1,relation='观察、假设与因果分别表述'),
                       dict(id='rh_k_rate_transmission',version=1,relation='利率及相反渠道')],
         source='ECB，Transmission mechanism；What is inflation?；SEC，Beginners’ Guide to Financial Statements。项目条件性机制概括。',
         publisher='European Central Bank / U.S. Securities and Exchange Commission',
         source_reference=dict(url=_ECB,publisher='European Central Bank',sections=['Affects expectations','Affects asset prices','Leads to changes in aggregate demand and prices'],
             publication_date=None,summary_status='通用传导渠道，不采用区域政策目标、参数或当期事实'),
         additional_source_references=[dict(url=_INFLATION,publisher='European Central Bank',sections=['Broad increase in prices','Different people buy different things'],
             publication_date=None,summary_status='购买力与个人消费篮子的通用概念'),
             dict(url=_SEC,publisher='U.S. Securities and Exchange Commission',sections=['Income Statements'],
             publication_date=None,summary_status='利润由收入、成本及费用共同决定的通用框架')],
         temporal_scope='事后通用机制说明，不是历史当时规则，不证明当前政策、主体事实或可交易收益。'),
]
ENTRIES.extend(_MACRO_READING_ENTRIES)
RETAINED_VERSIONS.update({(item['id'],item['version']):copy.deepcopy(item) for item in _MACRO_READING_ENTRIES})


def _runtime_available(entry):
    """Normal research has public access only, including retained revisions.

    The original bundled public entries predate access metadata. Preserve
    those versions without rewriting their hashes; explicit access values
    must always be public. There is no task-provided permission override.
    """
    return (entry.get("state") == "已发布" and not entry.get("known_error")
            and entry.get("access", "public") == "public")


def _catalogue_entries(revision):
    """Derive the eligible version per ID from the one retained authority.

    A newly introduced revision of an existing ID must not erase the prior
    eligible version from an older catalogue. Current withdrawals/errors and
    access restrictions still apply to every version of that ID.
    """
    revision = catalogue_revision(revision)
    versions = dict(RETAINED_VERSIONS)
    versions.update({(item['id'], item['version']): item for item in ENTRIES})
    result = []
    for latest in ENTRIES:
        candidates = [item for (ident, _), item in versions.items() if ident == latest['id']
                      and item.get('catalogue_introduced', 1) <= revision]
        if not candidates: continue
        chosen = copy.deepcopy(max(candidates, key=lambda item: item['version']))
        if not _runtime_available(latest):
            # Keep it excluded with the current authority's restriction,
            # never resurrect a withdrawn version just because it is old.
            for field in ('state', 'known_error', 'access'):
                if field in latest: chosen[field] = latest[field]
        result.append(chosen)
    return result


def extraction_guidance(task):
    """Project locked conceptual checks, never company facts or approval.

    Old studies keep their adopted knowledge versions. Only explicitly
    versioned new studies expose registered checks to the source reader.
    """
    policy = task.get('extraction_knowledge_guidance_version')
    if policy is None:
        return None
    if type(policy) is not int or policy != 1:
        raise ContractError('知识取证指引版本未登记')
    from .contracts import DOCUMENT_CONTENT_INTENT
    if task.get('intent') == DOCUMENT_CONTENT_INTENT or task.get('knowledge') is None:
        return None
    from .planning import input_fingerprint
    if task.get('input_hash') != input_fingerprint(task):
        raise ContractError('知识取证指引与当前研究范围不一致')
    ResearchKnowledge().validate_package(task['knowledge'])
    entries = [dict(id=item['id'], version=item['version'], title=item['title'],
                    entry_hash=fingerprint(item), checks=copy.deepcopy(item['extraction_checks']))
               for item in task['knowledge']['entries'] if item.get('extraction_checks')]
    if not entries:
        return None
    return dict(version=1, task_id=task['id'], research_version=task['version'],
        input_hash=task['input_hash'], entries=entries, financial_approval=False,
        automatic_need_coverage=False,
        rule='按核验要点阅读实际原件并选择直接依据；知识条目不是主体事实、披露数字或来源摘录，不能替代原件或补全缺口。')


def literal_navigation_rule(task):
    """A derived literal index for locked knowledge, not issuer facts.

    Patterns are registered literal terms, never code or regular expressions
    supplied by materials. The concept's published content stays unchanged.
    """
    if (type(task.get('original_concept_navigation_version')) is not int
            or task['original_concept_navigation_version']!=1):return None
    guidance=extraction_guidance(task)
    version=task.get('knowledge_reading_navigation_version')
    if not guidance and not (type(version) is int and version==2
            and task.get('extraction_knowledge_guidance_version')==1
            and task.get('intent')!=DOCUMENT_CONTENT_INTENT and isinstance(task.get('knowledge'),dict)):
        return None
    tracking = any(e['id']=='rh_k_etf_exposure' and e['version']==2 for e in (guidance or {}).get('entries',[]))
    if (type(task.get('knowledge_reading_navigation_version')) is int
            and task['knowledge_reading_navigation_version'] in (1,2)):
        # Version-bound search locations, never new evidence or obligations.
        registry = {
            ('rh_k_cash_profit',2): ['现金流量表补充资料','将净利润调节为经营活动现金流量',
                '经营性应收项目','经营性应付项目','合同负债','存货','应收账款','应付账款',
                '经营活动产生的现金流量净额变动原因','经营活动产生的现金流量净额增加',
                '吸收存款','存放同业'],
            ('rh_k_net_margin',3): ['非经常性损益','收入确认','收入的确认','收入构成',
                '主营业务分行业','主营业务分产品','主营业务分渠道'],
        }
        if version==2:
            registry[('rh_k_fee',1)]=['年费率','管理人报酬','托管费按']
        entries = [dict(id=e['id'],version=e['version'],entry_hash=fingerprint(e),
                        terms=registry[(e['id'],e['version'])])
                   for e in task['knowledge']['entries'] if (e['id'],e['version']) in registry]
        if entries:
            terms = (['标的指数为','标的指数是','本基金跟踪','以标的指数'] if tracking else [])
            terms = list(dict.fromkeys(terms+[term for entry in entries for term in entry['terms']]))
            return dict(version=3 if version==2 else 2,knowledge_entries=entries,terms=terms,
                selection='first_hit_per_term',financial_approval=False,automatic_need_coverage=False,
                rule='索引只定位同批原文。按原问题核对完整说明、合并与母公司范围及期间；不新增研究义务，不把命中或未命中当作结论。')
    if not tracking:return None
    return dict(version=1,knowledge_id='rh_k_etf_exposure',knowledge_version=2,
        terms=['标的指数为','标的指数是','本基金跟踪','以标的指数'],
        selection='first_hit_per_term',financial_approval=False)


class ResearchKnowledge:
    def catalog(self, revision=CATALOGUE_REVISION):
        """Browse public definitions; the catalog is not a task-adopted knowledge pack."""
        revision = catalogue_revision(revision)
        return copy.deepcopy([item for item in _catalogue_entries(revision) if _runtime_available(item)])

    def planning_definitions(self, task):
        """The same public candidate scope for planner, schema and host Skill."""
        entries = [entry for entry in self.catalog(task_catalogue_revision(task))
            if entry.get('knowledge_role') == GENERAL_DEFINITION_SCOPE and entry['type'] in ('概念', '指标')
            and (task['entry'] == '混合' or task['entry'] in entry['scope'])]
        return dict(version=1, answer_scope=GENERAL_DEFINITION_SCOPE, question_kind='fact',
            candidates=[dict(id=entry['id'], version=entry['version'], title=entry['title'],
                content=entry['content'], conditions=entry['conditions'], limitation=entry['counterexample'],
                entry_hash=fingerprint(entry)) for entry in entries],
            fact_approval=False, calculation_approval=False,
            selection_rule='knowledge_needs仅逐字选择candidates中的id。计算方法的knowledge_id、条目标题或模型自拟名称不能代替。此目录尚未被任务采用；明确选择后仍须由知识库锁定和核验版本。',
            missing_rule='没有适用定义时保留缺口，不猜id，不把缺少市场证据的原问题改成通用定义；独立事实和分析问题继续保持原范围。')

    def definition_contract(self, task, question):
        """An explicit knowledge-only scope, never a replacement for facts."""
        if (question.get("answer_scope") != GENERAL_DEFINITION_SCOPE or question.get("kind") != "fact" or
                any(question.get(field) for field in ("dependencies", "evidence_needs", "method_needs")) or
                not question.get("knowledge_needs")):
            raise ContractError("该问题不是独立通用口径说明")
        if task.get('knowledge') is None:
            return {'schema': 'researchhelper.definition-answer', 'version': 1,
                    'answer_scope': GENERAL_DEFINITION_SCOPE, 'generation_ready': False,
                    'required_knowledge_ids': list(question['knowledge_needs']),
                    'knowledge_refs': [], 'fact_approval': False, 'free_prose': False}
        self.validate_package(task['knowledge'])
        entries = {entry['id']: entry for entry in task['knowledge']['entries']}
        references = []
        for ident in question['knowledge_needs']:
            entry = entries.get(ident)
            if (not entry or entry.get('knowledge_role') != GENERAL_DEFINITION_SCOPE or
                    entry['type'] not in ('概念', '指标') or task['entry'] not in entry['scope'] and task['entry'] != '混合'
                    or entry.get('catalogue_introduced', 1) > task_catalogue_revision(task)):
                raise ContractError("口径说明缺少本版适用的已发布定义，不能借用机制、方法或主体事实")
            references.append({'id': ident, 'version': entry['version']})
        return {'schema': 'researchhelper.definition-answer', 'version': 1,
                'answer_scope': GENERAL_DEFINITION_SCOPE, 'generation_ready': True, 'knowledge_refs': references,
                'projection_fields': ['title', 'content', 'conditions', 'counterexample', 'source', 'temporal_scope'],
                'free_prose': False, 'numeric_segments': False, 'fact_approval': False}

    def definition_readiness(self, task, question):
        """Describe a missing definition locally; never hide corrupt knowledge.

        This is a preview contract for new tasks, not permission to adopt an
        invalid definition. Strict definition_answer remains the adoption gate.
        """
        if (question.get('answer_scope') != GENERAL_DEFINITION_SCOPE or question.get('kind') != 'fact' or
                any(question.get(field) for field in ('dependencies', 'evidence_needs', 'method_needs')) or
                not question.get('knowledge_needs')):
            raise ContractError('该问题不是独立通用口径说明')
        if task.get('knowledge') is None:
            return self.definition_contract(task, question)
        self.validate_package(task['knowledge'])
        entries = {entry['id']: entry for entry in task['knowledge']['entries']}
        missing = [ident for ident in question['knowledge_needs'] if ident not in entries or
            entries[ident].get('knowledge_role') != GENERAL_DEFINITION_SCOPE or
            entries[ident]['type'] not in ('概念', '指标') or
            task['entry'] not in entries[ident]['scope'] and task['entry'] != '混合']
        if missing:
            return {'schema': 'researchhelper.definition-answer', 'version': 1,
                    'answer_scope': GENERAL_DEFINITION_SCOPE, 'generation_ready': False,
                    'required_knowledge_ids': list(question['knowledge_needs']),
                    'missing_knowledge_ids': missing, 'knowledge_refs': [],
                    'fact_approval': False, 'free_prose': False, 'numeric_segments': False}
        return self.definition_contract(task, question)

    def definition_answer(self, task, question, proposal=None):
        """Project complete locked definitions; do not repair supplied prose."""
        from .planning import question_specs
        declared = next((item for item in question_specs(task) if item['id'] == question['id']), None)
        if declared is None or any(question.get(key) != value for key, value in declared.items()):
            raise ContractError("通用口径说明不属于本任务采用的问题")
        contract = self.definition_contract(task, question)
        if not contract['generation_ready']:
            raise ContractError("知识版本尚未采用，不能先生成口径说明")
        entries = {entry['id']: entry for entry in task['knowledge']['entries']}
        selected = [entries[ref['id']] for ref in contract['knowledge_refs']]
        prefix = ('通用口径说明，不代表当前研究对象事实。\n' if task.get('definition_projection_version') == 1
                  else '通用口径说明，不代表当前基金事实。\n')
        text = prefix + '\n\n'.join(
            entry['title'] + '\n' + entry['content'] + '\n适用条件：' + entry['conditions'] +
            '\n限制：' + entry['counterexample'] + '\n用途：' + entry['temporal_scope'] +
            '\n来源：' + entry['source'] for entry in selected)
        if proposal is not None:
            allowed = {'knowledge_refs', 'answer', 'refs', 'calculation_refs', 'task_id', 'research_version'}
            if (not isinstance(proposal, dict) or set(proposal) - allowed or
                    proposal.get('task_id', task['id']) != task['id'] or
                    type(proposal.get('research_version', task['version'])) is not int or
                    proposal.get('research_version', task['version']) != task['version'] or
                    proposal.get('refs', []) != [] or proposal.get('calculation_refs', []) != [] or
                    fingerprint(proposal.get('knowledge_refs')) != fingerprint(contract['knowledge_refs']) or
                    'answer' in proposal and proposal['answer'] != text):
                raise ContractError("口径说明只能引用完整本版定义，不能增加文字、金融数字、事实或投资判断")
        record = {'schema': contract['schema'], 'version': 1, 'task_id': task['id'],
                  'research_version': task['version'], 'input_hash': task['input_hash'],
                  'question_id': question['id'], 'question_hash': fingerprint(declared),
                  'knowledge_refs': copy.deepcopy(contract['knowledge_refs']),
                  'entry_hashes': {entry['id']: fingerprint(entry) for entry in selected},
                  'answer_hash': fingerprint(text), 'fact_approval': False}
        return {'answer': text, 'refs': [], 'calculation_refs': [],
                'knowledge_refs': copy.deepcopy(contract['knowledge_refs']), 'knowledge_integrity': record}

    def validate_definition_answer(self, task, question):
        expected = self.definition_answer(task, question, {key: question[key] for key in
            ('answer', 'refs', 'calculation_refs', 'knowledge_refs') if key in question})
        if question.get('knowledge_integrity') != expected['knowledge_integrity']:
            raise ContractError("通用口径说明正文、知识版本或任务绑定已变化")

    def _draft_entries(self, task):
        """Read only this task's locked, registered versions; failure is local."""
        package = task.get('knowledge')
        if not package:
            return {}, None
        try:
            self.validate_package(package, revision=task.get(CATALOGUE_POLICY))
        except (ContractError, KeyError, TypeError, ValueError, AttributeError):
            return {}, '知识包未通过版本核对，未作为本稿理论依据。'
        entry_type=task.get('entry')
        return {entry['id']: entry for entry in package['entries']
                if (not entry_type or entry_type == '混合' or entry_type in entry['scope'])
                and (task.get('synthetic') is True or '合成验收' not in entry.get('source',''))}, None

    def draft_context(self, task, *, limit=3200):
        """A bounded projection shared by App writing and the Skill host.

        Whole entries are selected without truncating a condition or counterexample.
        The original package remains the single authority; this is not a new index.
        """
        if type(limit) is not int or limit < 512:
            raise ValueError('知识上下文长度须为至少512个字符的整数')
        entries, notice = self._draft_entries(task)
        result = dict(version=1, binding=dict(
            task_id=task.get('id', task.get('task_id')),
            research_version=task.get('version', task.get('research_version')),
            input_hash=task.get('input_hash'),
            knowledge_hash=fingerprint(task['knowledge']) if entries else None),
            usage_scope='通用原理与登记方法，不是本次标的事实，不授予计算执行权限。',
            entries=[], omitted_count=0)
        if notice:
            result['notice'] = notice
        priority = {'机制': 0, '指标': 1, '方法': 2, '概念': 3}
        for entry in sorted(entries.values(), key=lambda row: priority.get(row['type'], 4)):
            projected = {key: copy.deepcopy(entry.get(key, '')) for key in (
                'id', 'version', 'type', 'title', 'content', 'conditions', 'counterexample')}
            # Carry the maintained positive channels as well as their limits.
            # Keep each entry whole and within the same bounded token budget.
            for field in ('channels', 'verification_indicators'):
                if entry.get(field):
                    projected[field] = copy.deepcopy(entry[field])
            proposed = dict(result, entries=result['entries'] + [projected], omitted_count=len(entries))
            if len(canonical(proposed)) <= limit:
                result['entries'].append(projected)
        result['omitted_count'] = len(entries) - len(result['entries'])
        return result

    def draft_references(self, task, references):
        """Normalize explicit citations, never infer that retrieved knowledge was used."""
        if not references:
            return [], []
        entries, notice = self._draft_entries(task)
        accepted, seen, invalid = [], set(), False
        reasons=[]
        if not isinstance(references, list):
            references, invalid = [], True
            reasons.append('知识引用须为对象列表，每项包含id和整数version。')
        for ref in references:
            if not isinstance(ref, dict) or set(ref) - {'id', 'version', 'entry_hash'}:
                invalid = True
                reasons.append('知识引用每项须为包含id和整数version的对象，可附entry_hash；不能只写条目名称或字符串。')
                continue
            entry = entries.get(ref.get('id')) if isinstance(ref.get('id'), str) else None
            reason = ('知识条目尚未登记到本任务的当前版本。' if entry is None else
                      '知识引用的version须为整数。' if type(ref.get('version')) is not int else
                      '知识引用版本与本任务锁定条目不一致。' if ref['version'] != entry['version'] else
                      '知识引用哈希与本任务锁定内容不一致。' if 'entry_hash' in ref and ref['entry_hash'] != fingerprint(entry) else None)
            if reason:
                invalid = True
                reasons.append(reason)
                continue
            key = (entry['id'], entry['version'])
            if key not in seen:
                accepted.append(dict(id=entry['id'], version=entry['version'], entry_hash=fingerprint(entry)))
                seen.add(key)
        notes = [notice] if notice else []
        if invalid or not entries:
            notes.append('部分知识引用未对应本任务锁定版本，正文保留，未登记为理论依据。')
        notes.extend(dict.fromkeys(reasons))
        return accepted, notes

    def read(self, entry_type, query="", methods=(), context=None):
        """Return bounded complete entries; ranking never authorizes facts or execution."""
        context = copy.deepcopy(context or {})
        if not isinstance(query, str) or not isinstance(context, dict):
            raise ContractError("知识检索问题与上下文格式无效")
        # Unbound legacy callers keep the original catalogue. New tasks pass
        # their explicit revision; browsing the public catalog is not adoption.
        revision = catalogue_revision(context.get(CATALOGUE_POLICY, 1))
        available_entries = _catalogue_entries(revision)
        intent = context.get("intent", "")
        questions = context.get("questions", [])
        if not isinstance(intent, str) or not isinstance(questions, list) or any(
                not isinstance(q, dict) or not isinstance(q.get("question"), str) or not isinstance(q.get("id"), str) for q in questions):
            raise ContractError("知识检索须使用明确意图及必要问题")
        if any(method not in METHOD_REGISTRY for method in methods):
            raise ContractError("知识需求中的计算方法未登记")
        needed = {METHOD_REGISTRY[method]["knowledge_id"] for method in methods}
        definition_needs = {ident for question in questions if question.get('answer_scope') == GENERAL_DEFINITION_SCOPE
                            for ident in question.get('knowledge_needs', [])}
        # Domain matching is an index, not a conclusion or a model-call shortcut.
        terms = {"rh_k_residual": ("收款", "分配", "剩余", "清算"), "rh_k_fee": ("费率", "费用", "管理费", "托管费"),
                 "rh_k_cost_pass": ("成本", "利润", "价格", "加工", "传导", "铜价"),
                 "rh_k_fee_difference": ("费率", "费用", "管理费", "托管费"),
                 "rh_k_price_change": ("价格", "涨跌", "收益", "区间", "表现"),
                 "rh_k_nav_premium": ("净值", "折价", "溢价"), "rh_k_gross_margin": ("毛利", "收入", "成本", "经营")}
        terms.update({item["id"]: tuple(item["aliases"]) for item in available_entries if item.get("aliases")})
        demands = [{"id": "query", "question": query}] + [q for q in questions if q.get("required", True)]
        ranked, excluded, reasons = [], [], {}
        for item in available_entries:
            ident = item["id"]
            if not _runtime_available(item):
                excluded.append({"id": ident, "version": item["version"], "reason": "未发布、停用、存在已知错误或无公开访问权限"})
                continue
            if entry_type not in item["scope"] and entry_type != "混合":
                excluded.append({"id": ident, "version": item["version"], "reason": "研究入口不在条目适用范围"})
                continue
            # Domain-specific mechanisms require all declared topic groups in
            # the same question. A policy-only fact must not acquire bank risk
            # analysis merely because a different question contains "银行".
            groups = item.get("retrieval_requirements", [])
            matches = {q["id"]: [word for word in terms.get(ident, ()) if word.casefold() in q["question"].casefold()]
                       if all(any(term.casefold() in q["question"].casefold() for term in group) for group in groups)
                       else [] for q in demands}
            matched = [qid for qid, words in matches.items() if words]
            forced = ident in needed or ident in definition_needs or ident == "rh_k_fact"
            if not forced and not matched:
                excluded.append({"id": ident, "version": item["version"], "reason": "未命中当前问题；不因入口相同整库采用"})
                continue
            reasons[ident] = {"reason": "明确通用口径说明" if ident in definition_needs else "必要计算方法" if ident in needed else "通用事实与预测核验" if ident == "rh_k_fact" else "问题主题命中，仍须任务适用性核验",
                              "question_ids": matched, "matched_terms": sorted({w for words in matches.values() for w in words}), "intent": intent}
            intent_fit = (intent == "解释变化" and item["type"] == "机制") or (intent == "比较对象" and item["type"] in ("概念", "指标"))
            ranked.append((forced, 2 * sum(bool(matches[q["id"]]) for q in demands) + int(intent_fit), item))
        found = {item["id"] for _, _, item in ranked}
        if needed - found:
            raise ContractError("必要方法知识未发布、失效或不适用当前入口")
        if definition_needs - found:
            raise ContractError("必要通用口径知识未发布、失效或不适用当前入口")
        ranked.sort(key=lambda row: (-int(row[0]), -row[1], row[2]["id"]))
        selected, optional_count, omitted_by_question = [], 0, {}
        for forced, _, item in ranked:
            if not forced and optional_count >= 4:
                matched_questions = list(reasons[item["id"]]["question_ids"])
                excluded.append({"id": item["id"], "version": item["version"], "question_ids": matched_questions,
                                 "reason": "本次有限知识包容量未选取；不代表问题已解决"})
                for qid in matched_questions:
                    omitted_by_question.setdefault(qid, []).append({"id": item["id"], "version": item["version"], "title": item["title"]})
                reasons.pop(item["id"], None)
                continue
            selected.append(copy.deepcopy(item))
            optional_count += not forced
        for item in list(selected):
            for dependency in item.get("dependencies", []):
                if not any(e["id"] == dependency["id"] and e["version"] == dependency["version"] for e in selected):
                    target = RETAINED_VERSIONS.get((dependency["id"], dependency["version"]))
                    if target is None or any(e["id"] == dependency["id"] for e in selected):
                        raise ContractError("知识依赖版本缺失或与知识包冲突")
                    selected.append(copy.deepcopy(target))
                    reasons[target["id"]] = {"reason": "已选条目的版本依赖", "question_ids": [], "matched_terms": [], "intent": intent}
        specialized = {qid for item in selected if item["id"] != "rh_k_fact" for qid in reasons[item["id"]]["question_ids"]}
        unmatched_questions = []
        for demand in demands:
            if not demand["question"].strip():
                continue
            omitted = omitted_by_question.get(demand["id"], [])
            if omitted:
                unmatched_questions.append({"id": demand["id"], "question": demand["question"],
                    "omitted_entries": omitted, "reason": "命中主题因知识包容量未采用：" + "、".join(item["title"] for item in omitted) +
                    "；其他主题入选不代表该问题知识覆盖充分"})
            elif demand["id"] not in specialized:
                unmatched_questions.append({"id": demand["id"], "question": demand["question"],
                    "reason": "未命中专用知识；通用核验不代表领域覆盖充分"})
        unmatched = [q["reason"] + "：" + q["question"] for q in unmatched_questions]
        if context.get("industry"):
            unmatched.append("未登记行业专用规则；行业标签不建立主体身份或默认受益关系。")
        package = {"authority_version": fingerprint(available_entries), CATALOGUE_POLICY: revision, "entries": selected,
                "index": {item["id"]: item["version"] for item in selected}, "query": query,
                "coverage": "有限通用原理、机制与登记方法；主题命中不证明问题覆盖充分，检索不核验主体身份，不提供当前事实或历史规则覆盖证明。",
                "selection_reasons": reasons, "excluded": excluded, "unmatched_questions": unmatched_questions,
                "retrieval_context": context, "unmatched": unmatched}
        # A selected entry's registered dependency is adopted too. Ranking
        # exclusions describe only entries absent from the final package,
        # otherwise the next unchanged retrieval changes its identity hash.
        package["excluded"] = [item for item in excluded if item["id"] not in package["index"]]
        self.reconcile_coverage(package)
        self.validate_package(package)
        return package

    def validate_package(self, package, *, revision=None):
        if not isinstance(package, dict) or not isinstance(package.get("entries"), list):
            raise ContractError("研究知识包缺失")
        locked = catalogue_revision(package.get(CATALOGUE_POLICY, 1))
        if revision is not None and locked != catalogue_revision(revision):
            raise ContractError('知识包与任务锁定的目录版本不一致，不能静默加入新版条目')
        current = {item["id"]: item for item in ENTRIES}
        index = {}
        for item in package["entries"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("version"), int) or isinstance(item.get("version"), bool):
                raise ContractError("知识条目身份与版本格式无效")
            key = (item.get("id"), item.get("version"))
            registered = RETAINED_VERSIONS.get(key)
            latest = current.get(key[0])
            if registered is None and latest and latest["version"] == key[1]:
                registered = latest
            if registered is None or fingerprint(item) != fingerprint(registered):
                raise ContractError("知识条目版本或正文与权威记录不一致")
            if registered.get('catalogue_introduced', 1) > locked:
                raise ContractError('知识条目不属于本包锁定的目录版本')
            if not _runtime_available(registered):
                raise ContractError("锁定知识版本未发布、已停用、存在已知错误或无公开访问权限")
            if not latest or not _runtime_available(latest):
                raise ContractError("知识条目已停用、发现错误或访问权限变化，不能继续受影响分析")
            if key[0] in index:
                raise ContractError("同一知识包不能混入同条目的多个版本")
            index[key[0]] = key[1]
        if package.get("index") != index:
            raise ContractError("知识派生索引与锁定版本不一致")
        for item in package["entries"]:
            if any(index.get(dependency["id"]) != dependency["version"] for dependency in item.get("dependencies", [])):
                raise ContractError("知识包缺少条目依赖的明确版本")

    def reconcile_coverage(self, package):
        """Retained checkpoint entries can satisfy exact-version capacity omissions."""
        self.validate_package(package)
        prior = package.get("unmatched_questions", [])
        derived = {item["reason"] + "：" + item["question"] for item in prior}
        independent = [reason for reason in package.get("unmatched", []) if reason not in derived]
        remaining = []
        for original in prior:
            item = copy.deepcopy(original)
            if "omitted_entries" in item:
                omitted = [entry for entry in item["omitted_entries"] if package["index"].get(entry["id"]) != entry["version"]]
                if not omitted:
                    continue
                item["omitted_entries"] = omitted
                item["reason"] = "命中主题因知识包容量未采用：" + "、".join(entry["title"] for entry in omitted) + "；其他主题入选不代表该问题知识覆盖充分"
            remaining.append(item)
        package["unmatched_questions"] = remaining
        package["unmatched"] = [item["reason"] + "：" + item["question"] for item in remaining] + independent

    def method_binding(self, package, method):
        self.validate_package(package)
        definition = METHOD_REGISTRY.get(method)
        if definition is None:
            raise ContractError("方法实现未登记")
        item = next((entry for entry in package["entries"] if entry["id"] == definition["knowledge_id"]), None)
        if not item or item["type"] != "方法" or item.get("implementation") != method:
            raise ContractError("计算缺少适用且锁定版本的方法知识")
        definition = method_definition_for_knowledge(method, item)
        binding = {"knowledge_id": item["id"], "knowledge_version": item["version"], "knowledge_hash": fingerprint(item),
                "implementation": method, "implementation_version": definition["implementation_version"],
                "definition_hash": fingerprint(definition)}
        if definition.get('same_input_unit'):
            binding['amount_units'] = copy.deepcopy(item.get('amount_units', ['百万元']))
        return binding

    def publish(self, *_):
        raise ContractError("普通研究不能发布、修订或停用权威知识")
