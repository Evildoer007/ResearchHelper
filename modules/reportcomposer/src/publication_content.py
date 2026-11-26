"""ReportComposer owns the client paper and per-logic internal workpaper."""
import hashlib
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from .contracts import canonical,ContractError,METHOD_REGISTRY,fingerprint,DOCUMENT_CONTENT_INTENT
from .financial_sources import FINANCIAL_FIELD_NAMES as _FINANCIAL_NAMES

PROFILES={'standard':'标准报告','detailed':'详细报告'}
READER_FORMAT_VERSION = 1
INLINE_OBSERVATION_LIMIT = 12
DRAFT_READING_POLICY = 'draft_reading_version'
DRAFT_READING_VERSION = 1
DRAFT_EDITORIAL_POLICY = 'draft_report_editorial_version'
DRAFT_EDITORIAL_VERSION = 2


def draft_editorial_enabled(value):
    return value.get(DRAFT_EDITORIAL_POLICY) in (1, DRAFT_EDITORIAL_VERSION)


def draft_reading_enabled(value):
    version = value.get(DRAFT_READING_POLICY)
    return value.get('observed_argument_sections') is True or type(version) is int and version == DRAFT_READING_VERSION


def draft_risks_at_end(content):
    if draft_editorial_enabled(content):
        return True
    grouped = ('peer_growth_series', 'peer_revenue_series', 'bank_profit_series', 'peer_operating_series',
               'event_session_summary')
    return any(any(logic.get(key) for key in grouped) for logic in content.get('draft', {}).get('logics', ()))

REPORT_CONDITION_BRIEF = ('数值阈值须注明来源、方法或用户设定；自行提出的条件只能作为待验证观察条件，不写成已经验证的转折或买卖规则。')
PARAGRAPH_BRIEF = '同一条逻辑的判断、关键数据和解释连贯成段。围绕同一判断自然换行，只有转入另一层论述时才另起一段。'
_STRATEGY_COUNT = r'(?P<count>\d{1,4}|[一二两三四五六七八九十百千]{1,4})\s*(?:条|个)'
_STRATEGY_NAME = r'(?:(?:策略|投资|研究)逻辑|(?:核心|投资|策略)论点)'
_NUMBERED_LOGIC_TITLE = re.compile(r'^(?:策略|传导|投资|研究)逻辑\s*(?:[一二三四五六七八九十]+|\d+)\s*[:：、]\s*(?=\S)')
_EDITORIAL_SENTENCE = re.compile(
    r'[^。！？\n]*(?:原始单位待核对的状态保存|正文只能引用|数据均已有登记|金额仅以已登记的同比增速呈现|'
    r'这一判断不构成对股价或估值的结论)[^。！？\n]*[。！？]?')
_EDITORIAL_CLAUSE = re.compile(
    r'(?P<lead>^|[，；。！？\n])\s*(?:该项数据缺少可核对的依据|该口径的宏观读数缺少可核对的依据|'
    r'分项贡献缺少可核对的读数|同比缺少可核对的读数|'
    r'数值待核对|读数待补)\s*(?P<tail>[，；。！？]|$)', re.M)
_CASH_REPORT_PROCESS_SENTENCE = re.compile(r'[^。！？\n]*原始金额单位尚未核对[^。！？\n]*[。！？]?')
_EMPTY_EDITORIAL_SENTENCE = re.compile(r'反过来说，提供的支撑有限[。！？]?')
_DRAFT_PROCESS_WORDING = re.compile(r'现有登记数据|登记规则明确')
_CLIENT_STANDALONE_FILLER = re.compile(
    r'(?:(?:综上所述|整体来看)[，,]?)?(?:本报告仅供参考|本报告不构成(?:任何)?投资建议|以上分析仅供参考|'
    r'建议(?:保持)?谨慎关注|后续表现仍有待进一步观察|相关情况有待进一步跟踪|'
    r'目前证据仍需进一步验证|本次(?:仅基于|仅依据)现有材料(?:作初步判断)?|'
    r'具体情况(?:仍)?需(?:进一步)?核实|受限于数据可得性[，,]?具体情况仍需进一步验证|'
    r'其余口径(?:仍)?待核验)[。！？]?')
_CLIENT_RENDERING_NOTE = re.compile(r'(?:折线图|图表)(?:可)?(?:按|用|使用)[^。！？\n]*(?:复现|绘制|生成)[^。！？\n]*[。！？]?')
_CLIENT_SENTENCE = re.compile(r'[^。！？\n]+[。！？]?')


def _filler_spans(text):
    for sentence in _CLIENT_SENTENCE.finditer(text):
        clean = sentence[0].strip()
        if _CLIENT_STANDALONE_FILLER.fullmatch(clean) or _CLIENT_RENDERING_NOTE.fullmatch(clean):
            yield sentence.span()


def remove_client_filler(text):
    """Drop only standalone publication boilerplate, before selecting a readout."""
    if isinstance(text, str):
        for first, last in reversed(list(_filler_spans(text))):
            text = text[:first] + text[last:]
    return text
_CLIENT_EDITORIAL_BRIEF = (
    '用研报语气写成自然段。首句交代方向与主要分歧，随后解释对利润、现金流、估值或相对表现的影响。'
    '事实和推断在句内用实际依据区分。推断写出条件和会改变判断的变量，不写仅供参考、仍需进一步验证、建议谨慎关注等通用话术。'
    '风险集中放在末尾，写明变量朝什么方向变化、影响什么指标。资料缺项有实质影响时直接交代对象和观测窗口，取数、接口及核验过程放底稿。'
    '数字只保留交代幅度、差距与归因所需的读数。小样本用句内对照，图只支撑本段判断，多行原值表留给底稿。'
)
_CLAUSE_START = r'(?:^|[，,。；;！？\n（(、])\s*'
_STRATEGY_COUNT_REQUESTS = (
    re.compile(_CLAUSE_START + r'(?:请\s*)?(?:给(?:出|我)?|写(?:出)?|列出|提供|生成|形成|提炼|'
               r'整理成|分成|输出|最多|只(?:写|要|保留)|保留|需要|我要)\s*' + _STRATEGY_COUNT
               + r'\s*(?:以内的?|有(?:数据|证据|事实)支[撑持]的|有依据的|相互独立的|独立的|核心的)?\s*' + _STRATEGY_NAME),
    re.compile(_CLAUSE_START + _STRATEGY_NAME + r'(?:的?数量)?\s*(?:控制在|设为|限定为|只写|写|最多)?\s*'
               + _STRATEGY_COUNT + r'(?=\s*(?:[，,。；;！？）)\n]|以内|就够|即可|$))'),
)


def requested_strategy_count(question):
    """Use an explicit count preference, apart from mentions of prior arguments."""
    from .numeric_integrity import _number

    counts = {int(_number(match['count'])) for pattern in _STRATEGY_COUNT_REQUESTS
              for match in pattern.finditer(question)}
    return next(iter(counts)) if len(counts) == 1 and 0 not in counts else None

_LEGACY_DIMENSIONS = '每条要写清比较后多知道了什么。财务研究分开看经营规模、利润率和现金回收；事件研究连起事实、传导路径和可观测结果；市场比较解释基准和差异来自哪里。'
_QUESTION_DRIVEN_ARGUMENT = (
    '先确定原问题要区分的解释和判断，再从实际资料选出能够区分它们的证据。'
    '只围绕问题涉及的传导渠道或差异展开，同一证据用于检验不同命题时须说明各自的推断边界。'
    '归因须区分直接披露的原因、可复算的贡献分解、相关变化和未证实的解释。'
    '证据只有结果读数时先交付可核对的发现，仅在依据允许时展开策略；条件写成能够改变具体判断的观察。'
)


# One business-argument rule also applies when the initial Chat scope has
# not yet been classified. Choosing a presentation scope is not enough by itself.
RESEARCH_LOGIC_BRIEF = (
    '投资研究开头交代时点和核心判断。明确事件、定价或基本面在这一阶段发生了什么变化，以及哪类标的能反映这一变化。'
    '每节围绕一个判断，说明现象、幅度和投资含义。事件研究优先交代窗口，比较研究优先交代基准，按问题决定逻辑条数。'
    '用户指定策略逻辑条数时，按该数量组织能独立成立的判断。材料不足以支撑该数量时按实际论点写，禁止把同一判断拆成多条或补造数据。'
    '指标比较可作为论据，不用指标复述、缺项说明或风险声明单独凑一条策略；条件性假设可写，但与事实分开。'
    '各条先给具体判断，再选最有解释力的数字；完整序列交给图表和底稿，核心结论不逐项重复。'
    + _LEGACY_DIMENSIONS +
    '资料能支持的解释要展开，说明关键数字如何验证判断、还有哪种解释成立，以及后续用什么数据区分。正文写成完整的研究段落。'
    '同比、环比和升降方向沿用原始读数或已登记计算的比较口径。'
    '比较多个标的或报表口径时，各项财务读数明确对应的标的与口径。'
    '行情和估值列明底稿实际观测日，最近取得的记录不能写成当日行情。'
    'claim只写主判断和决定该判断的一项关键前提；evidence只补尚未出现的依据；reasoning解释传导，不再复述同一组数字。'
    'condition写尚未说明的成立条件，implication写投资含义；同一限制不在这几个字段和risks中逐段重复。'
    '依据有限时写有条件的投资含义，只写会改变业务判断的前提。缺少可用依据时列明实际缺项，省略通用策略段落。'
    '逻辑标题写经济关系与成立方向，例如盈利兑现影响估值支撑；不以某字段待核验或缺少某张表作为独立策略。'
    '核验动作和补数步骤放workpaper_notes；正文的condition保留经济前提，必要口径边界简述一次。'
)

# Shared by the App model and the Skill host; no extra editing-model call.
CLIENT_REPORT_BRIEF = (
    '按对客策略简报行文。简短标题之后先给核心结论，再写各条策略逻辑。正文可比对有依据的研究标的，风险统一放在文末。'
    '标题只保留研究对象与核心议题，不串联各项数值或核验进度；核心结论先回答问题，不逐条复述全部指标。' + RESEARCH_LOGIC_BRIEF +
    '标题与走势核对完整观测区间，样本内极值须保留实际月份和范围。'
    '正文写研究判断，不出现数据或计算内部编号、来源ID、核验进度、模型、接口或ResDesk。'
    '段落和对应的图表服务于同一个判断。图表交代期间、比较对象与单位，文字写清差异意味着什么，避免复述全部读数。'
    '数据、计算、来源和实际使用的知识各填引用字段；知识引用每项包含id和整数version。'
    '每条逻辑优先用argument分别写claim、evidence、reasoning、condition，围绕一个判断展开，不把多个独立渠道塞进最后一条。'
    'detail_analysis只供详版，写同一判断的进一步归因、可成立的其他解释，以及什么证据会改变判断。'
    '用已有依据展开，省略重复正文和已经列在底稿的原值。'
    '确有传导内容时填写mechanism.steps，以同次写作的观察、条件和可能影响生成路径图；不为凑图制造关系。'
    '每步给简短label、kind及已有data_refs；kind用observation、hypothesis或condition。'
    '单位未知的金额不写绝对数额，采用已登记相对变化；比较前提说明一次。'
    'workpaper_notes存口径与核验推敲，reader_notes只简述一次适用范围；改变判断的限制仍写相关论述。'
    'risks归纳真实经营、定价与传导风险，不逐条重复；不包含衍生品产品推荐。'
) + REPORT_CONDITION_BRIEF



