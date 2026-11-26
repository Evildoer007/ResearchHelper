"""Typed research obligations, separate from literal source verification.

Constraints are proposed before acquisition and frozen with the plan. They do
not identify a metric by keywords or approve its financial meaning. In
particular, a genuine percentage cannot discharge an amount obligation.
"""
import copy
import math
import re

from .contracts import ContractError, FINANCIAL_AMOUNT_UNITS, fingerprint

POLICY='typed_evidence_needs_version'
FIELD='evidence_need_constraints'
DIMENSIONS=('amount','percentage','count','physical','price','index','other','not_applicable')
PERIODS=('any','point','interval','month')
_COUNT_BASE_UNITS = ('个', '家', '只', '人', '人次', '名', '户', '股', '份', '次', '台', '辆', '笔', '件', '套', '张', '宗')
SCALED_COUNT_UNITS = frozenset(
    scale + unit
    for scale in ('十', '百', '千', '万', '十万', '百万', '千万', '亿', '十亿', '百亿', '千亿', '万亿')
    for unit in _COUNT_BASE_UNITS
)
_COUNT_UNITS = SCALED_COUNT_UNITS.union(_COUNT_BASE_UNITS)
GUIDANCE=(
    '首次规划只将决定主问题能否回答、关键判断能否成立的依据列为必需；不把知识包的完整指标清单当作任务清单。'
    '辅助指标和拓展背景放入独立的required=false子问题，不能成为独立核心答案的前置依赖。'
    '解释风险不等于必须取齐所有风险指标；按实际依据讨论可识别风险和判断边界。'
    '关键反证、身份及适用口径不能伪装成辅助项，用户明确要求的字段、范围和计算仍须保留。'
    '取数前将evidence_needs逐条登记为带text、value_kind、unit_dimension、minimum_count、period_kind的记录。'
    '每条只描述一个可核对的指标或必要文字事实；多个业务金额分别列项，序列注明所需观测数。'
    '同一指标的水平和变化是不同观测，不合并成一条minimum_count=2的泛化需求；'
    '变化可由原文直接披露或由已登记方法计算，不能用任意两个百分比充抵。'
    'value_kind为number或text；金额选amount，比例选percentage，实物量选physical，价格选price，指数点位选index，数量选count。'
    'unit_dimension描述计量形式，不按指标名称中的指数二字决定。index仅指点单位的指数点位；'
    '扩散型景气指标如PMI通常以%披露，应按原计量形式登记percentage，不能把%改写成点或解释为增长率。'
    '文字选text、not_applicable；minimum_count保留所需不同观测数，period_kind保留原文事实适用的日期形状。'
    'period_kind是每条观测的期间，不是检索窗口：五个交易日的每日收盘价用point且minimum_count=5，不能用interval。'
    '两次声明各自的日期或段落可以登记text、minimum_count=2；不能用一份声明或重复摘录充抵两次。'
    '流量金额选interval，时点存量选point，月度指标选month；不从指标名称猜实际期间。'
    'minimum_count为本条所需不同观测数量，不能用重复摘录凑数。没有登记计算方法也必须保留数值需求。'
    '约束依据用户问题和必要研究目标，不能因原件中比例更易选择就把金额改成比例或文字。'
    '每条数值依据已经必须携带原始单位、适用期间和来源位置；用户要求保留这些属性时，将要求写入对应数值需求，'
    '不要再把相同属性拆成重复的必需文字需求、重复摘录或新增取数任务。'
    '用户独立要求比较口径、解释单位或核对披露日期时，才按实际问题另设相应研究需求。'
    '解释驱动、供需变化、风险和投资逻辑是研究推理义务，不要求外部原件必须逐字陈述研究结论。'
    '为这些推理登记必要基础事实与适用方法，不把需要研究者推断的结论改写成必须找到的原件摘录；'
    '仍须分析竞争解释和风险，不以未找到现成结论为由省略分析或用通用知识冒充主体事实。'
    '单位、数量和期间形状检查不证明指标含义正确；收入、净利润、存量、累计和比较期仍须读原文逐项核对。'
)


def enabled(task):
    value=task.get(POLICY)
    if value is None:return False
    if type(value) is not int or value!=1:raise ContractError('数据需求类型版本未登记')
    return True


