"""Financial regulator's quarterly commercial-bank statistics and original cells."""

import base64
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
import json
import re
from urllib.request import Request

from .contracts import ContractError, timepoint
from .https_transport import open_verified_https
from .lpr_rates import SHANGHAI, study_scope

OPERATION = 'nfra_bank_statistics'
POLICY = 'official_bank_statistics_version'
FIELD = 'bank_sector_net_interest_margin'
PUBLISHER = '国家金融监督管理总局'
HOST = 'https://www.nfra.gov.cn'
CATALOGUE_PAGE = HOST + '/cn/view/pages/ItemList.html?itemId=954&itemUrl=ItemListRightList.html'
CDN = HOST + '/cn/static/data/DocInfo/'
CATALOGUE = CDN + 'SelectDocByItemIdAndChild/data_itemId=954,pageIndex=1,pageSize=18.json'
DOC_TITLE = re.compile(r'(20\d{2})年商业银行主要监管指标情况表')
ATTACHMENT = re.compile(r'/chinese/docfile/20\d{2}/[0-9a-f]{32}\.xls')
QUARTERS = ('一季度', '二季度', '三季度', '四季度')
MAX_BYTES = 384_000


def _document_url(doc_id):
    return CDN + f'SelectByDocId/data_docId={doc_id:d}.json'


def _publish_time(value):
    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=SHANGHAI)


def _payload(snapshot, url):
    data = base64.b64decode(snapshot['original_base64'], validate=True)
    if not data or len(data) > MAX_BYTES or snapshot['url'] != url or snapshot['original_sha256'] != sha256(data).hexdigest():
        raise ContractError('金融监管总局响应地址或原始文件摘要不一致。')
    return data


def _json_data(snapshot, url):
    payload = json.loads(_payload(snapshot, url))
    if payload['rptCode'] != 200:
        raise ContractError('金融监管总局未返回有效发布记录。')
    return payload['data']


def _select_document(snapshot, cutoff):
    last = timepoint(cutoff).astimezone(SHANGHAI)
    choices = []
    for row in _json_data(snapshot, CATALOGUE)['rows']:
        if (match := DOC_TITLE.fullmatch(row.get('docTitle') or '')) and type(row.get('docId')) is int and row['docId'] > 0:
            if _publish_time(row['publishDate']) <= last and last.year - 1 <= int(match[1]) <= last.year:
                choices.append(dict(doc_id=row['docId'], year=match[1], published=row['publishDate']))
    return max(choices, key=lambda item: (item['year'], item['published']), default=None)


def _attachment(snapshot, selection):
    data = _json_data(snapshot, _document_url(selection['doc_id']))
    title = selection['year'] + '年商业银行主要监管指标情况表'
    if (data['docId'], data['docTitle'], data['publishDate']) != (selection['doc_id'], title, selection['published']):
        raise ContractError('监管统计正文与目录所列版本不一致。')
    candidates = [item['urlOtherName'] for item in data['attachmentInfoVOList']
                  if item['title'] == title+'.xls' and isinstance(item.get('urlOtherName'), str)
                  and ATTACHMENT.fullmatch(item['urlOtherName'])]
    if len(candidates) != 1:
        raise ContractError('监管统计表附件未能唯一定位。')
    return HOST+candidates[0]


@lru_cache(maxsize=4)
def _margin_cells(payload, year, cutoff):
    import xlrd

    book = xlrd.open_workbook(file_contents=payload, formatting_info=True, on_demand=True)
    try:
        sheet = book.sheet_by_index(0)
        title = year + '年商业银行主要监管指标情况表'
        if (sheet.cell_value(0, 0) != title or sheet.cell_value(2, 1) != year+'年'
                or tuple(sheet.row_values(3)[1:5]) != QUARTERS):
            raise ContractError('监管统计表的文件标题、年份或季度不一致。')
        rows = [i for i in range(sheet.nrows) if str(sheet.cell_value(i, 0)).strip() == '净息差']
        if len(rows) != 1:
            raise ContractError('监管统计表净息差行未能唯一定位。')
        row, = rows
        result = []
        for col, quarter_end in enumerate(('03-31', '06-30', '09-30', '12-31'), 1):
            period = year+'-'+quarter_end
            cell = sheet.cell(row, col)
            if period > cutoff or cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                continue
            code = book.xf_list[sheet.cell_xf_index(row, col)].format_key
            if (cell.ctype != xlrd.XL_CELL_NUMBER or '%' not in book.format_map[code].format_str
                    or not -0.2 < cell.value < 0.2):
                raise ContractError('净息差数据单元与百分比原始尺度不一致。')
            result.append(dict(period=period, raw=format(Decimal(str(cell.value))*100, 'f'),
                               locator=dict(kind=FIELD, sheet=sheet.name, row=row+1, column=col+1, raw_ratio=str(cell.value))))
        return tuple(result)
    finally:
        book.release_resources()