SCOPED_REPORT_STYLES = {
    'fact': '本次只做事实与数据核对，核心结论回答核对结果，logics按实际数据主题组织。不制造传导机制、估值或投资建议。',
    'comparison': '本次做直接比较，核心结论回答哪些指标可比及差异，logics按实际比较维度组织。不把单项差异扩写成整体投资价值判断。',
    'mechanism': '本次做机制解释，核心结论回答实际变化、可能渠道及成立条件。logics围绕独立解释与竞争解释组织，implication说明对原问题的含义。不强加投资建议，也不把同期变动写成确定因果。',
    'by_question': '先按原问题确定事实核对、直接比较、机制解释或投资研究。默认问题分类不是用户已指定的投资判断；用report_scope明确选定的内容范围。',
}
SCOPED_REPORT_COMMON = (
    '面向客户写简洁报告，标题只保留研究对象与核心议题，不串联数值和核验进度；核心结论直接回答原问题，保留实际数字与引用。'
    'report_scope只用research_scope.allowed_report_scopes中的值；它只组织章节，不改变问题、权限或核验状态。'
    '事实、推断和条件性假设分开；底稿状态、数据是否登记、核对操作和无法写入正文的字段均放入workpaper_notes。'
    '同期变化和单个样本不能单独证明归因；若引述原文判断，写明是原文表述并绑定对应来源。'
    '核对时保留字面数字及单位，单位未明确须说明；不按数量级推定或换算。形成投资判断时，单位未知的金额只留在底稿和引用中，正文使用已登记的相对比较并简述前提。'
    'workpaper_notes放口径推敲与核验备注，实质影响结论的限制写进正文或风险；reader_notes只简述一次必要适用范围。'
    'risks只列与本次问题实际相关的不确定性，不制造无关经营风险或重复每条限制。'
    '数据、来源和计算引用填实际使用的引用字段，保留计算条件。共享内核按引用单独配图，不额外调用模型选图。'
    '机制解释优先用argument分别写claim、evidence、reasoning、condition，每条围绕一个解释及其竞争解释。'
    '机制和投资判断可在detail_analysis里展开其他解释与复核条件，供内部详版使用。'
    '仅核对或直接比较时用analysis即可；只有确有传导内容时才用mechanism.steps，不制造机制、权重、概率或阈值。'
    '知识引用每项包含id和整数version；图表由程序按引用生成，正文不叙述JSON或绘图步骤。'
    '趋势和标题核对完整观测区间；原问题要求投资含义时，可提出明确标注的条件性假设，'
    '不把指标复述、缺项说明或风险声明单独列为策略逻辑。'
) + REPORT_CONDITION_BRIEF


DRAFT_TRACKING_BRIEF = (
    '需要后续验证时，可在相应logic中附tracking对象，含indicator、source、window、condition，可附benchmark；'
    '每项为非空文字，source说明今后核对的资料渠道，window给出观察期间或事件，condition说明什么变化需要复核本判断。'
    '跟踪安排写在tracking中，由工作台和详细报告呈现。分析正文保留具体的判断依据和风险。'
    '它是拟定的观察规则，不是当前事实、收益预测、已验证信号或自动监控；不写状态、任务或论点编号。'
    '阈值须说明来自已有依据、登记方法还是本次提出的待验证观察设定；没有依据不制造阈值或比较基准。'
    '仅作静态核对或不适用时省略tracking，不为满足格式制造投资目标。'
)


_INVESTMENT_OPPORTUNITY_BRIEF = (
    '投资机会要回答标的在用户所问期限内可能怎样走、谁能从什么变化中获利、何时有条件参与及什么情况推翻判断。'
    '先辨认现价可能已经反映的预期，再说明尚待兑现的驱动如何改变价格或相对表现。理由从本题和已取得的证据出发，论点按独立收益来源取舍。'
    '同一驱动的不同指标放在同一条。若只是历史表现、资产质量或回撤缓冲，说明它如何形成可参与机会，或保留为背景。'
    '判断相对强弱须用相同起终日期和明确价格口径的基准证据，证据不足时只写待验证的假设。累计财务比率不能直接说明单季盈利趋势。'
    '用claim写方向和期限，evidence写最能区别其他解释的依据，reasoning写预期与价格的传导，condition写可观察的参与条件。'
    'implication说明可能取得的收益及其来源；risk给出具体失效情形。按用户要求的数量组织，依据不足时减少条数并在结论简述原因。'
    '一条观点只使用完整argument或连贯analysis其中一种正文。详细报告用detail_analysis展开反例和敏感性。'
    '计算编号如A1只填calculation_refs，正文使用可核对的指标名称、口径和必要幅度。'
)


def client_report_brief(task=None):
    from .official_sources import research_fomc_rate_request
    from .planning import draft_research_scope, question_driven_arguments
    scope = draft_research_scope(task) if task is not None else None
    tracking = DRAFT_TRACKING_BRIEF if task is not None and type(task.get('draft_tracking_version')) is int and task['draft_tracking_version'] == 1 else ''
    if scope is None or scope['mode'] == 'research':
        brief = CLIENT_REPORT_BRIEF
    else:
        brief = SCOPED_REPORT_STYLES[scope['mode']] + SCOPED_REPORT_COMMON
        if scope['mode'] == 'by_question':
            brief += '仅在按原问题选择report_scope为research时，采用以下投资论证要求：' + RESEARCH_LOGIC_BRIEF
    if task is not None and question_driven_arguments(task):
        if _LEGACY_DIMENSIONS in brief:
            brief = brief.replace(_LEGACY_DIMENSIONS, _QUESTION_DRIVEN_ARGUMENT)
        elif scope and scope['mode'] == 'mechanism':
            brief += _QUESTION_DRIVEN_ARGUMENT
    if task is not None and research_fomc_rate_request(task):
        brief += ('会议声明可核对的政策利率区间及前次变化由报告内核写入核心结论。分析段落省略重复的区间读数。'
                  '围绕实际证据区分政策动作、会前预期、传导条件和竞争解释。未取得市场证据时将市场影响写成假设。')
    scoped_paragraphs = PARAGRAPH_BRIEF if scope is not None else ''
    editorial = _CLIENT_EDITORIAL_BRIEF if scope is not None and draft_editorial_enabled(task) else ''
    if (scope and task.get(DRAFT_EDITORIAL_POLICY) == DRAFT_EDITORIAL_VERSION
            and scope['mode'] in ('research', 'by_question')):
        if scope['mode'] == 'by_question':
            editorial += '原问题要求投资机会且选择research时，按下列要求写作。'
        editorial += _INVESTMENT_OPPORTUNITY_BRIEF
    return brief + scoped_paragraphs + editorial + tracking


def report_writing_example(task=None):
    """One executable writing shape for the App model and the Skill host.

    A business argument is distinct from its observations. The fields guide
    the same writing call; none is a new prerequisite for delivery.
    """
    from .planning import draft_research_scope
    scope = draft_research_scope(task) if task is not None else None
    mode = scope['mode'] if scope is not None else 'research'
    logic = dict(title='一个具体判断的简短标题', implication='对原问题的含义。',
                 risk='可能推翻本条判断的具体情况或竞争解释。',
                 data_refs=[], source_refs=[], calculation_refs=[], knowledge_refs=[], workpaper_notes=[])
    if mode in ('fact', 'comparison'):
        logic['analysis'] = ('按原问题核对实际数字、日期和单位。' if mode == 'fact' else
                             '按原问题的维度比较同口径数据，说明差异。')
    else:
        logic['detail_analysis'] = '展开该判断的替代解释，说明新增哪项证据、在什么情形下会改变判断。'
        logic['argument'] = dict(
            claim='围绕一个对象或经济渠道提出可检验判断；不把指标复述单列为策略。',
            evidence='只选支持本判断的实际观察与登记计算，不重述全部序列。',
            reasoning='解释观察如何影响该对象，区分事实、推断和竞争解释。',
            condition='说明这条传导成立的具体前提；缺少验证时使用条件性表述。')
        logic['mechanism'] = dict(steps=[
            dict(label='本次观察', kind='observation', data_refs=[]),
            dict(label='传导成立条件', kind='condition', data_refs=[]),
            dict(label='条件成立时的可能影响', kind='hypothesis', data_refs=[])])
    if (task is not None and task.get(DRAFT_EDITORIAL_POLICY) == DRAFT_EDITORIAL_VERSION
            and mode in ('research', 'by_question')):
        logic['implication'] = '投资研究中，说明哪种收益可能来自哪项变化、适用于什么期限。'
        logic['risk'] = '指出哪项可观察变化会削弱收益路径或推翻行情判断。'
    if task is not None and type(task.get('draft_tracking_version')) is int and task['draft_tracking_version'] == 1:
        logic['tracking'] = None
    return dict(title='简短研究标题', conclusion='直接回答原问题，给出主要判断与关键分歧。',
                logics=[logic], risks=['与原问题有关且尚未说明的主要风险'],
                reader_notes=[], workpaper_notes=[])


def draft_logic_heading(organization, index, title):
    """One heading shared by the paper and newly produced App workpapers."""
    title = _NUMBERED_LOGIC_TITLE.sub('', title)
    numeral = '一二三四五六七八九十'[index - 1] if 1 <= index <= 10 else str(index)
    if organization in ('fact', 'comparison'):
        return numeral + '、' + title
    prefix = '传导逻辑' if organization == 'mechanism' else '策略逻辑'
    return prefix + numeral + '：' + title


