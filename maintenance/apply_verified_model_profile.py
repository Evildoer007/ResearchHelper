"""Apply only the RH phase controls used by an independently checked App run."""
from datetime import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def apply(study_receipt, visual_receipt):
    from core.bootstrap import configure_imports
    configure_imports()
    from researchhelper.model import ModelSettings
    study_receipt, visual_receipt = map(lambda value: Path(value).resolve(), (study_receipt, visual_receipt))
    if any(not path.is_relative_to(ROOT / 'result/acceptance') or not path.is_file()
           for path in (study_receipt, visual_receipt)):
        raise RuntimeError('Only explicit local acceptance records are eligible')
    study, visual = (json.loads(path.read_text()) for path in (study_receipt, visual_receipt))
    controls = {'extraction_mode': 'direct', 'planning_effort': 'low', 'reasoning_effort': 'low'}
    if (not study.get('ordinary_chat_report_completed') or study.get('backend_mode') != 'readonly_dmg'
            or study.get('temporary_model_controls') != controls or not study.get('production_unchanged')
            or visual.get('status') != 'scoped_passed' or visual.get('task_id') != study.get('task_id')):
        raise RuntimeError('The exact App run and independent report checks must both pass')
    settings = ModelSettings(Path.home() / 'Library/Application Support/ResearchHelper/research-v1')
    private = settings.read(private=True)
    before = settings.read()
    if not private.get('api_key') or not settings.supports_direct_extraction(private):
        raise RuntimeError('This profile is limited to the configured official DeepSeek service')
    output = ROOT / 'result/maintenance/model-profile' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True, exist_ok=False)
    record = dict(status='preparing', study_receipt=str(study_receipt), visual_receipt=str(visual_receipt),
        before=before, controls=controls, model_changed=False, endpoint_changed=False,
        credential_value_changed=False, external_calls=0, native_window_opened=False)
    target = output / 'receipt.json'
    try:
        settings.save(dict(private, **controls))
        after = settings.read(private=True)
        assert all(after[key] == private[key] for key in ('api_key', 'endpoint', 'model'))
        assert all(after[key] == value for key, value in controls.items())
        record.update(status='applied_verified_phase_profile', after=settings.read())
        after.clear()
    except BaseException:
        settings.save(private)
        record['status'] = 'rolled_back'
        raise
    finally:
        private.clear()
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return {'status': record['status'], 'receipt': str(target)}


def apply_research_endpoint(study_receipt, visual_receipt):
    """Adopt the exact official endpoint proved by real frozen financial study.

    No key migration, provider/model switch, permissions change or live probe.
    The old phase-only entry remains unchanged for its existing receipts.
    """
    from core.bootstrap import configure_imports
    configure_imports()
    from researchhelper.model import ModelSettings
    paths = [Path(value).resolve() for value in (study_receipt, visual_receipt)]
    if any(not path.is_relative_to(ROOT/'result/acceptance') or not path.is_file() for path in paths):
        raise RuntimeError('Explicit local acceptance records are required')
    study, visual = [json.loads(path.read_text()) for path in paths]
    override = study.get('temporary_endpoint_override', {})
    endpoint = override.get('endpoint')
    eligible_endpoints = ('https://api.deepseek.com/beta/chat/completions',
                          'https://api.deepseek.com/responses')
    controls = {'extraction_mode':'direct', 'planning_effort':'low', 'reasoning_effort':'low'}
    if (study.get('backend_mode') != 'readonly_dmg' or not study.get('ordinary_chat_report_completed')
            or not study.get('financial_research_completed') or not study.get('production_unchanged')
            or study.get('temporary_model_controls') != controls or endpoint not in eligible_endpoints
            or override.get('production_endpoint_changed') is not False
            or any(study.get(key) for key in ('supplied_answers', 'supplied_evidence', 'supplied_plan', 'task_retry'))
            or visual.get('status') != 'scoped_passed' or visual.get('report_accepted') is not True
            or visual.get('task_id') != study.get('task_id')):
        raise RuntimeError('A complete frozen financial study and independent report review must both pass')
    settings = ModelSettings(Path.home()/'Library/Application Support/ResearchHelper/research-v1')
    private = settings.read(private=True)
    if private.get('endpoint') not in ('https://api.deepseek.com/chat/completions', *eligible_endpoints):
        raise RuntimeError('This profile only supports the already configured official DeepSeek service')
    calls = [entry for entry in study['ledger']['dispatches'] if entry['kind'] == 'model_calls']
    if not calls or any(entry.get('requested_model') != private['model'] for entry in calls):
        raise RuntimeError('The current configured model differs from the accepted study')
    if endpoint.endswith('/responses') and any(
            entry.get('completion_api') != 'deepseek_responses_v1' for entry in calls):
        raise RuntimeError('Every call must actually use the explicitly accepted Responses transport')
    output = ROOT/'result/maintenance/model-profile'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True, exist_ok=False)
    record = dict(status='preparing', study_receipt=str(paths[0]), visual_receipt=str(paths[1]),
        before=settings.read(), controls=controls, model_changed=False,
        endpoint_changed=private['endpoint'] != endpoint, credential_value_changed=False,
        external_calls=0, native_window_opened=False)
    target = output/'receipt.json'
    try:
        settings.save(dict(private, endpoint=endpoint, **controls))
        after = settings.read(private=True)
        assert all(after[key] == private[key] for key in ('api_key', 'model'))
        assert after['endpoint'] == endpoint and all(after[key] == value for key, value in controls.items())
        record.update(status='applied_verified_research_endpoint', after=settings.read())
        after.clear()
    except BaseException:
        settings.save(private)
        record['status'] = 'rolled_back'
        raise
    finally:
        private.clear()
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return {'status':record['status'], 'receipt':str(target)}
