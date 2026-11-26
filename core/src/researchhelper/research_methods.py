"""Versioned research recipes. These guide planning and carry no new execution rights."""
from copy import deepcopy
import re

from .contracts import ContractError, DOCUMENT_CONTENT_INTENT, market_date


REGISTRY_VERSION = 3
ANNUAL = 'https://www.csrc.gov.cn/csrc/c101954/c7547588/content.shtml'
ETF_PCF = 'https://www.sse.com.cn/disclosure/fund/etflist/'
ETF_DISCLOSURE = 'https://www.csrc.gov.cn/csrc/c106256/c1653985/content.shtml'
DISCLOSURE_GUIDE = 'https://one.sse.com.cn/onething/xxpl/'
INDEX_CALCULATION = 'https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000300_Index_Methodology_cn.pdf'
INDEX_CLASSIFICATION = 'https://oss-ch.csindex.com.cn/industryClassification/%E4%B8%AD%E8%AF%81%E8%A1%8C%E4%B8%9A%E5%88%86%E7%B1%BB%E7%9A%84%E8%AF%B4%E6%98%8E.pdf'
CONSUMPTION = 'https://www.stats.gov.cn/xxgk/sjfb/zxfb2020/202504/t20250416_1959321.html'
BROKERS = 'https://www.sac.net.cn/sjb/xxgk/hysj/jjsj/202603/t20260326_81468.html'
DRUG_TRIALS = 'https://zwfw.nmpa.gov.cn/web/taskview/11100000MB0341032Y100207202900001'
CPI = 'https://www.stats.gov.cn/zs/tjws/tjzb/202301/t20230101_1903757.html'
PPI = 'https://www.stats.gov.cn/zs/tjws/tjzb/202301/t20230101_1903637.html'
GDP = 'https://www.stats.gov.cn/zs/tjws/zytjzbqs/gnsczz/202502/t20250226_1958799.html'
PMI = 'https://www.stats.gov.cn/zs/tjws/tjzb/202301/t20230101_1903972.html'
FINANCING = 'https://www.pbc.gov.cn/diaochatongjisi/attachDir/2025/11/2025111913535757500.pdf'
FISCAL = 'https://www.mof.gov.cn/zhengwuxinxi/caizhengshuju/'
BALANCE_OF_PAYMENTS = 'https://www.safe.gov.cn/safe/zggjszphb/index.html'
INDEX_RETURNS = 'https://www.msci.com/indexes/index-resources/index-methodology'
AH_PREMIUM = 'https://www.hsi.com.hk/zh-hk/indexes/all-indexes/index-details/?seriesCode=ahpremium'
POLICY_TRANSMISSION = 'https://www.federalreserve.gov/monetarypolicy/monetary-policy-what-are-its-goals-how-does-it-work.htm'


def _recipe(ident, subject, title, terms, evidence, analysis, check, sources, *, industry='', since=None):
    return dict(id=ident, version=1, subject=subject, title=title, terms=terms, evidence=evidence,
                analysis=analysis, check=check, sources=sources, industry=industry, effective_from=since or '')


