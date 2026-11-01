"""First research delivery from acquired data, independent of review completion.

Raw observations stay distinct from approved evidence. Review may refine a
later version; missing fields do not prevent a useful first paper.
"""
import copy
import html
import json
import math
from collections import defaultdict
import re

from .contracts import ContractError, fingerprint
from .layout import resource_directory
from .publication_content import source_label,calculation_title,draft_logic_heading,draft_logic_data_refs


def source_binding(task):
    return fingerprint(dict(task_id=task['id'], version=task['version'], input_hash=task['input_hash'],
        records=[dict(id=r.get('id'), snapshot=fingerprint(r.get('snapshot')))
                 for r in task.get('source_records', []) + task.get('materials', [])]))


# Public compatibility alias; source decoding belongs to EvidenceCollector.
from .draft_sources import observations


# Question classification is shared with acquisition and the Skill host.
from .planning import draft_direct_scope as _direct_scope, draft_research_scope


def _report_organization(task, value):
    scope = draft_research_scope(task)
    if scope is None:
        return None
    selected = value.get('report_scope')
    if isinstance(selected, str) and selected in scope['allowed_report_scopes']:
        return selected
    return scope['mode'] if scope['mode'] in ('fact', 'comparison', 'mechanism') else None


def _direct_outline(kind, refs):
    return [dict(title='同口径对照' if kind == 'comparison' else '原始观测',
        analysis='按对象、日期和单位逐项对照已取得的数据。' if kind == 'comparison' else '列示已取得的原始数值及其日期、单位和来源。',
        implication='', risk='期间、统计范围或计量口径不同会影响可比性。', status='待验证',
        data_refs=refs, source_refs=[], calculation_refs=[], knowledge_refs=[])]


def _mechanism_outline(task):
    """A missing explanation stays conditional; never invent a company cause."""
    questions = [question['question'] for question in task.get('questions', [])
                 if question.get('required', True) and question.get('kind') == 'mechanism']
    return [dict(title=question,
        analysis='相关变化可能受到多种因素影响，需要区分已观察到的变化、传导条件与竞争解释。',
        implication='作用方向和规模取决于具体渠道、适用时点及对照条件。',
        risk='样本、事件窗口或对照条件不足时，相关关系不能认定为因果。',
        status='待验证', data_refs=[], source_refs=[], calculation_refs=[], knowledge_refs=[])
        for question in questions or [task['question']]]