def client_research_text(value, links=(), *, hide_unverified_amounts=False, editorial=False):
    """Project editorial notes out of a client paragraph and remap evidence links."""
    projected = list(links)

    def apply(span, replacement):
        nonlocal value, projected
        start, end = span
        step = len(replacement) - (end - start)
        retained = []
        for link in projected:
            first, last = link['start'], link['end']
            if last <= start:
                retained.append(link)
            elif first >= end:
                retained.append(dict(link, start=first + step, end=last + step))
        value = value[:start] + replacement + value[end:]
        projected = retained

    for match in reversed(list(_EDITORIAL_SENTENCE.finditer(value))):
        apply(match.span(), '')
    if hide_unverified_amounts:
        for match in reversed(list(_CASH_REPORT_PROCESS_SENTENCE.finditer(value))):
            apply(match.span(), '')
    while match := _EDITORIAL_CLAUSE.search(value):
        lead, tail = match['lead'], match['tail']
        delimiter = tail if lead in ('，', '；') and tail in ('。', '！', '？') else lead
        apply(match.span(), delimiter)
    if hide_unverified_amounts:
        for match in reversed(list(_EMPTY_EDITORIAL_SENTENCE.finditer(value))):
            apply(match.span(), '')
        for match in reversed(list(_DRAFT_PROCESS_WORDING.finditer(value))):
            apply(match.span(), '现有数据' if match[0] == '现有登记数据' else '')
    if editorial:
        for first, last in reversed(list(_filler_spans(value))):
            if not any(link['start'] < last and link['end'] > first for link in projected):
                apply((first, last), '')
    return value, projected


def draft_logic_title(title, claim):
    if title and (clean := client_research_text(title)[0].strip('，；。 ')):
        return clean
    if claim:
        first = re.split(r'[。！？\n]', claim, maxsplit=1)[0]
        for clause in reversed(re.split(r'[，；]', first)):
            subject = re.sub(r'^(?:因此|表明|意味着|说明|但)\s*', '', clause.strip())
            if 4 <= len(subject) <= 42 and not re.search(r'\d', subject) and client_research_text(subject)[0] == subject:
                return subject
    return '研究逻辑'


def draft_logic_data_refs(logic):
    """Explicit references on a path belong to its parent logic as well."""
    if not isinstance(logic,dict):return []
    refs=list(logic.get('data_refs',[])) if isinstance(logic.get('data_refs'),list) else []
    mechanism=logic.get('mechanism')
    steps=mechanism.get('steps',[]) if isinstance(mechanism,dict) else []
    for step in steps if isinstance(steps,list) else []:
        if isinstance(step,dict) and isinstance(step.get('data_refs'),list):refs.extend(step['data_refs'])
    return list(dict.fromkeys(ref for ref in refs if isinstance(ref,str) and ref))


def draft_logic_reading(logic, calculations):
    """Project the section's evidence without editing the submitted references."""
    if not logic.get('off_scope_data_refs'):
        return logic
    excluded = set(logic['off_scope_data_refs'])
    allowed_calculations = {row['id'] for row in calculations if excluded.isdisjoint(row.get('data_refs', ()))}
    reading = dict(logic, data_refs=[ref for ref in draft_logic_data_refs(logic) if ref not in excluded],
                   calculation_refs=[ref for ref in logic.get('calculation_refs', ()) if ref in allowed_calculations])
    if 'mechanism' in logic:
        steps = logic['mechanism']['steps']
        if any(step['kind'] == 'observation' and step['data_refs'] and set(step['data_refs']) <= excluded for step in steps):
            reading.pop('mechanism')
        else:
            reading['mechanism'] = dict(steps=[dict(step, data_refs=[ref for ref in step['data_refs'] if ref not in excluded])
                                               for step in steps])
    return reading


def source_label(source, *, records=()):
    """Presentation only: retain provider identity and name bound official channels."""
    if isinstance(source,dict):
        if (source.get('source'), source.get('operation')) == ('ifind', 'get_trade_dates'):
            return 'iFinD交易日历'
        from .intraday_quotes import REGISTRY as INTRADAY_REQUEST_REGISTRY

        if (definition := INTRADAY_REQUEST_REGISTRY.get(f"{source.get('source')}.{source.get('operation')}")) is not None:
            return 'iFinD' + definition['kind']
        if source.get('operation') == 'filing_original' and source.get('kind') == 'filing':
            directory = source.get('directory', {})
            if directory.get('security_name') and '半年度报告' in directory.get('title', ''):
                return directory['security_name']+'半年度报告'
        from .source_catalogue import official_record_label
        specific=official_record_label(source,records)
        if specific:return specific
        source=source.get('source')
    if not isinstance(source,str):return ''
    return {'ifind':'iFinD','official':'公开来源','tinyshare':'Tinyshare','wind':'Wind'}.get(source,source)


def publication_source_labels(content):
    """Resolve names once per projection, only within this task and version."""
    ident=content.get('task_id',content.get('id'));version=content.get('research_version',content.get('version'))
    if ident is None or version is None:return {}
    records=[row for row in content.get('source_records',[])+content.get('materials',[]) if isinstance(row,dict)
        and row.get('task_id')==ident and row.get('research_version')==version]
    groups={}
    for row in records:
        if isinstance(row.get('id'),str):groups.setdefault(row['id'],[]).append(row)
    return {key:source_label(rows[0],records=records) if len(rows)==1
        else source_label(rows[0].get('source')) for key,rows in groups.items()}


def financial_observation_label(row):
    """Use the adopted metric and statement scope in place of a whole excerpt."""
    if row.get('source_check') != '通过' or row.get('applicability_check') != '通过':
        return ''
    context = row.get('calculation_context')
    if not isinstance(context, dict) or not isinstance(context.get('metric'), str):
        return ''
    from .financial_sources import FINANCIAL_METRIC_LABELS
    label = FINANCIAL_METRIC_LABELS.get(context.get('metric'), '')
    scope = context.get('accounting_scope')
    scope = {'consolidated': '合并', 'parent': '母公司'}.get(scope) if isinstance(scope, str) else None
    return f'{label}（{scope}）' if label and scope else label


def observation_title(row):
    """Keep an object's literal identity beside its metric in workpapers."""
    label=financial_observation_label(row) or row.get('label') or row.get('object') or row.get('field','')
    subject=row.get('code') or row.get('object') or row.get('object_label','')
    return str(subject)+'：'+str(label) if subject and label and subject!=label else str(label or subject)


def observation_source_label(row, source_labels):
    source = source_labels.get(row.get('artifact_id')) or source_label(row.get('source')) or '原件'
    locator = row.get('record_locator')
    part = locator.get('part') if isinstance(locator, dict) else None
    page = re.match(r'第\d+页', part) if isinstance(part, str) else None
    return source + ('，' + page[0] if page else '')


def calculation_period(calculation):
    period = calculation.get('period') or calculation.get('parameters', {}).get('period')
    if calculation.get('comparison_kind') == 'previous_session':
        period = calculation['window_start'] + '至' + calculation['window_end']
    return str(reader_period_label(period)) if period else ''


def calculation_title(calculation, *, include_scope=True):
    """Identify the object and literal window of the displayed result."""
    method = calculation.get('method', '')
    title = calculation.get('label') or calculation.get('method_title') or METHOD_REGISTRY.get(method, {}).get('title', method)
    if not include_scope:
        return title
    code, period = calculation.get('code'), calculation_period(calculation)
    if code:
        title = str(code) + '：' + title
    return title + '，' + period if period else title


def observation_value(row):
    """A readable missing cell, without changing the raw export dataset."""
    value=row.get('raw_value',row.get('value',row.get('text','')))
    return '未返回' if value is None else str(value)




def reader_date_label(value):
    """Format only explicit valid calendar days, without guessing fiscal periods."""
    from .draft_sources import _sequence_day
    day = _sequence_day(value)
    return day.isoformat() if day is not None else value


def reader_period_label(value):
    """Format explicit calendar days or a literal day range, never a fiscal label."""
    if not isinstance(value,str):return value
    shown=reader_date_label(value)
    if shown!=value:return shown
    endpoint=r'(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{4}/[0-9]{2}/[0-9]{2}|[0-9]{8})'
    match=re.fullmatch('('+endpoint+r')(至|到|~|～| - |-)('+endpoint+')',value)
    if not match:return value
    from datetime import date
    try:
        dates=[date.fromisoformat(reader_date_label(match.group(index))).isoformat() for index in (1,3)]
    except ValueError:return value
    return dates[0]+match.group(2)+dates[1]


def requested_financial_statement(content, rows):
    """Name a common explicit request scope; never infer financial approval."""
    from .ifind_financial_catalog import definition
    owned={}
    for record in content.get('source_records', []):
        if (record.get('task_id')==content.get('task_id')
                and record.get('research_version')==content.get('research_version')):
            owned.setdefault(record.get('id'), []).append(record)
    labels=set()
    for row in rows:
        records=owned.get(row.get('artifact_id'), [])
        if len(records)!=1 or row.get('period_basis')!='请求D报告期':return ''
        record=records[0];params=record.get('parameters', {})
        if record.get('request_hash') and record['request_hash']!=fingerprint({key:record.get(key) for key in ('source','operation','parameters')}):return ''
        if (record.get('source')!='ifind' or record.get('operation')!='basic_data_service'
                or not isinstance(params,dict) or not isinstance(params.get('codes'),str)
                or row.get('code') not in [code.strip() for code in params['codes'].split(',')]
                or not isinstance(params.get('indipara'),list)):return ''
        selected=[item for item in params['indipara'] if isinstance(item,dict) and item.get('indicator')==row.get('field')]
        if len(selected)!=1 or not isinstance(selected[0].get('indiparams'),list) or len(selected[0]['indiparams'])!=2:return ''
        period,statement=selected[0]['indiparams']
        if (not isinstance(period,str) or not re.fullmatch(r'\d{8}',period)
                or reader_date_label(period)==period
                or reader_date_label(period)!=reader_date_label(row.get('period'))
                or not isinstance(statement,str)):return ''
        metadata=definition(row.get('field')) or {}
        choices={value:name for name,value in metadata.get('supported_report_types',{}).items()}
        if statement not in choices:return ''
        labels.add(choices[statement])
    return next(iter(labels)) if len(labels)==1 else ''


def financial_scope_labels(content, rows):
    """Disambiguate public display labels while retaining literal source cells."""
    from .ifind_financial_catalog import definition
    rows=list(rows);groups={};labels={}
    for row in rows:
        if not row.get('code') or not definition(row.get('field')):continue
        label=requested_financial_statement(content,[row]);labels[row['id']]=label
        groups.setdefault(row['code'],set()).add(canonical([row.get('series_basis',''),label]))
    return {row['id']:labels[row['id']] for row in rows if labels.get(row['id'])
            and len(groups.get(row.get('code'),()))>1}


def observation_period_heading(rows,default='观测期间'):
    """Name an explicit D-report window without treating it as a publication day."""
    return '报告期' if rows and all(row.get('period_basis')=='请求D报告期' for row in rows) else default


_VALUATION_FIELDS = {'ths_pe_ttm_stock': 'pe', 'ths_pb_mrq_stock': 'pb', 'ths_pb_latest_stock': 'pb'}
_VALUATION_ALIASES = {'pe': r'(?<![A-Za-z])P/?E(?![A-Za-z])|市盈率',
                      'pb': r'(?<![A-Za-z])P/?B(?![A-Za-z])|市净率'}