# The dated reading is a catalogue reference. General analytical steps remain
# usable for older periods. Industry supplements can depend on dated material.
_RECIPES = (
    _recipe('stock.financial', '个股', '财务质量', ('财务', '中报', '年报', '业绩', '利润', '营收', '营业收入', '现金流', '三表', '基本面'),
        ('同口径利润表', '经营活动现金流量', '分部收入和重大会计变更'),
        '先对齐合并范围和报告期，再解释收入、毛利与现金回收的差别。仅在注册输入齐备时计算利润率与现金覆盖。',
        '检查季节性、并表或追溯调整、预收预付及一次性收益。利润增长不能代替现金流证据。',
        (('证监会年度报告准则', ANNUAL),), since='2025-07-01'),
    _recipe('stock.valuation', '个股', '可比估值', ('估值', '市盈率', '市净率', 'pe', 'pb', '倍数'),
        ('观测日价格和估值', '盈利口径', '同日可比对象和基准'),
        '核对估值分子、分母和基期。横截面先确认行业可比性，历史分位只使用明确窗口内的同口径观测。',
        '区分亏损、负净资产和极端值。单一估值倍数不能说明相对位置。',
        (('证监会年度报告准则', ANNUAL), ('中证行业分类说明', INDEX_CLASSIFICATION)), since='2025-07-01'),
    _recipe('stock.disclosure', '个股', '公告与预期变化', ('公告', '业绩预告', '预告', '预期', '指引', '分红', '回购'),
        ('公告原文及其公开时点', '前次口径', '可核验的观察窗口'),
        '先列公告的新增信息和前次口径，再区分已发生事项、管理层指引与研究推断。',
        '公告日期与交易时段必须对齐。同期的其他信息和市场变动可能解释价格反应。',
        (('证监会年度报告准则', ANNUAL),), since='2025-07-01'),
    _recipe('etf.tracking', 'ETF', '跟踪与费率', ('跟踪', '费率', '费用', '托管费', '管理费', '基准', '跟踪误差'),
        ('同份额级别单位净值', '基准指数及收益口径', '合同费率及适用日期'),
        '按同一日期、币种和分红口径对齐基金与基准。区分年费率、实际扣费以及跟踪偏离和误差。',
        '费率较低无法单独证明跟踪更好。缺少基准历史时只核对可见的净值与费率。',
        (('证监会公募基金信息披露办法', ETF_DISCLOSURE), ('上交所ETF申赎清单', ETF_PCF))),
    _recipe('etf.liquidity', 'ETF', '流动性与折溢价', ('折价', '溢价', '净值偏离', '流动性', '买卖价差', '成交', '套利'),
        ('价格时点', '相同估值日的单位净值', '若研究交易成本则需可比盘口'),
        '先匹配份额与币种，再分别处理日度收盘对照和同一时点的交易观察。',
        '价格观察与净值披露可能时差。日度事后差额不能代替同刻成交机会。',
        (('上交所ETF申赎清单', ETF_PCF), ('证监会公募基金信息披露办法', ETF_DISCLOSURE))),
    _recipe('etf.exposure', 'ETF', '成分穿透', ('成分', '权重', '敞口', '持仓', '穿透', '集中度'),
        ('适用版本的指数成分与权重', '基金定期披露或实际持仓'),
        '先区分指数权重、基金持仓和申赎篮子，再比较集中度或行业敞口。',
        '样本日期、分母、份额级别或衍生敞口不一致时分别展示。',
        (('上交所ETF申赎清单', ETF_PCF), ('中证行业分类说明', INDEX_CLASSIFICATION)), since='2021-12-01'),
    _recipe('index.performance', '指数', '指数区间与基准比较',
        ('区间表现', '表现', '涨跌', '走势', '对比', '全收益', '超额收益', '回撤', '复权'),
        ('编制机构公布的指数身份与收益种类', '可比时点的点位或指数序列', '比较基准及股息处理'),
        '先区分价格指数与全收益指数，再核对同一观察窗口和计价口径。点位尺度不同的指数比较涨跌幅，排序沿用真实观测。',
        '区间始末点位只说明区间变化。日内波动、最大回撤与持续性需要可比的区间内序列。',
        (('中证指数沪深300编制方案', INDEX_CALCULATION), ('MSCI指数方法目录', INDEX_RETURNS))),
    _recipe('index.breadth', '指数', '成分贡献与市场宽度',
        ('贡献', '归因', '普涨', '带动', '成分股', '权重', '市场宽度', '上涨家数', '调样'),
        ('研究窗口适用的样本及权重', '成分收益、停牌与公司行动', '上涨家数与有效样本分母'),
        '先从编制规则确认样本、计权与调样，再分别核对权重贡献和参与上涨的成分比例。用同一成分范围比较两项指标。',
        '最新成分和权重无法回填过去的变动。少数权重股可能推升指数，过半成分仍可下跌。缺少当期权重或样本时，保留能够核对的表现。',
        (('中证指数沪深300编制方案', INDEX_CALCULATION),)),
    _recipe('event.timeline', '事件', '事件时点与可交易窗口',
        ('事件', '公告', '时间线', '生效', '发布', '适用范围', '反应', '窗口', 'ipo', '审批', '进展'),
        ('发布主体原文及首次公开时点', '事件生效和实施节点', '交易时间、基准与同期事件'),
        '区分事前预期、公告新增内容和实际落地。按首发时间和适用市场确定可交易窗口，必要时对齐基准和同期消息，再判断价格反应是否与机制吻合。',
        '跨时区或盘后发布可能改变事件归属。事前预期、基准或同期干扰未核对时，结论限于事件窗口的观察。',
        (('上海证券交易所信息披露服务', DISCLOSURE_GUIDE),)),
    _recipe('industry.cycle', '行业', '景气与供需', ('景气', '供需', '库存', '产能', '开工', '产量', '需求'),
        ('同口径产销量', '价格和库存', '样本范围与统计频率'),
        '按供给、需求、库存查看一致或背离的方向，区分基数、季节性和样本变化。',
        '确认名义金额、实际销量、同比和环比分属不同口径。宏观代理不能直接代表子行业。',
        (('国家统计局统计口径', CONSUMPTION), ('中证行业分类说明', INDEX_CLASSIFICATION)), since='2025-04-16'),
    _recipe('industry.earnings', '行业', '盈利传导', ('成本', '利润', '盈利', '竞争', '传导', '量价', '产业链'),
        ('产业环节和业务占比', '可比销量、售价、成本', '毛利和竞争格局'),
        '从上游变化到中下游利润逐段核对，明确可能受益或承压的对象以及传导条件。',
        '验证合同定价、库存、套保、替代品等相反渠道。保留条件性解释。',
        (('中证行业分类说明', INDEX_CLASSIFICATION), ('证监会年度报告准则', ANNUAL)), since='2025-07-01'),
    _recipe('industry.policy', '行业', '政策与事件', ('政策', '事件', '监管', '改革', '生效', '试点'),
        ('发布机关原文', '公开与生效日期', '适用对象和排除项'),
        '先确定事件及适用范围，再画传导环节或时间线，并检验有关业务实际受影响的条件。',
        '区分征求意见、发布、生效和执行结果。同期表现不能单独证明因果。',
        (('中证行业分类说明', INDEX_CLASSIFICATION),), since='2021-12-01'),
    _recipe('industry.sample', '行业', '样本覆盖与业绩分布',
        ('样本', '覆盖率', '覆盖情况', '公司名单', '横截面', '分布', '分化', '中位数', '行业汇总'),
        ('截止日行业分类或成分名册', '样本及缺失名单', '同口径财务读数和权重'),
        '先定义总体、成分日期、纳入规则和覆盖率分母，再比较个体、等权分布与按规模加权的结果。',
        '样本、业务归属和缺失项必须可追溯。成分变更、幸存者偏差或少数权重股可能改变结论。',
        (('中证行业分类说明', INDEX_CLASSIFICATION), ('证监会年度报告准则', ANNUAL))),
    _recipe('industry.valuation', '行业', '行业估值与可比组',
        ('估值', '估值分位', '市盈率', '市净率', 'pe', 'pb', '估值倍数', '可比公司'),
        ('同日行业指数或成分估值', '盈利或净资产口径', '历史成分、频率及估值窗口'),
        '区分行业整体法、成分中位数和个股倍数。比较历史位置时明示窗口、成分处理和负分母口径。',
        '行业分类或权重变动会移动估值分位。倍数差异需结合增长、盈利品质与风险解释。',
        (('中证行业分类说明', INDEX_CLASSIFICATION), ('MSCI指数方法目录', INDEX_RETURNS))),
    _recipe('industry.broker', '行业', '券商业务拆解', ('券商', '证券公司', '证券板块', '证券行业'),
        ('经纪、投行、资管、利息净收入、自营投资收入', '成交额与融资活动', '净资本及风险敞口'),
        '按业务分解盈利驱动，分清经纪佣金率与成交量，自营收益与公允价值变动。',
        '协会行业汇总与上市公司报表的合并范围不同。区分统计汇总与经审计个体报表。',
        (('证券业协会年度行业经营统计', BROKERS),), industry='券商', since='2026-03-27'),
    _recipe('industry.consumer', '行业', '消费量价与渠道', ('消费', '零售', '白酒', '餐饮'),
        ('零售额及样本口径', '上市公司销量与售价', '渠道与库存'),
        '区分全市场零售额、企业营业收入、终端销量和价格。只在可比数量价格齐备时拆解贡献。',
        '核对产品和渠道结构变化。金额同比不能直接解释实际销量。',
        (('国家统计局社会消费品零售总额统计口径', CONSUMPTION),), industry='消费', since='2025-04-16'),
    _recipe('industry.drug', '行业', '创新药商业化节点', ('创新药', '生物医药', '药品审评'),
        ('临床试验登记', '监管批件或公告', '商业化收入、支付和适应症披露'),
        '用主体、适应症、辖区和日期匹配公开节点，再观察收入及费用的可能传导。',
        '区分试验登记、正式批准、商业销售与支付准入。以适用辖区的披露为准。',
        (('国家药监局临床试验登记', DRUG_TRIALS),), industry='创新药'),
    _recipe('macro.inflation', '宏观', '物价、分项与基数',
        ('cpi', 'ppi', '核心cpi', '物价', '通胀', '居民消费价格', '工业生产者价格', '食品价格', '价格指数'),
        ('同地区同频率的总项和相关分项', '同比、环比及基期', '官方贡献或对应权数'),
        '先分清总项、核心和分项，按同一统计口径比较。同比和环比分开读，结合基期排查表观加速或回落。',
        '分项变化只说明读数方向。量化拉动需官方贡献或同版权数；缺少这些信息时保留归因。',
        (('国家统计局CPI编制方法', CPI), ('国家统计局PPI编制方法', PPI))),
    _recipe('macro.activity', '宏观', '经济活动与需求',
        ('pmi', 'gdp', '工业增加值', '经济增长', '新订单', '实际增速', '经济活动', '景气'),
        ('生产和需求指标及频率', '现价或不变价口径', '季调、基期和资料版本'),
        '把调查扩散指数与实际产出分开。对照生产、订单和需求，比较短期季调环比以及年度同比。',
        '50是PMI扩散方向的分界。GDP现价总量、实际增速和初值修订分别处理，避免把基数变化解释成需求冲击。',
        (('国家统计局GDP解读', GDP), ('国家统计局PMI定义与季调', PMI))),
    _recipe('macro.finance', '宏观', '货币政策、信用与利率',
        ('社融', '社会融资', '信用', '信贷', '货币政策', 'lpr', 'm2', 'fomc', '降息', '加息', '利率', '收益率曲线'),
        ('政策公告时间与政策工具', '信用存量及增量', '同大类市场利率和期限'),
        '分开读政策利率、信用数量和市场定价。按一致期限比较曲线，区分政策落地与市场已预期的变化。',
        '信用增量、存量和同比不能互换。市场利率也受发行、流动性和风险偏好影响。',
        (('人民银行社会融资规模统计说明', FINANCING), ('美联储货币政策传导', POLICY_TRANSMISSION))),
    _recipe('macro.fiscal', '宏观', '财政收支和政策兑现',
        ('财政', '赤字', '专项债', '财政支出', '一般公共预算', '政府性基金', '预算执行'),
        ('预算及调整文件', '月度实际收支和累计口径', '发债、拨付及实物工作量'),
        '先对齐一般公共预算与政府性基金范围，分别追踪安排、发行、支出和可能形成的需求。',
        '新增与再融资、当月与累计、预算与执行分别核对。发行进度不能直接代替需求兑现。',
        (('财政部财政数据', FISCAL),)),
    _recipe('macro.external', '宏观', '外需与国际收支',
        ('国际收支', '经常账户', '贸易差额', '贸易顺差', '外需', '进出口', '出口量', '跨境资本流动'),
        ('贸易金额、数量和币种', '服务和收入账户', '统计范围、频率及初修值'),
        '以同币种和计价口径比较外需，区分金额变动、数量和价格。另列国际收支内的服务与收益。',
        '海关贸易差额与国际收支货物差额范围不同。单月顺差不能直接推出资本流向或汇率方向。',
        (('外汇局国际收支数据', BALANCE_OF_PAYMENTS),)),
    _recipe('cross.returns', '跨市场', '同窗口表现与币种对照',
        ('表现', '走势', '涨跌', '收益率', '回报', '复权', '本币', '统一币种', '交易日'),
        ('各市场交易日与收盘时间', '同窗口同口径价格或全收益序列', '统一币种时对应汇率'),
        '取共同起止观测，区分本币价格变动与全收益。需要投资者币种收益时再按明确的汇率方向折算。',
        '收市时点、节假日、股息及公司行动均可能影响比较。资料不足时仅给本币原口径的窗口观察。',
        (('MSCI指数方法目录及收益口径', INDEX_RETURNS),)),
    _recipe('cross.valuation', '跨市场', '跨市场估值与A/H溢价',
        ('a/h', 'ah股', 'ah溢价', '溢价', '估值', '市盈率', '市净率', '双重上市', '同一发行人', '同一公司'),
        ('发行人与股份类别对应', '同日期股价与汇率', '盈利分母和财报口径'),
        '先判定是同一发行人的两类股份，还是不同发行人的可比估值。前者统一每股口径、币种和观察时点。',
        '估值差与价格溢价分别解释。考虑股东权利、交易限制、流动性和收盘时差。',
        (('恒生沪深港通AH股溢价指数', AH_PREMIUM), ('MSCI指数方法目录', INDEX_RETURNS))),
    _recipe('cross.transmission', '跨市场', '跨市场事件传导',
        ('传导', '外溢', '联动', '先后', '事件窗口', '风险偏好', '相关性', '共振'),
        ('事件公开时点和时区', '相关市场的可观测交易窗口', '汇率、利率和替代解释'),
        '先按交易时区建立事件时间线，再区分融资成本、折现率、汇率和盈利渠道。检验时间先后与替代解释。',
        '同步变动只能说明共现。缺少事前预期、基准或干扰事件控制时，保留观察，不归因。',
        (('美联储货币政策传导', POLICY_TRANSMISSION),)),
)

