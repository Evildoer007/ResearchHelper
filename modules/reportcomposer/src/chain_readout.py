"""Write a value-chain report from aligned, replayable industrial statistics."""

from decimal import Decimal

from .contracts import fingerprint
from .planning import input_fingerprint
from .industry_chain import CELL_KIND, selected_cells, study_scope


def _shown(value, places=1):
    shown = format(Decimal(str(value)), f',.{places}f')
    return shown.rstrip('0').rstrip('.') if '.' in shown else shown


def _change(value, unit='%'):
    value = Decimal(str(value))
    if not value:
        return '持平'
    return ('增长' if value > 0 else '下降') + _shown(abs(value)) + unit


def _section(title, analysis, cells, calculations=(), *, chart=None, notes=()):
    rows = [cell for cell in cells if cell]
    result = dict(title=title, analysis=analysis, implication='', risk='', status='待验证',
        data_refs=list(dict.fromkeys(row['id'] for row in rows)),
        calculation_refs=[row['id'] for row in calculations],
        source_refs=list(dict.fromkeys(row['artifact_id'] for row in rows)), knowledge_refs=[],
        industry_chain_readout=True, workpaper_notes=list(notes))
    if chart:
        result['industry_chain_chart'] = dict(kind=chart, data_refs=result['data_refs'].copy())
    return result


def industry_chain_readout(task, facts, calculations):
    if not (scope := study_scope(task)) or not (cells := selected_cells(facts)):
        return None
    year = str(scope['year'])
    previous = str(scope['year'] - 1)

    def current(*fields):
        return [cells.get((year, field)) for field in fields]

    def number(row, unit=''):
        return _shown(row['raw_value']) + (unit or row['unit'])

    def for_year(field_period, method):
        year_cells = [cells.get((field_period, field)) for field in ('vehicle_exports', 'vehicle_sales')]
        if not all(year_cells):
            return None
        expected = [row['id'] for row in year_cells]
        rows = [row for row in calculations if row['method'] == method and row['data_refs'] == expected]
        return rows[0] if len(rows) == 1 else None

    sections, summary, gaps = [], [], []
    carbonate, carbonate_yoy, carbonate_price, hydroxide, hydroxide_yoy, hydroxide_price = material = current(
        'carbonate_output', 'carbonate_yoy', 'carbonate_price', 'hydroxide_output', 'hydroxide_yoy', 'hydroxide_price')
    if all(material):
        a, b = (Decimal(row['raw_value']) for row in (carbonate_yoy, hydroxide_yoy))
        relative = ('碳酸锂产量增长快于氢氧化锂' if a > b and a > 0 else '两类锂盐产量增速出现分化' if a != b
                    else '两类锂盐产量变动同幅')
        analysis = (f'上半年电池级碳酸锂与氢氧化锂分别产出{number(carbonate)}和{number(hydroxide)}，'
                    f'同比{_change(a)}和{_change(b)}。两者对应的期均价分别为{number(carbonate_price)}和'
                    f'{number(hydroxide_price)}，后者为微粉级。评估电芯成本时，需按正极配方区分用锂量。'
                    '短周期采购先反映报价变化，持有较多库存或采用长单的厂商，成本变动的幅度与时点还取决于库存计价与采购合同重定价频率。')
        sections.append(_section(relative + '，成本传导取决于采购机制', analysis, material, notes=(
            '同比比较仅用于相同品种。期均价覆盖半年，若要识别成本何时传导，应取得同规格月度报价、供应商合同调价月份和企业库存周转日。',
            '若以成本传导解释电芯毛利变化，还需同时核对电芯售价、产品结构和单位制造成本，区分材料效应与产能利用率变化。',)))
        summary.append('两类锂盐产量增速' + ('分化' if a != b else '相近'))
    else:
        gaps.append('同一报告期的电池级锂盐产量、同比或均价未取齐。')
        available = [row for row in material if row]
        if available:
            analysis = '上半年' + '，'.join(row['label'] + number(row) for row in available) + '。'
            sections.append(_section('锂盐产出与价格', analysis, available))

    total, growth, power, storage, consumer, exported, export_growth = batteries = current(
        'battery_output', 'battery_yoy', 'battery_power', 'battery_storage', 'battery_consumer',
        'battery_export', 'battery_export_yoy')
    if all(batteries):
        prefix = '超过' if total.get('qualifier') == 'lower_exclusive' else '达到'
        categories = sorted((power, storage, consumer), key=lambda row: row['value'], reverse=True)
        leading = categories[0]['label'].removesuffix('产量')
        analysis = (f'全国锂电池总产量{prefix}{number(total)}，同比{_change(growth["raw_value"])}。'
                    f'{leading}是公布分类中规模最大的用途，储能与消费电池构成另外两条需求渠道。'
                    f'同期锂电池出口额为{number(exported)}，同比{_change(export_growth["raw_value"])}。'
                    '储能构成整车之外的实物需求来源。若产量快于出货，应核对库存变化；若出口额与出口数量增速分化，应再拆单价和产品结构。')
        sections.append(_section(f'{leading}贡献主要产出，需求来源多元', analysis, batteries, chart='battery_mix', notes=(
            '全国总产量为公报公布的规模读数，分类按动力、储能和消费用途列示。带有超过限定的总量只作为下界，计算份额时不能以该下界作为分母。',
            '出口额同时包含数量、单价和产品结构的变化。应结合相同编码的数量与均价、公报产量及企业交付，识别渠道各自的贡献。',)))
        summary.append(f'{leading}是电芯产出的最大用途')
    else:
        gaps.append('锂电池总产量、用途分项或出口额未取齐。')
        available = [row for row in batteries if row]
        if available:
            analysis = '上半年' + '，'.join(row['label'] + ('超过' if row.get('qualifier') else '') + number(row)
                                        for row in available) + '。'
            sections.append(_section('电池产量及用途', analysis, available))

    sales, exports, penetration = current('vehicle_sales', 'vehicle_exports', 'vehicle_penetration')
    prior = [cells.get((previous, key)) for key in ('vehicle_sales', 'vehicle_exports', 'vehicle_penetration')]
    domestic = [for_year(period, 'raw_difference_v1') for period in (previous, year)]
    shares = [for_year(period, 'raw_percent_v1') for period in (previous, year)]
    if sales and exports and penetration and all(prior) and all(domestic) and all(shares):
        p_sales, p_exports, p_penetration = prior
        exports_change = Decimal(exports['raw_value']) - Decimal(p_exports['raw_value'])
        domestic_change = Decimal(domestic[1]['exact']) - Decimal(domestic[0]['exact'])
        sales_change = Decimal(sales['raw_value']) - Decimal(p_sales['raw_value'])
        if exports_change > 0 > domestic_change:
            market = '外销扩张、内销收缩'
        elif domestic_change > 0 > exports_change:
            market = '内销扩张、外销收缩'
        else:
            market = '境内与出口市场分别贡献销量变动'
        secondary = (f'全国新能源汽车销量由{number(p_sales)}变为{number(sales)}，同比{_change(sales_change, "万辆")}。'
                     f'其中出口{_change(exports_change, "万辆")}，境内销量估算{_change(domestic_change, "万辆")}。')
        analysis = (secondary + f'出口占比由{_shown(shares[0]["exact"])}%变为{_shown(shares[1]["exact"])}%，'
                    f'同期新能源车占新车总销量比重由{number(p_penetration)}变为{number(penetration)}。'
                    '对动力电池及零部件供应商，整车客户的交付地区和车型结构，比全国销量变化更接近订单敞口。')
        sections.append(_section(market, analysis, [*prior, sales, exports, penetration],
                                [*domestic, *shares], chart='vehicle_channels', notes=(
            '境内销量按中汽协全国销量扣减出口量估算，两期分别从同口径原件取值。该估算沿用整车厂销量统计口径。',
            '验证动力电池订单敞口时，需对照各客户的境内和海外车型、纯电与插混占比、单车带电量和供应份额。',)))
        summary.append(market)
    else:
        gaps.append('中汽协同口径的两期累计数据未取齐，境内与出口的同比贡献暂无法拆分。')
        available = [row for row in (sales, exports, penetration) if row]
        if available:
            sections.append(_section('新能源汽车终端结构',
                '上半年' + '，'.join(row['label'] + number(row) for row in available) + '。', available))
    if not sections:
        return None
    from .publication_content import requested_strategy_count

    count = requested_strategy_count(task['question'])
    if count and count < len(sections):
        included = sections[:count - 1]
        related = sections[count - 1:]
        combined = dict(related[0], title='供需结构与价格传导' if count == 1 else '电芯扩张与终端需求分化',
                        analysis='\n'.join(section['analysis'] for section in related))
        for field in ('data_refs', 'source_refs', 'calculation_refs', 'workpaper_notes'):
            combined[field] = list(dict.fromkeys(ref for section in related for ref in section[field]))
        included.append(combined)
        sections = included
    conclusion = '。'.join(summary) + '。' if summary else '已取得所列产业环节对应的半年度读数。'
    if not gaps:
        conclusion = (f'{year}年上半年，' + '，'.join(summary) + '。'
                      '评估各环节盈利时，应分别检验锂盐成本重定价、电芯出货结构和车企客户的地区敞口。')
    risks = [
        '产量、出口金额和整车销量采用各自统计口径，需结合库存及出货判断供需平衡。',
        '锂盐半年均价反映期间绝对水平。利润变化还受采购重定价、成本和电芯成交价影响。',
    ]
    return dict(title=f'{year}年上半年锂电产业链研究', conclusion=conclusion, sections=sections, risks=risks,
                gaps=gaps)


