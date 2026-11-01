"""One bounded extraction-format observation; never repair or resume a study."""
import json
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests import probe_financial_shared_selectors_live as extraction
from researchhelper.model import ModelService


def container_diagnostic(payload):
    """Return only fixed shape names and counts, never response characters."""
    try:
        value = json.loads(payload)
        messages = [item for item in value.get('output', [])
                    if isinstance(item, dict) and item.get('type') == 'message']
        parts = [part for item in messages for part in item.get('content', [])
                 if isinstance(part, dict) and part.get('type') == 'output_text']
        if len(parts) != 1 or not isinstance(parts[0].get('text'), str):
            return {'kind': 'unrecognized_completion_envelope', 'body_retained': False}
        text = parts[0]['text'].strip()
        if not text:
            kind = 'empty_text'
        elif text.startswith('```json\n') and text.endswith('\n```'):
            kind = 'single_json_fence'
        elif text.startswith('{'):
            kind = 'json_object_start'
        elif text.startswith('['):
            kind = 'json_array_start'
        elif text.startswith('```'):
            kind = 'other_fenced_start'
        else:
            kind = 'non_json_start'
        return {'kind': kind, 'text_characters': len(parts[0]['text']), 'body_retained': False}
    except (ValueError, TypeError, AttributeError):
        return {'kind': 'unrecognized_completion_envelope', 'body_retained': False}


def run():
    case = 'financial-response-container-component-20261016-v1'
    retained = ROOT / 'result/acceptance/one-sentence-research-5wixw_pf/task.json'
    observed = []
    original = ModelService._request_bytes

    def inspect(service, task, request, started, timeout, record):
        payload = original(service, task, request, started, timeout, record)
        observed.append(container_diagnostic(payload))
        return payload

    with patch.object(ModelService, '_request_bytes', inspect):
        result = extraction.run(case=case, retained=retained, output_prefix='financial-response-container-')
    receipts = [path for path in (ROOT / 'result/acceptance').glob('financial-response-container-*/receipt.json')
                if json.loads(path.read_text()).get('case') == case]
    if len(receipts) != 1:
        raise RuntimeError('A unique bounded component receipt is required')
    result['response_container_observations'] = observed
    assert len(observed) <= 1 and result['new_model_calls'] <= 1
    result['historical_failed_response_reconstructed'] = False
    receipts[0].write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'status': result['status'], 'observed': observed,
                      'receipt': str(receipts[0])}), flush=True)
    return result


if __name__ == '__main__':
    run()