def normalized(question):
    constraints=question.get(FIELD)
    if constraints is None:return None
    if not isinstance(constraints,dict) or set(constraints)!=set(question.get('evidence_needs',[])):
        raise ContractError('数据需求约束须与每条必要依据逐字对应，不能遗漏或新增')
    for spec in constraints.values():
        if (not isinstance(spec,dict) or set(spec)!={'value_kind','unit_dimension','minimum_count','period_kind'}
                or spec['value_kind'] not in ('text','number') or spec['unit_dimension'] not in DIMENSIONS
                or type(spec['minimum_count']) is not int or not 1<=spec['minimum_count']<=1000
                or spec['period_kind'] not in PERIODS):
            raise ContractError('数据需求的类型、单位维度、数量或期间形状无效')
        if spec['value_kind']=='text':
            if spec['unit_dimension']!='not_applicable':
                raise ContractError('文字需求不能伪装成数值单位约束')
        elif spec['unit_dimension']=='not_applicable':
            raise ContractError('数值需求须明确单位维度')
    return copy.deepcopy(constraints)


def validate_plan(task,questions):
    if not enabled(task):return
    for question in questions:
        if question.get('answer_scope')=='general_definition':continue
        if normalized(question) is None and question.get('evidence_needs'):
            raise ContractError('新研究计划须先登记每条数据需求的数值类型、单位和期间约束')
    for prior in task.get('questions',[]):
        old=normalized(prior)
        if old is None:continue
        current=next((q for q in questions if q['id']==prior['id']),None)
        new=normalized(current) if current else None
        if new is None or any(new.get(text)!=spec for text,spec in old.items()):
            raise ContractError('已登记的数据需求约束不能在取数或修订中降级')


def unit_matches(unit,dimension):
    if not isinstance(unit,str) or not unit:return False
    if dimension=='amount':return unit in FINANCIAL_AMOUNT_UNITS
    if dimension=='percentage':return unit in ('%','%/年','百分点','个百分点','基点','bp','bps')
    if dimension=='count':return unit in _COUNT_UNITS
    if dimension=='price':return unit in ('元','港元','美元','元/股','港元/股','美元/股','元/份','元/吨','美元/桶')
    if dimension=='index':return unit=='点'
    # Open-ended literal physical and other units remain subject to ordinary
    # source checks. Do not infer a unit or convert it to fit this obligation.
    if dimension=='physical':
        return unit not in (*FINANCIAL_AMOUNT_UNITS,'%','%/年','百分点','个百分点','基点','bp','bps','点','不适用')
    return dimension=='other' and unit!='不适用'


def reported_duration_label(period):
    """Recognize a literal reporting duration, not its calendar endpoints.

    Source/column/financial applicability validation still has to establish
    that this label belongs to the selected amount. No dates are supplied.
    """
    if not isinstance(period, str):
        return False
    label = re.sub(r'\s+', '', period)
    if re.fullmatch(r'\d{4}(?:年度|年(?:半年度|上半年|年度|第[一二三四1234]季度|[一二三四1234]季度))', label):
        return True
    months = re.fullmatch(r'\d{4}年(?P<start>0?[1-9]|1[0-2])月?(?:至|-)(?P<end>0?[1-9]|1[0-2])月', label)
    if months and int(months['start']) <= int(months['end']):
        return True
    duration=re.fullmatch(r'截至(\d{4})年(\d{1,2})月(\d{1,2})日止(\d{1,2})个月期间',label)
    if duration:
        from calendar import monthrange
        from datetime import date
        year,month,day,count=map(int,duration.groups())
        try:
            date(year,month,day)
            return 1<=count<=month and day==monthrange(year,month)[1]
        except (ValueError,OverflowError):
            return False
    return False


def period_matches(period,shape):
    if not isinstance(period,str):return False
    return (shape=='any' or shape=='point' and bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}',period))
        or shape=='month' and bool(re.fullmatch(r'\d{4}-\d{2}',period))
        or shape=='interval' and (bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}',period))
                                  or reported_duration_label(period)))


def satisfies(item,spec):
    if spec['value_kind']=='text':
        if not isinstance(item.get('text'),str) or not item['text'].strip():return False
    else:
        value=item.get('value')
        if type(value) not in (int,float) or not math.isfinite(value) or not unit_matches(item.get('unit'),spec['unit_dimension']):return False
    return period_matches(item.get('period',''),spec['period_kind'])