_VALUATION_NUMBER = r'[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
_VALUATION_LEVEL = rf'{_VALUATION_NUMBER}(?:\s*(?:倍(?![于数])|[%％](?!分位)|[xX](?![A-Za-z0-9])))?(?![A-Za-z0-9_.])'
_VALUATION_MOVEMENT = r'(?:升|降|上升|下降|回升|回落|涨|跌)(?:至|到)|变为'
_VALUATION_QUOTE = re.compile(
    rf'(?:(?P<pe>{_VALUATION_ALIASES["pe"]})|(?P<pb>{_VALUATION_ALIASES["pb"]}))'
    r'(?:\s*(?:[（(]\s*(?:TTM|MRQ|LYR)\s*[）)]|TTM|MRQ|LYR))?(?:\s*(?:读数|数值))?\s*'
    rf'(?:约为|为|约|是|报|录得|由|从|[:：=]|{_VALUATION_MOVEMENT})?\s*(?P<first>{_VALUATION_LEVEL})'
    rf'(?:\s*(?:至|到|{_VALUATION_MOVEMENT})\s*(?P<last>{_VALUATION_LEVEL}))?', re.I)


def _valuation_value(row):
    raw = row.get('raw_value', row.get('value'))
    if type(raw) not in (int, float, str) or type(row.get('value')) is bool:
        return None
    try:
        value = Decimal(str(raw).replace(',', '').replace('−', '-'))
        return value if value.is_finite() and value == Decimal(str(row['value'])) else None
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return None


def _valuation_unit_corrections(text, rows):
    """Remove unsupported units from attributable PE/PB levels only."""
    from .numeric_integrity import draft_quantity_scope, draft_quantity_source_matches

    series = {}
    for row in rows:
        family = _VALUATION_FIELDS.get(row.get('field'))
        if family and (value := _valuation_value(row)) is not None:
            series.setdefault(family, []).append((row, value))
    if not series:
        return []

    corrections = []
    for quote in _VALUATION_QUOTE.finditer(text):
        prefix = re.split(r'[。！？!?；;，,\n]', text[:quote.start()])[-1]
        if re.search(r'目标|合理|假[设定如]|若|如果|预计|预测|预期|情景|设定|静态|动态|远期', prefix):
            continue
        if re.match(r'\s*(?:时|的情况下)', text[quote.end():]):
            continue
        candidates = series.get('pe' if quote['pe'] else 'pb', ())
        matched = []
        for group in ('first', 'last'):
            token = quote[group]
            if not token or token[-1] not in '倍%％xX':
                continue
            number = token[:-1].rstrip()
            reading = Decimal(number.replace(',', '').replace('−', '-'))
            scope = draft_quantity_scope(text, quote.start(group))
            with localcontext() as context:
                context.prec = max(50, len(reading.as_tuple().digits) + 2)
                step = Decimal(1).scaleb(reading.as_tuple().exponent)
                cited = [row for row, value in candidates if draft_quantity_source_matches(row, scope)
                         and value.adjusted() <= reading.adjusted() + 1
                         and value.quantize(step, rounding=ROUND_HALF_UP) == reading]
            if (cited and all(row.get('unit') == '原始单位待核对' for row in cited)
                    and len({(row.get('code'), row.get('field')) for row in cited}) == 1):
                matched.append((group, number, cited))
        for index, (group, number, cited) in enumerate(matched):
            end = quote.end(group)
            note = '（单位未明确）' if index == len(matched) - 1 and not text[end:].startswith('（单位未明确）') else ''
            corrections.append(dict(start=quote.start(group), end=end, original=quote[group],
                replacement=number + note, data_refs=[row['id'] for row in cited]))
    return corrections


def _valuation_label(value):
    """Display precision only; never turn a nonzero observation into zero."""
    try:
        with localcontext() as context:
            context.prec = max(50, len(value.as_tuple().digits) + abs(value.adjusted()) + 8)
            digits = 2
            rounded = value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
            while value and not rounded and digits < 8:
                digits += 1
                rounded = value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
            return None if value and not rounded else format(rounded, f'.{digits}f')
    except (InvalidOperation, OverflowError, ValueError):
        return None



def observation_unit(row):
    """Clarify an explicitly registered quote currency without changing raw units."""
    unit=row.get('unit','');reference=row.get('unit_reference')
    if not isinstance(reference,dict):reference={}
    if (unit=='元' and ('raw_unit' not in row or row['raw_unit']=='元') and row.get('currency')=='RMB' and row.get('source')=='ifind'
            and reference.get('kind')=='registered_field' and reference.get('registry')=='ifind.cmd_history_quotation'
            and reference.get('family')=='price' and reference.get('field')==row.get('field')
            and reference.get('currency_parameter')=='RMB'):
        return '人民币元'
    return unit


def observation_raw_unit(row):
    """Keep the unit paired with the original number, even after conversion."""
    unit = row.get('unit', '')
    raw_unit = row.get('raw_unit', unit)
    if 'raw_value' in row and raw_unit != unit:
        return raw_unit or ''
    return observation_unit(row)


def observation_presentation(row):
    """A client reading plus the unchanged original in its evidence link."""
    from .draft_visuals import display_observation
    shown = display_observation(row)
    if row.get('field') in _VALUATION_FIELDS and type(row.get('value')) in (int, float):
        value = _valuation_value(row)
        if value is not None:
            shown = _valuation_label(value) or shown
    unit = observation_unit(row)
    return dict(value=shown, period=reader_period_label(row.get('period', '')),
                unit='未确认' if unit == '原始单位待核对' else unit)


def draft_quantity_presentation(text, rows):
    """Round only exact, cited valuation readings in their named sentence.

    This is display precision, not unit approval or a new observation. The
    caller supplies task-owned rows. Unknown amounts, unmatched numbers,
    ambiguous metrics and tiny values that would become zero stay verbatim.
    """
    quantities = {}
    for row in rows:
        if (value := _valuation_value(row)) is not None:
            quantities.setdefault(value, []).append(row)
    token = re.compile(r'(?<![A-Za-z0-9_.,])[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{3,}(?![A-Za-z0-9_.%％])')
    changes = []
    for match in token.finditer(text):
        value = Decimal(match.group().replace(',', '').replace('−', '-'))
        selected = quantities.get(value, [])
        families = {_VALUATION_FIELDS.get(row.get('field')) for row in selected}
        if len(families) != 1 or None in families or len({row.get('code') for row in selected}) != 1:
            continue
        start = max((text.rfind(mark, 0, match.start()) for mark in '。；;!?！？\n'), default=-1) + 1
        stops = [text.find(mark, match.end()) for mark in '。；;!?！？\n']
        end = min((position for position in stops if position >= 0), default=len(text))
        named = {key for key, pattern in _VALUATION_ALIASES.items() if re.search(pattern, text[start:end], re.I)}
        if named != families:
            continue
        label = _valuation_label(value)
        if label is None:
            continue
        changes.append(dict(start=match.start(), end=match.end(), label=label,
            original=match.group(), data_refs=list(dict.fromkeys(row['id'] for row in selected))))
    return changes

def _basic_unit_cell_matches(row, record, claims):
    """Check the cited cell and D/C request, without approving its business value."""
    try:
        from .contracts import fingerprint
        parameters=record['parameters'];snapshot=record['snapshot'];locator=row['locator']
        if record.get('hash') not in (None,fingerprint(snapshot)):
            return False
        if not any(item['indicator']==row['field'] and item['indiparams'][:2]==[row['period'],claims['report_type']]
                   for item in parameters['indipara']):
            return False
        if row['code'] not in parameters['codes'].split(',') or locator['field']!=row['field']:
            return False
        table=snapshot['tables'][locator['table']]
        return (table.get('thscode',parameters['codes'])==row['code']
                and canonical(table['table'][row['field']][locator['row']])==canonical(row['raw_value']))
    except (KeyError,TypeError,ValueError,IndexError):
        return False


def unit_basis_table(rows,records,*,compact=False):
    """Compact provenance for the existing inspector and internal report only."""
    known={record['id']:record for record in records};result=[];seen=set();cohorts={}
    filing_snapshots = {}
    labels={'literal_source':'原件字面标注','source_metadata':'iFinD指标目录',
            'registered_field':'已登记字段口径','calibrated_definition':'限定范围的原件校准'}
    from .contracts import fingerprint
    for row in rows:
        reference=row.get('unit_reference')
        if not isinstance(reference,dict):continue
        locator = row.get('locator', {})
        if compact and locator.get('kind') == 'peer_filing_cell' and reference.get('kind') == 'literal_source':
            artifact = row.get('artifact_id')
            record = known.get(artifact)
            basis = locator.get('statement_basis')
            unit_basis = locator.get('unit_basis')
            if (record and basis and unit_basis and reference.get('artifact_id') == artifact
                    and reference.get('locator') == unit_basis):
                if artifact not in filing_snapshots:
                    filing_snapshots[artifact] = fingerprint(record.get('snapshot'))
                if reference.get('snapshot_hash') == filing_snapshots[artifact]:
                    unit = observation_unit(row)
                    key = ('peer_statement', artifact, fingerprint(basis), fingerprint(unit_basis), unit)
                    if key not in seen:
                        seen.add(key)
                        name = row.get('object_label') or row['code']
                        caption = '合并现金流量表' if '现金流量表' in basis.get('quote', '') else '合并利润表'
                        result.append([name+caption,unit,'原件'+unit_basis.get('part','')])
                    continue
        key=(row.get('code'),row.get('field'),row.get('unit'),fingerprint(reference))
        if key in seen:continue
        seen.add(key);kind=reference.get('kind');label=labels.get(kind,'依据待定位')
        if kind=='source_metadata':
            citations=reference.get('citations',[]);units=[]
            from .economic_sources import search_response
            valid=bool(citations)
            for citation in citations:
                record=known.get(citation.get('artifact_id'));locator=citation.get('locator',{})
                if (record is None or record.get('source')!='ifind' or record.get('operation')!='search_edb'
                        or fingerprint(record.get('snapshot'))!=citation.get('snapshot_hash')
                        or locator!=dict(indicator_id=row.get('code'),field='unit')):
                    valid=False;continue
                try:metadata=next(item for item in search_response(record['snapshot']) if item['displayId']==row['code'])
                except (ContractError,StopIteration,KeyError,TypeError,ValueError):valid=False;continue
                if metadata['unit']!=citation.get('raw_unit'):valid=False;continue
                units.append(metadata['unit'])
            normalized={unit.strip() for unit in units}
            if reference.get('status')=='明确' and normalized!={row.get('unit')}:valid=False
            if reference.get('status')=='冲突' and (len(normalized)<2 or row.get('unit')!='原始单位待核对'):valid=False
            if not valid:label='单位依据待定位'
            elif reference.get('status')=='冲突':label='目录单位冲突：'+'、'.join(sorted(set(units)))
        elif kind=='calibrated_definition':
            from .ifind_financial_catalog import evidence_definitions
            definitions=evidence_definitions(2)
            proof=next((item for item in definitions.values() if fingerprint(item)==reference.get('definition_hash')),None)
            claims=proof['claims'] if proof else {}
            basis=proof['basis'] if proof else {}
            scope=dict(codes=claims.get('applicable_codes'),periods=claims.get('applicable_periods'),report_type=claims.get('report_type'))
            record=known.get(row.get('artifact_id'),{})
            valid=(proof is not None and row.get('code') in claims['applicable_codes']
                and row.get('period') in claims['applicable_periods'] and row.get('field')==claims['indicator']
                and row.get('unit')==claims['unit'] and reference.get('scope')==scope
                and all(reference.get(key)==basis.get(value) for key,value in (
                    ('source','source'),('locator','locator'),('basis_hash','hash'),('available_at','obtained_at')))
                and record.get('source')=='ifind' and record.get('operation')=='basic_data_service'
                and _basic_unit_cell_matches(row,record,claims))
            label=(label+'；'+basis['locator']) if valid else '单位依据待定位'
        cohort=None
        record=known.get(row.get('artifact_id'),{})
        if (compact and row.get('axis_role')=='cross_section' and kind=='literal_source'
                and reference.get('artifact_id')==row.get('artifact_id')
                and reference.get('snapshot_hash')==fingerprint(record.get('snapshot'))):
            basis={key:value for key,value in reference.items() if key!='locator'}
            cohort=canonical([row.get('artifact_id'),row.get('field'),row.get('label'),row.get('period'),
                observation_unit(row),row.get('series_basis'),basis,label])
        if cohort in cohorts:
            index,count=cohorts[cohort];count+=1;cohorts[cohort]=(index,count)
            snapshot=record.get('snapshot') or {};parent=snapshot.get('parsed',{}).get('index_code','') if isinstance(snapshot,dict) else ''
            result[index][0]=str(row.get('label') or row.get('field'))+('，'+parent if isinstance(parent,str) and parent else '')+'，'+str(count)+'项'
        else:
            if cohort is not None:cohorts[cohort]=(len(result),1)
            result.append([observation_title(row),observation_unit(row),label])
    return [['指标','单位','单位依据']]+result if result else None


