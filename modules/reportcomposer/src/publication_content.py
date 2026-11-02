"""ReportComposer owns the client paper and per-logic internal workpaper."""
import hashlib
from .contracts import canonical,ContractError,METHOD_REGISTRY

PROFILES={'standard':'标准报告','detailed':'详细报告'}

REPORT_CONDITION_BRIEF = ('数值阈值须注明来源、方法或用户设定；自行提出的条件只能作为待验证观察条件，不写成已经验证的转折或买卖规则。')

# One business-argument rule also applies when the initial Chat scope has
# not yet been classified. Choosing a presentation scope is not enough by itself.
RESEARCH_LOGIC_BRIEF = (
    '投资研究按经济含义组织逻辑，每条连接关键事实、经济机制、成立条件、投资含义和反证；'
    '例如区分定价、利润或购买力等不同对象与路径，按问题取舍，不机械凑齐渠道或固定条数。'
    '指标比较可作为论据，不用指标复述、缺项说明或风险声明单独凑一条策略；条件性假设可写，但与事实分开。'
    '各条先给具体判断，再选最有解释力的数字；完整序列交给图表和底稿，核心结论不逐项重复。'
)

# Shared by the App model and the Skill host; no extra editing-model call.
CLIENT_REPORT_BRIEF = (
    '面向客户写投研报告，参考紧凑的券商策略报告。结构为简短标题、核心结论、几条有区别的策略逻辑和必要风险。'
    '核心结论先回答问题。' + RESEARCH_LOGIC_BRIEF +
    '标题与走势核对完整观测区间，样本内极值须保留实际月份和范围。'
    '正文写研究判断，不出现数据或计算内部编号、来源ID、核验进度、模型、接口或ResDesk。'
    '图表由交付程序按引用单独配图，正文不叙述JSON、绘图步骤或复现方法。'
    '数据、计算、来源和实际使用的知识各填引用字段；知识引用每项包含id和整数version。'
    '每条逻辑优先用argument分别写claim、evidence、reasoning、condition，围绕一个判断展开，不把多个独立渠道塞进最后一条。'
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
    '面向客户写简洁报告，标题简短，核心结论直接回答原问题，保留实际数字与引用。'
    'report_scope只用research_scope.allowed_report_scopes中的值；它只组织章节，不改变问题、权限或核验状态。'
    '事实、推断和条件性假设分开；不在正文写内部编号、模型、工具、接口或任务状态。'
    '核对时保留字面数字及单位，单位未明确须说明；不按数量级推定或换算。形成投资判断时，单位未知的金额不以绝对数额立论。'
    'workpaper_notes放口径推敲与核验备注，实质影响结论的限制写进正文或风险；reader_notes只简述一次必要适用范围。'
    'risks只列与本次问题实际相关的不确定性，不制造无关经营风险或重复每条限制。'
    '数据、来源和计算引用填实际使用的引用字段，保留计算条件。共享内核按引用单独配图，不额外调用模型选图。'
    '机制解释优先用argument分别写claim、evidence、reasoning、condition，每条围绕一个解释及其竞争解释。'
    '仅核对或直接比较时用analysis即可；只有确有传导内容时才用mechanism.steps，不制造机制、权重、概率或阈值。'
    '知识引用每项包含id和整数version；图表由程序按引用生成，正文不叙述JSON或绘图步骤。'
    '趋势和标题核对完整观测区间；原问题要求投资含义时，可提出明确标注的条件性假设，'
    '不把指标复述、缺项说明或风险声明单独列为策略逻辑。'
) + REPORT_CONDITION_BRIEF


def client_report_brief(task=None):
    from .planning import draft_research_scope
    scope = draft_research_scope(task) if task is not None else None
    if scope is None or scope['mode'] == 'research':
        return CLIENT_REPORT_BRIEF
    brief = SCOPED_REPORT_STYLES[scope['mode']] + SCOPED_REPORT_COMMON
    if scope['mode'] == 'by_question':
        brief += '仅在按原问题选择report_scope为research时，采用以下投资论证要求：' + RESEARCH_LOGIC_BRIEF
    return brief


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
        logic['argument'] = dict(
            claim='围绕一个对象或经济渠道提出可检验判断；不把指标复述单列为策略。',
            evidence='只选支持本判断的实际观察与登记计算，不重述全部序列。',
            reasoning='解释观察如何影响该对象，区分事实、推断和竞争解释。',
            condition='说明这条传导成立的具体前提；缺少验证时使用条件性表述。')
        logic['mechanism'] = dict(steps=[
            dict(label='本次观察', kind='observation', data_refs=[]),
            dict(label='传导成立条件', kind='condition', data_refs=[]),
            dict(label='条件成立时的可能影响', kind='hypothesis', data_refs=[])])
    return dict(title='简短研究标题', conclusion='直接回答原问题，给出主要判断与关键分歧。',
                logics=[logic], risks=['与原问题有关且尚未说明的主要风险'],
                reader_notes=[], workpaper_notes=[])


