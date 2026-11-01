"""Read-only Windows x64 tools/icon preflight, never download or configure a tool."""
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'packaging'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from windows_installer import find_installer_compiler
from icon_assets import verify_icons


def verify_icon(root=ROOT):
    return verify_icons(root)['ico']


def check(root=ROOT):
    root = Path(root)
    tools = json.loads((root / 'packaging/app/windows/build-tools.json').read_text())
    project = ET.parse(root / 'products/app/desktop/windows/ResearchHelper.Windows.csproj')
    declared = next((item.get('Version', '').strip('[]') for item in project.iter('PackageReference')
                     if item.get('Include') == 'Microsoft.Web.WebView2'), None)
    if declared != tools['webview2_sdk']:
        raise RuntimeError('WebView2项目版本与工具锁不一致')
    if platform.system() != 'Windows' or platform.machine().lower() not in ('amd64', 'x86_64') or sys.maxsize <= 2**32:
        raise RuntimeError('Windows原生安装包须在Windows x64机器构建')
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError('Tinyshare内置字节码要求现有64位Python3.12环境')
    verify_icon(root)
    dotnet = shutil.which('dotnet')
    if not dotnet or not shutil.which('powershell'):
        raise RuntimeError('缺少.NET SDK 8或Windows PowerShell，不会自动安装')
    result = subprocess.run([dotnet, '--list-sdks'], check=True, capture_output=True, text=True, timeout=20)
    sdks = [line.split()[0] for line in result.stdout.splitlines() if line.startswith(str(tools['dotnet_sdk_major']) + '.')]
    if not sdks:
        raise RuntimeError('需要已有.NET SDK 8及本机NuGet离线缓存')
    compiler = find_installer_compiler()
    # ISCC help does not consistently print a minor version. Read the file's
    # version metadata instead; the path is data, never interpolated script.
    result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                             '(Get-Item -LiteralPath $env:RESEARCHHELPER_ISCC_PROBE).VersionInfo.ProductVersion'],
                            env=dict(os.environ, RESEARCHHELPER_ISCC_PROBE=str(compiler)),
                            check=True, capture_output=True, text=True, timeout=20)
    version = re.search(r'(6\.\d+(?:\.\d+)?)', result.stdout)
    if not version or tuple(map(int, version.group(1).split('.')[:2])) < tuple(map(int, tools['inno_setup_minimum'].split('.'))):
        raise RuntimeError('Inno Setup须为6.3或更新的6.x版本')
    return {'dotnet': dotnet, 'sdk_versions': sdks, 'inno_setup': str(compiler),
            'webview2_sdk': declared,
            'inno_version': version.group(1), 'webview2_runtime': '待包内启动检查', 'nuget_mode': 'local_cache_only'}


if __name__ == '__main__':
    print(json.dumps(check(), ensure_ascii=False))