def _financial_change_groups(content, selected, validated, inspectable_refs):
    """Group only reproducible changes with the same reporting basis and conditions."""
    from .draft_analysis import BALANCE, METHODS
    from .tinyshare_sources import analysis_field

    groups = {}
    for item in selected:
        calc = validated.get(item['id'])
        if not calc or calc['method'] != 'raw_change_v1' or not set(calc['data_refs']) <= inspectable_refs:
            continue
        before, after = calc['rows']
        scope = requested_financial_statement(content, calc['rows'])
        basis = before.get('series_basis')
        if not scope or not isinstance(basis, str) or not basis:
            continue
        condition = calc.get('condition') or METHODS[calc['method']]['condition']
        key = (scope, basis, before['period'], after['period'], analysis_field(before) in BALANCE,
               calc['method_version'], calc['unit'], condition, bool(calc.get('conditional_equal_scale')))
        groups.setdefault(key, []).append(calc)
    return {members[0]['id']: (key, members) for key, members in groups.items() if len(members) > 1}


def _financial_change_blocks(group, target):
    from .draft_analysis import formula_mathml
    from .draft_visuals import FIELDS
    from .tinyshare_sources import analysis_field

    (scope, _, before, after, balance, _, _, condition, conditional), members = group
    title = ('营运资金余额同比' if balance else '经营指标同比') + '，' + scope
    periods = '基期' + reader_period_label(before) + '，本期' + reader_period_label(after)
    if conditional:
        condition += ' 原始单位未明确，比较以各输入尺度一致为前提。'
    return [
        dict(kind='heading', level=4, text=title),
        dict(kind='paragraph', text=periods),
        dict(kind='formula', mathml=formula_mathml('raw_change_v1', labels={'a':'基期原值','b':'本期原值'})),
        dict(kind='paragraph', text=condition),
        dict(kind='table', rows=[['标的','指标','同比']]+[
            [calc['code'], FIELDS[analysis_field(calc['rows'][0])], calc['display']+calc['unit']] for calc in members],
            calculation_rows=[dict(id=calc['id'],data_refs=calc['data_refs']) for calc in members],
            inspection_target=target),
    ]


_FILING_FORMULA_ROLES = {
    'raw_change_v1': ('基期原值', '本期原值'),
    'raw_difference_v1': ('基期原值', '本期原值'),
    'raw_margin_v1': ('营业收入', '营业成本'),
    'raw_percent_v1': ('分子', '分母'),
}


def _filing_calculation_groups(selected, verified, inspectable_refs, excluded):
    """Share formulas across reproduced inputs with the same roles and filing."""
    groups = {}
    for item in selected:
        calculation = verified.get(item['id'])
        if (not calculation or item['id'] in excluded or calculation['method'] not in _FILING_FORMULA_ROLES
                or calculation.get('conditional_equal_scale') or not set(calculation['data_refs']) <= inspectable_refs):
            continue
        before, after = calculation['rows']
        bases = (before.get('series_basis'), after.get('series_basis'))
        if before['artifact_id'] != after['artifact_id'] or not all(bases):
            continue
        method = calculation['method']
        comparison = method in ('raw_change_v1', 'raw_difference_v1') and bases[0] == bases[1]
        inputs = (before['period'], after['period']) if comparison else bases
        key = (before['artifact_id'], comparison, inputs, calculation['code'], method,
               calculation['method_version'], calculation['unit'], calculation['condition'])
        groups.setdefault(key, []).append(calculation)
    return {members[0]['id']: members for members in groups.values() if len(members) > 1}


def _filing_calculation_blocks(members, target):
    from .draft_analysis import METHODS, formula_mathml

    first = members[0]
    method = first['method']
    labels = {calc['label'] for calc in members}
    title = next(iter(labels)) if len(labels) == 1 else METHODS[method]['title']
    before, after = first['rows']
    if before['series_basis'] == after['series_basis']:
        roles = _FILING_FORMULA_ROLES[method]
    else:
        roles = tuple(row.get('label') or fallback for row, fallback in
                      zip(first['rows'], _FILING_FORMULA_ROLES[method], strict=True))
    variables = dict(zip('ab', roles, strict=True))
    wording = first['condition']
    if method == 'raw_change_v1' and before.get('locator', {}).get('kind') == 'peer_filing_cell':
        wording = '以上年同期金额为基数。'
    return [
        dict(kind='heading', level=4, text=first['code']+' '+title),
        dict(kind='formula', mathml=formula_mathml(method, labels=variables)),
        dict(kind='paragraph', text=wording),
        dict(kind='table', rows=[['计算项','报告期','结果']]+[
            [calc['label'], reader_period_label(calc['period']), calc['display']+calc['unit']] for calc in members],
            calculation_rows=[dict(id=calc['id'],data_refs=calc['data_refs']) for calc in members],
            inspection_target=target),
    ]


def _peer_profit_detail_blocks(logic, verified, target):
    """Show each filing line once, with its group-statement inputs."""
    from .draft_analysis import PEER_NET_AFTER_GROSS, peer_profit_components
    from .financial_data import UNIT_FACTORS

    money_scale = Decimal(100000000)
    calculations = [verified[ref] for ref in logic['calculation_refs'] if ref in verified]
    covered_calcs, covered_inputs, blocks = set(), set(), []
    for company in logic['peer_operating_series']:
        aggregate = verified.get(company['calculations'].get(PEER_NET_AFTER_GROSS))
        if not aggregate:
            continue
        components = peer_profit_components(aggregate, calculations)
        if not components:
            continue
        first = next(iter(components.values()))['calculation']['rows']
        prior, current = (row['period'][:4]+'年上半年' for row in first)
        entries = [[company['name']+'利润表项目', prior, current, '对归母净利润同比变动的影响']]
        links = []
        for item in components.values():
            calculation = item['calculation']
            before, after = (Decimal(str(row['raw_value']))*UNIT_FACTORS[row['unit'].removeprefix('人民币')]
                             for row in calculation['rows'])
            entries.append([item['label'], f'{before/money_scale:,.2f}', f'{after/money_scale:,.2f}',
                            f'{item["effect"]/money_scale:,.2f}'])
            links.append(dict(id=calculation['id'], data_refs=calculation['data_refs']))
            covered_calcs.add(calculation['id'])
            covered_inputs.update(calculation['data_refs'])
        residual = Decimal(aggregate['exact'])-sum(item['effect'] for item in components.values())
        blocks.extend([
            dict(kind='heading', level=4, text=company['name']+'毛利以下损益明细（亿元）'),
            dict(kind='table', rows=entries, calculation_rows=links, inspection_target=target, includes_inputs=True),
        ])
        if residual:
            blocks.append(dict(kind='paragraph', text=f'其余项目净影响{residual/money_scale:,.2f}亿元。'))
    return blocks, covered_calcs, covered_inputs