def draft_logic_heading(organization, index, title):
    """One heading shared by the paper and newly produced App workpapers."""
    numeral = '一二三四五六七八九十'[index - 1] if 1 <= index <= 10 else str(index)
    if organization in ('fact', 'comparison'):
        return numeral + '、' + title
    prefix = '传导逻辑' if organization == 'mechanism' else '策略逻辑'
    return prefix + numeral + '：' + title


def draft_logic_data_refs(logic):
    """Explicit references on a path belong to its parent logic as well."""
    if not isinstance(logic,dict):return []
    refs=list(logic.get('data_refs',[])) if isinstance(logic.get('data_refs'),list) else []
    mechanism=logic.get('mechanism')
    steps=mechanism.get('steps',[]) if isinstance(mechanism,dict) else []
    for step in steps if isinstance(steps,list) else []:
        if isinstance(step,dict) and isinstance(step.get('data_refs'),list):refs.extend(step['data_refs'])
    return list(dict.fromkeys(ref for ref in refs if isinstance(ref,str) and ref))


def source_label(source):
    """Public source names; the frozen record keeps the provider identifier."""
    if not isinstance(source,str):return ''
    return {'ifind':'iFinD','official':'公开来源','tinyshare':'Tinyshare','wind':'Wind'}.get(source,source)


def observation_title(row):
    """Keep an object's literal identity beside its metric in workpapers."""
    label=row.get('label') or row.get('object') or row.get('field','')
    subject=row.get('code') or row.get('object') or row.get('object_label','')
    return str(subject)+'：'+str(label) if subject and label and subject!=label else str(label or subject)


def calculation_title(calculation):
    """Identify the object and literal window of the displayed result."""
    method=calculation.get('method','')
    title=calculation.get('label') or calculation.get('method_title') or METHOD_REGISTRY.get(method,{}).get('title',method)
    code=calculation.get('code');period=calculation.get('period') or calculation.get('parameters',{}).get('period')
    if code:title=str(code)+'：'+title
    return title+'，'+str(period) if period else title


def observation_value(row):
    """A readable missing cell, without changing the raw export dataset."""
    value=row.get('raw_value',row.get('value',row.get('text','')))
    return '未返回' if value is None else str(value)


def unit_basis_table(rows,records):
    """Compact provenance for the existing inspector and internal report only."""
    known={record['id']:record for record in records};result=[];seen=set()
    labels={'literal_source':'原件字面标注','source_metadata':'iFinD指标目录',
            'registered_field':'已登记字段口径','calibrated_definition':'限定范围的原件校准'}
    from .contracts import fingerprint
    for row in rows:
        reference=row.get('unit_reference')
        if not isinstance(reference,dict):continue
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
        elif kind=='calibrated_definition':label+='；'+reference.get('locator','')
        result.append([observation_title(row),row.get('unit',''),label])
    return [['指标','单位','单位依据']]+result if result else None


