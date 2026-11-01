"""Verified vendor catalogue metadata, never financial or publication proof.

Analysis and review share a projection of actually downloaded originals.
No guessed timezone, source selection, completeness claim or coverage repair.
"""
import copy

from .contracts import ContractError, DOCUMENT_CONTENT_INTENT, fingerprint

POLICY = 'catalog_metadata_basis_version'
QUERY_POLICY = 'catalog_query_result_basis_version'
QUERY_COLUMNS = ['source_id', 'provider_code', 'title', 'supplier_announcement_date',
                 'supplier_publication_at', 'gaps']
LIMITATION = ('仅核对供应商目录的原始名称、证券代码、公告日期和实际检索范围。'
              '公告日期不等于首次公开时间；下载时间不证明历史可得。'
              '所列仅为实际下载原件，不证明窗口、全市场或最新版本完整覆盖，'
              '也不批准正文、指数代码、财务数值、投资判断或因果。')


def scope_basis(task):
    """Rebuild source metadata for explicitly versioned current tasks only."""
    policy = task.get(POLICY)
    if policy is None:
        return None
    if type(policy) is not int or policy != 1:
        raise ContractError('公告目录元数据依据版本未登记')
    query_policy = task.get(QUERY_POLICY)
    if query_policy is not None and (type(query_policy) is not int or query_policy != 1):
        raise ContractError('公告目录返回行依据版本未登记')
    if task.get('research_mode') != 'current' or task.get('intent') == DOCUMENT_CONTENT_INTENT:
        return None
    if type(task.get('version')) is not int or task['version'] < 1:
        raise ContractError('公告目录元数据的研究版本必须为登记整数')
    from .planning import input_fingerprint
    if task.get('input_hash') != input_fingerprint(task):
        raise ContractError('公告目录元数据与当前登记研究范围不一致')
    from .evidence import current_original_reference, current_query_reference, validate_filing_catalog_binding
    originals = [record for record in task.get('source_records', [])
        if record.get('kind') == 'filing' and record.get('source') == 'ifind'
        and record.get('operation') == 'filing_original']
    ids = [record.get('id') for record in originals]
    if len(ids) != len(set(ids)):
        raise ContractError('公告原件标识不唯一，不能生成目录元数据依据')
    rows, queries = [], {}
    for record in originals:
        availability = current_original_reference(task, record)
        if availability is None:
            continue  # Imports and stale snapshots remain readable, not approved.
        directory = validate_filing_catalog_binding(task, record['directory'])
        if directory != record['snapshot'].get('directory'):
            raise ContractError('公告原件的目录与实际下载快照不一致')
        reference = directory['catalog_ref']
        parent = next(source for source in task['source_records'] if source['id'] == reference['artifact_id'])
        query_reference = current_query_reference(task, parent)
        if query_reference is None:
            continue  # Downloaded bytes cannot authenticate imported catalogue dates.
        if query_policy == 1 and parent['id'] not in queries:
            # validate_filing_catalog_binding has rebuilt and compared every
            # directory entry. Project every returned row in provider order;
            # titles/dates are catalogue metadata, not body/first-publication proof.
            entries = parent['catalog_entries']
            query_rows = [[entry['source_id'], entry['issuer'], entry['title'],
                           entry['report_date'], entry['published_at'], copy.deepcopy(entry['gaps'])]
                          for entry in entries]
            queries[parent['id']] = dict(catalog_artifact_id=parent['id'],
                catalog_snapshot_hash=parent['hash'], catalog_request_hash=parent['request_hash'],
                query_reference=copy.deepcopy(query_reference),
                query_window=copy.deepcopy(parent['parameters']),
                columns=list(QUERY_COLUMNS), rows=query_rows, returned_row_count=len(query_rows),
                rows_hash=fingerprint(query_rows), all_returned_rows_projected=True,
                query_completeness=parent['catalog_coverage']['completeness'])
        rows.append(dict(original_artifact_id=record['id'],
            original_snapshot_hash=availability['snapshot_hash'],
            original_hash=availability['original_hash'],
            catalog_artifact_id=reference['artifact_id'], catalog_snapshot_hash=reference['snapshot_hash'],
            catalog_request_hash=reference['request_hash'], source_id=directory['source_id'],
            source='ifind', provider_code=directory['issuer'], title=directory['title'],
            supplier_announcement_date=directory['report_date'],
            supplier_publication_at=directory['published_at'],
            original_obtained_at=availability['obtained_at'],
            query_window=copy.deepcopy(directory['query_coverage']['requested']),
            query_completeness=directory['query_coverage']['completeness']))
    if not rows:
        return None
    result = dict(version=1, task_id=task['id'], research_version=task['version'],
        input_hash=task['input_hash'], records=rows, records_hash=fingerprint(rows),
        limitation=LIMITATION, financial_approval=False,
        first_publication_approved=False, historical_availability_approved=False,
        latest_report_completeness_approved=False, automatic_need_coverage=False)
    if query_policy == 1:
        result['catalog_query_results'] = list(queries.values())
        result['limitation'] = ('原件记录仅列实际下载项；catalog_query_results逐行保留关联查询实际返回的全部目录行。'
            '可比较这些返回行的标题与供应商公告日期，但供应商返回、指定窗口不等于全渠道或最新报告完整覆盖。'
            '公告日期不是首次公开时间，目录不批准正文、指数代码、金融值、历史可得或因果。')
    return result