def project_content(content,profile='standard',bindings=None,*,presented_data_refs=()):
    if not isinstance(profile,str) or profile not in PROFILES:raise ContractError('请选择标准报告或详细报告')
    groups={};draft=content.get('draft');bindings=bindings or {}
    risks_at_end = bool(draft) and draft_risks_at_end(content)
    scope_labels=financial_scope_labels(content,draft.get('facts',[]) if draft else content.get('evidence',[]))
    if profile=='detailed':
        facts={r['id']:r for r in (draft.get('facts',[]) if draft else content.get('evidence',[]))}
        calculations={r['id']:r for r in (draft.get('calculations',[]) if draft else content.get('calculations',[]))}
        source_records={r['id']:r for r in content.get('source_records',[]) if r.get('task_id')==content.get('task_id') and r.get('research_version')==content.get('research_version')}
        labels=publication_source_labels(content)
        validated, verified_filing = {}, {}
        if draft and any(logic.get('stock_quality_readout') or logic.get('peer_profit_details')
                         for logic in draft.get('logics', [])):
            from .draft_visuals import _calculation_dependencies

            verified_filing = _calculation_dependencies(content)
        if draft and draft.get('visual_layout_version') == 2:
            from .draft_visuals import _validated
            validated = {row['id']: row for row in _validated(content)}
        logics=draft.get('logics',[]) if draft else content.get('theses',[])
        question_workpapers = not draft and not logics and content.get('intent') != DOCUMENT_CONTENT_INTENT
        if question_workpapers:
            logics = [question for question in content.get('questions', [])
                if question.get('answer') and (question.get('status') == '已回答'
                    or question.get('research_coverage', {}).get('status')
                    in ('answered', 'requires_evidence', 'unidentifiable'))]
        for index,logic in enumerate(logics,1):
            if draft:
                logic = draft_logic_reading(logic, calculations.values())
            key = ('logic-' + str(index) if draft else 'answer-' + logic['id'] if question_workpapers else logic['id'])
            binding=bindings.get(key,{})
            selected_refs = (draft_logic_data_refs(logic) if draft else logic.get('refs', [])
                if question_workpapers else logic.get('data_refs', logic.get('support', [])))
            refs=list(dict.fromkeys(selected_refs+logic.get('counter',[])+binding.get('data_refs',[])))
            peer_tables = [set(table) for table in binding.get('peer_table_data_refs', [])]
            peer_changes = [calc['id'] for calc in validated.values()
                            if calc['method']=='raw_change_v1' and any(set(calc['data_refs']) <= table for table in peer_tables)]
            calc_refs=list(dict.fromkeys(logic.get('calculation_refs',[])+binding.get('calculation_refs',[])+peer_changes))
            selected=[calculations[ref] for ref in calc_refs if ref in calculations]
            for calc in selected:refs.extend(calc.get('data_refs',calc.get('input_refs',[])))
            allowed=set(facts)
            if draft:
                from .drafts import logic_basis
                basis=dict(logic,data_refs=refs,calculation_refs=calc_refs)
                allowed={row['id'] for row in logic_basis(content,basis)[0]}
            rows=[facts[ref] for ref in dict.fromkeys(refs) if ref in facts and ref in allowed];blocks=[]
            # Literal readings and table cells already printed beside the
            # logic need no second copy in the detailed workpaper.
            printed=set(binding.get('table_data_refs', [])) | set(binding.get('reading_data_refs', []))
            if draft and logic.get('peer_profit_details'):
                detail_verified = {ref: item for ref, item in verified_filing.items() if set(item['data_refs']) <= allowed}
                profit_blocks, component_calcs, component_inputs = _peer_profit_detail_blocks(
                    logic, detail_verified, 'draft-basis-'+str(index))
                blocks.extend(profit_blocks)
                printed.update(component_inputs)
                selected = [row for row in selected if row['id'] not in component_calcs]
            unshown=[row for row in rows if row['id'] not in printed]
            if unshown:
                cross_sections={}
                if draft:
                    for row in unshown:
                        if row.get('axis_role')=='cross_section':
                            cohort=canonical([row.get('artifact_id'),row.get('field'),row.get('period'),row.get('unit'),row.get('series_basis')])
                            cross_sections.setdefault(cohort,[]).append(row)
                shared=[group for group in cross_sections.values() if len(group)>INLINE_OBSERVATION_LIMIT]
                referenced={row['id'] for group in shared for row in group}
                inline=[row for row in unshown if row['id'] not in referenced]
                blocks.append(dict(kind='heading',level=3,text='数据依据'))
                if inline:blocks.append(dict(kind='table',data_refs=[row['id'] for row in inline],rows=[['对象与指标',observation_period_heading(inline),'原值','单位']]+[
                    [observation_title(r)+('，'+scope_labels[r['id']] if r['id'] in scope_labels else ''),reader_period_label(r.get('period','')),observation_value(r),observation_raw_unit(r)] for r in inline]))
                for group in shared:
                    source=source_records.get(group[0].get('artifact_id'),{})
                    parent=source.get('snapshot',{}).get('parsed',{}).get('index_code','')
                    scope=(str(parent)+'，' if parent else '')+str(reader_period_label(group[0].get('period') or ''))
                    text=(scope+'的' if scope else '')+str(len(group))+'项'+str(group[0].get('label') or group[0].get('field'))+'见完整数据。'
                    blocks.append(dict(kind='data_reference',text=text,data_refs=[r['id'] for r in group]))
            unit_table=unit_basis_table(rows,source_records.values(),compact=True)
            if unit_table:
                blocks.extend([dict(kind='heading',level=3,text='单位依据'),dict(kind='table',rows=unit_table)])
            from .drafts import logic_knowledge
            for entry in logic_knowledge(content,logic):
                blocks.extend([dict(kind='heading',level=3,text=entry['title']),
                    dict(kind='paragraph',text=entry['content']),
                    dict(kind='paragraph',text='适用条件：'+entry.get('conditions','')),
                    dict(kind='paragraph',text='反例与相反渠道：'+entry.get('counterexample','')),
                    dict(kind='paragraph',text='来源：'+entry.get('source',''))])
            if selected:
                from .draft_analysis import formula_mathml,METHODS
                grouped, filing_groups = {}, {}
                if validated or verified_filing:
                    inspectable = {row['id'] for row in logic_basis(content, logic)[0]}
                    grouped = _financial_change_groups(content, selected, validated, inspectable)
                    booked = {calc['id'] for _, members in grouped.values() for calc in members}
                    filing_groups = _filing_calculation_groups(selected, verified_filing, inspectable, booked)
                collapsed = {calc['id'] for _, members in grouped.values() for calc in members[1:]}
                collapsed.update(calc['id'] for members in filing_groups.values() for calc in members[1:])
                if grouped or filing_groups:
                    blocks.append(dict(kind='heading',level=3,text='计算依据'))
                for calc_index, calc in enumerate(selected):
                    if calc['id'] in collapsed:
                        continue
                    if calc['id'] in grouped:
                        blocks.extend(_financial_change_blocks(grouped[calc['id']], 'draft-basis-'+str(index)))
                        continue
                    if calc['id'] in filing_groups:
                        blocks.extend(_filing_calculation_blocks(filing_groups[calc['id']], 'draft-basis-'+str(index)))
                        continue
                    first=len(blocks)
                    if calc_index == 0 and not grouped and not filing_groups:
                        blocks.append(dict(kind='heading',level=3,text='计算依据'))
                    blocks.append(dict(kind='paragraph',text=calculation_title(calc)))
                    if calc['method'] in METHODS:
                        blocks.append(dict(kind='formula',mathml=formula_mathml(calc['method'])))
                        blocks.append(dict(kind='paragraph',text=calc.get('condition') or METHODS[calc['method']]['condition']))
                        if calc.get('conditional_equal_scale'):
                            blocks.append(dict(kind='paragraph',text='原始单位未明确，比较以各输入尺度一致为前提。'))
                    elif not draft:
                        from .reports import calculation_notation
                        _, notation, variables = calculation_notation(calc)
                        if notation:
                            blocks.append(dict(kind='formula',mathml=notation))
                            blocks.append(dict(kind='paragraph',text=variables))
                        limitations = calc.get('limitations', '')
                        if isinstance(limitations, list):
                            limitations = '；'.join(limitations)
                        if limitations:
                            blocks.append(dict(kind='paragraph',text=limitations))
                    output=calc.get('output',{})
                    blocks.append(dict(kind='paragraph',text='结果：'+str(calc.get('display',output.get('value','')))+str(calc.get('unit',output.get('unit','')))))
                    for block in blocks[first:]:block['calculation_ref']=calc['id']
            notes=logic.get('workpaper_notes',[])
            if notes:
                blocks.append(dict(kind='heading',level=3,text='研究备注'))
                blocks.extend(dict(kind='paragraph',text=note) for note in notes)
            risk=logic.get('risk',logic.get('limitation',''))
            if risk and not (risks_at_end or draft and draft_reading_enabled(draft)):
                blocks.extend([dict(kind='heading',level=3,text='反证与验证条件'),dict(kind='paragraph',text=risk)])
            names=[]
            for row in rows:
                source=source_records.get(row.get('artifact_id'),{});name=source.get('source',row.get('source',''))
                if name:names.append(labels.get(source.get('id'),source_label(name)))
            if names:blocks.extend([dict(kind='heading',level=3,text='原始来源'),dict(kind='paragraph',text='、'.join(dict.fromkeys(names)))])
            if blocks:groups[key]=blocks
        if draft and draft.get('workpaper_notes'):
            groups['summary']=[dict(kind='heading',level=3,text='研究备注')]+[
                dict(kind='paragraph',text=note) for note in draft['workpaper_notes']]
        if not logics:
            shown = set(presented_data_refs)
            rows = [row for ref, row in facts.items() if ref not in shown]
            financial = [row for row in rows if financial_observation_label(row)]
            excerpts = [row for row in rows if not financial_observation_label(row)]
            blocks = []
            if financial:
                blocks.append(dict(kind='table', source_links=True, data_refs=[row['id'] for row in financial],
                    rows=[['对象与指标','所属期','原值','单位','来源']]+[[observation_title(row),
                        reader_period_label(row.get('period', '')), observation_value(row),
                        observation_raw_unit(row), observation_source_label(row, labels)] for row in financial]))
            if excerpts:
                blocks.append(dict(kind='table', rows=[['对象','期间','依据']]+[[row.get('object',row.get('label','')),
                    row.get('period',''),row.get('text',str(row.get('value','')))] for row in excerpts]))
            if blocks:
                groups['research-basis'] = [dict(kind='heading',level=2,text='研究依据'), *blocks]
    return dict(profile=profile,audience='client' if profile=='standard' else 'internal',
        content_hash=hashlib.sha256(canonical(content).encode()).hexdigest(),logic_blocks=groups,
        appendix=[b for rows in groups.values() for b in rows])



def reader_notes(content, *, client=False, comparison_refs=None):
    """Keep material comparability conditions once, alongside business risks."""
    draft=content.get('draft') or {}
    notes=list(dict.fromkeys(draft.get('reader_notes',[])))
    from .draft_analysis import FINANCIAL, analysis_field
    from .draft_visuals import UNKNOWN_FINANCIAL_UNITS, _validated
    from .market_series import research_market_inputs_enabled

    comparisons = [row for row in _validated(content) if row.get('conditional_equal_scale')
                   and (comparison_refs is None or row['id'] in comparison_refs)]
    if comparisons:
        if client:
            consolidated = research_market_inputs_enabled(content) and all(row['method'] == 'raw_change_v1'
                and requested_financial_statement(content, row['rows']) == '合并报表' for row in comparisons)
            if consolidated:
                notes.append('财务同比按两期合并报表的同名项目测算。')
            else:
                notes.append('同比与比率以计量口径一致为比较前提。' if comparison_refs is not None
                             else '若报表披露口径不同，同比和比率需重新计算。')
        elif any(analysis_field(row) in FINANCIAL and observation_raw_unit(row) in UNKNOWN_FINANCIAL_UNITS
                 for comparison in comparisons for row in comparison['rows']):
            notes.append('相关金额的计量单位尚未核实，跨期比较以单位一致为前提。')
        else:
            notes.append('同比与比率按对应指标的原始计量口径比较。跨期比较要求单位一致。')
    return list(dict.fromkeys(notes))