def api_cell_need_groups(task,cells):
    """Offer compatible links, not a requirement to satisfy every need.

    A source-bound price/date row cannot discharge an interval calendar or
    an amount requirement. This only checks shape; labels, identity, actual
    meaning and source applicability remain the ordinary adoption checks.
    Text needs may cite the row's date/code/context, not invent a text value.
    """
    if task.get('api_need_compatibility_version') not in (1,2):return None
    from .provenance import query_requirements
    questions=[]
    for question in task['questions']:
        if question.get('answer_scope') == 'general_definition':
            continue  # Knowledge definitions do not claim API observation ownership.
        constraints=normalized(question)
        if constraints is None:return None
        queries={need['text'] for need in query_requirements(question)}
        questions.append((question['id'],constraints,queries))
    grouped={}
    for cell in cells:
        mapping={}
        for qid,constraints,queries in questions:
            needs=[text for text,spec in constraints.items() if text not in queries and (
                satisfies(cell,spec) if spec['value_kind']=='number'
                else period_matches(cell.get('period',''),spec['period_kind']))]
            if needs:mapping[qid]=needs
        key=fingerprint(mapping)
        group=grouped.setdefault(key,dict(need_choices=mapping,candidate_ids=[]))
        group['candidate_ids'].append(cell['candidate_id'])
    return list(grouped.values())


def validate_links(task,item,mapping):
    from .period_targets import validate_links as validate_period_links
    validate_period_links(task, item, mapping)
    if not enabled(task):return
    for question in task['questions']:
        selected=mapping.get(question['id'],[])
        if not selected:continue
        constraints=normalized(question)
        if constraints is None:raise ContractError('依据关联的问题尚未登记数据需求约束')
        for text,spec in constraints.items():
            ident=fingerprint(text)
            if {ident,question['id']+':'+ident}.intersection(selected) and not satisfies(item,spec):
                raise ContractError('所选依据不满足关联需求的数值类型、单位维度或期间形状，不能作为已取得的数据')


def _observation_identity(item):
    """Count one addressed API cell once, independently of its prose or alias.

    Registered point prices also keep their identity across query windows.
    This is not financial equivalence across sources, periods or units. The
    ordinary source gates establish the cell's identity; records without an
    exact API address keep the established excerpt counting behavior.
    """
    artifact = item.get('artifact')
    locator = item.get('record_locator')
    if (type(item.get('value')) in (int, float) and isinstance(artifact, dict)
            and artifact.get('kind') == 'api' and isinstance(locator, dict)
            and type(locator.get('table')) is int and locator['table'] >= 0
            and type(locator.get('row')) is int and locator['row'] >= 0
            and isinstance(locator.get('field'), str) and locator['field']
            and isinstance(artifact.get('source'), str) and artifact['source']
            and artifact.get('source') == item.get('source')
            and isinstance(artifact.get('operation'), str) and artifact['operation']
            and isinstance(artifact.get('request_hash'), str)
            and re.fullmatch(r'[0-9a-f]{64}', artifact['request_hash'])
            and isinstance(item.get('snapshot_hash'), str)
            and re.fullmatch(r'[0-9a-f]{64}', item['snapshot_hash'])
            and item['snapshot_hash'] == artifact.get('snapshot_hash', artifact.get('hash'))):
        context = item.get('calculation_context')
        definition = item.get('field_definition')
        price_keys = ('security_id', 'currency', 'price_basis', 'price_kind', 'observation_date')
        if (artifact['source'] == 'ifind' and artifact['operation'] == 'cmd_history_quotation'
                and isinstance(definition, dict) and isinstance(context, dict)
                and definition.get('registry') == 'ifind.cmd_history_quotation'
                and definition.get('field') == locator['field'] == context.get('price_kind')
                and locator['field'] in ('open', 'high', 'low', 'close')
                and all(isinstance(context.get(key), str) and context[key] for key in price_keys)
                and context['security_id'] == item.get('object')
                and context['observation_date'] == item.get('period')):
            # Query windows and download IDs do not create a second price at
            # the same instant. Keep currency, adjustment and field meaning.
            return fingerprint(dict(kind='registered_price_observation', source=artifact['source'],
                definition=definition, unit=item.get('unit'),
                context={key: context[key] for key in price_keys}))
        return fingerprint(dict(kind='api_cell', source=artifact['source'], operation=artifact['operation'],
            request_hash=artifact['request_hash'], snapshot_hash=item['snapshot_hash'],
            address={key: locator[key] for key in ('table', 'row', 'field')}))
    keys = (('artifact_id', 'object', 'date_role', 'period') if item.get('date_role') else
            ('object', 'text', 'value', 'unit', 'period', 'record_locator', 'calculation_context'))
    return fingerprint({key: item.get(key) for key in keys})