def verified_chain_answer(task, draft):
    """Require real, complete and unchanged source-backed sections for completion."""
    from .draft_analysis import current_analysis
    from .draft_sources import observations

    if (task.get('run_mode') not in ('direct', 'local_draft') or draft.get('origin') != 'data_outline'
            or not (scope := study_scope(task)) or task['input_hash'] != input_fingerprint(task)):
        return None
    question, = task['questions']
    if not question['required'] or question.get('evidence_needs') or question.get('method_needs'):
        return None
    facts = observations(task)
    used = [row for row in facts if row.get('locator', {}).get('kind') == CELL_KIND]
    if used != [row for row in draft['facts'] if row.get('locator', {}).get('kind') == CELL_KIND]:
        return None
    verified = current_analysis(task, facts)
    readout = industry_chain_readout(task, used, verified.get('results', ()))
    if not readout or readout['gaps'] or readout['conclusion'] != draft['conclusion']:
        return None
    fields = ('title', 'analysis', 'data_refs', 'source_refs', 'calculation_refs')
    expected = [{key: row[key] for key in fields} for row in readout['sections']]
    if [{key: row.get(key) for key in fields} for row in draft['logics']] != expected:
        return None
    return dict(version=1, task_id=task['id'], research_version=task['version'], input_hash=task['input_hash'],
                question_id=question['id'], year=scope['year'], facts_hash=fingerprint(used),
                answer_hash=fingerprint(dict(conclusion=readout['conclusion'], sections=expected)))
