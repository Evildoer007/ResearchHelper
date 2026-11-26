"""Loan benchmark research backed by the current task's two monthly releases."""

from decimal import Decimal, ROUND_HALF_UP

from .contracts import fingerprint
from .lpr_rates import FIELD, NAMES, direct_scope
from .nfra_bank_statistics import FIELD as BANK_MARGIN


def _decimal(value):
    return format(Decimal(str(value)).normalize(), 'f')


def _date(day):
    year, month, date = map(int, day.split('-'))
    return f'{year}年{month}月{date}日'


def _change(value):
    delta = Decimal(value)
    if not delta:
        return '持平'
    return ('上调' if delta > 0 else '下调') + _decimal(abs(delta)) + '个基点'


def _quarter(period):
    return f'{period[:4]}年' + '一二三四'[int(period[5:7])//3-1] + '季度'


def _display_margin(row):
    return f'{Decimal(row["raw_value"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)}%'


def lpr_readout(task, facts, calculations):
    from .knowledge import ResearchKnowledge
    from .publication_content import requested_strategy_count

    scope = direct_scope(task)
    rows = [row for row in facts if row.get('locator', {}).get('kind') == FIELD]
    if not scope or not rows:
        return None
    current_date = max(row['period'] for row in rows)
    current = {row['locator']['field']: row for row in rows if row['period'] == current_date}
    prior_date = max((row['period'] for row in rows if row['period'] < current_date), default='')
    prior = {row['locator']['field']: row for row in rows if row['period'] == prior_date}
    moves = {tenor: calc for tenor in current for calc in calculations
             if calc.get('comparison_kind') == 'lpr_move' and tenor in prior
             and calc['data_refs'] == [prior[tenor]['id'], current[tenor]['id']]}
    full = set(moves) == set(NAMES)
    latest_levels = '、'.join(f'{name}{_decimal(current[tenor]["raw_value"])}%'
                             for tenor, name in NAMES.items() if tenor in current)
    held = full and all(Decimal(calc['exact']) == 0 for calc in moves.values())
    lead = '两期限报价维持不变，' if held else '报价为'
    conclusion = f'{_date(current_date)}公布的LPR{lead}{latest_levels}。'
    if full and not held:
        conclusion += '较上期，' + '，'.join(f'{name}{_change(moves[tenor]["exact"])}'
                                              for tenor, name in NAMES.items()) + '。'
    sections, risks = [], []

    def add_section(title, paragraphs, used_rows, *, results=(), knowledge=None, detail='', notes=()):
        references, _ = ResearchKnowledge().draft_references(task, [dict(id=knowledge, version=1)] if knowledge else [])
        sections.append(dict(title=title, analysis='\n\n'.join(paragraphs), detail_analysis=detail,
            implication='', risk='', status='待验证',
            data_refs=[row['id'] for row in used_rows], source_refs=list(dict.fromkeys(row['artifact_id'] for row in used_rows)),
            calculation_refs=[row['id'] for row in results], knowledge_refs=references,
            workpaper_notes=list(notes), lpr_readout=True))

    if moves:
        earlier = _date(prior_date)
        if held:
            title = '两期限报价持平，贷款定价基准稳定'
            movement = f'以{earlier}公布值为基准，1年期和5年期以上报价均未变动。'
        else:
            title = '贷款重新定价取决于合同挂钩期限'
            changes = '，'.join(f'{NAMES[tenor]}{_change(moves[tenor]["exact"])}' for tenor in NAMES if tenor in moves)
            movement = f'比较基准为{earlier}的同期限报价。' if full else f'较{earlier}公布值，{changes}。'
        analysis = movement + '本次报价首先影响到期重定价和新发放贷款，传导幅度取决于合同挂钩期限及加点。'
        if held:
            analysis += '报价持平期间，贷款定价仍会随前期基准进入存量合同、银行加点调整及新增贷款结构而变化。'
        add_section(title, [analysis], rows, results=moves.values(),
                    notes=['按同期限、相邻公布月份配对。以百分数记录的本期读数减去上期读数，再将差额换算为基点。'])
    if scope['mortgage']:
        title = '存量浮动房贷按合同约定重定价'
        analysis = '存量浮动房贷通常在合同约定的重定价日更新基准，继续执行合同加点，直至下次重定价或条款变更。'
        if held:
            analysis += '本期报价沿用上月水平。若上一轮重定价采用更高基准，在本期到达重定价日的合同仍可能下调。'
        else:
            analysis += '应把重定价日可采用的报价与合同当前使用的基准直接比较，差额才体现本次基准更新的方向。'
        add_section(title, [analysis + '新发放贷款的执行利率还取决于银行给出的加点，需与存量合同分开观察。'],
                    [current['5Y']] if '5Y' in current else [], knowledge='rh_k_mortgage_repricing',
                    detail='同一公布日后的两个借款人，可能处在不同的重定价周期。分析存量贷款时，先以重定价日匹配其适用报价，'
                           '再与当前合同所用的基准比较。分地区比较新发贷款时，则按同期限基准扣出加点，区分报价与银行定价差异。',
                    notes=['复核贷款利率类型、挂钩期限、上轮适用的基准、下次重定价日与合同加点。'])
        risks.append('借款人提前还款与集中重定价可能改变按揭资产规模及收益兑现节奏。')
    if scope['banks']:
        title = '银行息差取决于贷款与存款的重定价速度'
        margins = sorted((row for row in facts if row.get('field') == BANK_MARGIN), key=lambda row: row['period'])[-2:]
        quarter_context = ''
        if len(margins) == 2:
            old, new = margins
            different = _display_margin(old) != _display_margin(new)
            if different:
                quarter_context = (f'{_quarter(new["period"])}商业银行合计净息差为{_display_margin(new)}，'
                                   f'{_quarter(old["period"])}为{_display_margin(old)}。')
            else:
                quarter_context = (f'{_quarter(old["period"])}和{_quarter(new["period"])}商业银行合计净息差'
                                   f'均为{_display_margin(new)}。')
            if new['period'] < current_date:
                quarter_context += '这一读数反映本次报价之前的经营情况。'
            if not different:
                title = '行业净息差暂稳，后续看贷款和存款的重定价'
        elif margins:
            latest, = margins
            quarter_context = f'{_quarter(latest["period"])}商业银行合计净息差为{_display_margin(latest)}。'
        analysis = ('贷款端先看参考LPR的浮动贷款何时重定价，以及新增贷款的实际收益率。负债端看定期存款到期后按何种利率续作。'
                    '贷款收益下行快于资金成本时，息差承压。若存款成本先回落，息差可获得缓冲。'
                    '用平均生息资产收益率与付息负债成本率的同期变化，可以区分资产定价压力与负债降成本的贡献。')
        add_section(title, [quarter_context + analysis], [*current.values(), *margins], knowledge='rh_k_bank_rate_repricing',
                    detail='即使行业净息差持平，资产收益率和资金成本也可能同时下降。复核具体银行时，将同一期间的贷款收益率、存款成本率'
                           '与平均余额配对，分开识别利率、期限和结构贡献。若贷款重定价比较集中，再把未来季度到期的定期存款按期限分组，'
                           '判断负债成本改善能否在同一窗口兑现。净息差和净利息收入还应结合平均生息资产规模交叉验证。',
                    notes=['息差用金融监管总局发布的商业银行合计季度表。按原表百分比格式展示至两位小数，保留原始单元尺度。'] if margins else [])
        risks.append('存款定期化、信贷竞争加剧或低收益资产占比上升，均可能推迟净息差企稳。')
    if not sections:
        add_section('两期限报价与贷款定价', ['LPR是贷款定价参考。测算具体执行利率时，按合同约定的挂钩期限和加点核对。'], rows)
    if scope['mortgage'] or scope['banks']:
        subjects = '存量房贷看合同重定价' if scope['mortgage'] else ''
        if scope['banks']:
            subjects += ('，' if subjects else '') + '银行息差看资产与负债的调整速度'
        conclusion += subjects + '。'
    limit = requested_strategy_count(task['question'])
    if limit and limit < len(sections):
        rest = sections[limit-1:]
        combined = dict(rest[0], title='贷款定价与利率传导',
                        analysis='\n\n'.join(section['analysis'] for section in rest),
                        detail_analysis='\n\n'.join(section['detail_analysis'] for section in rest if section['detail_analysis']))
        for key in ('data_refs', 'source_refs', 'calculation_refs', 'workpaper_notes'):
            combined[key] = list(dict.fromkeys(item for section in rest for item in section[key]))
        combined['knowledge_refs'] = [item for section in rest for item in section['knowledge_refs']]
        sections[limit-1:] = [combined]
    title = 'LPR与房贷及银行息差' if scope['mortgage'] and scope['banks'] else (
        'LPR与存量房贷重定价' if scope['mortgage'] else 'LPR与银行息差' if scope['banks'] else '贷款市场报价利率观察')
    return dict(title=title, conclusion=conclusion, sections=sections, risks=risks,
                gaps=[] if full else ['相邻月份的两期限报价未全部取得。'], workpaper_only_gaps=True)


def verified_lpr_answer(task, draft):
    from .draft_analysis import current_analysis
    from .draft_sources import observations
    from .planning import input_fingerprint

    if (task.get('run_mode') not in ('direct', 'model', 'local_draft') or draft.get('origin') != 'data_outline'
            or not (scope := direct_scope(task)) or task['input_hash'] != input_fingerprint(task)):
        return None
    facts = observations(task)
    fields = (FIELD, BANK_MARGIN)
    used = [row for row in facts if row.get('field') in fields]
    if used != [row for row in draft['facts'] if row.get('field') in fields]:
        return None
    report = lpr_readout(task, used, current_analysis(task, facts)['results'])
    fields = ('title', 'analysis', 'detail_analysis', 'data_refs', 'source_refs', 'calculation_refs', 'knowledge_refs')
    if not report or report['gaps'] or report['conclusion'] != draft['conclusion'] or report['risks'] != draft['risks']:
        return None
    expected = [{key: row[key] for key in fields} for row in report['sections']]
    if [{key: row.get(key) for key in fields} for row in draft['logics']] != expected:
        return None
    return dict(version=1, task_id=task['id'], research_version=task['version'], input_hash=task['input_hash'],
                question_id=scope['question_id'], facts_hash=fingerprint(used),
                answer_hash=fingerprint(dict(conclusion=report['conclusion'], sections=expected)))
