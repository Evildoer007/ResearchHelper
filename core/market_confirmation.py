"""所有研究取数目标的分析师确认与临时标的校验。

确认结果只在本次进程生效。自由文本不能绕过行业、证券真实性和流动性校验，
也不会自动写回人工维护的行业/ETF 库。
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace

from . import instruments, universe
from .provider import DataProvider, get_provider

MARKETS = ("A股", "港股", "跨市场")
_SPLIT_RE = re.compile(r"[、,，;/；]+")
_INVALID_SCOPE_RE = re.compile(r"^[\d\W_]+$")
# 不能用 ``\b``：Python 把中文也视为 word character，故“512690.SH的”在
# SH 与“的”之间没有词边界，用户最常见的自然语言写法会漏掉明确 ETF 代码。
_ETF_CODE_RE = re.compile(r"(?<![0-9A-Za-z])\d{6}\.(?:SH|SZ)(?![0-9A-Za-z])", re.I)
_COMMODITY_RE = re.compile(r"黄金|白银|原油|贵金属|商品|铜|豆粕|农产品", re.I)
# 问财会返回数百乃至上千条“相关基金”。动态发现只需为人工确认页补足少数候选，
# 绝不能为每一条结果发一次 iFinD 基础数据请求，否则确认页会在报告开始前假死数分钟。
_DISCOVERY_SCAN_LIMIT = 120
_DISCOVERY_BATCH_SIZE = 50
_DISCOVERY_TERM_LIMIT = 5
_PREFERRED_DAILY_AMOUNT = 1e8
# ETF 研究/报价候选的硬性流动性下限为 0.1 亿元（1,000 万元）。1 亿元仅代表
# 优选档，不能再把客户明确指定但日均成交较低的 ETF 直接排除。
_ETF_HARD_MIN_DAILY_AMOUNT = 1e7
_THEME_ETF_MIN_DAILY_AMOUNT = _ETF_HARD_MIN_DAILY_AMOUNT
_THEME_BASKET_MIN = 5
_THEME_BASKET_MAX = 20

# 这是检索语言，不是“主题→基金代码”白名单。它只补充行业中常见、且业务暴露
# 直接相关的表达；证券事实仍必须由 iFinD 官方简称、跟踪指数和流动性校验。
_ETF_SEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "机器人": ("机器人",),
    "人形机器人": ("人形机器人", "机器人"),
    "消费电子": ("消费电子", "智能终端", "电子"),
    "汽车电子": ("汽车电子", "智能驾驶", "智能汽车", "车联网", "汽车智能化"),
    "智能驾驶": ("智能驾驶", "智能汽车", "汽车电子", "车联网"),
    "智能汽车": ("智能汽车", "智能驾驶", "汽车电子", "车联网"),
    "光模块": ("光模块", "光通信", "通信设备", "5G通信"),
    "光通信": ("光通信", "光模块", "通信设备", "5G通信"),
    "人工智能": ("人工智能", "AI算力", "算力", "半导体", "通信设备"),
    "算力": ("算力", "AI算力", "人工智能", "半导体", "通信设备"),
    "黄金": ("黄金", "上海金", "贵金属"),
    "创新药": ("创新药", "港股创新药", "医药"),
    "白酒": ("白酒", "酒"),
    "酒": ("白酒", "酒"),
}


@dataclass
class Confirmation:
    market: str
    research_scope: str
    underlying_code: str = ""
    underlying_name: str = ""
    research_only: bool = False
    reason: str = ""                 # 选填，仅留痕
    research_theme: str = ""         # 细分研究主题；放在末尾以兼容旧位置参数
    theme_basket_codes: list[str] = field(default_factory=list)  # 分析师确认的本次主题公司篮子
    theme_basket_confirmed: bool = False
    theme_basket_evidence: dict[str, dict[str, str]] = field(default_factory=dict)
    # 仅当 ETF 的官方事实显示为“部分暴露”时使用：分析师必须同时勾选确认并
    # 写明映射理由，不能把这类工具当作自动通过的直接主题工具。
    partial_exposure_confirmed: bool = False
    # industry=按数据源标准行业成分研究；theme_basket=按分析师确认的主题公司篮子
    # 研究；theme_etf=按已核验主题 ETF 的真实成分研究。三者不能混用。
    research_mode: str = "industry"

    @property
    def industries(self) -> list[str]:
        return [x.strip() for x in _SPLIT_RE.split(self.research_scope or "") if x.strip()]

    @property
    def theme(self) -> str:
        """主题为空时兼容旧确认 JSON；新界面会要求分析师明确确认它。"""
        return self.research_theme.strip() or self.research_scope.strip()


@dataclass
class ValidationResult:
    confirmation: Confirmation
    ok: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    instrument: instruments.Instrument | None = None
    average_daily_amount: float | None = None
    exposure: "ExposureAssessment | None" = None


@dataclass
class ExposureAssessment:
    """一只 ETF/指数相对本次主题的统一暴露判定。

    候选排序和提交后的最终核验都调用同一套规则。``direct`` 可以自动通过；
    ``partial`` 必须由分析师确认映射理由；``unrelated`` 一律拒绝。证券、
    跟踪指数和流动性的真实性仍由 ``verify`` 的独立硬校验负责，不能人工绕过。
    """
    level: str
    scope: str
    reason: str
    official_name: str = ""
    tracking_index: str = ""
    major_constituents: list[str] = field(default_factory=list)
    matched_terms: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return {"direct": "直接暴露", "partial": "部分暴露", "unrelated": "不相关"}.get(
            self.level, "未判定")

    def display(self) -> str:
        facts = [f"主题暴露：{self.label}"]
        if self.tracking_index:
            facts.append(f"跟踪指数：{self.tracking_index}")
        if self.major_constituents:
            facts.append("主要成分：" + "、".join(self.major_constituents[:5]))
        return "；".join(facts + [self.reason])


def from_dict(raw: dict) -> Confirmation:
    basket_evidence = raw.get("theme_basket_evidence") or {}
    basket_evidence = basket_evidence if isinstance(basket_evidence, dict) else {}
    return Confirmation(
        market=str(raw.get("market") or "").strip(),
        research_scope=str(raw.get("research_scope") or "").strip(),
        research_theme=str(raw.get("research_theme") or "").strip(),
        underlying_code=_normalize_code(raw.get("underlying_code")),
        underlying_name=str(raw.get("underlying_name") or "").strip(),
        research_only=bool(raw.get("research_only", False)),
        reason=str(raw.get("reason") or "").strip(),
        theme_basket_codes=[_normalize_code(x) for x in (raw.get("theme_basket_codes") or [])
                            if _normalize_code(x)],
        theme_basket_confirmed="theme_basket_codes" in raw,
        theme_basket_evidence={
            _normalize_code(code): {
                "reason": str(value.get("reason") or "").strip()[:500],
                "source": str(value.get("source") or "").strip()[:500],
                "association": str(value.get("association") or "").strip()[:30],
            }
            for code, value in basket_evidence.items()
            if _normalize_code(code) and isinstance(value, dict)
        },
        partial_exposure_confirmed=bool(raw.get("partial_exposure_confirmed", False)),
        research_mode=str(raw.get("research_mode") or "industry").strip(),
    )


def validate(value: Confirmation) -> list[str]:
    """不访问数据源的结构校验；完整校验请用 :func:`verify`."""
    errors: list[str] = []
    if value.market not in MARKETS:
        errors.append("市场范围必须是 A股、港股或跨市场")
    if value.research_mode not in {"industry", "theme_basket", "theme_etf"}:
        errors.append("研究取数路径必须是标准行业、人工主题篮子或主题 ETF")
    if not value.research_scope.strip():
        errors.append("必须确认研究口径")
    elif _INVALID_SCOPE_RE.fullmatch(value.research_scope.strip()):
        errors.append("研究口径不能只是数字或符号")
    # 行业研究与产品报价是两件事：标准行业路径可以先完成研究，再在正式报价
    # 阶段选择挂钩工具。只有主题 ETF 路径需要 ETF 的真实成分作为研究数据源，
    # 因而必须在这里确定代码。
    if value.research_mode == "theme_etf" and not value.underlying_code.strip():
        errors.append("主题 ETF 取数路径必须选择一只经校验的 ETF")
    if value.research_mode == "theme_basket" and not value.theme_basket_codes:
        errors.append("人工主题篮子取数路径必须至少选择一只已核验主题公司")
    return errors


def needs_confirmation(brief) -> bool:
    """统一确认政策；普通单行业、明确证券及仅研究需求均不豁免。

    保留函数接口供 GUI/CLI 共用。确认是否已完成由本次 Brief 的市场确认契约
    判断，不再使用需求关键词、行业数量或产品诉求决定是否展示确认页。
    """
    return True


def _explicit_catalogued_etf(brief) -> str:
    """返回用户原文直接点名的、目录已核验 ETF；否则返回空。"""
    raw = str(getattr(brief, "原始需求", "") or "").upper()
    for match in _ETF_CODE_RE.finditer(raw):
        code = match.group(0).upper()
        item = instruments.get(code)
        if item is not None and "ETF" in item.类型.upper():
            return code
    return ""


def _explicit_stock_codes(brief) -> list[str]:
    """返回客户原文明确写出的 A 股个股代码（不含 ETF）。

    这只负责判断客户是否提出了“公司研究篮子”的明确输入，证券真实性仍由
    ``brief.parse`` 的代码核验链处理。不能只看 ``候选标的``，否则 LLM 漏掉
    客户点名公司时，又会被误判成“仅 ETF 需求”。
    """
    raw = str(getattr(brief, "原始需求", "") or "")
    codes: list[str] = []
    for match in _ETF_CODE_RE.finditer(raw):
        code = match.group(0).upper()
        if not universe._is_fund(code) and code not in codes:
            codes.append(code)
    return codes


def _normalize_code(value: object) -> str:
    code = str(value or "").strip().upper()
    # .SS 是 Bloomberg/Yahoo 常见的上海市场后缀；iFinD 使用 .SH。
    # 同时兼容部分终端的 XSHG/XSHE 写法，避免把格式差异误报成“证券不存在”。
    code = re.sub(r"\.SS$", ".SH", code)
    code = re.sub(r"\.XSHG$", ".SH", code)
    code = re.sub(r"\.XSHE$", ".SZ", code)
    if re.fullmatch(r"\d{6}", code):
        return code + (".SH" if code.startswith(("5", "6")) else ".SZ")
    return code


def _pick_col(table: dict, *starts: str) -> list | None:
    for start in starts:
        for col, values in table.items():
            if str(col).startswith(start):
                return values if isinstance(values, list) else None
    return None


def _discovery_query(brief) -> str:
    """检索与暴露校验共用短研究主题，不使用完整问题或宽行业代替主题。"""
    return _proposed_theme(brief)


def _is_short_theme(value: str) -> bool:
    return bool(value and len(value) <= 20 and value.upper() not in {
        "ETF", "A股", "行业", "板块", "主题", "市场",
    } and not _ETF_CODE_RE.search(value) and not _INVALID_SCOPE_RE.fullmatch(value)
        and not re.fullmatch(r"[一二三四五六七八九十百0-9半]+(?:个)?(?:月|周|季度|年)(?:内)?", value)
        and not re.search(r"分析|投资机会|市场走势|推荐|未来|怎么样|展望|[，,。；;！？?]", value))


def _discovery_terms(brief) -> tuple[str, ...]:
    """形成有限、可审计的 ETF 检索词，不让 LLM 直接猜证券代码。

    顺序为：需求解析器给出的短主题词 → 确定性同义词 → 原始主题兜底。
    最多执行有限次问财检索，避免确认页因全市场扫描长时间无反馈。
    """
    theme = _discovery_query(brief)
    if not theme:
        return ()
    components = re.split(r"与|和|及", theme)
    seeds = [theme, *components,
             *(str(x or "").strip() for x in (getattr(brief, "ETF检索词", []) or []))]
    expanded: list[str] = [theme]
    for key in sorted(_ETF_SEARCH_ALIASES, key=len, reverse=True):
        aliases = _ETF_SEARCH_ALIASES[key]
        if key.lower() in theme.lower():
            expanded.extend(aliases)
    expanded.extend(seeds)
    source_text = " ".join([str(getattr(brief, "原始需求", "") or ""),
                            str(getattr(brief, "主题", "") or "")]).lower()
    aliases = {alias.lower() for key, values in _ETF_SEARCH_ALIASES.items()
               if key.lower() in theme.lower() for alias in values}
    out: list[str] = []
    for term in expanded:
        term = str(term or "").strip()
        if not _is_short_theme(term):
            continue
        if term != theme and term.lower() not in aliases and term not in components:
            if term.lower() not in source_text or term.lower() in theme.lower():
                continue
        # 允许“AI”等英文主题，但不接受整段需求作为检索词。
        if term not in out:
            out.append(term)
        if len(out) >= _DISCOVERY_TERM_LIMIT:
            break
    return tuple(out)


def _proposed_theme(brief) -> str:
    """给确认页一个可编辑的细分研究主题。

    只能返回可识别的短主题词，绝不能把“酒ETF市场走势与投资机会分析”之类
    的整句标题塞给问财做概念股检索。
    """
    text = " ".join([str(getattr(brief, "原始需求", "") or ""),
                     str(getattr(brief, "主题", "") or "")])
    parsed_theme = str(getattr(brief, "研究主题", "") or "").strip()
    # 先处理由两个细分环节共同构成的主题，避免在确认页只留下其中一个关键词。
    if "固态电池" in text and "锂电池" in text:
        return "锂电池与固态电池"
    for word in ("固态电池", "锂电池", "电池", "汽车电子", "消费电子", "智能驾驶", "智能汽车", "光模块", "光通信", "黄金",
                 "白银", "原油", "贵金属", "人形机器人", "机器人", "创新药", "券商", "证券", "白酒", "酒", "消费",
                 "半导体", "人工智能", "算力", "医药", "互联网"):
        if word.lower() in text.lower():
            if (_is_short_theme(parsed_theme) and parsed_theme.lower() in text.lower()
                    and word.lower() in parsed_theme.lower()):
                return parsed_theme
            return word
    # 新主题不能只依赖上面的常见词。优先使用解析器提供且能在原需求中定位的
    # 短检索词，再使用短行业名；完整研报标题和证券代码不能用于概念股检索。
    seeds = [parsed_theme, *(getattr(brief, "ETF检索词", []) or []),
             *(getattr(brief, "涉及板块", []) or [])]
    for value in seeds:
        term = str(value or "").strip()
        if _is_short_theme(term) and term.lower() in text.lower():
            return term
    # 未收录的新主题也支持简单自然语言问法；复杂问题不强行截出一个主题。
    raw = str(getattr(brief, "原始需求", "") or "").strip()
    raw = re.sub(r"^(?:请问|请|帮我|分析一下|分析|看看)\s*", "", raw)
    raw = re.sub(r"^(?:未来|近|最近|今后)[一二三四五六七八九十百0-9半]+(?:个)?(?:月|周|季度|年)(?:内)?\s*", "", raw)
    raw = re.sub(r"(?:行业|板块)?(?:的)?(?:投资机会|投资价值|市场走势|怎么样|展望)(?:分析)?[？?。！!]*$", "", raw)
    raw = re.sub(r"(?:行业|板块)$", "", raw).strip()
    if _is_short_theme(raw):
        return raw
    return ""


def _discovery_audit(brief, stage: str, status: str, reason: str, *, code: str = "", term: str = "") -> None:
    """只记录公开检索词、代码及准入原因，不记录账号、凭证或请求正文。"""
    if not hasattr(brief, "_etf_discovery_audit"):
        brief._etf_discovery_audit = []
    brief._etf_discovery_audit.append({"stage": stage, "status": status,
                                       "reason": reason, "code": code, "term": term})


def _discovery_keywords(brief) -> tuple[str, ...]:
    """用于给问财返回的基金行排序/预筛，不把它当成最终主题暴露证明。"""
    terms = list(_discovery_terms(brief))
    # 排序词可比实际请求略宽，但不能宽到“科技/电子”这种几乎什么都能命中的词。
    extras = {
        "消费电子": ("智能终端",),
        "创新药": ("医疗",),
        "黄金": ("上海金", "贵金属"),
        "光模块": ("光通信", "通信设备", "通信"),
        "光通信": ("光模块", "通信设备", "通信"),
    }
    for key, values in extras.items():
        if any(key in term for term in terms):
            terms.extend(values)
    return tuple(dict.fromkeys(term for term in terms if term))


def _discovery_rows(codes: list, names: list, brief) -> list[tuple[str, str]]:
    """保留问财的相关性顺序，先选名称与主题相关的有限行再做官方代码核验。"""
    seen: set[str] = set()
    rows: list[tuple[str, str, int]] = []
    keywords = tuple(word.lower() for word in _discovery_keywords(brief) if word)
    for index, raw_code in enumerate(codes):
        code = _normalize_code(raw_code)
        name = str(names[index] if index < len(names) else "").strip()
        if not _ETF_CODE_RE.fullmatch(code) or not name or code in seen:
            continue
        seen.add(code)
        lowered = name.lower()
        # 0=直接主题命中，1=同一产业链表达，2=问财相关结果中的兜底项。
        score = next((rank for rank, word in enumerate(keywords) if word in lowered), len(keywords))
        rows.append((code, name, score))
    matched = [row for row in rows if row[2] < len(keywords)]
    # 先按主题命中强度，再保持问财原始排序；没有名字命中时仍保留前若干条，
    # 使诸如“通信设备 ETF”这类名称较宽的工具仍能进入后续正式主题校验。
    ranked = (matched or rows)
    ranked.sort(key=lambda row: row[2])
    return [(code, name) for code, name, _score in ranked[:_DISCOVERY_SCAN_LIMIT]]


def discover_etfs(brief, *, provider: DataProvider | None = None, limit: int = 8) -> list[dict]:
    """动态发现 ETF 候选。

    常用池只用于优先展示；这里通过 iFinD 问财找基金代码和简称，随后仍由 ``verify``
    校验代码、主题暴露和近 20 日流动性。接口不可用时返回空列表，绝不编造代码。
    """
    from .provider import iFinDProvider

    provider = provider or get_provider()
    if not isinstance(provider, iFinDProvider) or not provider.available():
        _discovery_audit(brief, "provider", "failed", "iFinD 基金动态检索不可用；请检查研究数据配置")
        return []
    merged_rows: list[tuple[str, str]] = []
    seen_rows: set[str] = set()
    search_terms = _discovery_terms(brief)
    if not search_terms:
        _discovery_audit(brief, "theme", "failed", "未识别出短研究主题；请确认研究主题后重试")
        return []
    try:
        provider._ensure_login()
        import iFinDPy as ths
    except Exception:
        _discovery_audit(brief, "provider", "failed", "iFinD 登录或接口加载失败")
        return []
    for term in search_terms:
        try:
            query = f"{term} ETF 基金代码 基金简称 基金全称 跟踪指数"
            data = ths.THS_iwencai(query, "fund")
            if data.get("errorcode", -1) != 0:
                _discovery_audit(brief, "search", "failed", "基金检索接口未成功返回", term=term)
                continue
            provider.total_data_vol += int(data.get("dataVol", 0) or 0)
            tables = data.get("tables") or []
            table = (tables[0].get("table") or {}) if tables else {}
            codes = _pick_col(table, "基金代码", "证券代码", "代码") or []
            names = _pick_col(table, "基金简称", "证券简称", "基金全称", "简称") or []
            selected_rows = _discovery_rows(codes, names, brief)
            _discovery_audit(brief, "search", "completed",
                             f"基金检索返回 {len(codes)} 行，名称相关性预筛保留 {len(selected_rows)} 行（非最终主题判定）", term=term)
            for code, name in selected_rows:
                if code not in seen_rows:
                    merged_rows.append((code, name))
                    seen_rows.add(code)
        except Exception:
            # 一个同义词查询失败不能抹掉此前已取得的候选；继续尝试其余受控词。
            _discovery_audit(brief, "search", "failed", "基金检索或结果读取失败", term=term)
            continue
    # 检索结果不是候选即真相：先以数据源简称、主题词和近 20 日成交额做轻量预检。
    # 正式选择时 ``verify`` 仍会再跑一遍完整核验（因此不会信任界面传回的标签）。
    # 注意必须批量取官方简称。此前逐只调用会把一次“光模块 ETF”检索变成近千次
    # THS_BasicData 请求；这既不提升准确性，也会让确认页长时间没有任何反馈。
    from . import history
    rows = merged_rows[:_DISCOVERY_SCAN_LIMIT]
    actual_names: dict[str, str] = {}
    tracking_codes: dict[str, str] = {}
    for offset in range(0, len(rows), _DISCOVERY_BATCH_SIZE):
        batch = [code for code, _name in rows[offset:offset + _DISCOVERY_BATCH_SIZE]]
        try:
            basic = provider.get_basic(batch, ["ths_stock_short_name_stock", "ths_tracking_index_code_fund"])
            if not getattr(basic, "ok", False):
                # 部分账号缺少跟踪指数字段权限，先尝试单独核验简称。
                basic = provider.get_basic(batch, ["ths_stock_short_name_stock"])
        except Exception:
            _discovery_audit(brief, "identity", "failed", "官方基金信息批量读取失败")
            continue
        if not getattr(basic, "ok", False):
            _discovery_audit(brief, "identity", "failed", "官方基金简称核验接口未返回可用数据")
            continue
        for code in batch:
            actual = str(_first_value(basic, code, "ths_stock_short_name_stock") or "").strip()
            tracking = str(_first_value(basic, code, "ths_tracking_index_code_fund") or "").strip()
            if actual:
                actual_names[code] = actual
            if tracking:
                tracking_codes[code] = tracking

    tracking_names: dict[str, str] = {}
    unique_tracking = list(dict.fromkeys(tracking_codes.values()))
    for offset in range(0, len(unique_tracking), _DISCOVERY_BATCH_SIZE):
        batch = unique_tracking[offset:offset + _DISCOVERY_BATCH_SIZE]
        try:
            basic = provider.get_basic(batch, ["ths_stock_short_name_stock"])
        except Exception:
            _discovery_audit(brief, "index", "failed", "跟踪指数名称读取失败；最终提交仍须核验真实指数")
            continue
        if not getattr(basic, "ok", False):
            continue
        for code in batch:
            name = str(_first_value(basic, code, "ths_stock_short_name_stock") or "").strip()
            if name:
                tracking_names[code] = name

    out: list[dict] = []
    exposure_scope = _proposed_theme(brief)
    if not rows:
        _discovery_audit(brief, "search", "rejected", "未返回可核验的基金代码与名称")
    for code, _searched_name in rows:
        actual_name = actual_names.get(code, "")
        if "ETF" not in actual_name.upper():
            _discovery_audit(brief, "identity", "rejected", "未取得官方 ETF 名称或证券不是 ETF", code=code)
            continue
        tracking_code = tracking_codes.get(code, "")
        tracking_name = tracking_names.get(tracking_code, "")
        commodity = bool(_COMMODITY_RE.search(" ".join([
            str(getattr(brief, "原始需求", "") or ""), actual_name,
        ])))
        item = instruments.Instrument(code, actual_name, actual_name,
                                      "商品ETF" if commodity else "行业ETF",
                                      [], "本次动态发现", tracking_code)
        assessment = assess_exposure(
            exposure_scope, item, official_name=actual_name,
            tracking_index=tracking_name or tracking_code,
        )
        if assessment.level == "unrelated":
            _discovery_audit(brief, "exposure", "rejected", assessment.reason, code=code)
            continue
        try:
            values = history.series(code, "ths_amt_stock", years=1, provider=provider,
                                    drop_nonpositive=False)
            recent = [float(x) for x in values[-20:] if isinstance(x, (int, float)) and math.isfinite(x)]
        except Exception:
            _discovery_audit(brief, "liquidity", "rejected", "成交额历史读取失败，无法核验流动性", code=code)
            continue
        if len(recent) < 10:
            _discovery_audit(brief, "liquidity", "rejected", "成交额有效样本不足 10 日", code=code)
            continue
        amount = sum(recent) / len(recent)
        if amount < _THEME_ETF_MIN_DAILY_AMOUNT:
            _discovery_audit(brief, "liquidity", "rejected",
                             f"近20日日均成交额 {amount / 1e8:.3f} 亿元，低于0.1亿元门槛", code=code)
            continue
        _discovery_audit(brief, "candidate", "completed", "官方主题关联及基础流动性预检通过", code=code)
        evidence_text = " ".join([actual_name, tracking_name, tracking_code])
        matched = [term for term in search_terms
                   if term.lower() in evidence_text.lower()]
        exposure_note = assessment.display()
        liquidity_label = ("优选" if amount >= _PREFERRED_DAILY_AMOUNT else
                           "可选但低于1亿元优选门槛")
        direct_rank = next((rank for rank, term in enumerate(search_terms)
                            if term.lower() in evidence_text.lower()), len(search_terms))
        out.append({"code": code, "name": actual_name, "type": item.类型,
                    "note": (f"iFinD 动态发现；检索命中“{'、'.join(matched) or search_terms[0]}”；"
                             f"{exposure_note}；近20日日均成交额 {amount / 1e8:.2f} 亿元（{liquidity_label}）"),
                    "origin": "动态发现", "average_daily_amount": amount,
                    "exposure": asdict(assessment), "exposure_level": assessment.level,
                    "_direct_rank": direct_rank})
    # 主题直接命中优先；同等主题相关度下选择流动性更好的工具。内部排序字段不传给 UI。
    out.sort(key=lambda value: (value.get("_direct_rank", 999),
                                -float(value.get("average_daily_amount") or 0)))
    for value in out:
        value.pop("_direct_rank", None)
    return out[:limit]


def _suggestions(brief, *, provider: DataProvider | None = None) -> list[dict]:
    brief._etf_discovery_audit = []
    text = " ".join([
        str(getattr(brief, "原始需求", "") or ""),
        str(getattr(brief, "主题", "") or ""),
        *(getattr(brief, "涉及板块", []) or []),
    ]).lower()
    codes: list[str] = []
    # 复用已维护的工具目录，而不是为每个新主题增加代码分支。
    # 目录匹配只生成待核验候选；最终提交仍须取得官方事实与历史成交额。
    for term in _discovery_terms(brief):
        codes.extend(item.代码 for item in instruments.find(term) if "ETF" in item.类型.upper())
    if "互联网" in text:
        codes += ["513050.SH", "513130.SH"]
    if ("创新药" in text or "医药" in text) and getattr(brief, "市场范围", "A股") == "A股":
        codes += ["159992.SZ", "512010.SH"]
    if re.search(r"ai|算力|人工智能", text, re.I):
        codes += ["515980.SH", "515050.SH", "512480.SH"]
    if "光模块" in text or "光通信" in text:
        # 仅作确认页的起始候选；仍保留 iFinD 动态发现来补充白名单外工具。
        codes += ["515050.SH"]
    for target in getattr(brief, "候选标的", []) or []:
        code = str(getattr(target, "代码", "") or "").upper()
        if code and getattr(target, "可用", False) and instruments.get(code):
            codes.append(code)
    exposure_scope = _proposed_theme(brief)
    out = []
    for code in dict.fromkeys(codes):
        item = instruments.get(code)
        if item:
            assessment = assess_exposure(
                exposure_scope, item, official_name=item.官方名,
                tracking_index=item.跟踪指数,
            )
            # 常用池只是发现入口；不能把已知不相关 ETF 呈现为可选主题工具。
            if assessment.level == "unrelated":
                _discovery_audit(brief, "exposure", "rejected", assessment.reason, code=code)
                continue
            out.append({"code": item.代码, "name": item.简称, "type": item.类型,
                        "note": item.说明 + "；" + assessment.display(), "origin": "常用池",
                        "exposure": asdict(assessment), "exposure_level": assessment.level,
                        "tracking_index": assessment.tracking_index})
            _discovery_audit(brief, "catalog", "completed", "常用池主题匹配；提交时仍须核验官方事实与流动性", code=code)
    known = {item["code"]: item for item in out}
    # 常用池只作起点，候选不足时再从 iFinD 动态扩展；不再每次无差别扫描全量基金。
    remaining = max(0, 8 - len(out))
    if remaining:
        for item in discover_etfs(brief, provider=provider, limit=remaining):
            code = item["code"]
            if code not in known:
                out.append(item)
                known[code] = item
            else:
                # 动态官方信息更完整时覆盖展示事实，保留两路来源，不重复展示代码。
                existing = known[code]
                origins = list(dict.fromkeys([existing.get("origin", ""), item.get("origin", "")]))
                existing.update(item)
                existing["origins"] = origins
    return out[:8]


def discover_theme_companies(brief, *, provider: DataProvider | None = None,
                             limit: int = _THEME_BASKET_MAX) -> list[dict]:
    """从 iFinD 动态发现细分主题的 A 股公司，供后续关联核实。

    问财的“概念股”结果只提供候选范围，不直接当成业务关联事实：代码与简称
    批量走 THS_BasicData 核验。它是一次运行内的候选池，不写回人工库。
    """
    from .provider import iFinDProvider

    provider = provider or get_provider()
    if not isinstance(provider, iFinDProvider) or not provider.available():
        return []
    theme = _proposed_theme(brief)
    if not theme:
        return []
    try:
        # 市值排序用于控制概念股噪声；分析师仍可在确认页排除不纯的公司。
        leaders = universe._query_iwencai(
            f"{theme} 概念股 总市值排名前{_THEME_BASKET_MAX} 上市日期", provider)
    except Exception:
        return []
    leaders = [item for item in leaders
               if re.fullmatch(r"(?:00|30|60|68)\d{4}\.(?:SH|SZ)", str(item.代码 or "").upper())]
    if not leaders:
        return []
    leaders = leaders[:limit]
    codes = [item.代码.upper() for item in leaders]
    basic = provider.get_basic(codes, ["ths_stock_short_name_stock"])
    if not getattr(basic, "ok", False):
        return []
    out: list[dict] = []
    for item in leaders:
        code = item.代码.upper()
        actual_name = str(_first_value(basic, code, "ths_stock_short_name_stock") or "").strip()
        if not actual_name or universe._is_fund(code):
            continue
        out.append({
            "code": code,
            "name": actual_name,
            "origin": "iFinD 动态主题发现",
            "note": f"iFinD 问财“{theme} 概念股”候选；仅代码与简称已核验，主题关联待核实",
            "market_value": item.总市值,
            "association": "concept_only",
            "reason": "概念股检索命中；尚无可核实的具体业务关联原文",
            "source_title": "iFinD 问财概念股检索",
            "source_url": "",
        })
    return out


def _enrich_theme_company_evidence(candidates: list[dict], theme: str,
                                   provider: DataProvider | None) -> list[dict]:
    """Check a bounded set of system candidates against source-linked passages.

    Never turn a search failure, concept inclusion, or a model suggestion into
    automatic core membership.  The analyst may still supply a source manually.
    """
    from .provider import iFinDProvider

    if not isinstance(provider, iFinDProvider) or not provider.available():
        return candidates
    from .evidence_discovery import build_searcher
    from .theme_company_evidence import search_company_association

    searcher = build_searcher()
    checked = 0
    for candidate in candidates:
        if candidate.get("association") == "client_named":
            continue
        if checked >= 8:  # confirmation must not issue 20 serial remote requests
            candidate["note"] += "；自动关联检索限额已达，请分析师补充依据"
            continue
        checked += 1
        assessment = search_company_association(theme, str(candidate.get("name") or ""), searcher)
        candidate.update(assessment)
        diagnostics = searcher.take_diagnostics()
        if diagnostics and not any(item.get("ok") for item in diagnostics):
            candidate.update(association="unverified", reason="自动关联检索服务暂不可用；未形成业务关联证明",
                             source_title="", source_url="")
            candidate["note"] = "关联检索不可用；可人工补充依据或改选其他取数路径"
            continue
        if assessment["association"] in {"direct", "indirect"}:
            candidate["note"] = "已检索到披露原文；请核对后决定是否纳入研究篮子"
        else:
            candidate["note"] = "证券身份已核验；主题业务关联仍待分析师核实"
    return candidates


def _theme_basket_candidates(brief, *, provider: DataProvider | None = None) -> list[dict]:
    """仅为“细分主题→标准行业”模式生成公司研究篮子。"""
    explicit_stock_codes = _explicit_stock_codes(brief)
    if _explicit_catalogued_etf(brief) and not explicit_stock_codes:
        return []
    proposed_theme = _proposed_theme(brief)
    proposed_scope = "、".join(getattr(brief, "宽口径成分行业", []) or
                                 getattr(brief, "涉及板块", []) or [])
    scope_parts = {part.strip() for part in _SPLIT_RE.split(proposed_scope) if part.strip()}
    out: list[dict] = []
    known: set[str] = set()

    # 用户点名的公司不依赖 LLM 是否复述，也不依赖问财是否刚好收录这个主题词。
    # 这些公司已在 Brief 解析阶段逐只完成“代码存在 + 简称匹配”校验；确认页要把
    # 它们作为可删减、默认勾选的核心样本展示，而不是被同一需求中的 ETF 遮蔽。
    by_code = {str(getattr(item, "代码", "") or "").upper(): item
               for item in (getattr(brief, "候选标的", []) or [])}
    for code in explicit_stock_codes:
        item = by_code.get(code)
        if item is None or not getattr(item, "可用", False):
            continue
        out.append({"code": code, "name": item.名称, "origin": "客户明确点名",
                    "note": "客户在原始需求中明确列示；证券身份已核验，业务关联仍须在研究中分析",
                    "association": "client_named", "reason": "客户在原始需求中明确点名该公司",
                    "source_title": "客户原始需求", "source_url": "", "core": True})
        known.add(code)

    # 普通行业、以及未能明确识别细分主题的需求，仍不把 LLM 自行给出的候选个股
    # 伪装成主题核心样本；但上面的“客户点名”样本必须保留。
    if not proposed_theme or not proposed_scope or proposed_theme in scope_parts:
        return out
    for item in (getattr(brief, "候选标的", []) or []):
        code = str(getattr(item, "代码", "") or "").upper()
        if not code or not getattr(item, "可用", False) or universe._is_fund(code) or code in known:
            continue
        out.append({"code": code, "name": item.名称, "origin": "需求解析建议（待核实）",
                    "note": "解析器提出该公司；仅代码与简称已核验，主题关联待核实",
                    "association": "unverified", "reason": "需求解析建议，尚无可核实的具体业务关联原文",
                    "source_title": "", "source_url": "", "core": False})
        known.add(code)

    # 只有细分主题与映射行业不同才扩展。例如“光模块→通信设备”；普通“证券→证券”
    # 继续用行业整体法，不额外伪造一套主题公司池。
    if proposed_theme and proposed_scope and proposed_theme != proposed_scope:
        for item in discover_theme_companies(brief, provider=provider):
            code = str(item.get("code") or "").upper()
            if code and code not in known:
                out.append(item)
                known.add(code)
    enriched = _enrich_theme_company_evidence(out[:_THEME_BASKET_MAX], proposed_theme, provider)
    rank = {"client_named": 0, "direct": 1, "indirect": 2, "concept_only": 3, "unverified": 4}
    return sorted(enriched, key=lambda item: rank.get(str(item.get("association") or ""), 4))


def _scope_options(brief, theme: str) -> list[str]:
    """为确认页生成*系统建议的标准行业*，不把细分主题塞回校验字段。

    ``涉及板块`` 是需求解析结果，常会同时含有“通信设备、光模块”这类
    行业 + 细分主题。旧界面把它们用顿号直接拼成一个可自由编辑的输入框，
    等于要求分析师知道哪一个词是数据源认可的行业名；随后又把同一串文本送
    给严格校验，必然造成“系统自己填、系统自己拒绝”的循环。

    这些候选经数据源核验后可供标准行业路径取数。是否采用行业成分、人工
    公司篮子或 ETF 真实成分，仍由分析师在确认页明确选择。
    """
    from .signals import _THEME_TO_SECTOR

    options: list[str] = []
    normalized_theme = (theme or "").strip()
    # 先采用人工维护的保守主题→行业映射。例如光模块→通信设备。
    mapped_theme = _THEME_TO_SECTOR.get(normalized_theme)
    if mapped_theme:
        options.append(mapped_theme)

    raw_scopes = list(getattr(brief, "宽口径成分行业", []) or
                      getattr(brief, "涉及板块", []) or [])
    for raw in raw_scopes:
        candidate = str(raw or "").strip()
        if not candidate:
            continue
        # 细分主题本身不能当“标准行业”再次展示；有已知映射时改成映射结果。
        resolved = _THEME_TO_SECTOR.get(candidate, candidate)
        # 主题也可能恰好是标准行业（如消费电子）。不要在数据源核验之前
        # 因名称相同就排除；细分主题由下面的行业真实性校验过滤。
        if resolved and resolved not in options:
            options.append(resolved)
    return options


def proposal(brief, *, provider: DataProvider | None = None) -> dict:
    sectors = list(getattr(brief, "涉及板块", []) or [])
    parts = list(getattr(brief, "宽口径成分行业", []) or [])
    raw = str(getattr(brief, "原始需求", "") or "")
    if not parts and "汽车电子" in raw:
        parts = ["汽车", "电子"]
    elif not parts and re.search(r"AI\s*算力|算力产业链", raw, re.I):
        parts = ["半导体", "通信设备", "计算机设备", "元件"]
    suggested = _suggestions(brief, provider=provider)
    basket_candidates = _theme_basket_candidates(brief, provider=provider)
    # 缓存于本次 Brief，后端只接受这份候选池中的代码，不能信任 UI 回传的任意代码。
    from .brief import TargetRef
    brief.主题篮子候选池 = [
        TargetRef(str(item.get("name") or ""), str(item.get("code") or ""), "ok:本次主题候选")
        for item in basket_candidates
    ]
    brief.主题篮子候选依据 = {
        str(item.get("code") or "").upper(): {
            key: str(item.get(key) or "")
            for key in ("association", "reason", "source_title", "source_url", "origin")
        }
        for item in basket_candidates if item.get("code")
    }
    audit = list(getattr(brief, "_etf_discovery_audit", []) or [])
    reasons = list(dict.fromkeys(row["reason"] for row in audit if row["status"] in {"failed", "rejected"}))
    discovery_note = ""
    if not suggested:
        discovery_note = "未形成 ETF 候选：" + "；".join(reasons[:4] or ["未发现与研究主题相关的可核验工具"])
        discovery_note += "。可检查数据配置、确认研究主题或输入已核实的 ETF 代码继续校验。"
    elif any(row["status"] == "failed" for row in audit):
        discovery_note = "已显示可核验候选，但动态检索存在缺口：" + "；".join(
            dict.fromkeys(row["reason"] for row in audit if row["status"] == "failed"))
    proposed_theme = _proposed_theme(brief)
    # 展示路径不等于采纳 ETF。即使短主题识别遗漏，已有候选也应允许分析师
    # 按原始研究描述选择 ETF；这个兜底只用于确认页，不能回灌概念股检索。
    etf_scope = proposed_theme or (
        str(getattr(brief, "研究主题", "") or getattr(brief, "主题", "") or raw).strip()
        if suggested else ""
    )
    scope_options = _scope_options(brief, proposed_theme)
    # 确认页的“系统建议”不能先展示一个数据源不承认的口径，再让分析师替系统
    # 承担校验失败。除商品主题外，在弹窗出现前先用同一数据源过滤；保留的才是
    # 可提交的 A 股行业选项。
    if scope_options and not _COMMODITY_RE.search(raw):
        try:
            valid, _invalid = universe.validate_industries(scope_options, provider=provider)
            scope_options = valid
        except Exception:
            # 数据源临时不可用时保留映射结果；提交阶段仍会再次校验并给出真实原因。
            pass
    return {
        "original_market": getattr(brief, "市场范围", "A股"),
        "topic": getattr(brief, "主题", ""),
        "event_path": getattr(brief, "事件路径", "非事件"),
        "impact_branches": [
            {
                "name": item.名称, "relation": item.传导关系, "ashare_object": item.A股对象,
                "evidence_needed": item.待验证证据, "quote_direction": item.可报价工具方向,
            }
            for item in (getattr(brief, "候选影响分支", []) or [])
        ],
        "proposed_theme": proposed_theme or etf_scope,
        # 兼容 CLI/旧调用方；新 GUI 使用 ``verified_scope_options`` 的 data 值，
        # 而不是让分析师辨认和编辑一串行业/主题混合文本。
        "proposed_scope": "、".join(scope_options),
        "verified_scope_options": scope_options,
        # 细分主题找到了可验证 ETF 时，不再要求分析师先猜一个标准行业。提交后
        # 会以 ETF 官方跟踪指数及真实成分完成研究口径校验与取数。
        "theme_etf_route": bool(etf_scope),
        "theme_etf_scope": etf_scope,
        "proposed_sectors": sectors,
        "proposed_industries": parts,
        "theme_basket_candidates": basket_candidates,
        "theme_basket_min": _THEME_BASKET_MIN,
        "theme_basket_max": _THEME_BASKET_MAX,
        # 三条路径互斥：有需人工确认的细分主题公司时走主题篮子；主题本身就是
        # 数据源已验证行业时直接走行业成分；只有没有可用行业/篮子时才建议 ETF。
        "recommended_research_mode": (
            "theme_basket" if (
                any(item.get("association") == "client_named" for item in basket_candidates)
                or sum(item.get("association") == "direct" for item in basket_candidates) >= _THEME_BASKET_MIN
            )
            else ("industry" if proposed_theme in scope_options
                  else ("theme_etf" if suggested and etf_scope
                        else ("industry" if scope_options
                              else ("theme_basket" if basket_candidates else ("theme_etf" if etf_scope else "industry")))))
        ),
        "reason": getattr(brief, "板块理由", ""),
        "suggested_instruments": suggested,
        "etf_search_terms": list(_discovery_terms(brief)),
        "etf_discovery_audit": audit,
        "discovery_notice": discovery_note,
        "notice": "候选包含常用池与 iFinD 动态发现结果；选择或输入代码后，系统才会校验真实性、主题暴露和近20日流动性。未确认不进入研究。",
    }


def _first_value(result, code: str, indicator: str):
    data = (getattr(result, "data", {}) or {}).get(code) or {}
    value = data.get(indicator)
    if isinstance(value, list):
        return value[0] if value else None
    if value is not None:
        return value
    return getattr(result, "value", None)


def _is_cross_border(item: instruments.Instrument | None, name: str) -> bool:
    if item and instruments.T_OVERSEA in item.标签:
        return True
    return any(x in name.upper() for x in ("QDII", "恒生", "港股", "中概", "H股"))


_EXPOSURE_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    # ``direct`` 是基金官方名称、跟踪指数或主要成分直接说明了本主题；``partial``
    # 是较宽行业/相邻产业链。二者不能再混为一个“主题匹配”的布尔值。
    "锂电池与固态电池": {
        "direct": ("锂电池", "固态电池", "新能源电池"),
        "partial": ("电池", "新能源车", "新能源汽车", "新能源", "电力设备", "锂"),
    },
    "固态电池": {
        "direct": ("固态电池",),
        "partial": ("锂电池", "新能源电池", "电池", "新能源车", "新能源汽车", "新能源", "电力设备"),
    },
    "锂电池": {
        "direct": ("锂电池", "新能源电池"),
        "partial": ("电池", "新能源车", "新能源汽车", "新能源", "电力设备", "锂"),
    },
    "汽车电子": {
        "direct": ("汽车电子", "智能驾驶", "智能汽车", "车联网", "汽车智能化", "智能车"),
        "partial": ("汽车", "电子", "新能源车", "新能源汽车"),
    },
    "智能驾驶": {
        "direct": ("智能驾驶", "智能汽车", "汽车电子", "车联网", "汽车智能化", "智能车"),
        "partial": ("汽车", "新能源车", "新能源汽车"),
    },
    "智能汽车": {
        "direct": ("智能汽车", "智能驾驶", "汽车电子", "车联网", "汽车智能化", "智能车"),
        "partial": ("汽车", "新能源车", "新能源汽车"),
    },
    "光模块": {
        "direct": ("光模块", "光通信", "光器件"),
        "partial": ("通信设备", "通信", "5g"),
    },
    "光通信": {
        "direct": ("光通信", "光模块", "光器件"),
        "partial": ("通信设备", "通信", "5g"),
    },
    "消费电子": {
        "direct": ("消费电子", "智能终端"),
        "partial": ("电子", "智能硬件"),
    },
    "人形机器人": {"direct": ("人形机器人",), "partial": ("机器人", "智能制造", "自动化设备")},
    "机器人": {"direct": ("机器人",), "partial": ("智能制造", "自动化设备", "机械设备")},
    "互联网": {"direct": ("互联网", "中概互联"), "partial": ("恒生科技", "科技")},
    "创新药": {"direct": ("创新药",), "partial": ("医药", "医疗")},
    "医药": {"direct": ("医药", "医疗"), "partial": ("创新药",)},
    "ai": {"direct": ("人工智能", "ai", "算力"), "partial": ("半导体", "通信", "科技")},
    "算力": {"direct": ("算力", "人工智能", "ai"), "partial": ("半导体", "通信", "科技")},
    "半导体": {"direct": ("半导体", "芯片"), "partial": ("人工智能", "算力")},
    "通信": {"direct": ("通信", "5g"), "partial": ("光通信", "通信设备")},
    "消费": {"direct": ("消费", "食品", "酒", "家电"), "partial": ()},
    "黄金": {"direct": ("黄金", "上海金", "贵金属"), "partial": ("商品",)},
    "贵金属": {"direct": ("黄金", "白银", "贵金属"), "partial": ("商品",)},
    "原油": {"direct": ("原油",), "partial": ("能源", "商品")},
}


def assess_exposure(scope: str, item: instruments.Instrument, *, official_name: str = "",
                    tracking_index: str = "", major_constituents: list[str] | None = None) -> ExposureAssessment:
    """按官方基金/指数事实和同一套主题规则，判定直接、部分或不相关暴露。"""
    scope = str(scope or "").strip()
    # 兼容旧交接件中的完整问题。短主题（含分析师更细的自定义主题）原样保留。
    if not _is_short_theme(scope):
        scope = _proposed_theme(SimpleNamespace(原始需求=scope, 主题="")) or scope
    facts = [official_name, item.官方名, item.简称, tracking_index, *item.标签, item.说明]
    facts.extend(major_constituents or [])
    text = " ".join(str(value or "") for value in facts).lower()
    matched_key = max((key for key in _EXPOSURE_RULES if key in scope.lower()), key=len, default="")
    if matched_key:
        rule = _EXPOSURE_RULES[matched_key]
        direct = [term for term in rule["direct"] if term.lower() in text]
        partial = [term for term in rule["partial"] if term.lower() in text]
        if direct:
            return ExposureAssessment(
                "direct", scope, f"官方名称、跟踪指数或主要成分直接命中“{matched_key}”主题规则",
                official_name, tracking_index, list(major_constituents or []), direct,
            )
        if partial:
            return ExposureAssessment(
                "partial", scope, f"仅命中“{matched_key}”的较宽行业或相邻产业链，需分析师确认映射理由",
                official_name, tracking_index, list(major_constituents or []), partial,
            )
        return ExposureAssessment(
            "unrelated", scope, f"官方名称、跟踪指数及已取得主要成分均未命中“{matched_key}”主题规则",
            official_name, tracking_index, list(major_constituents or []), [],
        )

    tokens = [token for token in re.split(r"[\s、,，产业链板块主题]+", scope.lower()) if len(token) >= 2]
    matched = [token for token in tokens if token in text]
    if matched:
        return ExposureAssessment(
            "direct", scope, "官方名称、跟踪指数或主要成分命中研究主题关键词",
            official_name, tracking_index, list(major_constituents or []), matched,
        )
    return ExposureAssessment(
        "unrelated", scope, "官方名称、跟踪指数及已取得主要成分未显示主题关联",
        official_name, tracking_index, list(major_constituents or []), [],
    )


def _exposure_matches(scope: str, item: instruments.Instrument, actual_name: str) -> bool:
    """兼容旧调用方：候选筛选保留直接和部分暴露，拒绝不相关标的。"""
    return assess_exposure(scope, item, official_name=actual_name).level != "unrelated"


def _official_major_constituents(code: str, provider: DataProvider) -> list[str]:
    """取跟踪指数前五大成分作暴露审计；权限/网络不足只形成可见缺口。"""
    try:
        rows = universe.etf_constituents(code, provider=provider)
    except Exception:
        return []
    return [str(row.简称 or "").strip() for row in rows[:5] if str(row.简称 or "").strip()]


def verify(value: Confirmation, brief=None, *, provider: DataProvider | None = None,
           min_daily_amount: float = _ETF_HARD_MIN_DAILY_AMOUNT) -> ValidationResult:
    """执行行业与标的的完整校验，成功时临时注册非白名单工具。"""
    result = ValidationResult(confirmation=value, errors=validate(value))
    if result.errors:
        return result
    provider = provider or get_provider()
    industries = value.industries

    commodity_scope = bool(_COMMODITY_RE.search(" ".join([
        value.research_scope, str(getattr(brief, "原始需求", "") or ""),
    ])))
    if value.market == "A股":
        if value.research_mode in {"industry", "theme_basket"} and not commodity_scope:
            to_check = [name for name in industries if not universe.is_broad(name)]
            if to_check:
                _ok, bad = universe.validate_industries(to_check, provider=provider)
                if bad:
                    result.errors.append("研究口径不是已验证的 A 股行业：" + "、".join(bad))
    elif brief is not None:
        raw = str(getattr(brief, "原始需求", "") or "")
        topic = str(getattr(brief, "主题", "") or "")
        if not any(name in raw or name in topic for name in industries):
            result.errors.append("港股/跨市场研究口径必须能在原始需求中找到依据")

    if brief is not None and value.research_mode == "theme_basket" and value.theme_basket_codes:
        # 主题篮子只能从本次确认页展示、且已核验过代码的候选中选择。这样既允许动态
        # 发现白名单外公司，也不会让 UI 回传任意股票混入整体法聚合。
        pool = list(getattr(brief, "主题篮子候选池", []) or
                    getattr(brief, "候选标的", []) or [])
        allowed = {str(item.代码 or "").upper() for item in pool if getattr(item, "可用", False)}
        invalid = [code for code in value.theme_basket_codes if code not in allowed]
        if invalid:
            result.errors.append("主题研究篮子包含未核验候选：" + "、".join(invalid))
        associations = getattr(brief, "主题篮子候选依据", {}) or {}
        for code in value.theme_basket_codes:
            candidate = associations.get(code) or {}
            if not candidate or candidate.get("association") in {"client_named", "direct", "indirect"}:
                continue
            manual = value.theme_basket_evidence.get(code) or {}
            if (not str(manual.get("reason") or "").strip()
                    or not str(manual.get("source") or "").strip()
                    or manual.get("association") not in {"direct", "indirect"}):
                result.errors.append(f"{code} 仅为待核实候选；请填写关联类型、主题关联理由及资料来源，或取消勾选")

    if value.reason == "":
        result.warnings.append("未填写映射理由（选填）")
    # “仅研究”只关闭后续产品报价，不得跳过主题 ETF 研究对象本身的真实性、
    # 跟踪指数、主题暴露和流动性校验。
    if value.research_only and value.research_mode != "theme_etf":
        result.ok = not result.errors
        return result

    # 标准行业研究不需要预先绑定产品。没有客户明确标的时，后续正式报价按钮
    # 会保持不可用；这比强迫分析师在研究前随意挑一只 ETF 更符合职责边界。
    if not value.underlying_code.strip():
        result.ok = not result.errors
        return result

    code = value.underlying_code
    static = instruments.get(code)
    basic = provider.get_basic([code], ["ths_stock_short_name_stock"])
    actual_name = str(_first_value(basic, code, "ths_stock_short_name_stock") or "").strip()
    if not getattr(basic, "ok", False) or not actual_name:
        result.errors.append(f"证券代码 {code} 无法从数据源验证：{getattr(basic, 'error', '') or '查不到简称'}")
        return result
    if value.underlying_name and static is None:
        expected = re.sub(r"\s+", "", value.underlying_name).lower()
        actual = re.sub(r"\s+", "", actual_name).lower()
        if expected not in actual and actual not in expected:
            result.errors.append(f"代码 {code} 实为「{actual_name}」，与输入名称「{value.underlying_name}」不符")
            return result

    if static is None and not ("ETF" in actual_name.upper() or "指数" in actual_name):
        result.errors.append(f"{actual_name}（{code}）不是可验证的 ETF 或指数")
        return result
    tracking = static.跟踪指数 if static is not None else ""
    if not tracking and "ETF" in actual_name.upper():
        tracking_result = provider.get_basic([code], ["ths_tracking_index_code_fund"])
        if getattr(tracking_result, "ok", False):
            tracking = str(_first_value(tracking_result, code, "ths_tracking_index_code_fund") or "")
    tracking_name = ""
    if tracking:
        tracking_basic = provider.get_basic([tracking], ["ths_stock_short_name_stock"])
        if getattr(tracking_basic, "ok", False):
            tracking_name = str(_first_value(
                tracking_basic, tracking, "ths_stock_short_name_stock") or "").strip()
    is_commodity = bool(_COMMODITY_RE.search(" ".join([
        value.research_scope, str(getattr(brief, "原始需求", "") or ""), actual_name,
    ])))
    item = static or instruments.Instrument(
        code, actual_name, actual_name,
        "跨境ETF" if _is_cross_border(None, actual_name) else (
            "商品ETF" if is_commodity else ("行业ETF" if "ETF" in actual_name.upper() else "指数")),
        [instruments.T_OVERSEA] if _is_cross_border(None, actual_name) else [],
        "本次运行经数据源验证的临时标的",
        tracking,
    )
    if value.market == "港股" and not (code.endswith(".HK") or _is_cross_border(item, actual_name)):
        result.errors.append("港股研究只能选择港股工具或明确的跨境 ETF，不能静默换成普通 A 股 ETF")
    major_constituents = _official_major_constituents(code, provider) if "ETF" in item.类型.upper() else []
    target_scope = value.theme if value.research_mode in {"theme_basket", "theme_etf"} else value.research_scope
    primary_exposure = assess_exposure(
        target_scope, item, official_name=actual_name,
        tracking_index=tracking_name or tracking, major_constituents=major_constituents,
    )
    original_exposure = (assess_exposure(
        str(getattr(brief, "原始需求", "") or ""), item, official_name=actual_name,
        tracking_index=tracking_name or tracking, major_constituents=major_constituents,
    ) if brief is not None else None)
    # 多行业产业链和主题路径可由客户原始主题补足行业词差异；单一标准行业仍要
    # 与自身口径直接对齐，避免把较宽 ETF 偷换成行业研究对象。
    original_allowed = ((value.research_mode in {"theme_basket", "theme_etf"}
                         and not value.research_theme.strip())
                        or len(industries) > 1 or value.market != "A股")
    if value.research_mode == "theme_etf" and value.research_theme.strip():
        # 分析师已确认细分主题时，不能用原需求中的宽主题把部分暴露升级为直接暴露。
        original_allowed = False
    assessment = primary_exposure
    if original_allowed and original_exposure is not None:
        rank = {"unrelated": 0, "partial": 1, "direct": 2}
        if rank[original_exposure.level] > rank[assessment.level]:
            assessment = original_exposure
    result.exposure = assessment
    if not major_constituents and "ETF" in item.类型.upper():
        result.warnings.append(
            f"{code} 未取得可展示的官方主要成分；主题分级暂以官方基金简称和跟踪指数为依据，"
            "研究取数阶段仍会再次读取真实指数成分。")
    # 只有主题 ETF 路径会把该 ETF 的真实成分作为研究对象，因而需要在此对
    # 主题暴露做硬门。标准行业/人工篮子路径中的 ETF 不应在研究前被当成取数
    # 对象；旧运行若遗留该字段，只记录分级供后续报价审核，不反向阻断研究。
    if value.research_mode == "theme_etf":
        if assessment.level == "unrelated":
            result.errors.append(
                f"{item.简称} 与研究主题「{target_scope}」不相关：{assessment.reason}")
        elif assessment.level == "partial":
            if not value.partial_exposure_confirmed or not value.reason.strip():
                result.errors.append(
                    f"{item.简称} 仅为研究主题「{target_scope}」的部分暴露：{assessment.reason}。"
                    "请勾选“确认部分暴露”并填写映射理由，或改选直接暴露 ETF。")
            else:
                result.warnings.append(
                    f"{item.简称} 为部分暴露，已按分析师确认继续：{value.reason.strip()}")

    if "ETF" in item.类型.upper():
        from . import history
        values = history.series(code, "ths_amt_stock", years=1, provider=provider,
                                drop_nonpositive=False)
        recent = [float(x) for x in values[-20:] if isinstance(x, (int, float)) and math.isfinite(x)]
        if len(recent) < 10:
            result.errors.append(f"{code} 成交额样本不足，无法确认流动性")
        else:
            result.average_daily_amount = sum(recent) / len(recent)
            hard_floor = (_THEME_ETF_MIN_DAILY_AMOUNT
                          if value.research_mode == "theme_etf" else min_daily_amount)
            if result.average_daily_amount < hard_floor:
                result.errors.append(
                    f"{code} 近20日日均成交额 {result.average_daily_amount / 1e8:.2f} 亿元，"
                    f"低于 {hard_floor / 1e8:.1f} 亿元最低门槛")
            elif result.average_daily_amount < _PREFERRED_DAILY_AMOUNT:
                result.warnings.append(
                    f"{code} 近20日日均成交额 {result.average_daily_amount / 1e8:.2f} 亿元，"
                    f"低于 {_PREFERRED_DAILY_AMOUNT / 1e8:.0f} 亿元优选门槛；已按 0.1 亿元最低门槛通过，"
                    "正式询价前需结合名义本金复核冲击成本")

    if not result.errors:
        if static is None:
            instruments.register_temporary(item)
            result.warnings.append("该标的不在人工白名单中，仅作为本次运行的临时标的")
        result.instrument = item
        result.ok = True
    return result


def apply_to_brief(result: ValidationResult, brief, *, provider: DataProvider | None = None) -> None:
    """把已校验确认应用到本次 Brief；不会修改任何持久化目录。"""
    if not result.ok:
        raise ValueError("不能应用未通过校验的市场确认")
    value = result.confirmation
    # 兼容 2026-09-03 前保存的确认 JSON：旧版把“标准行业 + 主题公司篮子”合并
    # 为 industry；只要其中确实带有已选公司，就按新的 theme_basket 解释。商品 ETF
    # 的旧确认同理按 ETF 自身研究，避免升级后历史运行突然去查询矿业股锚点。
    effective_mode = value.research_mode
    if effective_mode == "industry" and value.theme_basket_codes:
        effective_mode = "theme_basket"
    if (effective_mode == "industry" and result.instrument is not None
            and result.instrument.类型 == "商品ETF"):
        effective_mode = "theme_etf"
    brief.市场范围 = value.market
    brief.市场确认 = asdict(value)
    brief.市场确认["research_mode"] = effective_mode
    if result.exposure is not None:
        brief.市场确认["etf_exposure"] = asdict(result.exposure)
    brief.研究主题 = value.theme
    # 三条取数路径互斥：标准行业使用行业成分，人工主题篮子使用分析师勾选公司，
    # 主题 ETF 使用 ETF 真实成分。挂钩工具只是报价对象，不能反过来改写研究篮子。
    brief.研究篮子口径 = (value.research_scope if effective_mode == "industry"
                          else value.theme)
    # 兼容旧字段名：这里只记录主题 ETF 研究取数目标，绝不表示正式报价标的已确认。
    brief.确认挂钩标的 = value.underlying_code if result.instrument else ""
    brief.确认挂钩标的类型 = result.instrument.类型 if result.instrument else ""
    brief.研究资产类型 = result.instrument.类型 if result.instrument else ""
    if result.instrument:
        rationale = (f"分析师确认主题 ETF 取数目标 {result.instrument.简称}（{result.instrument.代码}）："
                     f"{result.exposure.label if result.exposure else '已核验'}，"
                     f"已核验与研究主题“{value.theme}”的暴露及近20日流动性")
        if result.exposure and result.exposure.tracking_index:
            rationale += f"（跟踪指数 {result.exposure.tracking_index}）"
        if result.exposure and result.exposure.major_constituents:
            rationale += f"（主要成分 {'、'.join(result.exposure.major_constituents[:5])}）"
        if result.average_daily_amount is not None:
            rationale += f"（日均成交额 {result.average_daily_amount / 1e8:.2f} 亿元）"
        # 分析师自行填写的理由优先保留，并补上系统实际核验过的事实。
        brief.板块理由 = (value.reason + "；" if value.reason else "") + rationale
    # 主题篮子来自确认页勾选的本次候选池；候选池由解析阶段核心样本 + iFinD 动态
    # 主题发现组成。它们用于“光模块”等细分主题的整体法，不是报价 ETF 成分。
    from .universe import _is_fund
    pool = list(getattr(brief, "主题篮子候选池", []) or
                getattr(brief, "候选标的", []) or [])
    by_code = {str(item.代码 or "").upper(): item for item in pool
               if item.可用 and not _is_fund(item.代码)}
    # 只有显式选择“人工主题篮子”时，公司复选结果才进入取数。标准行业路径即使
    # payload 里残留旧候选也必须忽略，避免行业整体法被一小撮公司暗中替换。
    selected_codes = (value.theme_basket_codes if effective_mode == "theme_basket" else [])
    brief.主题篮子候选 = [by_code[code] for code in dict.fromkeys(selected_codes)
                       if code in by_code][:_THEME_BASKET_MAX]
    parts = value.industries
    if value.market == "A股":
        if effective_mode == "theme_etf":
            brief.涉及板块 = [value.theme]
            brief.宽口径成分行业 = []
            brief.候选标的 = []
            return
        # 人工主题篮子路径保留细分主题名；标准行业路径始终使用已验证行业，不能再
        # 通过“主题名与行业名不同”这种隐式条件切换口径。
        theme_mode = effective_mode == "theme_basket"
        if theme_mode:
            brief.涉及板块 = [value.theme]
            brief.宽口径成分行业 = []
        elif len(parts) > 1:
            basket_name = (str(getattr(brief, "主题", "") or "").strip() or "分析师确认行业篮子")[:30]
            brief.涉及板块 = [basket_name]
            brief.宽口径成分行业 = universe.register_broad(
                basket_name, parts, basis="分析师本次确认（不写回全局行业库）")
        else:
            brief.涉及板块 = parts
            brief.宽口径成分行业 = []
        brief.候选标的 = []
        # 商品 ETF 是研究对象本身，不存在“拿一只矿业股当数据锚点”的合理口径。
        # 已确认 ETF 时主入口会直接以该 ETF 作为研究对象，不再额外查一只行业龙头；
        # 已确认主题篮子时以篮子首个样本作内部锚点；标准行业无论是否另选报价 ETF，
        # 都从行业成分选内部锚点，避免报价工具改写研究取数对象。
        lead = (None if theme_mode else
                universe.pick_representative(brief.涉及板块[:1], provider=provider or get_provider()))
        # 细分主题的动态候选已经由 iFinD/代码校验确认过时，优先把它作为本次
        # 取数锚点。行业龙头检索偶发超时不应把“只研究、不报价”误判为必须补 ETF。
        # 这只影响内部取数锚点，不会把该公司写成客户看到的推荐标的。
        if lead is None and brief.主题篮子候选:
            first = brief.主题篮子候选[0]
            lead = type("ThemeLead", (), {"简称": first.名称, "代码": first.代码})()
        if lead:
            from .brief import TargetRef
            brief.候选标的 = [TargetRef(lead.简称, lead.代码,
                                       f"ok:{lead.简称}（分析师确认口径后重选）")]