def _research_outline(task):
    # Conditional research paths, never invented company-specific findings.
    logics = [dict(title='经营变化与持续性',
        analysis='围绕研究对象的收入、成本与盈利变化，检验需求、价格和成本能否解释经营表现。'
                 '先使用已取得资料，缺失维度保留为后续验证条件。',
        implication='若经营变化具有持续性，再评估盈利预期是否需要调整。',
        risk='单期变化可能来自基数、业务结构或一次性项目。', status='待验证'),
        dict(title='现金兑现与反证',
        analysis='对照利润、现金流及相关经营资料，区分账面增长与现金兑现，检查是否存在相反解释。',
        implication='现金表现与经营趋势一致时，才进一步讨论判断的可靠性。',
        risk='缺少资产负债项目或附注时，无法确定变化原因。', status='待验证')]
    # The declared object family wins over incidental words in a question.
    # A company event is not automatically a monetary-policy transmission.
    from .contracts import ENTRIES
    entry=task.get('entry')
    topic=entry if entry in ENTRIES else task.get('question','')
    if '事件' in topic:
        logics=[dict(title='事件变化与实际暴露',
            analysis='先区分已确认的事件事实与待验证解释，核对发生时间、影响对象及实际业务暴露。不能仅凭前后发生认定因果。',
            implication='只有暴露与传导条件成立时，才讨论对相关对象的影响。',
            risk='事件可能尚未落地，实际暴露也可能与市场叙事不同。',status='待验证'),
            dict(title='预期差与相反解释',
            analysis='比较事件新增信息、此前预期和其他同期变化，区分经营影响与价格反应。',
            implication='影响方向与大小仍取决于预期、持续时间和对象差异。',
            risk='缺少事件前预期或适用对照时，不认定超预期、已充分定价或价格变化由该事件造成。',status='待验证')]
    elif any(word in topic for word in ('宏观', '降息', '加息', '通胀', '货币政策', 'CPI')):
        logics = [dict(title='利率与风险溢价传导',
            analysis='宏观或事件变化可能通过利率、风险溢价和资金条件影响资产。方向取决于预期差与政策反应，不能只看事件名称。',
            implication='分别检验贴现率变化与风险偏好变化。', risk='增长预期下修或风险溢价上升可能抵消利率变化。', status='待验证'),
            dict(title='增长与盈利兑现', analysis='同时检查需求、成本和盈利预期是否变化，比较主要解释与相反证据。',
            implication='将宏观传导与研究对象的实际经营暴露连接后再判断。', risk='缺少对象数据时只保留机制假设，不认定实际影响。', status='待验证')]
        if not any(word in task.get('question','') for word in ('利率','降息','加息','货币政策')):
            logics[0].update(title='宏观读数与驱动分解',
                analysis='区分已取得的总体、分项读数和实际贡献，按问题检验需求、价格、供给或基数等竞争解释。没有分解时不认定主导原因。',
                implication='先确认读数变化及其范围，再讨论对增长、通胀或政策的条件性含义。',
                risk='缺少权重、环比或历史对照时，不能仅凭同比读数确定贡献和驱动。')
    elif '指数' in topic:
        logics=[dict(title='成分结构与市场暴露',
            analysis='从指数编制范围、实际成分和权重识别市场暴露，区分指数读数与成分公司的经营变化。',
            implication='仅在适用时点和真实权重下讨论成分对表现的贡献。',
            risk='当前成分不能代替历史成分，少量样本也不能代表整个指数。',status='待验证'),
            dict(title='表现比较与判断条件',
            analysis='按已取得的同口径序列比较区间表现、估值读数及适用对照，不把指数点位当作基金净值。',
            implication='收益、风险和估值判断分别依赖对应数据，缺失部分保留为条件。',
            risk='短窗口或单次估值读数不足以证明趋势、便宜或昂贵。',status='待验证')]
    elif any(word in topic for word in ('ETF', '基金')):
        logics = [dict(title='底层暴露与收益来源', analysis='从跟踪范围、成分结构和权重判断收益暴露，区分市场变化与产品特有影响。',
            implication='先核对研究对象实际跟踪的资产，再解释表现。', risk='成分或权重变化可能改变暴露，当前结构不能代替历史结构。', status='待验证'),
            dict(title='跟踪与交易约束', analysis='对照净值、市场价格和费用等已取得资料，区分底层收益、跟踪偏差和交易影响。',
            implication='在适用日期和份额口径下比较。', risk='数据不足时不把单期价差或费用差直接变成买卖建议。', status='待验证')]
    elif any(word in topic for word in ('行业', '产业链', '产业')):
        logics = [dict(title='供需与利润分配', analysis='沿需求、供给与成本变化分析产业链影响，区分上下游受益与受损环节。',
            implication='用具体环节的数据验证传导方向。', risk='供需预期和实际兑现可能不同，不能用少量公司代替整个行业。', status='待验证'),
            dict(title='量价兑现与竞争反证', analysis='对照数量、价格和利润变化，检验需求改善、成本变化和竞争加剧等解释。',
            implication='关注改善能否转化为可持续的经营结果。', risk='缺少样本或量价拆分时，行业判断保持条件性。', status='待验证')]
    elif entry=='混合':
        logics=[dict(title='对象关系与影响路径',
            analysis='围绕原问题区分各类对象、直接暴露及可能的传导关系，使用已取得资料检验连接是否成立。',
            implication='分别判断受影响环节与适用条件，不把跨对象关联自动当成因果。',
            risk='缺少某个环节的数据时，影响路径保持为待验证解释。',status='待验证'),
            dict(title='可比结果与相反解释',
            analysis='在同一观察窗口和适用口径下比较对象变化，区分共同因素、对象差异与竞争解释。',
            implication='只将能够定位的观察用于判断，其余关系保留条件。',
            risk='不同市场、期间或样本口径可能造成表面上的差异。',status='待验证')]
    return logics