def count_satisfied(question,text,items):
    constraints=normalized(question)
    if constraints is None:return True
    spec=constraints[text];ident=fingerprint(text)
    observations=set()
    for item in items:
        links=item.get('applicability_review',{}).get('evidence_need_ids',{}).get(question['id'],[])
        if question['id'] not in item.get('question_ids',[]) or not {ident,question['id']+':'+ident}.intersection(links):continue
        if not satisfies(item,spec):continue
        observations.add(_observation_identity(item))
    return len(observations)>=spec['minimum_count']


def pending(task):
    if not enabled(task):return []
    from .contracts import missing_evidence_requirements
    from .provenance import query_requirements
    result=[]
    for question in task['questions']:
        if not question.get('required',True) or question.get('answer_scope')=='general_definition':continue
        query_ids={need['id'] for need in query_requirements(question)}
        query_ids.update(fingerprint(text) for text,scope in question.get('evidence_need_scopes',{}).items()
            if scope=='market_calendar_coverage')
        for need in missing_evidence_requirements(question,task['evidence']):
            if need['id'] not in query_ids:result.append(dict(question_id=question['id'],**need))
    return result


def processing_state(task, selected=None):
    """Describe adopted need coverage, never the absence of source content.

    A failed/unsubmitted extraction is not a source fact. This small shared
    projection gives author and reviewer the same explicit processing states
    without selecting a source, weakening an obligation or rewriting prose.
    """
    version = task.get('evidence_processing_state_version')
    if version is None: return None
    if type(version) is not int or version != 1:
        raise ContractError('依据处理状态版本未登记')
    from .provenance import query_requirements
    from .period_targets import validate_links as validate_period_links
    rows = []
    for question in task['questions']:
        if selected is not None and question['id'] not in selected: continue
        queries = {need['text'] for need in query_requirements(question)}
        scopes = question.get('evidence_need_scopes', {})
        for text, constraint in (normalized(question) or {}).items():
            if text in queries or scopes.get(text) == 'market_calendar_coverage': continue
            need_id = fingerprint(text); eligible = []
            for item in task.get('evidence', []):
                links = item.get('applicability_review', {}).get('evidence_need_ids', {}).get(question['id'], [])
                if (item.get('task_id') != task['id'] or item.get('research_version') != task['version']
                        or item.get('source_check') != '通过' or item.get('applicability_check') != '通过'
                        or question['id'] not in item.get('question_ids', [])
                        or not {need_id, question['id'] + ':' + need_id}.intersection(links)
                        or not satisfies(item, constraint)): continue
                try: validate_period_links(task, item, {question['id']: links})
                except ContractError: continue
                eligible.append(item)
            sufficient = count_satisfied(question, text, eligible)
            rows.append(dict(question_id=question['id'], need=text, required=question.get('required', True),
                status='adopted_sufficient' if sufficient else 'adopted_partial' if eligible else 'not_adopted',
                source_absence_proved=False))
    from .source_inventory import inventory
    downloaded = inventory(task,selected)
    return dict(version=1, requirements=rows, financial_approval=False, automatic_completion=False,
        **({'downloaded_fields':downloaded} if downloaded is not None and downloaded['records'] else {}),
        rule='仅说明本任务已采用依据是否满足登记需求。未采用或部分采用不代表尚未下载、原文未披露或信息不存在；'
             '原文有但尚未定位、提取或核验的内容不能写成没有提供。已采用充分也不自动证明分析或投资判断。'
             '不能按处理状态补数、删需求、批准来源、改写报告或自动追加模型请求。')


def numeric_slots(task):
    """Stable proposal slots, not values, source matches or approvals."""
    if (type(task.get('typed_numeric_selection_version')) is not int
            or task['typed_numeric_selection_version'] not in (1,2) or not enabled(task)):return []
    from .contracts import evidence_requirements
    slots=[]
    for question in task['questions']:
        constraints=normalized(question)
        if constraints is None:continue
        for index,need in enumerate(evidence_requirements(question),1):
            spec=constraints[need['text']]
            if spec['value_kind']=='number':
                slots.append(dict(key='numeric_'+str(len(slots)+1),question_id=question['id'],
                    need_ref='n'+str(index),text=need['text'],constraints=spec))
    completion=task.get('reasoning_state',{}).get('required_numeric_completion',{})
    if completion.get('status')=='已派发':
        wanted={(n['question_id'],n['text']) for n in completion.get('missing_numeric_needs',[])}
        slots=[s for s in slots if (s['question_id'],s['text']) in wanted]
    return slots


