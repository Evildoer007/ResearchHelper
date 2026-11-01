"""RH-only Inno installer compilation and disposable verification installation."""
import os
from pathlib import Path
import re
import shutil
import subprocess


def find_installer_compiler():
    explicit = os.environ.get('RESEARCHHELPER_ISCC', '').strip().strip('"')
    candidates = [Path(explicit)] if explicit else []
    if not explicit:
        if value := shutil.which('ISCC.exe'):
            candidates.append(Path(value))
        for name in ('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA'):
            if base := os.environ.get(name):
                candidates.extend((Path(base) / 'Inno Setup 6/ISCC.exe',
                                   Path(base) / 'Programs/Inno Setup 6/ISCC.exe'))
    for path in candidates:
        if path.is_absolute() and path.is_file():
            return path.resolve()
    raise RuntimeError('未找到Inno Setup 6.3或更新6.x。可通过RESEARCHHELPER_ISCC指定现有ISCC.exe，不会自动安装。')


def installer_command(compiler, script, app, output, version, icon):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', version)
    if not match or any(int(part) > 65535 for part in match.groups()):
        raise ValueError('安装包须使用有效的ResearchHelper版本')
    output = Path(output)
    numeric = '.'.join(str(int(part)) for part in match.groups()) + '.0'
    return [str(compiler), '/Qp', f'/DAppSource={app}', f'/DSetupOutput={output.parent}',
            f'/DSetupName={output.stem}', f'/DAppVersion={version}', f'/DAppNumericVersion={numeric}',
            f'/DAppIcon={icon}', str(script)]


def install_for_verification(installer, destination):
    if os.name != 'nt' or Path(destination).exists():
        raise RuntimeError('安装验收仅能在Windows的全新临时目录运行')
    log = Path(destination).parent / 'installer-verification.log'
    command = [str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
               '/VERIFYINSTALL=1', '/NOCLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS',
               f'/DIR={destination}', f'/LOG={log}']
    process = subprocess.Popen(command)
    try:
        if process.wait(timeout=180):
            raise RuntimeError('临时安装验收失败，正式安装和快捷方式未修改；检查installer-verification.log')
    except BaseException:
        if process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           check=True, capture_output=True, timeout=20)
            process.wait(timeout=10)
        raise