def normalize_report(value, task, facts):
    """Accept useful prose fields independently; formatting errors are advisory."""
    if isinstance(value, str):
        text = value.strip()
        try:
            value = json.loads(text.removeprefix('```json').removeprefix('```').removesuffix('```').strip())
        except (ValueError, TypeError):
            value = dict(conclusion=text)
    if not isinstance(value, dict):
        value = {}
    text = lambda item: item.strip() if isinstance(item, str) else ''
    notes = lambda values: list(dict.fromkeys(text(item) for item in values if text(item))) if isinstance(values,list) else []
    logics = []
    direct_scope = _direct_scope(task)
    from .knowledge import ResearchKnowledge
    knowledge = ResearchKnowledge()
    source_ids = {row['id'] for row in task.get('source_records', []) + task.get('materials', [])
                  if row.get('task_id') == task.get('id') and row.get('research_version') == task.get('version')}
    fact_ids = {row['id'] for row in facts if row.get('artifact_id') in source_ids}
    from .draft_analysis import current_analysis,interpretation_context
    calculation_ids={row['id'] for row in current_analysis(task,facts).get('results',[])} if task.get('id') and task.get('version') else set()
    rows = value.get('logics', value.get('strategies', []))
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        analysis = text(item.get('analysis', item.get('logic')))
        if analysis:
            logic = dict(title=text(item.get('title')) or '研究逻辑', analysis=analysis,
                implication=text(item.get('implication')), risk=text(item.get('risk')),
                status='待验证', workpaper_notes=notes(item.get('workpaper_notes',[])))
            for name, allowed in (('data_refs', fact_ids), ('source_refs', source_ids),('calculation_refs',calculation_ids)):
                refs = item.get(name, [])
                if not isinstance(refs, list):
                    refs = []
                logic[name] = list(dict.fromkeys(ref for ref in refs if isinstance(ref, str) and ref in allowed))
                if any(not isinstance(ref, str) or ref not in allowed for ref in refs):
                    logic.setdefault('reference_notes', []).append('部分引用未对应本任务资料，正文保留，待底稿补充。')
            logic['knowledge_refs'], knowledge_notes = knowledge.draft_references(task, item.get('knowledge_refs', []))
            if knowledge_notes:
                logic.setdefault('reference_notes', []).extend(knowledge_notes)
            mechanism=item.get('mechanism')
            if isinstance(mechanism,dict) and isinstance(mechanism.get('steps'),list):
                steps=[]
                for step in mechanism['steps']:
                    if not isinstance(step,dict) or not text(step.get('label')):continue
                    supplied=step.get('data_refs',[]) if isinstance(step.get('data_refs',[]),list) else []
                    refs=[ref for ref in supplied if isinstance(ref,str) and ref in fact_ids]
                    if any(not isinstance(ref,str) or ref not in fact_ids for ref in supplied):
                        logic.setdefault('reference_notes',[]).append('部分引用未对应本任务资料，正文保留，待底稿补充。')
                    if re.search(r'\d',step['label']) and not refs:
                        logic.setdefault('reference_notes',[]).append('示意标签中的数字未关联资料，未生成该路径图。');steps=[];break
                    steps.append(dict(label=text(step['label']),data_refs=list(dict.fromkeys(refs)),
                                      kind=step.get('kind') if step.get('kind') in ('observation','hypothesis','condition') else 'hypothesis'))
                if len(steps)>=2:logic['mechanism']=dict(steps=steps)
            logic['data_refs']=draft_logic_data_refs(logic)
            if logic.get('reference_notes'):logic['reference_notes']=list(dict.fromkeys(logic['reference_notes']))
            logics.append(logic)
    organization = _report_organization(task, value)
    if not logics:
        organization = organization or direct_scope
        refs = [row['id'] for row in facts if row['id'] in fact_ids]
        if organization in ('fact', 'comparison'):
            logics = _direct_outline(organization, refs)
        elif organization == 'mechanism':
            logics = _mechanism_outline(task)
        else:
            logics = _research_outline(task)
    if task.get('draft_question_scope_version') == 2:
        for index, logic in enumerate(logics, 1):
            logic['display_heading'] = draft_logic_heading(organization, index, logic['title'])
    risks = value.get('risks', [])
    risks = [text(item) for item in risks if text(item)] if isinstance(risks, list) else []
    result = dict(title=text(value.get('title')) or (task['objects'][0] + '研究' if task.get('objects') else '研究报告'),
        conclusion=text(value.get('conclusion')) or (
            '本稿根据已取得资料整理研究线索，重点检验下列逻辑及其反证。'
            if facts else '当前资料尚不足以形成确定性结论，先列出可检验的研究逻辑及所需依据。'),
        logics=logics, risks=risks, knowledge_context=knowledge.draft_context(task),
        interpretation_context=interpretation_context(task,facts),
        workpaper_notes=notes(value.get('workpaper_notes',[])),
        reader_notes=notes(value.get('reader_notes',[])))
    from .draft_visuals import VISUAL_LAYOUT_VERSION
    result['visual_layout_version']=VISUAL_LAYOUT_VERSION
    from .draft_sources import request_field_context,observation_context
    observed_context=observation_context(task,facts)
    if observed_context:
        result['observation_context']=dict(version=1,task_id=task['id'],research_version=task['version'],
            input_hash=task.get('input_hash'),facts_hash=fingerprint(facts),**observed_context)
    request_context = request_field_context(task)
    if request_context:
        result['request_context'] = dict(version=1, task_id=task['id'], research_version=task['version'],
            input_hash=task.get('input_hash'), source_binding=source_binding(dict(task, input_hash=task.get('input_hash'))),
            request_hash=fingerprint([dict(id=row['id'], parameters=row.get('parameters'))
                for row in task.get('source_records', []) if row['id'] in {item['source_ref'] for item in request_context['notices']}]),
            **request_context)
    if organization:
        result['organization'] = organization
        if organization in ('fact', 'comparison') and not text(value.get('conclusion')):
            result['conclusion'] = ('以下列示本次已取得的数据及适用口径。' if facts else
                                    '现有资料不足以完成本次核对，未取得的项目保留为待核对事项。')
    if organization == 'mechanism' and not text(value.get('conclusion')):
        result['conclusion'] = ('已取得观测可用于讨论传导渠道及其条件，相关关系不直接认定为因果。' if facts else
                                '本次保留待检验的传导解释，现有资料不足以认定原因、净方向或作用规模。')
    return result


