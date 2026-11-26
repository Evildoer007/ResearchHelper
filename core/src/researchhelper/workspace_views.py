"""Workspace projections and the separate, account-scoped draft state."""
import re

from .contracts import MAX_BROWSER_INTEGER, ContractError, browser_json_values

SUMMARY_VERSION = 1
SUMMARY_KEYS = ('id', 'version', 'revision', 'question', 'entry', 'intent', 'objects',
                'status', 'cutoff', 'created_at', 'updated_at', 'thread_id', 'parent',
                'owner_account', 'fixture', 'synthetic', 'research_mode')
DELIVERY_KEYS = ('version', 'digest', 'html_sha256', 'created_at', 'saved_at')
NODE_KEYS = ('id', 'title', 'status')


def workspace_task_summary(task):
    """Saved list metadata only, never a substitute for research validation."""
    summary = {key: task[key] for key in SUMMARY_KEYS if key in task}
    summary.update(workspace_view_version=1, workspace_summary_version=SUMMARY_VERSION,
                   message_count=len(task['messages']) if isinstance(task.get('messages'), list) else 0)
    summary['nodes'] = [{key: node[key] for key in NODE_KEYS if key in node}
                        for node in (task.get('nodes') if isinstance(task.get('nodes'), list) else [])
                        if isinstance(node, dict)]
    delivery = task.get('delivery')
    summary['delivery'] = ({key: delivery[key] for key in DELIVERY_KEYS if key in delivery}
                           if isinstance(delivery, dict) else None)
    return browser_json_values(summary)


def valid_cached_summary(value, task_id, revision):
    """A corrupt/private-field cache falls back to a complete canonical read."""
    allowed = set(SUMMARY_KEYS) | {'workspace_view_version', 'workspace_summary_version',
                                 'message_count', 'nodes', 'delivery'}
    if (not isinstance(value, dict) or set(value) - allowed or value.get('id') != task_id
            or value.get('revision') != browser_json_values(revision)
            or type(value.get('workspace_summary_version')) is not int
            or value['workspace_summary_version'] != SUMMARY_VERSION
            or type(value.get('workspace_view_version')) is not int or value['workspace_view_version'] != 1
            or type(value.get('message_count')) is not int or value['message_count'] < 0
            or not isinstance(value.get('nodes'), list)):
        return False
    if any(not isinstance(node, dict) or set(node) - set(NODE_KEYS) for node in value['nodes']):
        return False
    delivery = value.get('delivery')
    return delivery is None or isinstance(delivery, dict) and not set(delivery) - set(DELIVERY_KEYS)


TASK_ID = re.compile(r'rh_[0-9a-f]{32}')
DRAFT_WRITE_ID = re.compile(r'[0-9a-f]{32}')
FILE_HASH = re.compile(r'[0-9a-f]{64}')


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _text_key(value):
    return isinstance(value, str) and bool(value) and value not in ('__proto__', 'prototype', 'constructor')


def _composer_key(value):
    return value == 'new' or _matches(TASK_ID, value)


def _object_map(value, check_value, check_key=_text_key):
    return isinstance(value, dict) and all(check_key(key) and check_value(item) for key, item in value.items())


def _bounded_number(value, upper=MAX_BROWSER_INTEGER):
    return type(value) in (int, float) and 0 <= value <= upper


def _reading_position(value):
    return (isinstance(value, dict) and {'top', 'ratio', 'atBottom'} <= value.keys()
            and not value.keys() - {'top', 'ratio', 'atBottom', 'width', 'height'}
            and type(value['atBottom']) is bool and _bounded_number(value['ratio'], 1)
            and all(_bounded_number(value[key]) for key in ('top', 'width', 'height') if key in value))


def _draft_files(value):
    return (isinstance(value, list) and len(value) <= 10
            and all(isinstance(file, dict) and file.keys() == {'name', 'size', 'sha256'}
                    and isinstance(file['name'], str) and bool(file['name'])
                    and type(file['size']) is int and 0 < file['size'] <= 25 * 1024 * 1024
                    and _matches(FILE_HASH, file['sha256']) for file in value)
            and sum(file['size'] for file in value) <= 50 * 1024 * 1024)


def _pending_submission(value):
    fields = {'key', 'nonce', 'taskId', 'files'}
    if not isinstance(value, dict) or not fields <= value.keys() <= fields | {'parent'}:
        return False
    if 'parent' in value:
        parent = value['parent']
        if (not isinstance(parent, dict) or parent.keys() != {'id', 'version', 'revision'}
                or not _matches(TASK_ID, parent['id'])
                or any(type(parent[key]) is not int or not 0 < parent[key] <= MAX_BROWSER_INTEGER
                       for key in ('version', 'revision'))):
            return False
    return (_matches(FILE_HASH, value['key']) and _matches(DRAFT_WRITE_ID, value['nonce'])
            and (value['taskId'] is None or _matches(TASK_ID, value['taskId'])) and _draft_files(value['files']))


def _pending_submissions(value):
    return (_object_map(value, _pending_submission, _composer_key)
            and all('parent' not in pending or pending['parent']['id'] == key for key, pending in value.items()))


def _report_style(value):
    return (isinstance(value, dict) and value.keys() == {'theme', 'schemes'}
            and _text_key(value['theme']) and _object_map(value['schemes'], _text_key))


def _report_edition(value):
    return (isinstance(value, dict) and value.keys() == {'version', 'digest'}
            and type(value['version']) is int and 1 <= value['version'] <= MAX_BROWSER_INTEGER
            and _matches(FILE_HASH, value['digest']))


def validate_workspace_draft(state):
    """Accept workspace state and file identities only. No original or task data."""
    validators = {
        'activeTaskId': lambda value: _matches(TASK_ID, value),
        'activeHistoryKey': _text_key,
        'workspaceMode': lambda value: value in ('chat', 'desk'),
        'assistantOpen': lambda value: type(value) is bool,
        'deskStages': lambda value: _object_map(value, lambda stage: type(stage) is int and 0 <= stage <= 4),
        'composerTexts': lambda value: _object_map(value, lambda text: isinstance(text, str), _composer_key),
        'composerFiles': lambda value: _object_map(value, _draft_files, _composer_key),
        'pendingSubmissions': _pending_submissions,
        'readingPositions': lambda value: _object_map(value, _reading_position),
        'deskReadingPositions': lambda value: _object_map(value, _reading_position),
        'reportEditions': lambda value: _object_map(value, _report_edition, lambda key: _matches(TASK_ID, key)),
        'reportProfiles': lambda value: _object_map(value, lambda profile: profile in ('standard', 'detailed')),
        'reportStyles': lambda value: _object_map(value, _report_style),
    }
    for field in ('entry', 'intent', 'objects', 'research-mode', 'cutoff', 'requests', 'models', 'seconds'):
        validators[field] = lambda value: isinstance(value, str)
    if (not isinstance(state, dict) or state.keys() - validators.keys()
            or not all(validators[field](value) for field, value in state.items())):
        raise ContractError('草稿格式无效，未覆盖已保存内容')


def valid_workspace_draft_write(revision, write_id):
    return type(revision) is int and 0 <= revision < MAX_BROWSER_INTEGER and _matches(DRAFT_WRITE_ID, write_id)
