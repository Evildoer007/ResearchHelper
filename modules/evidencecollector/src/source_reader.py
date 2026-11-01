"""Bounded, content-bound original reading. Search results never approve evidence.

The archive remains lossless. Context windows are explicit selections of page
or paragraph spans, with publication and exception searches alongside the
research query. Neither search rank nor a reading receipt asserts completeness.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import re

from .contracts import ContractError, canonical, fingerprint
from .material_parser import verified_material
from .reasoning import original_sources, source_views

READER_VERSION = 2
SUPPORTED_READER_VERSIONS = (1, 2, 3, 4, 5, 6, 7)
MAX_QUERY_CANDIDATES = 80
CONTEXT_LIMIT = 80_000
MAX_REQUESTS = 12
MAX_TERMS = 8
MAX_PARTS = 8
MAX_SPAN = 8_000
WINDOW_RADIUS = 600
PUBLICATION_TERMS = ("发布日期", "公布日期", "披露日期", "公告日期", "发布时间", "披露时间",
                     "Publication date", "Published", "Disclosure date", "Release date")
EXCEPTION_TERMS = ("不适用", "除外", "例外", "风险", "限制", "不得", "附注", "免责声明",
                   "risk", "Risk", "exception", "Exception", "not applicable", "footnote", "Footnote", "limitation", "Limitation")
IDENTITY_CONTEXT_TERMS = ('证券代码', '股票代码', '股票简称', '基金代码', '基金简称')
MAX_IDENTITY_CONTEXTS = 2


def financial_reader_terms(task):
    """Registered method locations, not source facts or financial approvals."""
    from .contracts import FINANCIAL_PERIOD_METHODS
    methods={method for question in task.get('questions',[]) for method in question.get('method_needs',[])}
    methods.update(item.get('method') for item in task.get('calculation_specs',[]))
    active=methods.intersection(FINANCIAL_PERIOD_METHODS)
    if not active:return []
    terms=['合并资产负债表','企业会计准则','合并范围']
    if active.intersection(('gross_margin_v1','net_profit_margin_v1')):
        terms+=['合并利润表','营业收入']
    if 'gross_margin_v1' in active:terms+=['营业成本']
    if active.intersection(('net_profit_margin_v1','operating_cash_profit_ratio_v1')):
        terms+=['净利润']
    if 'operating_cash_profit_ratio_v1' in active:
        terms+=['合并现金流量表','经营活动产生的现金流量净额']
    return list(dict.fromkeys(terms))


def _base_reader_version(task):
    if concept_reader_rule(task):return 6
    if statistical_reader_terms(task):return 5
    if fee_reader_terms(task):return 4
    return 3 if financial_reader_terms(task) else READER_VERSION


def reader_version_for(task):
    if (type(task.get('financial_input_reading_priority_version')) is int
            and task['financial_input_reading_priority_version'] == 3 and financial_reader_terms(task)):
        return 7
    return _base_reader_version(task)


def reading_request_capacity(task):
    """Bound declared multi-original reading by the existing execution quota.

    The aggregate character/context budget stays unchanged. Older tasks keep
    their 12-request wire; this does not authorize extra downloads or facts.
    """
    version = task.get('declared_reading_capacity_version')
    if version is None:
        return MAX_REQUESTS
    if type(version) is not int or version != 1:
        raise ContractError('声明式原件读取容量版本未登记')
    quota = task.get('ledger', {}).get('limits', {}).get('data_requests')
    if type(quota) is not int or quota < 0:
        raise ContractError('原件读取缺少有效执行预算')
    return max(MAX_REQUESTS, quota)


def _complete_declared_catalog(task, source_catalog, artifact_ids):
    """Page the actual selected catalogue, never expand its artifact scope."""
    if artifact_ids is None or source_catalog.get('offset') != 0:
        return None
    first = catalog(task, artifact_ids)
    if fingerprint(first) != fingerprint(source_catalog):
        return None
    items = list(first['items']); cursor = first['next_offset']
    while cursor is not None:
        page = catalog(task, artifact_ids, offset=cursor)
        if (page['binding'] != first['binding'] or page['total_artifacts'] != first['total_artifacts']
                or not page['items'] or page['offset'] != cursor):
            return None
        items.extend(page['items']); cursor = page['next_offset']
    if len(items) != first['total_artifacts'] or len({item['id'] for item in items}) != len(items):
        return None
    return dict(first, items=items, next_offset=None)


def registered_reading_requests(task, source_catalog, question_ids):
    """Navigate registered financial locations, never select/approve facts.

    Extraction already reads the resulting original windows. A separate
    language call is unnecessary when the task has explicit registered inputs
    and the complete acquired-document catalogue fits one bounded request set.
    Other problems keep the existing model-selected reading path.
    """
    if (type(task.get('registered_source_reading_version')) is not int
            or task['registered_source_reading_version'] != 1
            or type(task.get('financial_input_reading_priority_version')) is not int
            or task['financial_input_reading_priority_version'] not in (1, 2, 3)):
        return None
    terms = financial_reader_terms(task)
    if not terms:
        return None
    if not isinstance(source_catalog, dict) or source_catalog.get('binding') != binding(task):
        raise ContractError('登记原文导航的目录绑定已过期，未选择任何材料')
    if source_catalog.get('offset') != 0 or source_catalog.get('next_offset') is not None:
        return None
    items = source_catalog.get('items', [])
    if not isinstance(items, list) or len(items) != source_catalog.get('total_artifacts'):
        return None
    documents = [item for item in items if item.get('kind') in ('material', 'filing')]
    if not 1 <= len(documents) <= MAX_REQUESTS or any(item.get('status') != '已解析' for item in documents):
        return None
    known = {q['id'] for q in task['questions']}
    if (not isinstance(question_ids, list) or not question_ids or len(set(question_ids)) != len(question_ids)
            or any(ident not in known for ident in question_ids)):
        raise ContractError('登记原文导航须对应本轮明确问题')
    if set(question_ids) != known:
        return None  # A financial task must not choose reading for a different sub-scope.
    requests = []
    for item in documents:
        reference = item.get('request_binding')
        expected = dict(artifact_id=item['id'], snapshot_hash=item['snapshot_hash'])
        if reference != expected:
            raise ContractError('登记原文导航缺少完整原件绑定，未自动补写')
        requests.append(dict(reference, queries=terms[:MAX_TERMS], parts=[],
            reason='按登记计算输入与锁定知识定位原文；片段仍须提取、来源及适用性核验，不批准事实或完整覆盖。',
            question_ids=list(question_ids)))
    return requests


def validate_acquisition_reading_queries(task, need):
    """Validate declared reading intent separately from provider parameters."""
    if 'reading_queries' not in need:return []
    version=task.get('acquisition_reading_intent_version')
    if type(version) is not int or version!=1:
        raise ContractError('本任务未登记下载原件时的读取意图格式')
    if (need.get('source','ifind'),need.get('operation')) not in (
            ('ifind','filing_original'),('official','release_original')):
        raise ContractError('读取意图仅用于下载文字原件，不传给行情、目录或其他接口')
    queries=need['reading_queries']
    if (not isinstance(queries,list) or len(queries)>MAX_TERMS
            or any(not isinstance(q,str) or not q.strip() or len(q)>80 for q in queries)
            or len(set(queries))!=len(queries)):
        raise ContractError('下载读取意图须为最多8个不重复的明文词组，不猜页码或生成内容')
    return copy.deepcopy(queries)


def acquisition_reading_requests(task, source_catalog, question_ids, *, artifact_ids=None):
    """Reuse explicitly declared queries from actually completed downloads.

    No inferred keywords or locations. Every acquired document must have its
    own matching request/receipt, otherwise keep normal model-selected reading.
    The existing bounded reader still selects and verifies exact source spans.
    """
    version=task.get('acquisition_reading_intent_version')
    if version is None:return None
    if type(version) is not int or version!=1:raise ContractError('下载读取意图版本未登记')
    if not isinstance(source_catalog,dict) or source_catalog.get('binding')!=binding(task):
        raise ContractError('下载读取意图的原件目录绑定已变化')
    if source_catalog.get('offset')!=0:return None
    if source_catalog.get('next_offset') is not None:
        if task.get('declared_reading_capacity_version') is None:return None
        reading_request_capacity(task)
        source_catalog = _complete_declared_catalog(task, source_catalog, artifact_ids)
        if source_catalog is None:return None
    items=source_catalog.get('items',[])
    if not isinstance(items,list) or len(items)!=source_catalog.get('total_artifacts'):return None
    documents=[item for item in items if item.get('kind') in ('material','filing')]
    if not 1<=len(documents)<=reading_request_capacity(task) or any(item.get('status')!='已解析' for item in documents):return None
    known={q['id'] for q in task['questions']}
    if (not isinstance(question_ids,list) or not question_ids or len(set(question_ids))!=len(question_ids)
            or any(q not in known for q in question_ids)):
        raise ContractError('下载读取意图须对应本轮明确问题')
    needs=list(task.get('data_need_specs',[]))
    state=task.get('reasoning_state',{}).get('acquisition',{})
    current={key:task[key] for key in ('id','version','cutoff','input_hash')}
    if state:
        if any(state.get('binding',{}).get(key)!=value for key,value in current.items()):return None
        for decision in state.get('decisions',[]):
            if decision.get('status')=='completed' and decision.get('binding')==state.get('binding'):
                if decision.get('reading_intent_hash')!=fingerprint(dict(
                        binding=decision['binding'],data_requests=decision.get('data_requests',[]))):return None
                needs.extend(decision.get('data_requests',[]))
    records={r['id']:r for r in original_sources(task)}
    from .evidence import validate_source_request
    result=[]
    for item in documents:
        record=records.get(item['id']);reference=item.get('request_binding')
        if (not record or reference!=dict(artifact_id=item['id'],snapshot_hash=item['snapshot_hash'])
                or record.get('task_id')!=task['id'] or type(record.get('research_version')) is not int
                or record['research_version']!=task['version'] or record.get('status')!='成功待核验'
                or record.get('snapshot_hash',record.get('hash'))!=item['snapshot_hash']
                or fingerprint(record.get('snapshot'))!=item['snapshot_hash']):
            raise ContractError('下载读取意图与本任务实际原件不一致')
        matched=[]
        for need in needs:
            if need.get('id') not in record.get('data_need_ids',[]):continue
            queries=validate_acquisition_reading_queries(task,need)
            if not queries:continue
            parameters=validate_source_request(task,need.get('source','ifind'),need['operation'],need['parameters'])
            request_hash=fingerprint(dict(source=need.get('source','ifind'),operation=need['operation'],parameters=parameters))
            if request_hash!=record.get('request_hash'):continue
            if not any(d.get('kind')=='data_requests' and d.get('fingerprint')==request_hash
                       and d.get('status')=='成功' for d in task['ledger']['dispatches']):continue
            if set(need['question_ids']).issubset(question_ids):matched.append((need,queries))
        if not matched:return None
        queries=list(dict.fromkeys(q for _,words in matched for q in words))
        selected=list(dict.fromkeys(q for need,_ in matched for q in need['question_ids']))
        if len(queries)>MAX_TERMS:return None  # Never silently trim declared queries.
        result.append(dict(reference,queries=queries,parts=[],question_ids=selected,
            reason='沿用本任务实际下载请求明确提交的读取词；来源、范围、例外及金融适用性仍须独立核验。'))
    return result


def concept_reader_rule(task):
    """Use the locked conceptual index before choosing reading windows."""
    if (type(task.get('original_concept_reading_version')) is not int
            or task['original_concept_reading_version']!=1):return None
    from .knowledge import literal_navigation_rule
    return literal_navigation_rule(task)


def concept_reader_queries(rule, parts):
    """Find actual literal spellings, including original PDF whitespace.

    Search terms are escaped registered concepts, not material instructions.
    First hits are navigation only; absence, negation and truth remain open.
    """
    if not rule:return []
    found=[]
    for term in rule['terms']:
        pattern=re.compile(r'\s*'.join(re.escape(char) for char in term))
        for part in parts:
            match=pattern.search(part['text'])
            if match:
                phrase=match.group()
                if len(phrase)<=80 and phrase not in found:found.append(phrase)
                break
    return found


def fee_reader_terms(task):
    """Locate required literal fee/identity fields; never provide their values."""
    methods={method for question in task.get('questions',[]) for method in question.get('method_needs',[])}
    methods.update(item.get('method') for item in task.get('calculation_specs',[]))
    if (type(task.get('fee_candidate_encoding')) is int and task['fee_candidate_encoding']==1
            and methods.intersection(('management_custody_fee_comparison_v1','management_custody_fee_summary_v1','fee_difference_v2'))):
        return ['基金主代码','交易代码','年费率','管理人报酬','托管费按']
    return []


def statistical_reader_terms(task):
    """Prioritize explicitly requested PMI indicators, never their values.

    Only the user's question enables these literal locators. Model-added
    hypotheses and source text cannot silently expand the required scope.
    Matching a term does not establish sector, month, unit or financial use.
    """
    if type(task.get('statistical_reading_version')) is not int or task['statistical_reading_version'] != 1:
        return []
    question = task.get('question', '')
    if not isinstance(question, str) or '制造业' not in question.replace('非制造业', '') or not (
            'pmi' in question.lower() or '采购经理指数' in question):
        return []
    terms = []
    for requested, locator in (('生产', '生产指数'), ('新订单', '新订单指数')):
        if requested in question:
            terms.append(locator)
    return terms


class ReadingSelectionError(ContractError):
    """A bounded locator rejection, never private proposed names or values."""
    def __init__(self, code, message, index, *, duplicate_of=None):
        super().__init__(message)
        self.diagnostic = {'version': 1, 'code': code, 'request_index': index,
                           'selection_accepted': False, 'financial_approval': False}
        if duplicate_of is not None:
            self.diagnostic['duplicate_of'] = duplicate_of


class ReadingAnchorError(ContractError):
    """Identify a rejected field without exposing its proposed quote or value."""
    def __init__(self, index, field):
        super().__init__('提取或口径依据不在本轮实际读取片段中')
        self.diagnostic = {'version': 1, 'code': 'unread_anchor',
            'specification_index': index, 'field': field, 'evidence_approved': False}


def binding(task):
    result = {"task_id": task["id"], "research_version": task["version"], "input_hash": task["input_hash"]}
    if _mixed_atomic_enabled(task):
        result['mixed_atomic_api_reading_version'] = 1
    if _geometry_projection_enabled(task):
        result['table_geometry_projection_version'] = 1
    return result


def _geometry_projection_enabled(task):
    version=task.get('table_geometry_projection_version')
    if version is None:return False
    if type(version) is not int or version!=1:
        raise ContractError('表格坐标展示版本未登记')
    return True


def _reading_table_metadata(task, metadata):
    """Keep full geometry in the original; expose its hash and table shape.

    Actual words, signs, dates and notes remain in the read text. All numeric
    validators resolve the unchanged original coordinates from the snapshot.
    """
    result=copy.deepcopy(metadata)
    if _geometry_projection_enabled(task) and 'word_cells' in result:
        result['word_cells_reference']=fingerprint(result.pop('word_cells'))
    return result


def _mixed_atomic_enabled(task):
    version = task.get('mixed_atomic_api_reading_version')
    if version is None:
        return False
    if type(version) is not int or version != 1:
        raise ContractError('混合原件与完整API读取版本未登记')
    return True


def _mixed_atomic_views(task, records, views):
    """Intersect current extractability with the caller's explicit allowed set.

    Never recover a missing API by broadening artifact_ids. The extraction
    caller must authorize its scope before reading; full snapshots are atomic.
    """
    from .reasoning import extractable_source_views
    eligible = {view['id']: view for view in extractable_source_views(task)
                if view.get('kind') == 'api'}
    by_id = {record['id']: record for record in records}
    if len(by_id) != len(records):
        raise ContractError('混合读取原件标识重复')
    atomic = []
    for view in views:
        if view.get('kind') != 'api' or view['id'] not in eligible:
            continue
        record = by_id[view['id']]
        if (type(record.get('content_version')) is not int or record['content_version'] < 1
                or view.get('content_version') != record['content_version']
                or view != eligible[view['id']]):
            raise ContractError('完整API读取内容版本或可提取快照不一致')
        if len(canonical(atomic + [view])) <= CONTEXT_LIMIT // 4:
            atomic.append(copy.deepcopy(view))
    return atomic


def _records(task, artifact_ids=None):
    records = original_sources(task)
    if artifact_ids is not None:
        if not isinstance(artifact_ids, list) or any(not isinstance(ident, str) for ident in artifact_ids) or len(artifact_ids) != len(set(artifact_ids)):
            raise ContractError("读取范围须为不重复的本任务原件标识")
        known = {r["id"] for r in records}
        if any(not isinstance(ident, str) or ident not in known for ident in artifact_ids):
            raise ContractError("读取原件不存在或不属于当前任务")
        by_id = {r["id"]: r for r in records}
        records = [by_id[ident] for ident in artifact_ids]
    for record in records:
        if record.get("task_id") != task["id"] or record.get("research_version") != task["version"] or \
                fingerprint(record.get("snapshot")) != record.get("snapshot_hash", record.get("hash")):
            raise ContractError("读取原件与任务、研究版本或留存哈希不一致")
    return records


def _material(record):
    if record["kind"] == "material":
        return record["snapshot"]
    if record["kind"] == "filing":
        return record["snapshot"].get("material", {})
    return {}


def _verify_original_text(record):
    material = _material(record)
    if material.get("status") != "已解析":
        if material.get("parts"):
            raise ContractError("解析受限原件不能附带可读正文")
        return
    try:
        payload = base64.b64decode(material["original_base64"], validate=True)
    except (ValueError, KeyError):
        raise ContractError("读取原件字节缺失或损坏") from None
    if hashlib.sha256(payload).hexdigest() != material.get("hash"):
        raise ContractError("读取原件字节与文件哈希不一致")
    extracted = verified_material(material, payload)
    if extracted["parts"] != material.get("parts") or extracted["extract_version"] != material.get("extract_version"):
        raise ContractError("读取正文不是当前原件提取版本")


def catalog(task, artifact_ids=None, *, offset=0, limit=24):
    """Paginated metadata and short page headings, not an inferred summary."""
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 24:
        raise ContractError("原件目录分页范围无效")
    records = _records(task, artifact_ids)
    items = []
    for record in records[offset:offset + limit]:
        material = _material(record)
        parts = material.get("parts", [])
        item = {key: copy.deepcopy(record[key]) for key in ("id", "kind", "source", "content_version")}
        item.update(snapshot_hash=record.get("snapshot_hash", record["hash"]),
                    name=material.get("name", record.get("operation", "留存原件")),
                    status=material.get("status", record.get("status", "未获取")),
                    part_count=len(parts), text_characters=sum(len(p["text"]) for p in parts),
                    headings=[{"part": p["locator"], "characters": len(p["text"]), "opening": p["text"][:96]}
                              for p in parts[:12]], headings_omitted=max(0, len(parts) - 12))
        item['reading_spans'] = [{'part': p['locator'], 'start': 0,
                                  'end': min(len(p['text']), MAX_SPAN)}
                                 for p in parts[:12] if p['text']]
        if material.get('limitation'):
            item['parse_limitation'] = material['limitation']
        if record['kind'] in ('material', 'filing'):
            item['request_binding'] = {'artifact_id': record['id'], 'snapshot_hash': item['snapshot_hash']}
            if type(task.get('original_period_selection_version')) is int and task['original_period_selection_version'] == 1:
                from .financial_sources import original_period_anchors
                locations = {}
                for anchor in original_period_anchors({'parts': parts}):
                    locators = locations.setdefault(anchor['period'], [])
                    if anchor['part'] not in locators:
                        locators.append(anchor['part'])
                item['literal_period_locations'] = [dict(period=period, parts=locators)
                    for period, locators in locations.items()]
                item['literal_period_locations_rule'] = '完整起止日期的原文位置提示；需明确读取后再选择，不批准报告期间、金融适用或历史可得。'
        # No binary bytes, credentials, raw API response or arbitrary URLs.
        items.append(item)
    return {"schema": "researchhelper.source-catalog", "reader_version": reader_version_for(task),
            "binding": binding(task), "items": items, "total_artifacts": len(records),
            "offset": offset, "next_offset": offset + len(items) if offset + len(items) < len(records) else None,
            "selection_contract": {'version': 1, 'one_request_per_original': True,
                'required_fields': ['artifact_id','snapshot_hash','queries','parts','reason','question_ids'],
                'binding_source': 'items.request_binding', 'automatic_repair': False},
            "limitation": "开头与检索片段只用于定位，未展示部分不代表不存在事实、反例或附注。"}


def context_sources(task, artifact_ids=None):
    """Keep established full-source behavior for small inputs; disclose large ones."""
    records = _records(task, artifact_ids)
    allowed = {r["id"] for r in records}
    views = [v for v in source_views(task) if v["id"] in allowed]
    if len(canonical(views)) <= CONTEXT_LIMIT:
        return {"mode": "full", "views": views, "catalog": None}
    atomic = []
    if _mixed_atomic_enabled(task):
        atomic = _mixed_atomic_views(task, records, views)
    else:
        for view in views:
            if view["kind"] == "api" and len(canonical(atomic + [view])) <= CONTEXT_LIMIT // 4:
                atomic.append(view)
    ordered = [r["id"] for r in records if r["kind"] != "api"] + [r["id"] for r in records if r["kind"] == "api"]
    return {"mode": "selected", "views": atomic, "catalog": catalog(task, ordered)}


def _validate_selection(task, requests, expected_binding, records):
    if expected_binding != binding(task):
        raise ContractError("读取请求所属任务、版本或输入已过期")
    if not isinstance(requests, list) or not 1 <= len(requests) <= reading_request_capacity(task):
        raise ContractError("读取请求须在本次容量内逐项选择明确原件" if task.get('declared_reading_capacity_version') is not None
                            else "读取请求须为1至12项有明确目的的原件选择")
    by_id = {r["id"]: r for r in records}
    question_ids = {q["id"] for q in task["questions"]}
    seen = {}
    for index, request in enumerate(requests):
        fields = {"artifact_id", "snapshot_hash", "queries", "parts", "reason", "question_ids"}
        if not isinstance(request, dict) or set(request) != fields:
            raise ContractError("读取请求只接受原件、哈希、关键词、页段、原因及关联问题")
        ident = request["artifact_id"]
        if not isinstance(ident, str) or ident not in by_id:
            raise ReadingSelectionError('unknown_artifact', '读取原件标识不在本次目录，未读取任何正文', index)
        if ident in seen:
            raise ReadingSelectionError('duplicate_artifact', '同一原件只能提交一项读取，请合并关键词、页段及问题',
                                        index, duplicate_of=seen[ident])
        seen[ident] = index
        record = by_id[ident]
        if request["snapshot_hash"] != record.get("snapshot_hash", record["hash"]):
            raise ContractError("读取请求原件哈希已过期")
        if record["kind"] not in ("material", "filing"):
            raise ContractError("分页读取仅适用于文字原件，API快照需保留完整字段结构")
        if not isinstance(request["reason"], str) or not request["reason"].strip() or len(request["reason"]) > 1000:
            raise ContractError("读取须说明核验目的")
        selected = request["question_ids"]
        if not isinstance(selected, list) or not selected or any(not isinstance(q, str) or q not in question_ids for q in selected) or \
                len(selected) != len(set(selected)):
            raise ContractError("读取目的必须对应本任务问题")
        queries, parts = request["queries"], request["parts"]
        if not isinstance(queries, list) or len(queries) > MAX_TERMS or \
                any(not isinstance(query, str) or not query.strip() or len(query) > 80 for query in queries) or len(queries) != len(set(queries)):
            raise ContractError("读取关键词须为最多8个不重复的明文词组")
        if not isinstance(parts, list) or len(parts) > MAX_PARTS or not (queries or parts):
            raise ContractError("读取须指定关键词或最多8个原件页段")
        pages = {p["locator"]: p["text"] for p in _material(record).get("parts", [])}
        for part in parts:
            if not isinstance(part, dict) or set(part) != {"part", "start", "end"} or not isinstance(part["part"], str) or part["part"] not in pages or \
                    type(part["start"]) is not int or type(part["end"]) is not int or \
                    not 0 <= part["start"] < part["end"] <= len(pages[part["part"]]) or part["end"] - part["start"] > MAX_SPAN:
                raise ReadingSelectionError('invalid_span',
                    "读取页段与字符范围无效，须选择目录实际范围或改用关键词，单段最多8000字", index)
    return copy.deepcopy(requests)


def _hits(text, terms):
    """Count literal hits and locate bounded, distributed candidates per term."""
    matches, count = set(), 0
    for term in terms:
        occurrences = text.count(term)
        count += occurrences
        if not occurrences:
            continue
        # The exact count is cheap even for a repeated multi-megabyte paragraph.
        # Candidate sampling is explicit, not a claim to return every match.
        positions = [text.find(term), text.rfind(term)]
        positions += [text.find(term, len(text) * index // 16) for index in range(1, 16)]
        matches.update((at, at + len(term), term) for at in positions if at >= 0)
    return count, sorted(matches)


def _merge(intervals):
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def _table_group(part):
    metadata = part.get('pdf_table')
    if (isinstance(metadata, dict) and type(metadata.get('version')) is int
            and (metadata['version'] == 1 or metadata['version'] == 2 and metadata.get('representation') == 'aligned_words')
            and metadata.get('geometry_only') is True and isinstance(metadata.get('group'), str)
            and metadata['group']):
        return metadata['group']
    return None


def _query_candidates(parts, terms):
    """Fair, bounded literal search; plain pages cannot crowd out ruled cells.

    Keep table and prose candidates separate before sampling each term across
    its actual matches. The geometry group is a reading unit, not an accounting
    scope or evidence approval. Publication/exception channels remain separate.
    """
    total = sum(len(part['text']) for part in parts)
    streams, hits_by_term = [], {}
    for term in terms:
        buckets = {'table': {}, 'text': {}}
        tails = {'table': [], 'text': []}
        offset = count = 0
        seen_groups = set()
        for part in parts:
            count_here, matches = _hits(part['text'], [term])
            count += count_here
            group = _table_group(part)
            for start, end, _ in matches:
                if group and group in seen_groups:
                    continue
                kind = 'table' if group else 'text'
                candidate = (part['locator'], max(0, start-WINDOW_RADIUS), min(len(part['text']), end+WINDOW_RADIUS), term)
                bucket = min(31, (offset+start)*32//max(total, 1))
                buckets[kind].setdefault(bucket, candidate)
                tails[kind] = (tails[kind]+[candidate])[-8:]
                if group:
                    seen_groups.add(group)
            offset += len(part['text'])
        hits_by_term[term] = count
        channels = [list(dict.fromkeys([*buckets[kind].values(), *tails[kind]])) for kind in ('table', 'text')]
        if any(channels):
            streams.append(channels)
    if not streams:
        return [], hits_by_term
    quotas = _fair_quotas([sum(map(len, channels)) for channels in streams], MAX_QUERY_CANDIDATES)
    sampled = [_sample_channels(channels, quota) for channels, quota in zip(streams, quotas)]
    return [stream[index] for index in range(max(map(len, sampled)))
            for stream in sampled if index < len(stream)][:MAX_QUERY_CANDIDATES], hits_by_term


def _fair_quotas(lengths, limit):
    quotas = [0]*len(lengths)
    for _ in range(min(sum(lengths), limit)):
        choices = [index for index, length in enumerate(lengths) if quotas[index] < length]
        index = min(choices, key=lambda index: quotas[index])
        quotas[index] += 1
    return quotas


def _sample_channels(channels, limit):
    # Sample within representations, never at a fixed stride over their
    # interleaving, which can accidentally select only prose or only tables.
    quotas = _fair_quotas(list(map(len, channels)), limit)
    sampled = [channel if len(channel) <= quota else
               [channel[index*(len(channel)-1)//(quota-1)] for index in range(quota)] if quota > 1 else
               channel[:quota] for channel, quota in zip(channels, quotas)]
    return [channel[index] for index in range(max(map(len, sampled), default=0))
            for channel in sampled if index < len(channel)]


def _financial_input_groups(task, record, view, lookup, groups, part_groups):
    """Find literal method-input table locations, not approve their values.

    The same retained parser provides positions for navigation. Nothing from
    this full-document scan is exposed as evidence until its complete reading
    group fits the ordinary window and passes the normal extraction checks.
    """
    if (type(task.get('financial_input_reading_priority_version')) is not int
            or task['financial_input_reading_priority_version'] not in (1, 2, 3)):
        return []
    from .contracts import FINANCIAL_PERIOD_METHODS, METHOD_REGISTRY
    from .financial_sources import (financial_table_candidates, financial_shared_context_choices,
                                     financial_reporting_currency_choices)
    methods = {method for question in task.get('questions', []) for method in question.get('method_needs', [])}
    methods.update(item['method'] for item in task.get('calculation_specs', []))
    metrics = {field for method in methods.intersection(FINANCIAL_PERIOD_METHODS)
               for field in METHOD_REGISTRY[method]['input_units']}
    if not metrics:
        return []
    found = {}
    context_parts = set()
    if task['financial_input_reading_priority_version'] >= 2:
        # Navigation only: bring the actual shared context with each amount
        # group before optional prose consumes the window. The model must still
        # select it and the normal original/currency/scope gates still run.
        currencies = financial_reporting_currency_choices(record, view)
        if currencies:
            context_parts.add(currencies[0]['part'])
        for choices in financial_shared_context_choices(task, record, view).values():
            if choices:
                context_parts.add(choices[0]['basis']['part'])
    for cell in financial_table_candidates(task, record, view, maximum=64)['cells']:
        if cell['metric'] not in metrics:
            continue
        locator = cell['proposal_fields']['record_locator']
        group = part_groups.get(locator['part'])
        locators = set(groups.get(group, [locator['part']]))
        locators.update((locator['unit_part'], locator['financial_column']['header_part']))
        if locator.get('financial_unit_scope'):
            locators.update(locator['financial_unit_scope'][field] for field in ('target_part','chapter_part'))
        locators.update(context_parts)
        if not locators.issubset(lookup):
            continue
        key = tuple(name for name in lookup if name in locators)
        entry = found.setdefault(key, dict(parts=list(key), metrics=[]))
        if cell['metric'] not in entry['metrics']:
            entry['metrics'].append(cell['metric'])
    return list(found.values())


def _identity_context_groups(parts, lookup, groups, part_groups):
    """Bounded literal field navigation, never a security-code inference.

    Search the actual original rather than assuming identity is on page one.
    Keep physical tables whole; plain long parts use an exact bounded span.
    Repeated plain-page/table representations share one context slot.
    """
    result, seen = [], set()
    for part in parts:
        text, locator = part['text'], part['locator']
        matches = [re.search(r'\s*'.join(re.escape(char) for char in term), text)
                   for term in IDENTITY_CONTEXT_TERMS]
        hits = [match.start() for match in matches if match is not None]
        if not hits:
            continue
        page = re.fullmatch(r'第(\d+)页(?:表\d+)?', locator)
        key = ('page', page[1]) if page else ('part', locator)
        if key in seen:
            continue
        seen.add(key)
        group = part_groups.get(locator)
        if group:
            spans = {name: [(0, len(lookup[name]))] for name in groups[group]}
        elif len(text) <= MAX_SPAN:
            spans = {locator: [(0, len(text))]}
        else:
            at = min(hits)
            spans = {locator: [(max(0, at-WINDOW_RADIUS), min(len(text), at+WINDOW_RADIUS))]}
        result.append(dict(spans=spans))
        if len(result) == MAX_IDENTITY_CONTEXTS:
            break
    return result


def _try_read_expansion(item, expansion, prepared, views):
    """Atomically add exact spans within the one shared reading limit."""
    previous = {locator: item['intervals'].get(locator, []) for locator in expansion}
    item['intervals'].update(expansion)
    candidate_parts = [{"locator": locator, "text": item['lookup'][locator][start:end],
                        "reading_start": start, "reading_end": end,
                        "original_characters": len(item['lookup'][locator]),
                        **({'pdf_table': copy.deepcopy(item['table_metadata'][locator])}
                           if locator in item['table_metadata'] else {})}
                       for locator, intervals in item['intervals'].items() for start, end in intervals]
    candidate = dict(item['view'], parts=candidate_parts)
    all_views = views + [candidate if entry is item else dict(entry['view'], parts=entry['view']['parts'])
                         for entry in prepared]
    if len(canonical(all_views)) > CONTEXT_LIMIT:
        item['intervals'].update(previous)
        return False
    item['view']['parts'] = candidate_parts
    return True


def read(task, requests, expected_binding, artifact_ids=None, *, reader_version=None):
    """Select bounded spans and expose exact omissions without changing the archive."""
    reader_version = reader_version_for(task) if reader_version is None else reader_version
    if type(reader_version) is not int or reader_version not in SUPPORTED_READER_VERSIONS:
        raise ContractError('原件读取版本未登记，不自动套用其他版本')
    if reader_version == 7 and (type(task.get('financial_input_reading_priority_version')) is not int
            or task['financial_input_reading_priority_version'] not in (2, 3) or not financial_reader_terms(task)):
        raise ContractError('身份上下文读取只用于显式财务方法，不升级普通任务或批准证券身份')
    profile = _base_reader_version(task) if reader_version == 7 else reader_version
    if profile==4 and not fee_reader_terms(task):
        raise ContractError('费用读取版本只适用于已登记该策略及费用方法的当前任务，不升级其他读取')
    if profile==5 and not statistical_reader_terms(task):
        raise ContractError('统计指标优先读取仅适用于明确登记策略且用户提出指标需求的任务')
    concept_rule=concept_reader_rule(task) if profile==6 else None
    if profile==6 and concept_rule is None:
        raise ContractError('概念原文读取须有当前任务锁定的知识检索规则，不升级旧读取')
    records = _records(task, artifact_ids)
    selection = _validate_selection(task, requests, expected_binding, records)
    by_id = {r["id"]: r for r in records}
    for selected in selection:
        _verify_original_text(by_id[selected["artifact_id"]])
    full = {v["id"]: v for v in source_views(task) if v["id"] in by_id}
    views, coverage, limitations = [], [], []
    mixed_atomic = _mixed_atomic_enabled(task)
    atomic_ids = ({view['id'] for view in _mixed_atomic_views(task, records, list(full.values()))}
                  if mixed_atomic else None)
    # API snapshots are atomic: do not truncate columns or silently shift rows.
    for record in records:
        if record["kind"] != "api":
            continue
        view = full[record["id"]]
        included = (record['id'] in atomic_ids if mixed_atomic
                    else len(canonical(views + [view])) <= CONTEXT_LIMIT // 4)
        if included:
            views.append(view)
        else:
            limitations.append("接口快照" + record["id"] +
                ("不在本轮可提取完整API集合或超过完整结构预算，未提供给模型，不可引用未展示字段。" if mixed_atomic else
                 "超过本轮完整结构读取预算，未提供给模型，不可引用未展示字段。"))
        coverage.append({"artifact_id": record["id"], "snapshot_hash": view["snapshot_hash"],
                         "mode": "full_api" if included else "not_read", "included": included})
        if mixed_atomic:
            coverage[-1]['content_version'] = record['content_version']
    selections = {r["artifact_id"]: r for r in selection}
    prepared = []
    for record in records:
        if record["kind"] == "api":
            continue
        view = copy.deepcopy(full[record["id"]])
        parts = view.pop("parts")
        view["parts"] = []
        request = selections.get(record["id"])
        method_terms=financial_reader_terms(task) if profile>=3 and request else []
        if profile>=4 and request:method_terms=fee_reader_terms(task)+method_terms
        concept_terms=concept_reader_queries(concept_rule,parts) if request else []
        required_terms=statistical_reader_terms(task) if profile in (5,6) and request else []
        method_terms=concept_terms+method_terms
        queries=list(dict.fromkeys(required_terms+(method_terms if profile>=4 else [])+
            (request['queries'] if request else [])+(method_terms if profile<4 else [])))
        candidates = {"publication": [], "query": [], "exception": [], "explicit": []}
        counts = {"publication": 0, "query": 0, "exception": 0}
        lookup = {p["locator"]: p["text"] for p in parts}
        table_metadata = {p['locator']: _reading_table_metadata(task,p['pdf_table']) for p in parts if 'pdf_table' in p}
        groups, part_groups = {}, {}
        if reader_version >= 2:
            for part in parts:
                group = _table_group(part)
                if group:
                    groups.setdefault(group, []).append(part['locator'])
                    part_groups[part['locator']] = group
        if request:
            buckets = {key: {} for key in counts}
            tails = {key: [] for key in counts}
            total_text = sum(len(p["text"]) for p in parts)
            offset = 0
            for part in parts:
                for category, terms in (("publication", PUBLICATION_TERMS), ("query", queries), ("exception", EXCEPTION_TERMS)):
                    count, hits = _hits(part["text"], terms)
                    counts[category] += count
                    for start, end, term in hits:
                        candidate = (part["locator"], max(0, start - WINDOW_RADIUS), min(len(part["text"]), end + WINDOW_RADIUS), term)
                        bucket = min(31, (offset + start) * 32 // max(total_text, 1))
                        buckets[category].setdefault(bucket, candidate)
                        tails[category] = (tails[category] + [candidate])[-8:]
                offset += len(part["text"])
            for category in counts:
                candidates[category] = list(dict.fromkeys(list(buckets[category].values()) + tails[category]))
            if reader_version >= 2:
                candidates['query'], term_counts = _query_candidates(parts, queries)
            for part in request["parts"]:
                candidates["explicit"].append((part["part"], part["start"], part["end"], "指定页段"))
        prepared.append({"record": record, "view": view, "lookup": lookup, "candidates": candidates,
                         "counts": counts, "intervals": {}, "included_hits": {k: 0 for k in candidates},
                         'table_metadata': table_metadata, 'groups': groups, 'part_groups': part_groups,
                         'term_counts': term_counts if reader_version >= 2 and request else {},
                         'provided_terms': {}, 'unread_groups': set(),'method_terms':method_terms,
                         'required_terms':required_terms,'concept_terms':concept_terms})
        if reader_version >= 3 and request:
            prepared[-1]['input_groups'] = _financial_input_groups(task, record, full[record['id']],
                                                                  lookup, groups, part_groups)
        if reader_version == 7 and request:
            prepared[-1]['identity_groups'] = _identity_context_groups(parts, lookup, groups, part_groups)
        if request:
            from .financial_data import financial_reading_groups
            prepared[-1]['financial_data_groups'] = financial_reading_groups(task, record)
    # Preserve registered input tables and their actual header/unit locations
    # before optional keyword windows consume the shared budget. Round-robin
    # documents; an overlarge group is omitted explicitly, never truncated.
    for index in range(max((len(item.get('input_groups', [])) for item in prepared), default=0)):
        for item in prepared:
            if index < len(item.get('input_groups', [])):
                group = item['input_groups'][index]
                expansion = {locator: [(0, len(item['lookup'][locator]))] for locator in group['parts']}
                _try_read_expansion(item, expansion, prepared, views)
    # Supplemental data stays with its actual source/unit/period pages. The
    # same shared size limit applies; never truncate a table to imply coverage.
    for index in range(max((len(item.get('financial_data_groups', [])) for item in prepared), default=0)):
        for item in prepared:
            if index < len(item.get('financial_data_groups', [])):
                group = item['financial_data_groups'][index]
                expansion = {locator:[(0,len(item['lookup'][locator]))] for locator in group['parts']}
                _try_read_expansion(item, expansion, prepared, views)
    # Identity context uses the same remaining window, after registered inputs
    # and before optional keyword fragments. Omissions stay explicit.
    for index in range(MAX_IDENTITY_CONTEXTS if reader_version == 7 else 0):
        for item in prepared:
            if index < len(item.get('identity_groups', [])):
                group = item['identity_groups'][index]
                expansion = {locator: _merge(item['intervals'].get(locator, [])+spans)
                             for locator, spans in group['spans'].items()}
                group['included'] = _try_read_expansion(item, expansion, prepared, views)
    # Round-robin both documents and channels. Positive query frequency cannot
    # consume the entire window before a late-page exception is considered.
    indices = {(i, category): 0 for i in range(len(prepared)) for category in ("publication", "explicit", "query", "exception")}
    exhausted = set()
    while len(exhausted) < len(indices):
        for category in ("publication", "explicit", "query", "exception"):
            for i, item in enumerate(prepared):
                key = (i, category)
                if key in exhausted:
                    continue
                at = indices[key]
                hits = item["candidates"][category]
                if at >= len(hits):
                    exhausted.add(key)
                    continue
                part, start, end, term = hits[at]
                indices[key] += 1
                group = item['part_groups'].get(part) if reader_version >= 2 and category == 'query' else None
                expansion = {locator: [(0, len(item['lookup'][locator]))] for locator in item['groups'][group]} if group else {
                    part: _merge(item['intervals'].get(part, [])+[(start, end)])}
                # Merged spans remain exact originals; a full very long
                # paragraph is still allowed only within the shared budget.
                if not _try_read_expansion(item, expansion, prepared, views):
                    if group:
                        item['unread_groups'].add(group)
                    # Future candidates may overlap included spans and be cheap;
                    # keep checking, but no binary/text truncation to force fit.
                else:
                    item["included_hits"][category] += 1
                    if reader_version >= 2 and category == 'query':
                        item['provided_terms'][term] = item['provided_terms'].get(term, 0)+1
                        if group:
                            item['unread_groups'].discard(group)
    for item in prepared:
        record, view = item["record"], item["view"]
        if view["parts"]:
            views.append(view)
        total = sum(len(text) for text in item["lookup"].values())
        included = sum(end - start for spans in item["intervals"].values() for start, end in spans)
        entry = {"artifact_id": record["id"], "snapshot_hash": view["snapshot_hash"],
                 "name": view.get("name"), "mode": "full_text" if included == total and total else "selected" if included else "not_read",
                 "total_parts": len(item["lookup"]), "read_parts": sum(bool(spans) for spans in item["intervals"].values()),
                 "total_characters": total, "read_characters": included, "omitted_characters": total - included,
                 "hit_counts": item["counts"], "candidate_counts": {k: len(v) for k, v in item["candidates"].items()},
                 "provided_candidate_counts": item["included_hits"]}
        coverage.append(entry)
        if item.get('financial_data_groups'):
            entry['financial_data_reading'] = dict(version=1, financial_approval=False, completeness_approved=False,
                groups=[dict(field=group['field'],parts=group['parts'],read_in_full=all(
                    item['intervals'].get(locator) == [(0,len(item['lookup'][locator]))] for locator in group['parts']))
                    for group in item['financial_data_groups']])
        if reader_version == 7:
            entry['identity_context_reading'] = dict(version=1, profile_version=profile,
                terms=list(IDENTITY_CONTEXT_TERMS), maximum_contexts=MAX_IDENTITY_CONTEXTS,
                financial_approval=False, identity_approval=False, absence_is_proof=False,
                groups=[dict(parts=list(group['spans']),
                             spans={locator: [list(span) for span in spans]
                                    for locator, spans in group['spans'].items()},
                             read_in_full=group['included'])
                        for group in item.get('identity_groups', [])])
        if item.get('input_groups'):
            entry['registered_input_reading'] = dict(version=task['financial_input_reading_priority_version'], financial_approval=False,
                absence_is_proof=False, groups=[dict(metrics=group['metrics'], parts=group['parts'],
                    read_in_full=all(item['intervals'].get(locator) == [(0, len(item['lookup'][locator]))]
                                     for locator in group['parts'])) for group in item['input_groups']])
        if reader_version >= 2:
            entry['query_coverage'] = [{'term': term, 'hit_count': count,
                'provided_candidates': item['provided_terms'].get(term, 0)} for term, count in item['term_counts'].items()]
            entry['unread_table_groups'] = sorted(item['unread_groups'])
            entry['table_group_scope'] = 'geometry_only_not_financial_approval'
            if reader_version>=3:
                entry['method_queries']={'version':1,'terms':item['method_terms'],
                    'shared_query_candidate_limit':MAX_QUERY_CANDIDATES,'financial_approval':False}
            if profile==5 or profile==6 and item['required_terms']:
                entry['required_indicator_queries'] = {'version': 1,
                    'origin': 'explicit_user_question',
                    'terms': [{'term': term, 'hit_count': item['term_counts'].get(term, 0),
                               'provided_candidates': item['provided_terms'].get(term, 0)}
                              for term in item['required_terms']],
                    'shared_query_candidate_limit': MAX_QUERY_CANDIDATES,
                    'scope': 'literal_locator_not_period_sector_or_value_verification',
                    'financial_approval': False}
            if item['unread_groups']:
                limitations.append((view.get('name') or '留存原件')+'部分命中表格无法在本轮预算内完整读取，未截断表格后当作完整上下文。')
        if included < total:
            limitations.append((view.get("name") or "留存原件") + "本轮读取" + str(included) + "/" + str(total) +
                               "字；未读部分仍在原件，关键词定位不证明已穷尽事实、反例或附注。")
    bundle = {"schema": "researchhelper.source-reading", "reader_version": reader_version,
              "binding": binding(task), "selection": selection, "artifact_ids": [r["id"] for r in records],
              "views": views, "coverage": coverage, "limitations": limitations}
    if mixed_atomic:
        bundle['mixed_atomic_api_reading'] = dict(version=1,
            allowed_artifact_ids=[record['id'] for record in records],
            atomic_artifacts=[dict(artifact_id=view['id'], snapshot_hash=view['snapshot_hash'],
                content_version=view['content_version']) for view in views if view['kind'] == 'api'],
            source_approval=False, financial_approval=False)
    if profile==6:
        # Keep one rule authority in the archived reading. Per-source terms are
        # already recorded in method_queries; do not multiply an identical
        # navigation explanation across every extraction source contract.
        bundle['concept_navigation']=dict(rule=copy.deepcopy(concept_rule),
            queries=[dict(artifact_id=item['record']['id'],terms=item['concept_terms']) for item in prepared],
            shared_query_candidate_limit=MAX_QUERY_CANDIDATES,financial_approval=False,
            completeness_approved=False)
    bundle["reading_hash"] = fingerprint(bundle)
    return bundle


def validate_reading(task, bundle):
    if not isinstance(bundle, dict) or bundle.get("binding") != binding(task):
        raise ContractError("读取记录不属于当前任务、版本或输入")
    rebuilt = read(task, bundle.get("selection"), bundle["binding"], bundle.get("artifact_ids"),
                   reader_version=bundle.get('reader_version'))
    if rebuilt != bundle:
        raise ContractError("读取范围、摘录或覆盖记录与原件不一致")


def retained_reading(task, reading_hash):
    """Resolve a saved reading and revalidate it before exposing its spans."""
    if not isinstance(reading_hash, str) or not reading_hash:
        raise ContractError('宿主读取标识须为已保存的有效哈希')
    matches = [record for record in task.get('source_readings', []) if record.get('reading_hash') == reading_hash]
    if len(matches) != 1:
        raise ContractError('宿主读取记录不存在、不唯一或不属于当前任务')
    validate_reading(task, matches[0])
    return copy.deepcopy(matches[0])


def validate_read_specs(task, specs, bundle):
    """Model claims must be grounded in actually exposed text, not unseen pages."""
    validate_reading(task, bundle)
    exposed = {v["id"]: v for v in bundle["views"]}

    def anchor(reference, quote, part, index, field):
        view = exposed.get(reference)
        if not view or not isinstance(quote, str) or not quote or not any(
                p["locator"] == part and quote in p["text"] for p in view.get("parts", [])):
            raise ReadingAnchorError(index, field)

    for index, spec in enumerate(specs):
        view = exposed.get(spec.get("artifact_id"))
        if not view:
            raise ContractError("模型不能引用本轮未读取的原件")
        # The model's API selection intentionally omits original values and
        # dates. Decode its bound cell before validating the observation time,
        # exactly as the normal evidence path does. No legacy field fallback.
        if view['kind']=='api' and 'api_value_reference' in spec:
            from .evidence import expand_api_value_reference,expand_api_context_reference
            artifact=next((record for record in original_sources(task)
                if record['id']==spec['artifact_id']),None)
            if artifact is None:
                raise ContractError('API数值引用不属于本任务实际读取的原件')
            spec=expand_api_value_reference(task,artifact,spec)
            if 'api_context_reference' in spec:
                spec=expand_api_context_reference(task,artifact,spec)
        if view["kind"] != "api":
            locator = spec.get("record_locator", {})
            anchor(spec["artifact_id"], spec.get("text"), locator.get("part"), index, 'text')
            for field in ('financial_column', 'financial_amount_column'):
                column = locator.get(field)
                if column is not None:
                    if not isinstance(column, dict):
                        raise ContractError('财务列定位须为实际读取的原件表头与金额行')
                    anchor(spec['artifact_id'], column.get('row_quote'), locator.get('part'), index, field+'.row_quote')
                    anchor(spec['artifact_id'], column.get('header_quote'), column.get('header_part'), index, field+'.header_quote')
                    if field == 'financial_amount_column' and column.get('report_period_quote') is not None:
                        anchor(spec['artifact_id'], column.get('report_period_quote'), column.get('report_period_part'),
                               index, field+'.report_period_quote')
                    if field == 'financial_amount_column':
                        anchor(spec['artifact_id'], column.get('unit_quote'), locator.get('unit_part'),
                               index, field+'.unit_quote')
                        anchor(spec['artifact_id'], column.get('period_basis_quote'), column.get('period_basis_part'),
                               index, field+'.period_basis_quote')
            if locator.get("unit_part"):
                anchor(spec["artifact_id"], spec.get("raw_unit"), locator["unit_part"], index, 'raw_unit')
            if isinstance(spec.get('availability'), dict) and spec['availability'].get('kind') == 'current_original':
                anchor(spec['artifact_id'], locator.get('period_quote'), locator.get('period_part'), index, 'period_quote')
        from .contracts import validate_evidence_usage
        document_only = validate_evidence_usage(spec, task)
        from .evidence import validate_current_snapshot
        current_only = validate_current_snapshot(spec, task)
        publication = spec.get("publication", {}).get("basis_ref")
        if not document_only and not current_only and (not isinstance(publication, dict) or publication.get("kind") != "directory"):
            publication = publication if isinstance(publication, dict) else {}
            anchor(publication.get("artifact_id"), publication.get("quote"), publication.get("part"), index, 'publication')
        for field, basis in spec.get("calculation_context_basis", {}).items():
            if basis.get("kind") == "material":
                known = {'security_id','currency','price_basis','price_kind','window_start','window_end','observation_date',
                    'corporate_action_status','entity','metric','period','accounting_scope','accounting_standard'}
                anchor(basis.get("artifact_id"), basis.get("quote"), basis.get("part"), index,
                       'calculation_context_basis.' + (field if field in known else '*'))
        period_basis = spec.get('reporting_period_basis')
        if period_basis is not None:
            anchor(period_basis.get('artifact_id'), period_basis.get('quote'),
                   period_basis.get('part'), index, 'reporting_period_basis')
