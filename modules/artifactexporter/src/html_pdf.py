"""Print the saved offline HTML with the bundled macOS WebKit worker."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from .contracts import ContractError
from .layout import default_resource_root


def renderer_path(resources=None):
    root=Path(resources or default_resource_root()).resolve()
    development=(root/'products/app/frontend').is_dir()
    directory=root/'result/native-tools' if development else root/'app/tools'
    worker=directory/'ResearchHelperHTMLPDF'
    if directory.is_symlink() or worker.is_symlink() or not worker.is_file() or worker.resolve()!=worker:
        raise ContractError('PDF转换组件缺失，请恢复当前安装包；HTML已保留')
    if development:
        try:
            metadata=json.loads((directory/'renderer.json').read_text())
            source=root/'modules/artifactexporter/assets/macos/HTMLPDF.swift'
            current=(metadata.get('sha256')==hashlib.sha256(worker.read_bytes()).hexdigest() and
                metadata.get('source_sha256')==hashlib.sha256(source.read_bytes()).hexdigest())
        except (OSError,ValueError,TypeError,AttributeError):current=False
        if not current:raise ContractError('PDF开发组件不是当前版本，请使用一键构建更新；HTML已保留')
    return worker


def render_saved_html(source,*,expected_sha256,resources=None):
    source=Path(source).resolve()
    if hashlib.sha256(source.read_bytes()).hexdigest()!=expected_sha256:
        raise ContractError('HTML版本已变化，未转换')
    renderer=renderer_path(resources)
    # Some ZIP installers do not preserve execute bits. Run a private copy;
    # the installed Skill stays read-only and the worker is removed afterwards.
    with tempfile.TemporaryDirectory(prefix='.pdf-render-',dir=source.parent) as folder:
        folder=Path(folder);executable=folder/'ResearchHelperHTMLPDF';target=folder/'report.pdf'
        shutil.copyfile(renderer,executable);executable.chmod(0o755)
        try:
            result=subprocess.run([str(executable),str(source),str(target)],cwd=folder,
                capture_output=True,text=True,timeout=40,
                env={key:value for key,value in os.environ.items() if key not in {'DYLD_INSERT_LIBRARIES','PYTHONPATH'}})
            state=json.loads(result.stdout) if result.stdout else {}
        except (OSError,subprocess.TimeoutExpired,ValueError) as error:
            (source.parent/'pdf-conversion.json').write_text(json.dumps(dict(status='failed',engine='webkit',error_type=type(error).__name__),ensure_ascii=False))
            raise ContractError('PDF转换未完成，HTML已保留') from error
        if (result.returncode or state.get('status')!='saved' or state.get('engine')!='webkit' or
                any(state.get(key) is not False for key in ('foreground_opened','system_panels_shown','physical_job_sent')) or
                not target.is_file()):
            (source.parent/'pdf-conversion.json').write_text(json.dumps(dict(status='failed',engine='webkit',exit_code=result.returncode,worker=state,diagnostic=result.stderr[-2000:]),ensure_ascii=False))
            raise ContractError('PDF未保存，HTML已保留')
        if hashlib.sha256(source.read_bytes()).hexdigest()!=expected_sha256:
            raise ContractError('HTML版本已变化，未保存PDF')
        data=target.read_bytes()
        if not data.startswith(b'%PDF-') or len(data)<1024:raise ContractError('PDF内容不可用，HTML已保留')
        return data
