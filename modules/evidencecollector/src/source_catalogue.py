"""Business-facing source map shared by App and Skill.

This is implemented scope, not a connectivity monitor or permission promise.
Task-specific request contracts remain the only executable parameter authority.
"""
from .announcement_sources import CHANNELS as ANNOUNCEMENT_CHANNELS
from .official_sources import POLICY_CHANNELS
from .miit_releases import CHANNELS as MIIT_CHANNELS, POLICY as MIIT_POLICY



OFFICIAL_CHANNEL_LABELS = {'nbs_release': '国家统计局', 'fed_fomc': '美联储',
    **{key: value['name'] for key, value in {**POLICY_CHANNELS, **MIIT_CHANNELS}.items()},
    **{key: value['name'] for key, value in ANNOUNCEMENT_CHANNELS.items()}}


def official_record_label(record, records=()):
    """Name a retained acquisition channel, without approving its evidence.

    Original documents inherit the channel of their bound directory entry.
    Missing or inconsistent metadata only loses the more specific label; it
    never prevents report generation or changes a verification result.
    """
    from .contracts import ContractError, fingerprint
    from .etf_sources import request_url
    from .etf_listing import MARKET_NAMES, OPERATION as ETF_LISTING
    from .etf_trading import DAILY as ETF_DAILY, NAV_HISTORY as ETF_NAV, endpoint as trading_endpoint
    from .event_market import (MAINLAND_OPERATION, OPERATION as MARKET_HISTORY, PUBLISHER,
                               close_history_url, mainland_endpoint)
    from .treasury_rates import OPERATION as H15_YIELDS, PUBLISHER as H15_PUBLISHER, source_url as h15_url
    from .lpr_rates import OPERATION as LPR_HISTORY, PUBLISHER as LPR_PUBLISHER, source_url as lpr_url
    from .nfra_bank_statistics import OPERATION as NFRA_STATISTICS, PUBLISHER as NFRA
    from .index_sources import CSI_PROFILES, validate_csi_url
    from .official_sources import article_url

    def retained(item):
        return (isinstance(item, dict) and item.get('source') == 'official'
            and isinstance(item.get('snapshot'), dict) and isinstance(item.get('parameters'), dict)
            and fingerprint(item['snapshot']) == item.get('snapshot_hash', item.get('hash'))
            and item.get('request_hash') == fingerprint(dict(source='official',
                operation=item.get('operation'), parameters=item['parameters'])))

    try:
        if not retained(record):
            return ''
        operation, snapshot, parameters = record.get('operation'), record['snapshot'], record['parameters']
        if operation == ETF_LISTING:
            market = parameters.get('market')
            return MARKET_NAMES.get(market, '') if snapshot.get('market') == market else ''
        if operation in (ETF_DAILY, ETF_NAV):
            code = parameters.get('ts_code')
            url, _, _ = trading_endpoint(operation, code)
            if (snapshot.get('url'), snapshot.get('code'), snapshot.get('operation')) != (url, code, operation):
                return ''
            if operation == ETF_NAV:
                return '深交所ETF单位净值'
            return ('上交所' if code.endswith('.SH') else '深交所')+'ETF日行情'
        if operation == MARKET_HISTORY:
            return PUBLISHER if snapshot.get('url') == close_history_url(parameters.get('codes')) else ''
        if operation == MAINLAND_OPERATION:
            url, _, exchange = mainland_endpoint(parameters.get('ts_code'))
            return exchange if snapshot.get('url') == url else ''
        if operation == H15_YIELDS:
            return H15_PUBLISHER if snapshot.get('url') == h15_url(parameters) else ''
        if operation == LPR_HISTORY:
            return LPR_PUBLISHER if snapshot.get('url') == lpr_url(parameters) else ''
        if operation == NFRA_STATISTICS:
            return NFRA
        if operation == 'etf_pcf':
            code = parameters.get('ts_code'); origin = snapshot.get('exchange_origin')
            rows = snapshot.get('data')
            if (not isinstance(origin, dict) or origin.get('url') != request_url(code)
                    or not isinstance(rows, list) or not rows
                    or any(not isinstance(row, dict) or row.get('ts_code') != code for row in rows)):
                return ''
            return '上交所ETF申赎清单'
        if operation in ('index_catalog', 'index_file'):
            code, role = snapshot.get('index_code'), snapshot.get('role')
            if code not in CSI_PROFILES:
                return ''
            if operation == 'index_catalog':
                if role != 'catalog':
                    return ''
                validate_csi_url(snapshot.get('url'), code, 'catalog')
                return '中证指数文件目录'
            parsed = snapshot.get('parsed')
            if (role not in ('members', 'weights') or not isinstance(parsed, dict)
                    or parsed.get('index_code') != code or parsed.get('role') != role):
                return ''
            if snapshot.get('url') is not None:
                validate_csi_url(snapshot['url'], code, role)
            return '中证指数成分权重' if role == 'weights' else '中证指数成分名单'
        if operation == 'release_catalog':
            return OFFICIAL_CHANNEL_LABELS.get(parameters.get('channel'), '')
        if operation != 'release_original':
            return ''
        parents = [item for item in records if isinstance(item, dict)
            and item.get('id') == parameters.get('catalog_id')
            and item.get('task_id') == record.get('task_id')
            and item.get('research_version') == record.get('research_version')]
        if len(parents) != 1 or not retained(parents[0]) or parents[0].get('operation') != 'release_catalog':
            return ''
        parent = parents[0]; channel = parent['parameters'].get('channel')
        origin = snapshot.get('official_origin')
        entries = parent['snapshot'].get('entries')
        if not isinstance(entries, list) or not isinstance(origin, dict):
            return ''
        selected = [row for row in entries if isinstance(row, dict) and row.get('id') == parameters.get('release_id')]
        if len(selected) != 1 or selected[0] != origin:
            return ''
        article_url(origin.get('url'), channel=channel)
        return OFFICIAL_CHANNEL_LABELS.get(channel, '')
    except (ContractError, KeyError, TypeError, ValueError):
        return ''