def compose(task):
    draft = task.get('draft_report')
    if not isinstance(draft, dict) or draft.get('source_binding') != source_binding(task):
        raise ContractError('报告对应的任务或原始资料已变化，请保存当前版本后重新生成')
    content = dict(schema='researchhelper.report-content', schema_version=1,
        report_kind='research_draft', task_id=task['id'], research_version=task['version'],
        cutoff=task['cutoff'], input_hash=task['input_hash'], title=draft['title'], question=task['question'],
        entry=task['entry'], intent=task['intent'], objects=task['objects'], synthetic=task['synthetic'],
        scope='研究草稿，原始取数与初步判断尚未完成独立复核。',
        draft=copy.deepcopy(draft), questions=copy.deepcopy(task['questions']),
        evidence=copy.deepcopy(task['evidence']), calculations=copy.deepcopy(task['calculations']),
        theses=[], tracking=[], limitations=copy.deepcopy(task.get('gaps', [])),
        source_records=copy.deepcopy(task.get('source_records', [])),
        materials=copy.deepcopy(task.get('materials', [])),
        knowledge=copy.deepcopy(task.get('knowledge', {})), template_version='rh-draft-8' if task.get('draft_question_scope_version') == 2 else 'rh-draft-7' if task.get('draft_question_scope_version') == 1 else 'rh-draft-6' if task.get('draft_direct_scope_version') == 1 else 'rh-draft-5',
        changes=task.get('changes'), update_from=task.get('update_from'),
        optional_findings=[], reasoning_state=copy.deepcopy(task.get('reasoning_state', {})))
    if 'draft_quote_scope_version' in task:
        content['draft_quote_scope_version'] = task['draft_quote_scope_version']
    if 'draft_source_reader_version' in task:
        content['draft_source_reader_version'] = task['draft_source_reader_version']
    if 'draft_direct_scope_version' in task:
        content['draft_direct_scope_version'] = task['draft_direct_scope_version']
    if 'draft_question_scope_version' in task:
        content['draft_question_scope_version'] = task['draft_question_scope_version']
    content['content_hash'] = fingerprint(content)
    return content


def draft_figures(facts):
    """Choose actual series or cross-sections; keep gaps and independent bases."""
    groups = defaultdict(list)
    multiple_objects = len({row['code'] for row in facts if row.get('axis_role', 'time') == 'time'}) > 1
    for row in facts:
        role = row.get('axis_role', 'time')
        groups[(row.get('series_basis', ''), row['field'], row['unit'], role,
                row['period'] if role == 'cross_section' else row['code'])].append(row)
    specs = []
    for (_, field, unit, role, _), rows in groups.items():
        if role == 'cross_section':
            points = [row for row in rows if type(row['value']) in (int, float) and math.isfinite(row['value'])]
            if len(points) < 2:
                continue
            points = sorted(points, key=lambda row: row['value'], reverse=True)[:15]
            specs.append(dict(name=rows[0]['label'] + ('，' + rows[0]['period'] if rows[0]['period'] else '')
                + ('，前15项' if len(rows) > 15 else ''), field=field,
                unit=unit, dates=[row.get('object_label') or row['code'] for row in points],
                values=[row['value'] for row in points], horizontal=True, type='bar',
                original_rows=len(rows)))
            continue
        by_date = defaultdict(list)
        for row in rows:
            by_date[row['period']].append(row)
        # Identical overlapping observations can share a plotted point. A
        # conflicting revision remains in an independent source series.
        conflict = any(len({row['value'] for row in batch}) > 1 for batch in by_date.values())
        batches = defaultdict(list)
        if conflict:
            for row in rows:
                batches[row['artifact_id']].append(row)
        else:
            batches['same_basis'] = [batch[0] for batch in by_date.values()]
        for values in batches.values():
            if len(values) < 2 or len({row['period'] for row in values}) != len(values):
                continue
            if sum(type(row['value']) in (int, float) and math.isfinite(row['value']) for row in values) < 2:
                continue
            values = sorted(values, key=lambda row: row['period'])
            temporal = all(re.fullmatch(r'\d{4}(?:-?\d{2}){1,2}', row['period']) for row in values)
            title = ((values[0].get('object_label') or values[0]['code']) if multiple_objects and field!='value' else '') + values[0]['label']
            specs.append(dict(name=title, field=field, unit=unit,
                dates=[row['period'] for row in values], values=[row['value'] for row in values],
                horizontal=False, type='line' if len(values) >= 3 and temporal else 'bar', original_rows=len(rows),
                data_refs=[row['id'] for row in values]))
    for index, spec in enumerate(specs):
        spec['target'] = 'draft-chart-' + str(index + 1)
        if spec['unit'] == '原始单位待核对':
            spec['unit'] = '接口原值'
    return specs