def project_content(content,profile='standard',bindings=None):
    if not isinstance(profile,str) or profile not in PROFILES:raise ContractError('请选择标准报告或详细报告')
    groups={};draft=content.get('draft');bindings=bindings or {}
    if profile=='detailed':
        facts={r['id']:r for r in (draft.get('facts',[]) if draft else content.get('evidence',[]))}
        calculations={r['id']:r for r in (draft.get('calculations',[]) if draft else content.get('calculations',[]))}
        source_records={r['id']:r for r in content.get('source_records',[]) if r.get('task_id')==content.get('task_id') and r.get('research_version')==content.get('research_version')}
        logics=draft.get('logics',[]) if draft else content.get('theses',[])
        for index,logic in enumerate(logics,1):
            key='logic-'+str(index) if draft else logic['id'];binding=bindings.get(key,{})
            refs=list(dict.fromkeys((draft_logic_data_refs(logic) if draft else logic.get('data_refs',logic.get('support',[])))+logic.get('counter',[])+binding.get('data_refs',[])))
            calc_refs=list(dict.fromkeys(logic.get('calculation_refs',[])+binding.get('calculation_refs',[])))
            selected=[calculations[ref] for ref in calc_refs if ref in calculations]
            for calc in selected:refs.extend(calc.get('data_refs',calc.get('input_refs',[])))
            allowed=set(facts)
            if draft:
                from .drafts import logic_basis
                basis=dict(logic,data_refs=refs,calculation_refs=calc_refs)
                allowed={row['id'] for row in logic_basis(content,basis)[0]}
            rows=[facts[ref] for ref in dict.fromkeys(refs) if ref in facts and ref in allowed];blocks=[]
            if rows:
                blocks.extend([dict(kind='heading',level=3,text='数据依据'),dict(kind='table',rows=[['对象与指标','观测期间','原值','单位']]+[
                    [observation_title(r),r.get('period',''),observation_value(r),r.get('unit','')] for r in rows])])
            unit_table=unit_basis_table(rows,source_records.values())
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
                blocks.append(dict(kind='heading',level=3,text='计算依据'))
                from .draft_analysis import formula_mathml,METHODS
                for calc in selected:
                    blocks.append(dict(kind='paragraph',text=calculation_title(calc)))
                    if calc['method'] in METHODS:
                        blocks.append(dict(kind='formula',mathml=formula_mathml(calc['method'])))
                        blocks.append(dict(kind='paragraph',text=calc.get('condition') or METHODS[calc['method']]['condition']))
                        if calc.get('conditional_equal_scale'):
                            blocks.append(dict(kind='paragraph',text='原始单位未明确，比较以各输入尺度一致为前提。'))
                    output=calc.get('output',{})
                    blocks.append(dict(kind='paragraph',text='结果：'+str(calc.get('display',output.get('value','')))+str(calc.get('unit',output.get('unit','')))))
            notes=logic.get('workpaper_notes',[])
            if notes:
                blocks.append(dict(kind='heading',level=3,text='研究备注'))
                blocks.extend(dict(kind='paragraph',text=note) for note in notes)
            risk=logic.get('risk',logic.get('limitation',''))
            if risk:blocks.extend([dict(kind='heading',level=3,text='反证与验证条件'),dict(kind='paragraph',text=risk)])
            names=[]
            for row in rows:
                source=source_records.get(row.get('artifact_id'),{});name=source.get('source',row.get('source',''))
                if name:names.append(source_label(name))
            if names:blocks.extend([dict(kind='heading',level=3,text='原始来源'),dict(kind='paragraph',text='、'.join(dict.fromkeys(names)))])
            if blocks:groups[key]=blocks
        if draft and draft.get('workpaper_notes'):
            groups['summary']=[dict(kind='heading',level=3,text='研究备注')]+[
                dict(kind='paragraph',text=note) for note in draft['workpaper_notes']]
        if not logics:
            rows=list(facts.values())
            if rows:groups['research-basis']=[dict(kind='heading',level=2,text='研究依据'),dict(kind='table',rows=[['对象','期间','依据']]+[[r.get('object',r.get('label','')),r.get('period',''),r.get('text',str(r.get('value','')))] for r in rows])]
    return dict(profile=profile,audience='client' if profile=='standard' else 'internal',
        content_hash=hashlib.sha256(canonical(content).encode()).hexdigest(),logic_blocks=groups,
        appendix=[b for rows in groups.values() for b in rows])



def reader_notes(content):
    """Keep material comparability conditions once, alongside business risks."""
    draft=content.get('draft') or {}
    notes=list(dict.fromkeys(draft.get('reader_notes',[])))
    from .draft_visuals import _validated
    if any(row.get('conditional_equal_scale') for row in _validated(content)):
        notes.append('财务比较以相关数据单位一致为前提；金额单位尚未确认。')
    return list(dict.fromkeys(notes))


def publication_dataset(content):
    """Public data embedded in the canonical HTML for later conversion."""
    draft=content.get('draft');records={r['id']:r for r in content.get('source_records',[])}
    facts=draft.get('facts',[]) if draft else content.get('evidence',[]);rows=[]
    for row in facts:
        source=records.get(row.get('artifact_id'),{}).get('source',row.get('source',''))
        rows.append([row.get('code',row.get('object','')),row.get('label',row.get('field',row.get('text',''))),
                     row.get('period',''),row.get('raw_value',row.get('value')),row.get('unit',''),source_label(source)])
    calculations=[]
    for calc in (draft.get('calculations',[]) if draft else content.get('calculations',[])):
        output=calc.get('output',{})
        calculations.append(dict(label=calc.get('label',calc.get('method','')),method=calc.get('method',''),
            value=calc.get('value',output.get('value')),unit=calc.get('unit',output.get('unit',''))))
    return dict(rows=rows,calculations=calculations)
