"""Account-scoped workspace recovery with no research or provider calls."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from researchhelper.archive import ResearchArchive, WorkspaceDraftConflict
from researchhelper.contracts import ContractError
from researchhelper.service import make_server

TASK_ID = 'rh_' + '1' * 32
FILE = {'name': '原件😀.txt', 'size': 42, 'sha256': 'a' * 64}
DRAFT = {
    'activeTaskId': TASK_ID, 'workspaceMode': 'desk', 'assistantOpen': True,
    'deskStages': {TASK_ID: 3}, 'composerTexts': {TASK_ID: '待发送问题😀\n第二行'},
    'composerFiles': {'new': [FILE]},
    'pendingSubmissions': {TASK_ID: {'key': 'b' * 64, 'nonce': 'c' * 32, 'taskId': None, 'files': [FILE]}},
    'deskReadingPositions': {'reading': {'top': 23.5, 'ratio': .25, 'width': 700, 'height': 500, 'atBottom': False}},
    'reportEditions': {TASK_ID: {'version': 2, 'digest': 'd' * 64}},
    'reportStyles': {TASK_ID + ':1': {'theme': 'research', 'schemes': {'fig-1': 'multi_line'}}},
    'research-mode': 'historical', 'cutoff': '2026-07-20T12:00',
}


def test_draft_persists_without_modifying_research_rows(tmp_path):
    archive = ResearchArchive(tmp_path)
    # A draft must also migrate into an existing archive schema.
    with archive.connect() as db:
        db.execute('DROP TABLE workspace_drafts')
        db.execute('INSERT INTO tasks VALUES(?,?,?)', (TASK_ID, 1, '{"unchanged":true}'))
    archive = ResearchArchive(tmp_path)
    receipt = archive.save_workspace_draft('admin', 0, '1' * 32, DRAFT)
    assert receipt == {'revision': 1, 'write_id': '1' * 32}
    reopened = ResearchArchive(tmp_path)
    assert reopened.workspace_draft('admin') == dict(receipt, state=DRAFT)
    assert reopened.workspace_draft('sales') == {'revision': 0, 'write_id': None, 'state': {}}
    assert reopened.save_workspace_draft('admin', 0, '1' * 32, DRAFT) == receipt
    assert reopened.save_workspace_draft('sales', 0, '1' * 32, {'composerTexts': {'new': '销售草稿'}})['revision'] == 1
    assert reopened.save_workspace_draft('admin', 1, '2' * 32, {})['revision'] == 2
    assert reopened.workspace_draft('admin')['state'] == {}
    with reopened.connect() as db:
        assert db.execute('SELECT id, revision, body FROM tasks').fetchall() == [(TASK_ID, 1, '{"unchanged":true}')]


def test_two_writers_cannot_overwrite_the_same_revision(tmp_path):
    archives = [ResearchArchive(tmp_path), ResearchArchive(tmp_path)]
    def write(index):
        text = str(index)
        try:
            return archives[index].save_workspace_draft('admin', 0, text * 32, {'composerTexts': {'new': text}})
        except WorkspaceDraftConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = [receipt for receipt in pool.map(write, (0, 1)) if receipt]
    assert len(receipts) == 1
    saved = archives[0].workspace_draft('admin')
    assert saved == dict(receipts[0], state={'composerTexts': {'new': receipts[0]['write_id'][0]}})
    with pytest.raises(WorkspaceDraftConflict):
        archives[0].save_workspace_draft('admin', 1, saved['write_id'], {'composerTexts': {'new': '替换旧回执'}})


def test_follow_up_draft_round_trips_the_parent_revision(tmp_path):
    archive = ResearchArchive(tmp_path)
    pending = dict(DRAFT['pendingSubmissions'][TASK_ID], parent=dict(id=TASK_ID, version=2, revision=6))
    draft = dict(DRAFT, pendingSubmissions={TASK_ID: pending})
    archive.save_workspace_draft('admin', 0, '1'*32, draft)
    assert archive.workspace_draft('admin')['state'] == draft
    for parent in (dict(pending['parent'], id='rh_'+'2'*32), dict(pending['parent'], revision=True)):
        with pytest.raises(ContractError):
            archive.save_workspace_draft('admin', 1, '2'*32,
                dict(draft, pendingSubmissions={TASK_ID: dict(pending, parent=parent)}))
    assert archive.workspace_draft('admin')['state'] == draft


@pytest.mark.parametrize('state', [
    {'account': 'sales'}, {'materials': [FILE]}, {'composerTexts': {'constructor': '坏键'}},
    {'composerFiles': {'new': [dict(FILE, base64='b3JpZ2luYWw=')]}},
    {'composerFiles': {'new': [dict(FILE, sha256=None)]}}, {'composerFiles': {'new': [FILE] * 11}},
    {'pendingSubmissions': {'new': {'key': 'x', 'nonce': 'c' * 32, 'taskId': None, 'files': []}}},
    {'readingPositions': {'a': {'ratio': 1.01, 'top': 20, 'atBottom': False}}},
    {'reportEditions': {'another': {'version': 1, 'digest': 'b' * 64}}},
    {'reportEditions': {TASK_ID: {'version': True, 'digest': 'b' * 64}}},
    {'reportEditions': {TASK_ID: {'version': 1, 'digest': 'b' * 63}}},
    {'reportEditions': {TASK_ID: {'version': 1, 'digest': 'b' * 64, 'html': '<main></main>'}}},
    {'deskStages': {TASK_ID: 5}}, {'assistantOpen': 'true'}, {'workspaceMode': ['desk']},
])
def test_invalid_states_cannot_replace_saved_text(tmp_path, state):
    archive = ResearchArchive(tmp_path)
    archive.save_workspace_draft('admin', 0, '1' * 32, DRAFT)
    with pytest.raises(ContractError):
        archive.save_workspace_draft('admin', 1, '2' * 32, state)
    assert archive.workspace_draft('admin')['state'] == DRAFT


def test_local_http_binds_draft_to_authenticated_account_and_handles_unreadable_record(tmp_path):
    server = make_server(tmp_path, offline=True, authentication=True, preset_accounts=True)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = 'http://127.0.0.1:' + str(server.server_port)
    token = ''
    def request(route, body=None, *, cookie='', guard=True):
        headers = {'X-ResearchHelper-View': 'workspace-v2', 'Cookie': cookie}
        if body is not None:
            headers['Content-Type'] = 'application/json'
            if guard:
                headers['X-ResearchHelper-Token'] = token
        outgoing = Request(base + route, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            response = urlopen(outgoing, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response), response.headers.get('Set-Cookie', '').split(';')[0]
    try:
        token = request('/api/auth/status')[1]['token']
        _, _, admin = request('/api/auth/login', {'account': 'admin', 'password': '66666666', 'remember': False})
        _, _, sales = request('/api/auth/login', {'account': 'sales', 'password': '88888888', 'remember': False})
        payload = dict(account='admin', expected_revision=0, write_id='1' * 32, state=DRAFT)
        assert request('/api/workspace-draft')[0] == 401
        assert request('/api/workspace-draft', payload)[0] == 401
        assert request('/api/workspace-draft', payload, cookie=admin, guard=False)[0] == 400
        assert request('/api/workspace-draft', payload, cookie=sales)[0] == 403
        assert request('/api/workspace-draft', payload, cookie=admin)[:2] == (200, {'revision': 1, 'write_id': '1' * 32})
        assert request('/api/workspace-draft', dict(payload, state={}), cookie=admin)[0] == 409
        assert request('/api/workspace-draft', cookie=admin)[:2] == (200, {
            'account': 'admin', 'revision': 1, 'write_id': '1' * 32, 'state': DRAFT})
        assert request('/api/workspace-draft', cookie=sales)[:2] == (200, {
            'account': 'sales', 'revision': 0, 'write_id': None, 'state': {}})
        assert request('/api/bootstrap', cookie=admin)[1]['workspace_draft']['state'] == DRAFT
        assert request('/api/bootstrap', cookie=sales)[1]['workspace_draft']['state'] == {}
        with server.agent.archive.connect() as db:
            db.execute('UPDATE workspace_drafts SET body=? WHERE owner_account=?', ('broken JSON', 'admin'))
        status, bootstrap, _ = request('/api/bootstrap', cookie=admin)
        assert status == 200 and bootstrap['workspace_draft'] is None
        assert bootstrap['tasks'] == []
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