def _figure_keywords(spec):
    aliases = {
        'ths_revenue_stock': ['收入', '营收'], 'ths_operating_cost_stock': ['成本'],
        'ths_np_stock': ['净利润', '净利率'], 'ths_np_atoopc_stock': ['归母', '归属母公司'],
        'ths_ncf_from_oa_stock': ['现金流', '现金兑现'],
        'close': ['股价', '收盘', '价格走势', '指数走势'], 'unit_nav': ['净值'], 'weight': ['权重', '成分'],
    }
    return spec.get('keywords') or aliases.get(spec['field'], [spec['name']])


def logic_basis(content, item):
    """Resolve optional draft references only inside this frozen task/version."""
    ident = content.get('task_id', content.get('id'))
    version = content.get('research_version', content.get('version'))
    records = {row['id']: row for row in content.get('source_records', []) + content.get('materials', [])
               if row.get('task_id') == ident and row.get('research_version') == version}
    facts = (content.get('draft') or content.get('draft_report') or {}).get('facts', [])
    calculations=(content.get('draft') or content.get('draft_report') or {}).get('calculations',[])
    refs=set(draft_logic_data_refs(item))|{ref for value in calculations if value['id'] in item.get('calculation_refs',[]) for ref in value['data_refs']}
    selected = [row for row in facts if row.get('id') in refs
                and row.get('artifact_id') in records]
    sources = set(item.get('source_refs', [])) | {row['artifact_id'] for row in selected}
    for row in selected:
        sources.update(citation.get('artifact_id') for citation in row.get('unit_reference',{}).get('citations',[])
            if citation.get('artifact_id') in records and citation.get('snapshot_hash')==fingerprint(records[citation['artifact_id']].get('snapshot')))
    return selected, [row for key, row in records.items() if key in sources]


def logic_knowledge(content, item):
    """Resolve explicit same-version knowledge for reading, never as company facts."""
    from .knowledge import ResearchKnowledge
    context = (content.get('draft') or {}).get('knowledge_context', {})
    package = content.get('knowledge') or {}
    if not isinstance(context, dict) or not isinstance(package, dict):
        return []
    expected = dict(task_id=content.get('task_id', content.get('id')),
        research_version=content.get('research_version', content.get('version')),
        input_hash=content.get('input_hash'), knowledge_hash=fingerprint(package))
    if context.get('binding') != expected:
        return []
    references, _ = ResearchKnowledge().draft_references(content, item.get('knowledge_refs', []))
    selected = {ref['id'] for ref in references}
    return [entry for entry in package.get('entries', []) if entry['id'] in selected]