def disclosure_fields(record):
    """Read catalogue labels from an original whose binding was checked by its caller."""
    snapshot = record.get('snapshot')
    if not isinstance(snapshot, dict):
        return {}
    if record.get('kind') == 'filing' and record.get('source') == 'ifind':
        directory = snapshot.get('directory')
        if (not isinstance(directory, dict) or directory != record.get('directory')
                or not isinstance(directory.get('catalog_ref'), dict)):
            return {}
        fields = dict(title=directory.get('title'), issuer_code=directory.get('issuer'),
                      issuer_name=directory.get('security_name'), directory_date=directory.get('report_date'),
                      provider_time_hint=directory.get('published_at'), channel='iFinD')
    elif record.get('source') == 'official' and record.get('operation') == 'release_original':
        origin = snapshot.get('official_origin')
        if not isinstance(origin, dict):
            return {}
        issuer = origin.get('issuer')
        fields = dict(title=origin.get('title'), issuer_code=origin.get('code'),
                      issuer_name=issuer.get('name') if isinstance(issuer, dict) else None,
                      directory_date=origin.get('listed_date'), listed_at=origin.get('listed_at'),
                      path_date_hint=origin.get('path_date_hint'), channel=origin.get('channel'))
    else:
        return {}
    return {key: value for key, value in fields.items() if isinstance(value, str) and value.strip()}