_DEFAULTS = {'个股': 'stock.financial', 'ETF': 'etf.tracking', '行业': 'industry.cycle',
             '宏观': 'macro.activity', '跨市场': 'cross.returns',
             '指数': 'index.performance', '事件': 'event.timeline'}
_BY_REFERENCE = {(item['id'], item['version']): item for item in _RECIPES}
_NEGATED_REQUEST = re.compile(
    r'(?:不分析|不研究|不考虑|不计算|不查|无需|不要|不做|不需要)\s*[^，。；？！,;\n]*?(?=[，。；？！,;\n]|但|改为|只|仅|$)')
_BROAD_RESEARCH = re.compile(r'综合研究|全面分析|投资逻辑|投资机会')
_MARKETS = (
    re.compile(r'a股|沪深|上证|深证|(?<!\d)\d{6}\.(?:sh|sz|bj)(?![a-z0-9])'),
    re.compile(r'h股|港股|香港股市|恒生|(?<!\d)\d{4,5}\.hk(?![a-z0-9])'),
    re.compile(r'美股|纳斯达克|纳指|标普500|道琼斯|(?<![a-z])(?:nyse|nasdaq)(?![a-z])|'
               r'(?<![a-z0-9])[a-z]{1,5}\.(?:us|o|n)(?![a-z0-9])'),
    re.compile(r'欧股|欧洲股市|富时|德国dax|法国cac'),
    re.compile(r'日股|日本股市|日经|topix'),
)
_CROSS_SCOPE = re.compile(r'跨市场|跨境对照|a/h(?:股|溢价)?|(?<![a-z])ah(?:股|溢价)')
_CROSS_TASK = re.compile(r'比较|对比|对照|相对|差异|溢价|联动|传导|外溢|共振|相关性')
_READOUT = re.compile(r'^\s*(?:只|仅)?(?:查(?:询|看)?|获取|提取|读出|列出|核对)')
_INTERPRET = re.compile(r'解释|分析|研究|比较|对比|对照|传导|表现|趋势|走势|原因|判断|拆解|归因')
_INDUSTRY_SCOPE = re.compile(r'行业(?:景气|研究|格局|供需|竞争|样本|估值)|板块(?:机会|景气|投资|轮动)|'
                             r'产业链|(?:研究|分析|比较)[^，。？]{1,14}(?:行业|板块)')