def render(content, resources, visual_profile=None, report_profile='standard'):
    """Use the existing client-paper style; workpaper diagnostics stay outside."""
    from .reports import check_content, research_date_label
    from .visual import inspection_link, inspection_surface, INSPECTION_SCRIPT
    check_content(content)
    draft = content['draft']
    escape = lambda value: html.escape(str(value), quote=True)
    paragraph = lambda value: ''.join('<p>' + escape(line) + '</p>' for line in str(value).splitlines() if line.strip())
    facts = draft['facts']
    from .draft_visuals import logic_figure_plan
    from .visual_selection import ledger_html
    chart_specs=logic_figure_plan(content,report_profile=report_profile)
    drawn = set()
    inspection_records = []
    displayed_data = {ref for spec in chart_specs for ref in spec.get('data_refs', [])}

    def observation_table(rows, target):
        # A measured value remains useful even when it cannot form a chart.
        # Use the original cell, date and unit; never interpolate another point.
        from .draft_visuals import display_observation
        records = {record['id']: record for record in content['source_records'] + content.get('materials', [])}
        cells = []
        for row in rows:
            source = records.get(row['artifact_id'], {}).get('source', '')
            source = source_label(source) or '留存资料'
            value = inspection_link(target, display_observation(row), '查看' + row['label'] + '的原始数据')
            cells.append('<tr data-observation-id="' + escape(row['id']) + '"><th scope="row">'
                + escape(row['code']) + '</th><td>' + escape(row['label']) + '</td><td>'
                + escape(row['period'] or '未标注') + '</td><td>' + value + '</td><td>'
                + escape(row['unit']) + '</td><td>' + escape(source) + '</td></tr>')
        return ('<div class="report-data-scroll argument-observations" tabindex="0" role="region" aria-label="观测数据">'
            '<table><thead><tr><th>标的</th><th>指标</th><th>日期</th><th>原值</th><th>单位</th><th>来源</th></tr></thead>'
            '<tbody>' + ''.join(cells) + '</tbody></table></div>')

    def basis_record(target, item, rows, records):
        cells = ''.join('<tr data-observation-id="'+escape(row['id'])+'"><td>' + '</td><td>'.join(escape(value) for value in
            (row['code'], row['label'], row['period'],
             '未返回' if row['value'] is None else row.get('raw_value', row['value']), row['unit']))
            + '</td></tr>' for row in rows)
        body = ('<div class="table-wrap" tabindex="0"><table><thead><tr><th>标的</th><th>指标</th>'
            '<th>期间</th><th>原值</th><th>单位</th></tr></thead><tbody>' + cells + '</tbody></table></div>') if rows else ''
        from .publication_content import unit_basis_table
        unit_rows=unit_basis_table(rows,records)
        if unit_rows:
            body+='<h3>单位依据</h3><div class="table-wrap" tabindex="0"><table><thead><tr>'+''.join('<th>'+escape(value)+'</th>' for value in unit_rows[0])+'</tr></thead><tbody>'
            body+=''.join('<tr>'+''.join('<td>'+escape(value)+'</td>' for value in values)+'</tr>' for values in unit_rows[1:])+'</tbody></table></div>'
        for entry in logic_knowledge(content, item):
            body += '<h3>'+escape(entry['title'])+'</h3>'+paragraph(entry['content'])
            body += paragraph('适用条件：'+entry.get('conditions',''))
            body += paragraph('反例与相反渠道：'+entry.get('counterexample',''))
            body += paragraph('来源：'+entry.get('source',''))
        from .draft_analysis import formula_mathml,METHODS
        for calculation in draft.get('calculations',[]):
            if calculation['id'] not in item.get('calculation_refs',[]):continue
            body+='<h3>'+escape(calculation_title(calculation))+'</h3>'+formula_mathml(calculation['method'])
            body+='<p>结果：'+escape(calculation['display'])+escape(calculation['unit'])+'</p>'
            body+='<p>'+escape('，'.join(key+'='+str(value) for key,value in calculation['inputs'].items()))+'</p>'
            body+=paragraph(calculation.get('condition') or METHODS[calculation['method']]['condition'])
            if calculation.get('conditional_equal_scale'):body+='<p>原始单位未明确，比较以各输入尺度一致为前提。</p>'
        for record in records:
            name = source_label(record.get('source')) or '留存资料'
            snapshot = record.get('snapshot', record)
            material = snapshot.get('material', snapshot) if isinstance(snapshot, dict) else {}
            body += '<h3>' + escape(material.get('name') or name) + '</h3>'
            for part in material.get('parts', []):
                if not isinstance(part, dict) or not isinstance(part.get('text'), str):
                    continue
                locator = part.get('locator', '')
                if isinstance(locator, dict):
                    locator = '第' + str(locator['page']) + '页' if locator.get('page') else ''
                body += ('<p class="figure-source">' + escape(locator) + '</p>' if locator else '') + paragraph(part['text'])
        notes=item.get('workpaper_notes',[])+item.get('reference_notes',[])
        if item.get('risk'):notes=[item['risk'],*notes]
        if notes:body+='<h3>判断边界</h3>'+''.join(paragraph(note) for note in notes)
        return '<section id="' + target + '" data-inspection-title="' + escape(item['title']) + '"><h3>研究依据</h3>' + body + '</section>'

    def figure(spec):
        drawn.add(spec['target'])
        height = spec.get('height',400 if spec['horizontal'] else 250)
        caption=escape(spec['name'])
        target=None
        if spec.get('calculation_refs') or spec.get('data_refs') or spec.get('coverage_kind')=='mechanism':
            rows,sources=logic_basis(content,spec)
            target='draft-figure-basis-'+spec['target']
            inspection_records.append(basis_record(target,dict(spec,title=spec['name']),rows,sources))
            caption=inspection_link(target,spec['name'],'查看'+spec['name']+'的图表依据')
        from .draft_analysis import _day
        period_label=lambda value:(_day(value).isoformat() if _day(value) else str(value))
        context=escape(spec.get('context') or (period_label(spec['period'])+'较'+period_label(spec['comparison']) if spec.get('comparison') else ''))
        if context:context='<p class="figure-context">'+context+'</p>'
        if spec.get('auto_visual',{}).get('scheme')=='dumbbell':
            height=min(height,max(130,70+45*len(spec['auto_visual']['data']['pairs'])))
        plot='<div class="chart" id="'+spec['target']+'" style="height:'+str(height)+'px" tabindex="0" role="img" aria-label="'+escape(spec['name'])+'图，按回车查看数据依据"></div>'
        display=ledger_html(spec,plot,target) if report_profile=='detailed' else plot
        return '<figure><figcaption>'+caption+'</figcaption>'+context+display+'</figure>'


    sections = ['<section id="summary"><h2>核心结论</h2>' + paragraph(draft['conclusion']) + '</section>']
    for index, item in enumerate(draft['logics'], 1):
        related=[spec for spec in chart_specs if spec.get('paper_logic')=='logic-'+str(index)]
        prose = paragraph(item['analysis']) + (paragraph(item['implication']) if item['implication'] else '')
        if report_profile=='detailed' and item['risk']:
            prose += '<p class="argument-boundary">验证条件：' + escape(item['risk']) + '</p>'
        if related:
            prose = '<div class="argument-reading"><div class="argument-introduction">' + prose + '</div><div class="argument-visual"><div class="draft-figures">' + ''.join(figure(spec) for spec in related) + '</div></div></div>'
        label = draft_logic_heading(draft.get('organization'), index, item['title'])
        basis_rows, basis_sources = logic_basis(content, item)
        plotted = {ref for spec in related for ref in spec.get('data_refs', [])}
        sparse = [row for row in basis_rows if row['id'] not in plotted]
        if any(type(row['value']) in (int, float) and math.isfinite(row['value']) for row in sparse):
            prose += observation_table(sparse, 'draft-basis-' + str(index))
            displayed_data.update(row['id'] for row in sparse)
        if basis_rows or basis_sources or item.get('workpaper_notes') or item.get('risk'):
            target = 'draft-basis-' + str(index)
            inspection_records.append(basis_record(target, item, basis_rows, basis_sources))
            heading = inspection_link(target, label, '查看' + item['title'] + '的研究依据')
        else:
            heading = escape(label)
        sections.append('<section id="logic-' + str(index) + '"><div class="argument-heading"><h2>'
            + heading + '</h2></div>' + prose + '</section>')
    remaining = [spec for spec in chart_specs if spec['target'] not in drawn]
    if facts:
        from .draft_visuals import display_observation as display_value
        from .draft_visuals import comparison_table,temporal_table
        matrix=comparison_table(content) if any(spec.get('kind') for spec in chart_specs) else None
        aligned=temporal_table(content)
        if aligned:
            rows=''.join('<tr><td>'+escape(item['period'])+'</td>'+''.join(
                '<td data-raw-value="'+escape(value['id'])+'">'+escape(display_value(value))+'</td>'
                for value in item['observations'])+'</tr>' for item in aligned['rows'])
            table=('<table class="financial-comparison-table temporal-comparison-table" data-raw-observations="'+str(aligned['observations'])+'">'
                '<caption>数据对照<span class="table-unit">单位：'+escape(aligned['unit'])+'</span></caption>'
                '<thead><tr><th>日期</th>'+''.join('<th>'+escape(label)+'</th>' for label in aligned['columns'])
                +'</tr></thead><tbody>'+rows+'</tbody></table>')
        elif matrix:
            rows=''.join('<tr><td>'+escape(row['label'])+'</td>'+''.join(
                '<td data-raw-value="'+escape(value['id'])+'">'+escape(display_value(value))+'</td>'
                for value in row['observations'])+'</tr>' for row in matrix['rows'])
            unit='金额单位待核对，保留接口原值' if matrix['unit']=='原始单位待核对' else '单位：'+matrix['unit']
            table=('<table class="financial-comparison-table" data-raw-observations="'+str(matrix['observations'])+'">'
                '<caption>'+escape(matrix['code'])+'<span class="table-unit">'+escape(unit)+'</span></caption>'
                '<thead><tr><th>指标</th>'+''.join('<th>'+escape(period)+'</th>' for period in matrix['periods'])
                +'</tr></thead><tbody>'+rows+'</tbody></table>')
        else:
            named = any(row.get('object_label') not in (None, '', row['code']) for row in facts)
            rows = ''.join('<tr><td>' + '</td><td>'.join(escape(value) for value in (
                [row['code']] + ([row.get('object_label', '')] if named else [])
                + [row['label'], row['period'] or '未标注', display_value(row), row['unit']]))
                + '</td></tr>' for row in facts)
            table='<table><thead><tr><th>标的</th>'+('<th>名称</th>' if named else '')+'<th>指标</th><th>期间</th><th>原值</th><th>单位</th></tr></thead><tbody>'+rows+'</tbody></table>'
        complete_data='<div class="report-data-scroll" tabindex="0" role="region" aria-label="完整原始数据，可横向滚动">'+table+'</div>'
        if report_profile=='detailed':
            sections.append('<section id="data"><h2>关键数据</h2><div class="draft-figures">'+''.join(figure(spec) for spec in remaining)+'</div>'+complete_data+'</section>')
        else:
            inspection_records.append('<section id="complete-report-data" data-inspection-title="数据与来源"><h3>完整数据</h3>'+complete_data+'</section>')
            if remaining:
                sections.append('<section id="data"><h2>补充图表</h2><div class="draft-figures">'+''.join(figure(spec) for spec in remaining)+'</div></section>')
            elif not displayed_data:
                # Reports without usable plot references still expose acquired
                # observations in a table, instead of hiding every value.
                sections.append('<section id="data"><h2>关键数据</h2>'+complete_data+'</section>')
    from .publication_content import reader_notes
    basis_notes=reader_notes(content)
    public_risks=draft['risks'] or [item['risk'] for item in draft['logics'] if item.get('risk')]
    if public_risks or basis_notes:
        sections.append('<section id="risk"><h2>风险提示</h2>'
            + ('<ul>'+''.join('<li>' + escape(item) + '</li>' for item in dict.fromkeys(public_risks))+'</ul>' if public_risks else '')
            + ''.join('<p class="reader-basis">'+escape(note)+'</p>' for note in basis_notes)+'</section>')
    sources = list(dict.fromkeys(source_label(row.get('source'))
        for row in content['source_records'] if source_label(row.get('source'))))
    css = (resource_directory(resources, 'frontend') / 'report.css').read_text()
    if facts and aligned:
        css+='\n.temporal-comparison-table th:not(:first-child){white-space:normal;line-height:1.5}.temporal-comparison-table th:first-child,.temporal-comparison-table td:first-child{white-space:nowrap}'
    vendor = (resource_directory(resources, 'vendor') / 'echarts.min.js').read_text() if chart_specs else ''
    data = json.dumps(chart_specs, ensure_ascii=False).replace('<', '\\u003c')
    script=(resource_directory(resources,'frontend')/'draft-figures.js').read_text().replace('__RH_DRAFT_FIGURES__',data)
    from .visual_catalog import catalogue
    visual_profile=visual_profile or {'theme':catalogue()['default_theme']}
    if visual_profile.get('theme') not in {row['id'] for row in catalogue()['themes']}:raise ValueError('配色未登记')
    profile=json.dumps(visual_profile,ensure_ascii=False).replace('<','\\u003c')
    visual_catalog=(resource_directory(resources,'frontend')/'visual-catalog-data.js').read_text()
    visual_library=(resource_directory(resources,'frontend')/'visual-library.js').read_text()
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' \
        + '<title>' + escape(draft['title']) + '</title><style>' + css \
        + '.draft-figures{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.draft-figures figure{min-width:0;margin:8px 0}.argument-visual .draft-figures{grid-template-columns:minmax(0,1fr)}table{width:100%;border-collapse:collapse}th,td{text-align:right;padding:8px;border-bottom:1px solid #DFE5EC}th:first-child,td:first-child{text-align:left}@media(max-width:640px){.draft-figures{grid-template-columns:1fr}}' \
        + '</style><main><header class="report-header"><h1>' + escape(draft['title']) \
        + '</h1><p class="report-meta"><time>' + escape(research_date_label(content['cutoff'], compact=True)) \
        + '</time></p></header>' + ''.join(sections) + '<footer class="report-source-line"><p>来源：' \
        + escape('、'.join(sources) or '研究资料') + '。' + (' '+inspection_link('complete-report-data','数据与来源') if facts and report_profile=='standard' else '') + '</p></footer></main>' \
        + (inspection_surface(inspection_records) + '<script>' + INSPECTION_SCRIPT + '</script>' if inspection_records else '') \
        + (('<script>' + vendor + '</script>') if chart_specs else '') + '<script>'+visual_catalog+'</script><script>'+visual_library+'</script><script id="rh-visual-profile" type="application/json">'+profile+'</script><script>' + script + '</script></html>'
