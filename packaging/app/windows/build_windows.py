"""Build the RH WinForms/WebView2 host, embedded Python and Inno candidate.

Builds only on Windows x64, from the same assembled kernel as macOS/Skill.
Native restore is restricted to cached packages. No installation or publishing.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packaging'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.bootstrap import configure_imports
configure_imports()
from release_contract import VERSION, OUTPUT
from shared_payload import copy_runtime, copy_tree, hashes, runtime_identity
from check_build_tools import check, verify_icon
from windows_installer import installer_command, install_for_verification
from verify_windows import verify_backend


def run(command, cwd, environment):
    subprocess.run([str(part) for part in command], cwd=cwd, env=environment, check=True, timeout=900)


def windows(output=None):
    output = Path(output or OUTPUT).resolve()
    if platform.system() != 'Windows':
        raise RuntimeError('不能在macOS交叉构建或伪造Windows安装包')
    stage = (ROOT / 'result/build-staging').resolve()
    if output == stage or not output.is_relative_to(stage):
        raise RuntimeError('Windows候选只能写入项目构建目录')
    tools = check(ROOT)
    output.mkdir(parents=True, exist_ok=True)
    app = output / 'ResearchHelper-windows-x64'
    app.mkdir(exist_ok=False)
    resources = app / 'Resources'
    resources.mkdir()
    runtime = output / 'windows-runtime-source'
    copy_runtime(runtime / 'researchhelper')
    spec = importlib.util.spec_from_file_location('rh_common_freeze', ROOT / 'packaging/app/build_app.py')
    common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(common)
    environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(ROOT / '.cache/pyinstaller'),
                       DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1',
                       DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE='true', DOTNET_NOLOGO='1',
                       NUGET_CERT_REVOCATION_MODE='offline', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
               '--name', 'researchhelper-server', '--distpath', resources / 'backend',
               '--workpath', output / 'windows-pyinstaller-work', '--specpath', output,
               '--paths', runtime, '--paths', ROOT, '--hidden-import', 'pypdf', '--hidden-import', 'docx',
               '--hidden-import', 'xlrd']
    command += common.tinyshare_build_options()
    command += ['--add-data', str(runtime / 'researchhelper/VERSION') + os.pathsep + 'researchhelper']
    for module in ('wmi', 'win32com.client', 'pythoncom', 'pywintypes'):
        command += ['--hidden-import', module]
    for package in common.EXCLUDED_MODULES:
        if package != 'wmi':
            command += ['--exclude-module', package]
    command.append(ROOT / 'products/app/backend/entry.py')
    print('[Windows 1/5] 冻结共享研究内核与Python。', flush=True)
    run(command, ROOT, environment)
    copy_tree(ROOT / 'products/app/frontend', resources / 'app/web')
    copy_tree(ROOT / 'products/app/data', resources / 'app/data')
    copy_tree(ROOT / 'assets/vendor', resources / 'assets/vendor')
    common.LICENSE_PACKAGES += ('WMI', 'pywin32', 'pywin32-ctypes')
    common.licenses(resources)
    icon = verify_icon(ROOT)
    sdk = max(tools['sdk_versions'], key=lambda value: tuple(map(int, value.split('-')[0].split('.'))))
    (output / 'global.json').write_text(json.dumps({'sdk': {'version': sdk, 'rollForward': 'disable'}}))
    source = output / 'windows-native-source'
    copy_tree(ROOT / 'products/app/desktop/windows', source)
    project = source / 'ResearchHelper.Windows.csproj'
    empty_feed = output / 'nuget-offline-feed'
    empty_feed.mkdir()
    config = output / 'NuGet.Config'
    config.write_text('<configuration><packageSources><clear /></packageSources></configuration>')
    print('[Windows 2/5] 使用已有NuGet缓存构建同系列原生壳，不联网下载。', flush=True)
    run([tools['dotnet'], 'restore', project, '--runtime', 'win-x64', '--configfile', config,
         '--source', empty_feed, '-p:SelfContained=true', '-p:NuGetAudit=false',
         '-p:RestoreIgnoreFailedSources=true'], output, environment)
    run([tools['dotnet'], 'publish', project, '-c', 'Release', '-r', 'win-x64', '--self-contained', 'true',
         '--no-restore', '-o', app, '-p:PublishSingleFile=false', '-p:ApplicationIcon=' + str(icon),
         '-p:Version=' + VERSION], output, environment)
    # Preserve the WebView2 SDK notice with the self-contained native runtime.
    packages = Path(environment.get('NUGET_PACKAGES') or Path.home() / '.nuget/packages')
    package = packages / 'microsoft.web.webview2' / tools['webview2_sdk']
    notices = [path for path in package.rglob('*') if path.is_file() and ('LICENSE' in path.name.upper() or 'NOTICE' in path.name.upper())]
    if not notices:
        raise RuntimeError('本机WebView2 SDK缓存缺少许可文件，候选不发布')
    for path in notices:
        target = resources / 'licenses/WebView2' / path.relative_to(package)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    native = app / 'ResearchHelper.exe'
    print('[Windows 3/5] 检查WebView2 Runtime并验证包内服务。', flush=True)
    run([native, '--check-webview2'], app, environment)
    backend = resources / 'backend/researchhelper-server/researchhelper-server.exe'
    staged = verify_backend(backend, resources, output / 'windows-staged-backend-check')
    setup = output / f'ResearchHelper-{VERSION}-windows-x64-setup.exe'
    print('[Windows 4/5] 编译ResearchHelper独立安装器。', flush=True)
    run(installer_command(tools['inno_setup'], ROOT / 'packaging/app/windows/ResearchHelper.iss', app, setup, VERSION, icon), ROOT, environment)
    if not setup.is_file():
        raise RuntimeError('Inno Setup没有生成安装包')
    verification = output / 'windows-install-check'
    verification.mkdir()
    installed = verification / 'installed'
    print('[Windows 5/5] 临时安装验收，不写用户快捷方式或卸载注册。', flush=True)
    install_for_verification(setup, installed)
    if hashes(app) != hashes(installed):
        raise RuntimeError('临时安装物与候选载荷不一致')
    final = verify_backend(installed / 'Resources/backend/researchhelper-server/researchhelper-server.exe',
                           installed / 'Resources', verification / 'backend-check')
    manifest = {'version': VERSION, 'runtime': runtime_identity(ROOT), 'platform': 'windows', 'architecture': 'x64',
                'python_embedded': True, 'dotnet_self_contained': True,
                'webview2_runtime': 'installed prerequisite, not bundled',
                'signing': 'unsigned', 'installed_to_user': False, 'native_ui_verified': False,
                'installer_verified': True, 'backend_checks': [staged, final],
                'real_model_verified': False, 'real_provider_verified': False,
                'sdk_version': sdk, 'files': hashes(app),
                'installer_sha256': hashlib.sha256(setup.read_bytes()).hexdigest()}
    (output / 'windows-app-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return setup