_UNIVERSE_REVIEW = re.compile(r'(?:行业|板块)[^。；?!]{0,82}(?:覆盖率|覆盖情况|完整公司名单|样本覆盖)')
_EVENT_SCOPE = re.compile(r'首次公开|公告生效|事件(?:窗口|反应|时间线)|上市事件|政策事件|(?<![a-z])ipo(?![a-z])')
_METHODS = {
    'stock.financial': ('gross_margin_v1', 'net_profit_margin_v1', 'operating_cash_profit_ratio_v1'),
    'stock.disclosure': ('observed_price_return_v1',),
    'etf.tracking': ('management_custody_fee_comparison_v1', 'fee_difference_v2'),
    'etf.liquidity': ('etf_daily_nav_premium_v1',),
    'index.performance': ('observed_index_change_v1',),
    'industry.policy': ('observed_index_change_v1',),
}


def catalogue():
    """Return the same public recipe metadata to the App, file demo and Skill."""
    return dict(version=REGISTRY_VERSION, methods=[
        dict({key: deepcopy(value) for key, value in row.items() if key != 'terms'},
             calculations=list(_METHODS.get(row['id'], ()))) for row in _RECIPES
    ])


def _subject(task, question):
    entry = task.get('entry')
    if _CROSS_SCOPE.search(question) or (
            _CROSS_TASK.search(question) and sum(bool(pattern.search(question)) for pattern in _MARKETS) >= 2):
        return '跨市场'
    if entry == '产业链':
        return '行业'
    if entry not in ('指数', '宏观', '事件', '混合'):
        return entry
    universe_review = _UNIVERSE_REVIEW.search(question)
    if _READOUT.search(question) and not (_INTERPRET.search(question) or universe_review):
        return None
    macro_question = any(
        _match_count(question, row['terms']) for row in _RECIPES if row['subject'] == '宏观')
    if entry == '事件' and macro_question and _INTERPRET.search(question):
        return '宏观'
    if entry != '混合':
        return entry
    from .evidence import declared_security_codes
    codes = declared_security_codes(task)
    categories = set()
    if 'etf' in question.casefold() or any(
            re.fullmatch(r'(?:51|56|58)\d{4}\.SH|159\d{3}\.SZ', code) for code in codes):
        categories.add('ETF')
    if '个股' in question or any(
            re.fullmatch(r'(?:60[0135]|68[89])\d{3}\.SH|(?:00[0123]|30[01])\d{3}\.SZ', code)
            for code in codes):
        categories.add('个股')
    if _INDUSTRY_SCOPE.search(question) or universe_review:
        categories.add('行业')
    if not categories:
        from .index_sources import registered_market_index_names
        indices = registered_market_index_names(task)
        if macro_question:
            categories.add('宏观')
        elif (any(code in codes for code, _ in indices.values()) or
              any(re.search(re.escape(alias.casefold()) + r'(?![a-z0-9_])', question)
                  for alias in indices)):
            categories.add('指数')
        elif _EVENT_SCOPE.search(question):
            categories.add('事件')
    return next(iter(categories)) if len(categories) == 1 else None