def requirement_slots(task):
    """One explicit outer owner for every text or numeric requirement."""
    if task.get('typed_numeric_selection_version')!=3 or not enabled(task):return []
    from .contracts import evidence_requirements
    slots=[]
    for question in task['questions']:
        constraints=normalized(question)
        if constraints is None:continue
        for index,need in enumerate(evidence_requirements(question),1):
            slots.append(dict(key='requirement_'+str(len(slots)+1),question_id=question['id'],
                need_ref='n'+str(index),text=need['text'],constraints=constraints[need['text']]))
    completion=task.get('reasoning_state',{}).get('required_numeric_completion',{})
    if completion.get('status')=='已派发':
        wanted={(n['question_id'],n['text']) for n in completion.get('missing_numeric_needs',[])}
        slots=[s for s in slots if (s['question_id'],s['text']) in wanted]
    return slots


def unproposed_numeric(task,specs):
    """Provisional missing-input check; never source/financial acceptance."""
    if not enabled(task):return []
    proxies=[]
    for spec in specs:
        review=spec.get('review') or {}
        mapping=copy.deepcopy(review.get('evidence_need_ids',{}))
        for qid,texts in review.get('evidence_need_texts',{}).items():
            mapping.setdefault(qid,[]).extend(fingerprint(text) for text in texts)
        proxies.append(dict(spec,value=spec.get('value',spec.get('raw_value')),
                            applicability_review={'evidence_need_ids':mapping}))
    result=[]
    for question in task['questions']:
        if not question.get('required',True):continue
        for text,spec in (normalized(question) or {}).items():
            if spec['value_kind']=='number' and not count_satisfied(question,text,proxies):
                result.append(dict(question_id=question['id'],text=text,constraints=spec))
    return result


def _constrain_original_periods(properties,choice,shapes,*,task=None,exact_period=None):
    """Remove date shapes that the same typed need cannot later adopt.

    Only existing source-bound references are filtered. No observation date,
    reporting interval or mapping is supplied, repaired or inferred here.
    """
    if 'source_label_ref' in properties:
        from .output_schema import source_label_choices
        if task is None: raise ContractError('来源标注选择须绑定本任务')
        allowed = {row['reference'] for row in source_label_choices(task, choice)
                   if any(period_matches(row['period'], shape) for shape in shapes)}
        properties['source_label_ref']['enum'] = [reference for reference in properties['source_label_ref']['enum']
                                                if reference in allowed]
        return bool(properties['source_label_ref']['enum'])
    temporal=properties.get('temporal_reference')
    if not temporal:
        return True
    from .output_schema import original_observation_choices, document_date_choices
    dates=choice['description']['dates']
    observed={row['reference']:row['period'] for row in original_observation_choices(choice)}
    indices={index:date.get('observation_period',{}).get('value',date.get('date'))
             for index,date in enumerate(dates) if not date.get('date_role')}
    intervals={row['ref']:row['period'] for row in choice.get('period_anchors',[])}
    declared = {row['reference']: row['period'] for row in document_date_choices(choice)}
    catalogues=dict(observation_ref=observed,observation_anchor=indices,period_ref=intervals,document_date_ref=declared)
    kept=[]
    for branch in temporal['anyOf']:
        fields=branch['properties']
        if len(fields)!=1 or next(iter(fields)) not in catalogues:
            raise ContractError('原件期间选择格式未登记，未扩大时间用途')
        field=next(iter(fields))
        if field == 'document_date_ref' and (properties.get('kind', {}).get('enum') != ['text']
                or properties.get('date_role', {}).get('enum') != ['document_declared_publication']):
            raise ContractError('原页日期只提供独立文字分支，不扩大数值期间用途')
        allowed=[ref for ref in fields[field]['enum']
                 if ref in catalogues[field] and any(period_matches(catalogues[field][ref],shape) for shape in shapes)
                 and (exact_period is None or catalogues[field][ref] == exact_period)]
        if allowed:
            fields[field]['enum']=allowed
            kept.append(branch)
    temporal['anyOf']=kept
    return bool(kept)