def source_catalogue(task=None):
    from .evidence import active_source_capabilities
    capabilities = active_source_capabilities()
    operations = {name: {row['operation'] for row in value.get('registered_operations', [])}
                  for name, value in capabilities.items()}
    expanded = task is None or task.get('official_source_expansion_version') == 1
    official = task is None or task.get('official_channels_version') == 1
    def route(source, label, candidates, scope, **extra):
        return dict(source=source, label=label, operations=[op for op in candidates if op in operations.get(source, set())],
                    scope=scope, connection_status='按实际响应核验', **extra)
    families = [
        dict(id='market', name='行情与估值', scope='价格、成交、估值及交易日', routes=[
            route('ifind', 'iFinD', ['cmd_history_quotation', 'real_time_quotation', 'high_frequency', 'date_sequence', 'get_trade_dates'],
                '按问题选择日线量价、最新报价、指定时段的1分钟收盘、估值序列或交易日历', priority='主来源'),
            route('tinyshare', 'Tinyshare', ['daily', 'daily_basic', 'fund_daily'],
                '已登记的股票与ETF接口，须有独立权限', priority='按需备选')]),
        dict(id='financial', name='财务与经营', scope='收入、利润、现金流、资产负债及经营指标', routes=[
            route('ifind', 'iFinD', ['basic_data_service', 'get_indicators', 'get_indicator_params'],
                '优先固定财务API；复用字段、单位和报告期，缺项只影响对应判断', priority='主来源'),
            route('tinyshare', 'Tinyshare', ['income', 'balancesheet', 'cashflow', 'fina_indicator'],
                '独立报表接口；不混用报告期、币种或合并范围', priority='按需备选')]),
        dict(id='disclosure', name='公司公告', scope='定期报告、临时公告及经营披露', routes=[
            route('ifind', 'iFinD', ['report_query', 'filing_original'],
                '查询目录；确需核对正文时再取单份原件', priority='主来源')]),
        dict(id='policy', name='宏观与政策', scope='宏观数值、统计解读、货币及财政政策', routes=[
            route('ifind', 'iFinD', ['search_edb', 'edb_service'],
                '经济指标先查代码和口径，再取实际时间序列', priority='数值主来源')]),
        dict(id='fund_index', name='指数与ETF', scope='净值、份额、成分、权重及申赎清单', routes=[
            route('ifind', 'iFinD', ['date_sequence', 'data_pool', 'cmd_history_quotation'],
                '只使用已登记的字段与指数名单配置', priority='主来源'),
            route('official', '中证指数', ['index_catalog', 'index_file'],
                '按指数代码和实际文件日期核对成分与权重', priority='原始文件'),
            route('official', '上交所ETF清单', ['etf_pcf'],
                '按产品及净值所属日核对申赎清单', priority='原始文件')])]
    if official:
        from .etf_trading import DAILY, NAV_HISTORY, POLICY as ETF_TRADING_POLICY

        if task is None or task.get(ETF_TRADING_POLICY) == 1:
            families[0]['routes'].append(route('official', '沪深交易所ETF交易数据', [DAILY, NAV_HISTORY],
                '取未复权收盘价、成交额及已公布单位净值。净值与价格须按估值日配对。',
                priority='交易所数据', credential_required=False))
        if task is None or task.get('official_etf_listing_version') == 1:
            families[2]['routes'].append(route('official', '沪深交易所ETF名录', ['etf_listing'],
                '按问题中的简称与管理人核对代码。价格、费率和表现从相应数据源核对。',
                priority='官方标的目录', credential_required=False))
        from .event_market import MAINLAND_OPERATION, MAINLAND_POLICY, OPERATION as MARKET_HISTORY, PUBLISHER

        families[0]['routes'].append(route('official', PUBLISHER, [MARKET_HISTORY],
            'FOMC声明所列时间对应的香港交易窗口；恒指与恒生科技价格指数收盘对照',
            priority='官方价格指数', credential_required=False))
        if task is None or task.get(MAINLAND_POLICY) == 1:
            families[0]['routes'].append(route('official', '沪深交易所指数行情', [MAINLAND_OPERATION],
                '核对政策声明前后的境内行业及宽基价格指数收盘。',
                priority='官方价格指数', credential_required=False))
        families[3]['routes'].append(route('official', '国家统计局', ['release_catalog', 'release_original'],
            '统计发布目录与静态原文，保留所属期和正文定位', channel='nbs_release', priority='官方原文'))
        if task is None or task.get('official_policy_channels_version') == 1:
            families[3]['routes'].append(route('official', '美联储', ['release_catalog', 'release_original'],
                '固定FOMC日历中的政策声明', channel='fed_fomc', priority='官方原文'))
        from .treasury_rates import OPERATION as H15_YIELDS, POLICY as H15_POLICY, PUBLISHER as H15_PUBLISHER
        from .lpr_rates import OPERATION as LPR_HISTORY, POLICY as LPR_POLICY, PUBLISHER as LPR_PUBLISHER
        from .nfra_bank_statistics import OPERATION as NFRA_STATISTICS, PUBLISHER as NFRA

        if task is None or task.get(H15_POLICY) == 1:
            families[3]['routes'].append(route('official', H15_PUBLISHER, [H15_YIELDS],
                '按美东议息日核对名义固定期限国债收益率', priority='官方发布数据', credential_required=False))
        if task is None or task.get(LPR_POLICY) == 1:
            families[3]['routes'].append(route('official', LPR_PUBLISHER, [LPR_HISTORY],
                '1年期及5年期以上LPR实际发布日期和报价', priority='官方发布数据', credential_required=False))
            families[3]['routes'].append(route('official', NFRA, [NFRA_STATISTICS],
                '商业银行合计净息差，以当期季报披露表为准',
                priority='官方发布数据', credential_required=False))
        if expanded:
            for channel, profile in ANNOUNCEMENT_CHANNELS.items():
                families[2]['routes'].append(route('official', profile['name'], ['release_catalog', 'release_original'],
                    profile['market']+'；按代码、日期、标题查询，证券标识取自官方目录',
                    channel=channel, priority='独立官方渠道', credential_required=False))
            for channel, profile in POLICY_CHANNELS.items():
                families[3]['routes'].append(route('official', profile['name'], ['release_catalog', 'release_original'],
                    '当前发布栏目与实际链接原文；不宣称历史目录完整',
                    channel=channel, priority='官方原文', credential_required=False))
            if task is None or task.get(MIIT_POLICY) == 1:
                for channel, profile in MIIT_CHANNELS.items():
                    families[3]['routes'].append(route('official', profile['name'], ['release_catalog', 'release_original'],
                        '发布目录、日期及实际链接原文，统计口径以正文为准', channel=channel,
                        priority='官方原文', credential_required=False))
    # A route is visible only if its runtime operation is actually registered.
    for family in families:
        family['routes'] = [row for row in family['routes'] if row['operations']]
    return dict(version=1, default_source='ifind', families=families,
        local_material=dict(label='本地材料', formats=capabilities.get('material', {}).get('formats', [])),
        policy=dict(automatic_source_switch=False, ordinary_numbers_download_pdf=False,
            selected_original_may_be_pdf=True, catalogue_is_body=False, raw_data_immutable=True,
            reuse_scope='同任务、同研究版本、同参数和实际原件',
            missing_data='保留可用资料出稿，记录缺项，不因单个来源失败阻断全部研究',
            publication='列表日期、观察日期、取得时间分别留存；不推定首次公开时刻'))
