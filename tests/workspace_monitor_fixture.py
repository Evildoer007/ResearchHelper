"""Project the unchanged live collector's status polling onto the real UI view.

Only the monitoring GET changes. The ordinary Chat submission, budget, final
canonical read, task binding, cancellation and all assertions remain intact.
"""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import subprocess
from unittest.mock import patch

from tests import ROOT
from tests.accept_one_sentence_research import NODE

ORIGINAL = ROOT / 'tests/accept_one_sentence_research.mjs'
POLL = 'const response=await context.request.get(route,{timeout:10000});'
VIEW_POLL = "const response=await context.request.get(route,{timeout:10000,headers:{'X-ResearchHelper-View':'workspace-v1'}});"


def browser_source(source):
    if source.count(POLL) != 1:
        raise ValueError('原轮询入口已变化，不能替换其他读取或断言')
    return source.replace(POLL, VIEW_POLL, 1)


@contextmanager
def workspace_monitor(output):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / 'result/acceptance') or not output.is_dir():
        raise ValueError('观察脚本必须生成在独立验收目录')
    before = hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()
    fixture = output / 'workspace-view-monitor.mjs'
    if fixture.exists():
        raise ValueError('不覆盖既有验收观察脚本')
    fixture.write_text(browser_source(ORIGINAL.read_text()))
    original_popen = subprocess.Popen
    metadata = dict(original_collector_sha256=before, status_view='workspace-v1',
        fixture=str(fixture), final_read='canonical', original_assertions_preserved=True,
        fixture_removed=False)
    def launch(arguments, *args, **kwargs):
        if isinstance(arguments, list) and arguments[:2] == [str(NODE), str(ORIGINAL)]:
            arguments = [arguments[0], str(fixture), *arguments[2:]]
        return original_popen(arguments, *args, **kwargs)
    try:
        with patch('tests.accept_one_sentence_research.subprocess.Popen', side_effect=launch):
            yield metadata
    finally:
        if hashlib.sha256(ORIGINAL.read_bytes()).hexdigest() != before:
            raise RuntimeError('原验收脚本已变化，不批准本次观察')
        if fixture.exists():
            if fixture.is_symlink() or fixture.read_text() != browser_source(ORIGINAL.read_text()):
                raise RuntimeError('临时观察脚本已被修改，保留文件，不自动清理')
            fixture.unlink()
        metadata['fixture_removed'] = not fixture.exists()