_DRAFT_CALCULATION_INPUT_LABELS = {
    'etf_daily_nav_premium_v1': ('收盘价', '单位净值'),
    'raw_change_v1': ('基期', '本期'),
    'raw_difference_v1': ('基期', '本期'),
    'raw_percentage_points_v1': ('基期', '本期'),
    'raw_percent_v1': ('分子', '分母'),
    'raw_multiple_v1': ('分子', '分母'),
    'raw_margin_v1': ('营业收入', '营业成本'),
    'raw_range_v1': ('最小值', '最大值'),
}


def _publication_calculation_context(calc, positions, rows, *, draft):
    """Describe only observations available in this same public dataset."""
    refs = calc.get('data_refs' if draft else 'input_refs')
    if not isinstance(refs, list):
        refs = []
    if draft:
        from .draft_analysis import GROSS_BRIDGE_INPUTS, GROSS_BRIDGE_METHODS, PEER_NET_AFTER_GROSS, PEER_PROFIT_INPUTS

        method = calc.get('method')
        roles = GROSS_BRIDGE_INPUTS if method in GROSS_BRIDGE_METHODS else _DRAFT_CALCULATION_INPUT_LABELS.get(method, ())
        if method == PEER_NET_AFTER_GROSS:
            roles = PEER_PROFIT_INPUTS
        if len(roles) != len(refs):
            roles = ['原值'] * len(refs)
        bindings = list(zip(roles, refs))
    else:
        input_bindings = calc.get('input_bindings')
        bindings = (input_bindings.items() if isinstance(input_bindings, dict) and input_bindings
                    else [('引用原值', ref) for ref in refs])
    inputs = []
    for role, ref in bindings:
        matches = positions.get(ref, ()) if isinstance(ref, str) else ()
        if len(matches) != 1:
            inputs = []
            break
        inputs.append(dict(label=role, index=matches[0]))
    input_rows = [rows[item['index']] for item in inputs]
    subjects = list(dict.fromkeys(row[0] for row in input_rows if isinstance(row[0], str) and row[0]))
    periods = {row[2] for row in input_rows if isinstance(row[2], str) and row[2]}
    subject = calc.get('code') or '、'.join(subjects)
    period = calc.get('period') or (next(iter(periods)) if len(periods) == 1 else '')
    condition = calc.get('condition') or calc.get('limitations') or ''
    if isinstance(condition, list):
        condition = '\n'.join(part for part in condition if isinstance(part, str))
    elif not isinstance(condition, str):
        condition = ''
    if calc.get('conditional_equal_scale') is True:
        condition = '以原值单位一致为前提。' + condition
    result = {key: value for key, value in (('code', subject), ('period', period), ('condition', condition))
              if isinstance(value, str) and value}
    if inputs:
        result['input_rows'] = inputs
        if (len(inputs) == 2 and inputs[0]['label'] == '基期'
                and isinstance(input_rows[0][2], str) and input_rows[0][2] and input_rows[0][2] != input_rows[1][2]):
            result['base_period'] = input_rows[0][2]
    return result


def publication_dataset(content):
    """Public data embedded in the canonical HTML for later conversion."""
    draft=content.get('draft');records={r['id']:r for r in content.get('source_records',[])};labels=publication_source_labels(content)
    facts=draft.get('facts',[]) if draft else content.get('evidence',[]);rows=[]
    positions={}
    scope_labels=financial_scope_labels(content,facts)
    for row in facts:
        source=records.get(row.get('artifact_id'),{}).get('source',row.get('source',''))
        label=financial_observation_label(row) or row.get('label',row.get('field',row.get('text','')))
        if row.get('id') in scope_labels:label=str(label or '')+'，'+scope_labels[row['id']]
        if isinstance(row.get('id'), str):
            positions.setdefault(row['id'], []).append(len(rows))
        rows.append([row.get('code',row.get('object','')),label,
                     row.get('period',''),row.get('raw_value',row.get('value')),observation_raw_unit(row),labels.get(row.get('artifact_id'),source_label(source))])
    calculations=[]
    for calc in (draft.get('calculations',[]) if draft else content.get('calculations',[])):
        output=calc.get('output',{})
        method = calc.get('method', '')
        label = calc.get('label') or calc.get('method_title') or METHOD_REGISTRY.get(method, {}).get('title', method)
        result=dict(label=label,method=method,
            value=calc.get('value',output.get('value')),unit=calc.get('unit',output.get('unit','')))
        result.update(_publication_calculation_context(calc, positions, rows, draft=bool(draft)))
        if calc.get('window_start') and calc.get('window_end') and not calc.get('comparison_kind'):
            result.update(code=calc['code'], period=calc['period'],
                          window_start=calc['window_start'], window_end=calc['window_end'])
        if method=='raw_range_v1':
            result.update(minimum=calc.get('minimum',output.get('minimum')),
                maximum=calc.get('maximum',output.get('maximum')))
        calculations.append(result)
    return dict(rows=rows,calculations=calculations)


def _prepare_financial_period_copy(task, proposal, facts):
    from .draft_sources import financial_period_scope
    scope=financial_period_scope(task,facts)
    if not scope or task.get('intent')=='原件内容核对':
        return proposal,[]
    pattern=re.compile(r'(?:两期|两个报告期|两个年度|两年|2期|2个报告期|2个年度)(?:的)?(?P<noun>(?:经营|财务|会计|报表)?(?:原始)?(?:原值|数据))')
    changes=[]
    def revise(text,path,periods=None):
        periods=scope['available_periods'] if periods is None else periods
        if not isinstance(text,str) or len(periods)>1:return text
        replacements=[]
        for match in pattern.finditer(text):
            start=max(text.rfind(mark,0,match.start()) for mark in '。；;！？\n')+1
            prefix=text[start:match.start()]
            if (not re.search(r'本次|本报告|本研究|本稿|本轮',prefix)
                    or not re.search(r'取得|获得|覆盖|观察|掌握|使用|采用|比较|对照',prefix)
                    or re.search(r'未|无|不|若|如果|假如|假设|假定|预计|计划|将|拟|待|缺|公告|原文|文件|[“”"「」『』]',prefix)):
                continue
            replacement='现有'+match['noun'] if periods else '本次尚未取得所需财务原值'
            change_start=match.start() if periods else start
            if any(change_start<end for _,end,_ in replacements):continue
            replacements.append((change_start,match.end(),replacement))
            changes.append(dict(path=path,original=text[change_start:match.end()],replacement=replacement,
                reason='financial_period_scope',available_periods=periods,financial_approval=False))
        for start,end,replacement in reversed(replacements):text=text[:start]+replacement+text[end:]
        return text
    proposal['conclusion']=revise(proposal.get('conclusion'),'conclusion')
    byid={row['id']:row for row in facts if row.get('id') in scope['financial_refs']}
    for index,logic in enumerate(proposal.get('logics',proposal.get('strategies',[])) or []):
        if not isinstance(logic,dict):continue
        refs=draft_logic_data_refs(logic)
        periods=sorted({byid[ref]['period'] for ref in refs if ref in byid}) or scope['available_periods']
        for field in ('analysis','logic','implication','risk','detail_analysis'):
            if field in logic:logic[field]=revise(logic[field],f'logics.{index}.{field}',periods)
        if isinstance(logic.get('argument'),dict):
            for field,value in logic['argument'].items():logic['argument'][field]=revise(value,f'logics.{index}.argument.{field}',periods)
    return proposal,changes


