"""Independent retained-response checks, never reopen a study or call an API."""
from decimal import Decimal
import hashlib
import json
import math

from tests import ROOT


def inspect_response(request, response):
    if response.get('errorcode') not in (0, '0') or isinstance(response.get('errorcode'), bool):
        raise ValueError('Business response is not successful')
    codes = request['codes'].split(',')
    fields = {row['indicator']: row['indiparams'] for row in request['indipara']}
    if len(codes) != len(set(codes)) or len(fields) != len(request['indipara']):
        raise ValueError('Requested codes or fields are duplicated')
    reflected = response.get('inputParams', {}).get('params', [])
    if len(reflected) != len(fields) or {row.get('function') for row in reflected} != set(fields):
        raise ValueError('Indicator reflection differs from the request')
    for row in reflected:
        params = row.get('params', [])
        if len({p.get('name') for p in params}) != len(params):
            raise ValueError('Reflected parameter names are duplicated')
        values = {p['name']: p.get('value') for p in params}
        if [values.get('FDREPORT'), values.get('FL')] != fields[row['function']]:
            raise ValueError('Report period or report-type reflection differs')
    tables = response.get('tables')
    if not isinstance(tables, list) or len(tables) != len(codes):
        raise ValueError('Returned code count differs')
    actual = [row.get('thscode') for row in tables]
    if len(set(actual)) != len(actual) or set(actual) != set(codes):
        raise ValueError('Returned codes differ or are duplicated')
    numeric = missing = 0
    values = {}
    for row in tables:
        columns = row.get('table', {})
        if set(columns) != set(fields):
            raise ValueError('Returned financial columns differ')
        values[row['thscode']] = {}
        for field, cells in columns.items():
            if not isinstance(cells, list) or len(cells) != 1:
                raise ValueError('Cross-section cells are not one per code and field')
            value = cells[0]
            if value is None:
                missing += 1
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('Financial cell is not a finite numeric original')
            else:
                numeric += 1
            values[row['thscode']][field] = value
    return dict(codes=len(codes), fields=len(fields), numeric_cells=numeric, missing_cells=missing,
        values=values, units_verified=False, currency_verified=False,
        financial_values_approved=False, full_industry_universe_verified=False)


def run():
    folder = ROOT/'result/acceptance/ifind-financial-basic-20261023'
    target = folder/'independent-review.json'
    if target.exists():
        raise FileExistsError('Original independent review already exists')
    metadata = ROOT/'result/acceptance/ifind-financial-metadata-20261023'
    paths = [folder/'request.json', folder/'raw-response.json', folder/'receipt.json', metadata/'receipt.json']
    metadata_receipt = json.loads((metadata/'receipt.json').read_text())
    for event in metadata_receipt['requests']:
        if event.get('file'):
            path = ROOT/event['file']
            assert hashlib.sha256(path.read_bytes()).hexdigest() == event['sha256']
            paths.append(path)
    receipt = json.loads((folder/'receipt.json').read_text())
    raw = (folder/'raw-response.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == receipt['response_sha256']
    request = json.loads((folder/'request.json').read_text())
    checked = inspect_response(request, json.loads(raw))
    assert checked['codes'] == 48 and checked['fields'] == 2 and checked['numeric_cells'] == 96
    original_path = ROOT/receipt['source_codes_file']
    original_bytes = original_path.read_bytes()
    assert hashlib.sha256(original_bytes).hexdigest() == receipt['source_codes_sha256']
    original = json.loads(original_bytes)['tables'][0]['table']
    mapping = {'ths_revenue_stock': '营业收入[20260630]',
        'ths_np_atoopc_stock': '归属于母公司所有者的净利润[20260630]'}
    same = sum(Decimal(str(values[field])) == Decimal(str(original[mapping[field]][original['股票代码'].index(code)]))
        for code, values in checked['values'].items() for field in mapping)
    assert same == 96
    assert metadata_receipt['requests_started'] == 5 and receipt['requests_started'] == 2
    assert metadata_receipt['production_unchanged'] and receipt['production_unchanged']
    assert metadata_receipt['temporary_credentials_cleared'] and receipt['temporary_credentials_cleared']
    paths.append(original_path)
    record = dict(status='raw_data_verified_not_financial_adoption', model_calls=0,
        new_data_requests=0, closed_probe_requests=7, returned_codes=48, numeric_cells=96,
        same_retained_semantic_query_values=same, units_verified=False, currency_verified=False,
        historical_availability_verified=False, full_industry_universe_verified=False,
        product_channel_registered=False, installed_app_changed=False,
        original_hashes={str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        findings=['基础接口可按明确报告期和合并报表参数批量取得原值，不必先下载48份PDF。',
            '原响应UNIT参数为空，未提供有效单位或币种，数值一致不能替代单位核验。',
            '搜索候选路径含保险公司，不能用指标目录路径判断证券所属行业。',
            '动态元数据检索与本次基础财务字段尚未接入R154正式研究渠道。'])
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=record['status'], codes=48, cells=96, new_data_requests=0,
        receipt=str(target)), ensure_ascii=False))
    return target