def request_options(task):
    """Offer only the fixed directory and follow-ups backed by retained responses."""
    from .evidence import current_query_reference

    scope = study_scope(task)
    cutoff = timepoint(task['cutoff']).astimezone(SHANGHAI)
    if (task.get(POLICY) != 1 or not scope or not scope['banks']
            or scope['month'] not in ('', cutoff.date().isoformat()[:7])):
        return {}
    options = {'directory': dict(parameters={'stage': 'directory'}, url=CATALOGUE)}
    records = [record for record in task.get('source_records', ())
               if (record.get('source'), record.get('operation')) == ('official', OPERATION)
               and current_query_reference(task, record)]
    directory = next((record for record in reversed(records) if record['parameters'] == {'stage': 'directory'}), None)
    if directory is None:
        return options
    try:
        selection = _select_document(directory['snapshot'], task['cutoff'])
        if selection is None:
            return options
        parameters = dict(stage='details', directory_id=directory['id'])
        options['details'] = dict(parameters=parameters, url=_document_url(selection['doc_id']), selection=selection)
        details = next((record for record in reversed(records) if record['parameters'] == parameters), None)
        if details is not None:
            options['table'] = dict(parameters=dict(stage='table', details_id=details['id']),
                                    url=_attachment(details['snapshot'], selection), selection=selection)
    except (ContractError, KeyError, TypeError, ValueError, AttributeError):
        pass
    return options


def validated_request(task, parameters):
    options = request_options(task)
    if (selected := options.get(parameters.get('stage'))) and selected['parameters'] == parameters:
        return selected
    raise ContractError('监管统计表下载须由本任务的官方目录和附件定位。')


def validate_snapshot(task, selected, snapshot):
    stage = selected['parameters']['stage']
    if stage == 'directory':
        return _select_document(snapshot, task['cutoff'])
    if stage == 'details':
        return _attachment(snapshot, selected['selection'])
    month = timepoint(task['cutoff']).astimezone(SHANGHAI).date()
    return _margin_cells(_payload(snapshot, selected['url']), selected['selection']['year'], month.isoformat())


def retained_cells(task, record):
    from .evidence import current_query_reference

    try:
        parameters = record['parameters']
        if parameters.get('stage') == 'table' and current_query_reference(task, record):
            selected = validated_request(task, parameters)
            return validate_snapshot(task, selected, record['snapshot'])
    except (ContractError, KeyError, TypeError, ValueError, AttributeError):
        pass
    return ()


def download_snapshot(url, timeout):
    allowed = url == CATALOGUE or re.fullmatch(re.escape(CDN)+r'SelectByDocId/data_docId=[1-9]\d{0,9}\.json', url)
    if not allowed and (not url.startswith(HOST) or not ATTACHMENT.fullmatch(url[len(HOST):])):
        raise ContractError('金融监管总局资料地址未登记。')
    request = Request(url, headers={'Accept-Encoding': 'identity'})
    with open_verified_https(request, timeout=timeout, follow_redirects=False) as response:
        expected = 'application/vnd.ms-excel' if url.endswith('.xls') else 'application/json'
        if response.headers.get_content_type() != expected:
            raise ContractError('金融监管总局响应格式与登记资料类型不符。')
        raw = response.read(MAX_BYTES+1)
    return dict(url=url, original_base64=base64.b64encode(raw).decode(), original_sha256=sha256(raw).hexdigest())
