"""Read-only build preflight; no environment creation, package install or network."""
import importlib.metadata
from importlib import util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from release_contract import ROOT
from icon_assets import verify_icons


def build_platform():
    if platform.system() == 'Darwin' and platform.machine() == 'arm64':
        return 'macos'
    if platform.system() == 'Windows' and platform.machine().lower() in {'amd64', 'x86_64'} and sys.maxsize > 2**32:
        return 'windows'
    raise RuntimeError('构建须在Apple Silicon macOS或Windows x64上运行，不提供伪交叉编译')


def verify_environment(root=ROOT):
    target = build_platform()
    verify_icons(root)
    tools = json.loads((Path(root) / ('packaging/app/' + target + '/build-tools.json')).read_text())
    if sys.version_info[:2] != tuple(map(int, tools['python'].split('.'))):
        raise RuntimeError('Python版本不符合工具锁，要求' + tools['python'] + '，当前' + sys.version.split()[0])
    inventory = {}
    locks = ['core/requirements.lock', 'packaging/build-requirements.lock']
    if target == 'windows':
        locks.append('packaging/app/windows/requirements.lock')
    for relative in locks:
        for line in (Path(root) / relative).read_text().splitlines():
            if not line or line.startswith('#'):
                continue
            name, expected = line.split('==')
            try:
                actual = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError as error:
                raise RuntimeError('缺少锁定依赖：' + name + '==' + expected + '；请在已有环境恢复' + relative) from error
            if actual != expected:
                raise RuntimeError('依赖版本不一致：' + name + '要求' + expected + '，当前' + actual + '；锁文件：' + relative)
            inventory[name] = actual
    record = {'platform': target, 'python': sys.version.split()[0], 'architecture': platform.machine(),
              'dependencies': inventory, 'environments_created': 0, 'packages_installed': 0, 'network_requests': 0}
    if target == 'windows':
        spec = util.spec_from_file_location('rh_windows_tools', Path(root) / 'packaging/app/windows/check_build_tools.py')
        module = util.module_from_spec(spec)
        spec.loader.exec_module(module)
        record.update(module.check(root))
        return record
    missing = [tool for tool in tools['tools'] if not shutil.which(tool)]
    if missing:
        raise RuntimeError('缺少macOS构建工具：' + '、'.join(missing) + '；请先恢复已有开发工具')
    compiler = subprocess.check_output(['xcrun', '--find', 'swiftc'], text=True, timeout=15).strip()
    sdk = subprocess.check_output(['xcrun', '--show-sdk-path'], text=True, timeout=15).strip()
    return dict(record, native_icons='approved_hashed_assets', swiftc=compiler, sdk=sdk)
