"""Explicit synthetic mapping originals for unit and source-contract tests."""
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import fingerprint, validate_evidence
from researchhelper.evidence import candidate_evidence, evidence_binding, extract_material, material_record


def synthetic_identity_basis(task, source, code, start, end):
    """Exercise both witness checks; never invent an approved mapping dict."""
    statement = (f'供应商：{source}；供应商代码：{code}；研究对象：{code}；'
                 f'映射有效期起点：{start}；映射有效期终点：{end}')
    published = '合成取证样例，仅用于软件检查。发布日期：2026年9月20日。'
    payload = (published + '\n\n' + statement).encode()
    record = material_record(task, extract_material('synthetic-identity.txt', payload), payload)
    task.setdefault('materials', []).append(record)
    ident = 'synthetic_identity_' + fingerprint([source, code, start, end])[:16]
    question = task['questions'][0]['id']
    review = dict(question_ids=[question], allowed_natures=['已实现事实'],
        conditions=['仅用于合成映射与字段单位的软件检查'], reason='同任务原件完整声明，不代表真实证券身份')
    spec = dict(id=ident, artifact_id=record['id'], object=code, text=statement,
        record_locator={'part': '段落2'}, period=start + '至' + end, nature='已实现事实',
        unit='不适用', question_ids=[question], published_at='2026-09-20',
        publication=dict(at='2026-09-20', precision='日', timezone='Asia/Shanghai', certainty='已确认',
            basis='合成原件明确列出的发布日期', basis_ref=dict(kind='material', artifact_id=record['id'],
                snapshot_hash=record['snapshot_hash'], part='段落1', quote=published)))
    proof = candidate_evidence(task, record, spec)
    proof = ResearchAnalyzer().assess(task, proof, review, task['materials'] + task.get('source_records', []))
    validate_evidence(proof, task)
    task.setdefault('evidence', []).append(proof)
    return dict(provider_code=code, object=code, basis=statement, valid_from=start, valid_to=end,
        basis_ref=dict(version=1, evidence_id=proof['id'], evidence_hash=evidence_binding(proof),
                       content_version=proof['content_version']))