def prepare_draft_client_copy(task, proposal, facts, organization=None, *, quantity_links=None, logic_exclusions=None):
    """Keep unapproved amount conversions out of a new client draft.

    Only a currency expression matching a cited raw financial cell is changed.
    Known units, prices, ratios, surrounding clauses and original submissions
    stay intact. A matching registered comparison replaces an unknown-scale
    amount; no new arithmetic, model call or overall report gate is introduced.
    """
    from copy import deepcopy
    from .draft_analysis import current_analysis, FINANCIAL
    from .draft_sources import CLIENT_COPY_POLICY, CLIENT_COPY_VERSIONS
    from .numeric_integrity import draft_quantity_scope, draft_quantity_source_matches
    from .tinyshare_sources import analysis_field
    version=task.get(CLIENT_COPY_POLICY)
    if version is None:return proposal,[]
    if type(version) is not int or version not in CLIENT_COPY_VERSIONS:raise ContractError('对客呈现版本未登记')
    from .numeric_integrity import literal_quantity_ranges, quantity_ranges_enabled
    protect_ranges = quantity_ranges_enabled(task)
    result=deepcopy(proposal);adjustments=[]
    owned={row['id'] for row in task.get('source_records',[])+task.get('materials',[])
        if row.get('task_id')==task.get('id') and row.get('research_version')==task.get('version')}
    available=[row for row in facts if row.get('artifact_id') in owned]
    byid={row['id']:row for row in available}
    financial_scope = None
    if version >= 2:
        from .draft_financial_scope import DraftFinancialScope
        financial_scope = DraftFinancialScope(task, available)
    calculations=current_analysis(task,facts).get('results',[])
    quantities=re.compile(r'(?<![A-Za-z0-9_.,])[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:亿元|万元|元)')
    scales={'元':Decimal(1),'万元':Decimal(10000),'亿元':Decimal(100000000)}
    literal=organization in ('fact','comparison')

    # Resolve the complete measure before looking for a corresponding number.
    # These names describe distinct measures, not newly supported data fields.
    aliases={name:field for field,labels in _FINANCIAL_NAMES.items() for name in labels}
    distinct_measures=(
        '扣非净利润','扣非归母净利润','扣除非经常性损益后的净利润',
        '扣除非经常性损益后归属于母公司所有者的净利润',
        '扣除非经常性损益后归属于上市公司股东的净利润',
        '每股净利润','每股营业收入','人均营业收入','单位营业收入',
    )
    aliases.update({name:None for name in distinct_measures})
    custom={}
    for row in available:
        field=analysis_field(row);label=row.get('label')
        if field in FINANCIAL and isinstance(label,str) and label and label not in aliases:
            custom.setdefault(label,set()).add(field)
    aliases.update({name:next(iter(fields)) if len(fields)==1 else None for name,fields in custom.items()})
    transition_signs={}
    for sign,verbs in ((1,('升','上升','增','增加','增长','提升','提高','回升','上涨','增大','上调')),
                       (-1,('降','下降','减','减少','降低','回落','下滑','下跌','缩减','下调'))):
        transition_signs.update({verb+ending:sign for verb in verbs for ending in ('至','到')})
    transition_signs.update({'至':0,'到':0,'变为':0,'变成':0})
    transitions='|'.join(re.escape(name) for name in sorted(transition_signs,key=len,reverse=True))
    amount_transition=re.compile(r'\s*('+transitions+r')\s*')
    lead_names=['为','是','约为','约','由','从','达到','录得']+[name for name in transition_signs if name not in ('至','到')]
    connector=re.compile(r'\s*(?:余额)?\s*(?P<lead>'+ '|'.join(re.escape(name) for name in sorted(lead_names,key=len,reverse=True)) +r')?\s*')

    def named_metric(text,position):
        candidates=[]
        for label,field in aliases.items():
            start=text.rfind(label,0,position)
            between=connector.fullmatch(text[start+len(label):position]) if start>=0 else None
            if between:candidates.append((start,label,field,between['lead']))
        return max(candidates,key=lambda item:(len(item[1]),item[0])) if candidates else None

    def matched(token,rows,unknown):
        found=[]
        unit=re.search(r'(亿元|万元|元)$',token).group()
        text=token[:-len(unit)].strip().replace(',','').replace('−','-')
        try:number=Decimal(text);digits=max(0,-number.as_tuple().exponent)
        except (InvalidOperation,ValueError,TypeError,OverflowError):return []
        for row in rows:
            if row.get('value') is None or type(row.get('value')) is bool:continue
            if unknown:
                if row.get('unit')!='原始单位待核对' or analysis_field(row) not in FINANCIAL:continue
            elif row.get('unit') not in scales:continue
            try:
                raw=Decimal(str(row.get('raw_value',row['value'])))
                if not raw.is_finite():continue
                with localcontext() as context:
                    context.prec=max(50,len(raw.as_tuple().digits)+abs(raw.adjusted())+digits+12)
                    # Correspondence does not approve an unknown unit. A known
                    # amount is compared in its actual registered scale.
                    expected=raw/scales[unit] if unknown else raw*scales[row['unit']]/scales[unit]
                    if expected.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP)==number:
                        found.append(row)
            except (InvalidOperation,ValueError,TypeError,OverflowError):continue
        return found

    def comparison(rows,label,endpoint_refs=None,direction=None):
        selected={row['id'] for row in rows};eligible=[]
        for calc in calculations:
            refs=calc.get('data_refs',[])
            if calc.get('method')!='raw_change_v1' or len(refs)!=2:continue
            if (len(selected)>1 and set(refs)!=selected) or (len(selected)==1 and refs[-1] not in selected):continue
            if any(ref not in byid for ref in refs):continue
            if endpoint_refs is not None and any(ref not in endpoint for ref,endpoint in zip(refs,endpoint_refs)):continue
            first,last=[byid[ref] for ref in refs]
            if analysis_field(first)!=analysis_field(last):continue
            change=Decimal(str(calc.get('exact',calc['display'])))
            if direction==-1 and change>0:continue
            if direction==1 and change<0:continue
            eligible.append(calc)
        if len(eligible)!=1:return '',[]
        calc=eligible[0]
        return label+'同比'+str(calc['display'])+'%',[calc['id']]

    def revise_valuation(text, refs, path):
        if not isinstance(text, str):
            return text
        corrections = _valuation_unit_corrections(text, [byid[ref] for ref in refs if ref in byid])
        if protect_ranges and corrections:
            intervals = tuple(literal_quantity_ranges(text))
            corrections = [edit for edit in corrections if not any(
                edit['start'] < span.end and span.start < edit['end'] for span in intervals)]
        for edit in corrections:
            adjustments.append(dict(path=path, original=edit['original'], replacement=edit['replacement'],
                data_refs=edit['data_refs'], calculation_refs=[], reason='valuation_unit_unconfirmed',
                financial_approval=False))
        for edit in reversed(corrections):
            text = text[:edit['start']] + edit['replacement'] + text[edit['end']:]
        return text

    def revise(text,refs,path,heading=None):
        if not isinstance(text,str):return text,[]
        rows=[byid[ref] for ref in refs if ref in byid];used=[];replacements=[]
        intervals = tuple(literal_quantity_ranges(text)) if protect_ranges else ()
        # Change the named numerical phrase only. An adjacent mechanism,
        # known price, caveat or other financial metric must remain verbatim.
        previous_end = 0
        for token in quantities.finditer(text):
            phrase_start, previous_end = previous_end, token.end()
            if any(token.start() < span.end and span.start < token.end() for span in intervals):
                continue
            named=named_metric(text,token.start())
            if named is None or named[2] is None:continue
            start,label,field,lead=named
            if any(start<end for _,end,_,_,_ in replacements):continue
            candidates=[row for row in rows if analysis_field(row)==field]
            # Parse a named pair atomically. Preserve each known endpoint;
            # an unmatched or reversed endpoint cannot create a new claim.
            tokens=[token];end=token.end();pairing=None
            following=quantities.search(text,end)
            if following and not any(following.start() < span.end and span.start < following.end() for span in intervals):
                pairing=amount_transition.fullmatch(text[end:following.start()])
                if pairing:tokens.append(following);end=following.end()
            parts=[]
            for amount in tokens:
                scope = draft_quantity_scope(text, amount.start())
                qualifier = None
                if financial_scope and field in FINANCIAL:
                    qualifier = financial_scope.read(text, amount.start(), amount.end(), scope,
                                                     phrase_start=phrase_start, heading=heading)
                    scope = scope._replace(code=qualifier.code)
                scoped = [row for row in candidates if draft_quantity_source_matches(row, scope, financial=True)
                          and (qualifier is None or financial_scope.accepts(qualifier, row))]
                if qualifier is not None and not financial_scope.unambiguous(scoped):
                    scoped = []
                known=matched(amount.group(),scoped,False)
                parts.append((amount,[] if known else matched(amount.group(),scoped,True)))
            selected=list({row['id']:row for _,raw in parts for row in raw}.values())
            if not selected:continue
            endpoint_refs=[{row['id'] for row in raw} for _,raw in parts] if pairing else None
            # A range introduced by from/to is not a standalone amount when
            # its connecting wording is unknown. Keep its meaning intact.
            can_compare=not literal and all(raw for _,raw in parts) and (pairing is not None or lead not in ('由','从'))
            direction=transition_signs.get(pairing[1] if pairing else lead,0)
            replacement,calc_refs=comparison(selected,label,endpoint_refs,direction) if can_compare else ('',[])
            if not replacement:
                replacement=text[start:end]
                for amount,raw in reversed(parts):
                    originals={str(row.get('raw_value',row['value'])) for row in raw}
                    if not originals:continue
                    display=next(iter(originals))+'（单位未明）' if len(originals)==1 else '金额单位待核对'
                    begin,finish=amount.start()-start,amount.end()-start
                    replacement=replacement[:begin]+display+replacement[finish:]
            else:
                parenthetical=re.match(r'\s*[（(](?:同比)?([+\-−]?\d+(?:\.\d+)?)%[）)]',text[end:])
                calc=next(item for item in calculations if item['id']==calc_refs[0])
                if parenthetical and Decimal(parenthetical[1].replace('−','-'))==Decimal(str(calc['display'])):
                    end+=parenthetical.end()
            original=text[start:end]
            replacements.append((start,end,replacement,selected,calc_refs));used.extend(calc_refs)
            adjustments.append(dict(path=path,original=original,replacement=replacement,
                data_refs=[row['id'] for row in selected],calculation_refs=calc_refs,
                reason='amount_unit_unconfirmed',financial_approval=False))
        for start,end,replacement,_,_ in reversed(replacements):text=text[:start]+replacement+text[end:]
        return revise_valuation(text, refs, path),list(dict.fromkeys(used))

    allrefs=list(byid)
    if 'title' in result:
        result['title'] = revise_valuation(result['title'], allrefs, 'title')
    report_heading = financial_scope.heading(result.get('title')) if financial_scope else None
    result['conclusion'],_=revise(result.get('conclusion'),allrefs,'conclusion',report_heading)
    for field in ('risks','reader_notes'):
        if isinstance(result.get(field),list):
            result[field]=[revise(text,allrefs,f'{field}.{index}')[0] for index,text in enumerate(result[field])]
    logics=result.get('logics',result.get('strategies',[]))
    for index,logic in enumerate(logics if isinstance(logics,list) else []):
        if not isinstance(logic,dict):continue
        refs=draft_logic_data_refs(logic);added=[]
        heading = financial_scope.heading(logic.get('title')) if financial_scope else None
        if 'title' in logic:
            logic['title'] = revise_valuation(logic['title'], refs, f'logics.{index}.title')
        for field in ('analysis','logic','implication','risk','detail_analysis'):
            if field in logic:
                logic[field],used=revise(logic[field],refs,f'logics.{index}.{field}',heading);added.extend(used)
        if isinstance(logic.get('argument'),dict):
            for field,value in logic['argument'].items():
                logic['argument'][field],used=revise(value,refs,f'logics.{index}.argument.{field}',heading);added.extend(used)
        mechanism = logic.get('mechanism')
        if isinstance(mechanism, dict) and isinstance(mechanism.get('steps'), list):
            for step_index, step in enumerate(mechanism['steps']):
                if isinstance(step, dict) and step.get('kind') == 'observation' and isinstance(step.get('data_refs'), list):
                    step_refs = [ref for ref in step['data_refs'] if isinstance(ref, str)]
                    if 'label' in step:
                        step['label'] = revise_valuation(step['label'], step_refs, f'logics.{index}.mechanism.steps.{step_index}.label')
        if added:
            original=logic.get('calculation_refs',[])
            logic['calculation_refs']=list(dict.fromkeys((original if isinstance(original,list) else [])+added))
    if any(edit['reason'] == 'amount_unit_unconfirmed' for edit in adjustments):
        # A draft requiring numerical corrections uses a neutral task title.
        # The original model title remains in the submission and adjustment log.
        objects=[obj for obj in task.get('objects',[]) if isinstance(obj,str) and obj]
        subject='、'.join(objects[:2])+('等' if len(objects)>2 else '')
        title=(subject+'研究报告') if subject and len(subject)<=32 else '专题研究报告'
        if result.get('title')!=title:
            adjustments.append(dict(path='title',original=result.get('title'),replacement=title,
                reason='neutral_title_after_quantity_correction',financial_approval=False))
            result['title']=title
    if version >= 2:
        result,period_adjustments=_prepare_financial_period_copy(task,result,facts)
        adjustments.extend(period_adjustments)
        from .draft_numeric_review import review_draft_quantities
        adjustments.extend(review_draft_quantities(task, result, available, calculations,
            financial_scope=financial_scope, quantity_links=quantity_links, logic_exclusions=logic_exclusions))
        if logic_exclusions is not None:
            original_logics = proposal.get('logics', proposal.get('strategies', []))
            for index, logic in enumerate(logics if isinstance(logics, list) else []):
                if isinstance(logic, dict):
                    excluded = financial_scope.logic_exclusions(original_logics[index], logic, available)
                    if excluded:
                        logic_exclusions[index] = list(dict.fromkeys([*logic_exclusions.get(index, ()), *excluded]))
    return result,adjustments