def _match_count(text, terms):
    return sum(bool(re.search(r'(?<![a-z])' + re.escape(term) + r'(?![a-z])', text)) if term.isascii()
               else term in text for term in terms)


def select_methods(task):
    if task.get('intent') == DOCUMENT_CONTENT_INTENT:
        return []
    from .subject_scope import scope_question
    try:
        text = _NEGATED_REQUEST.sub(' ', scope_question(task).casefold())
        subject = _subject(task, text)
        cutoff = market_date(task['cutoff'])
    except (AttributeError, ContractError, KeyError, TypeError):
        return []
    if subject not in _DEFAULTS:
        return []
    eligible = [row for row in _RECIPES if row['subject'] == subject
                and (not row['industry'] or row['effective_from'] <= cutoff)]
    general = [row for row in eligible if not row['industry']]
    scores = {row['id']: _match_count(text, row['terms']) for row in general}
    industry = [row for row in eligible if row['industry'] and any(term in text for term in row['terms'])]
    if not (any(scores.values()) or industry or _BROAD_RESEARCH.search(text)
            or subject == '跨市场' and _CROSS_TASK.search(text)):
        return []
    primary = max(general, key=lambda row: (scores[row['id']], row['id'] == _DEFAULTS[subject]))
    chosen = [primary, *industry] if len(industry) == 1 else [primary]
    return [dict(id=row['id'], version=row['version'], title=row['title'],
                 reason='研究问题明确指定行业' if row['industry'] else '按研究对象与分析任务匹配')
            for row in chosen]


def method_selection(task):
    """Use the frozen selection for the same version and input, or match the current task."""
    binding = {key: task.get(key) for key in ('id', 'version', 'input_hash')}
    locked = task.get('research_methods')
    if isinstance(locked, dict) and locked.get('binding') == binding:
        refs = locked.get('methods')
        if isinstance(refs, list) and all(
                isinstance(ref, dict) and isinstance(ref.get('id'), str) and type(ref.get('version')) is int
                and (ref['id'], ref['version']) in _BY_REFERENCE for ref in refs):
            return locked
    return dict(version=REGISTRY_VERSION, binding=binding, methods=select_methods(task))


def method_guidance(task):
    selection = method_selection(task)
    guidance = []
    for ref in selection['methods']:
        row = _BY_REFERENCE[(ref['id'], ref['version'])]
        guidance.append(dict({key: row[key] for key in ('id', 'version', 'title', 'analysis', 'check')},
                             evidence=list(row['evidence'])))
    return guidance