def _source_literal_units(choice,candidates):
    """Keep exact unit lexemes present in the actual read original.

    This is only a lexical filter. The model must still select its unit
    address; scope, financial meaning and annual-rate context are not filled.
    Existing source-local unit identifiers and their ordering never change.
    """
    pattern=re.compile(r'(?<![A-Za-z百千万亿])(?:'+
        '|'.join(re.escape(unit) for unit in sorted(candidates,key=len,reverse=True))+
        r')(?![A-Za-z]|[ \t\u00a0\u3000]*[/／])')
    found=set()
    for line in choice['lines']:
        for match in pattern.finditer(line['text']):
            prefix=line['text'][:match.start()]
            if re.search(r'[/／][ \t\u00a0\u3000]*$',prefix):continue
            unit=match.group()
            if (unit in FINANCIAL_AMOUNT_UNITS and re.search(r'[\u4e00-\u9fff]$',prefix)
                    and not prefix.endswith('人民币')):continue
            found.add(unit)
    return found


def constrain_original_variants(task,variants,choices):
    """Offer number units for planned numeric needs, not convenient ratios.

    No number, meaning, unit address or date is filled in. Complete originals
    and their address catalogues remain visible; only incompatible submission
    alternatives are removed. The adoption gate repeats these constraints.
    """
    if not enabled(task):return variants
    from .output_schema import _named_quantity_index,_adjacent_quantity_units,_span_need_references
    from .contracts import evidence_requirements
    references=_span_need_references(task,{'question_requirements':{
        question['id']:evidence_requirements(question) for question in task['questions']}})
    by_source={choice['source_ref']:choice for choice in choices}
    result=[]
    for variant in variants:
        properties=variant['properties'];kind=properties['kind']['enum'][0]
        if kind not in ('text','number'):
            result.append(variant);continue
        copy_variant=copy.deepcopy(variant);properties=copy_variant['properties']
        links=properties.get('review',{}).get('properties',{}).get('need_links',{})
        if 'anyOf' not in links.get('items',{}):
            result.append(copy_variant);continue
        allowed=[];dimensions=set();period_shapes=set()
        for group in links['items']['anyOf']:
            qid=group['properties']['question_id']['enum'][0]
            question=next(q for q in task['questions'] if q['id']==qid)
            constraints=normalized(question)
            if constraints is None:raise ContractError('原件选择前须登记数据需求约束')
            ids=[]
            existing=set(group['properties']['need_ids']['items'].get('enum',references[qid]))
            for ref,need in references[qid].items():
                spec=constraints[need['text']]
                if ref in existing and spec['value_kind']==kind:
                    ids.append(ref);dimensions.add(spec['unit_dimension']);period_shapes.add(spec['period_kind'])
            if ids:
                group=copy.deepcopy(group)
                group['properties']['need_ids']['items']={'type':'string','enum':ids}
                allowed.append(group)
        if not allowed:continue
        links['items']['anyOf']=allowed
        if ('temporal_reference' in properties or 'source_label_ref' in properties) and not _constrain_original_periods(
                properties,by_source[properties['source_ref']['enum'][0]],period_shapes,task=task):
            continue
        if kind=='number' and 'number_reference' in properties:
            choice=by_source[properties['source_ref']['enum'][0]]
            reference=properties['number_reference'];branches=reference.get('anyOf',[reference]);kept=[]
            for branch in branches:
                fields=branch['properties']
                if 'quantity_ref' in fields:
                    quantities=(_named_quantity_index(choice) if task.get('literal_quantity_reference_version')==5
                                else _adjacent_quantity_units(choice))
                    refs=[ref for ref,value in quantities.items()
                        if any(unit_matches(value['unit'],dimension) for dimension in dimensions)]
                    if not refs:continue
                    fields['quantity_ref']={'type':'string','enum':refs}
                if 'unit_ref' in fields:
                    refs=[unit['ref'] for unit in choice.get('unit_references',[])
                        if any(unit_matches(unit['unit'],dimension) for dimension in dimensions)]
                    if not refs:continue
                    fields['unit_ref']={'type':'string','enum':refs}
                if 'unit' in fields and dimensions.issubset({'amount','percentage','index'}):
                    units=(*FINANCIAL_AMOUNT_UNITS,'%','%/年','百分点','个百分点','基点','bp','bps','点')
                    present=_source_literal_units(choice,units)
                    allowed=[unit for unit in units if unit in present
                        and any(unit_matches(unit,dimension) for dimension in dimensions)]
                    if not allowed:continue
                    fields['unit']={'type':'string','enum':allowed}
                kept.append(branch)
            if not kept:continue
            properties['number_reference']={'anyOf':kept} if len(kept)>1 else kept[0]
        result.append(copy_variant)
    return result
