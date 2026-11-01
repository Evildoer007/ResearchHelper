from __future__ import annotations
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packaging'))
from core.bootstrap import configure_imports
configure_imports()
from release_contract import VERSION, OUTPUT
from shared_payload import hashes, copy_tree, copy_runtime, runtime_identity, copy_browser_notices, copy_report_design
from icon_assets import verify_icons
from build_progress import progress
PYTHON = Path(sys.executable)
DMG_FILESYSTEM = "HFS+"
TINYSHARE_BYTECODE = ("__init__.pyc", "config.pyc", "token_utils.pyc", "formula.pyc", "cache_utils.pyc", "device_id.pyc")
TINYSHARE_HIDDEN_IMPORTS = ("tinyshare", "pandas", "numpy", "requests", "urllib3", "certifi",
                          "charset_normalizer", "idna", "dateutil", "pytz", "tzdata")
EXCLUDED_MODULES = ("PySide6", "PyQt6", "scipy", "sklearn", "matplotlib", "torch", "tensorflow",
                    "IPython", "pytest", "pyarrow", "fastparquet", "tables", "numexpr", "bottleneck",
                    "numba", "llvmlite", "sympy", "xarray", "wmi")
LICENSE_PACKAGES = ("pypdf", "pdfplumber", "pdfminer.six", "pypdfium2", "cryptography", "cffi", "pycparser",
                    "python-docx", "lxml", "Pillow", "reportlab", "openpyxl", "et_xmlfile", "xlrd", "charset-normalizer", "PyInstaller", "altgraph", "macholib",
                    "tinyshare", "pandas", "numpy", "requests", "urllib3", "certifi", "idna", "python-dateutil", "six", "pytz", "tzdata")



def tinyshare_payload():
    """Select only the six installed CPython 3.12 distribution bytecode files.

    The vendor wrapper execs physical package-local bytecode. Module analysis
    cannot see those imports, and its copy-on-import fallback cannot run inside
    a signed read-only App. Never collect the package directory or user caches.
    """
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Tinyshare release payload requires the configured Python 3.12 runtime")
    spec = importlib.util.find_spec("tinyshare")
    if spec is None or not spec.origin:
        raise RuntimeError("Tinyshare is missing from the configured build environment")
    folder = Path(spec.origin).parent / "py312"
    if {path.name for path in folder.glob("*.pyc")} != set(TINYSHARE_BYTECODE):
        raise RuntimeError("Tinyshare py312 distribution does not match the six-file runtime contract")
    files = []
    for name in TINYSHARE_BYTECODE:
        path = folder / name
        content = path.read_bytes()
        if len(content) <= 16 or content[:4] != importlib.util.MAGIC_NUMBER:
            raise RuntimeError("Tinyshare bytecode does not match this CPython runtime: " + name)
        files.append((path, "tinyshare"))
    return files


def tinyshare_build_options():
    options = []
    for source, destination in tinyshare_payload():
        options += ["--add-data", str(source) + os.pathsep + destination]
    for module in TINYSHARE_HIDDEN_IMPORTS:
        options += ["--hidden-import", module]
    # timezone and TLS data are resources, not imports from the opaque bytecode.
    for package in ("tzdata", "certifi"):
        options += ["--collect-data", package]
    return options


def freeze_python_backend(command, *, runner=None):
    """Keep compiler caches outside the frozen source and shared user caches.

    The cache belongs to this synchronous freeze only. Collected App resources
    remain at the explicit distpath; source, logs and local delivery retain
    their project locations. Failure and cancellation unwind the same owner.
    """
    with tempfile.TemporaryDirectory(prefix="researchhelper-pyinstaller-") as directory:
        environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=directory,
                           RESEARCHHELPER_BUILD_PYTHON=str(PYTHON))
        return (runner or subprocess.run)(command, cwd=ROOT, check=True, env=environment)


def licenses(resources):
    """Keep installed dependency notices with the executable release."""
    directory = resources / "licenses"
    directory.mkdir(parents=True, exist_ok=True)
    inventory = copy_browser_notices(resources, root=ROOT)
    for name in LICENSE_PACKAGES:
        distribution = importlib.metadata.distribution(name)
        for relative in distribution.files or []:
            license_directory = any(part.upper() in {'LICENSES', 'LICENSE', 'NOTICES', 'NOTICE', 'BUILD_LICENSES'}
                                    for part in relative.parts[:-1])
            if not license_directory and not any(part in relative.name.upper() for part in ("LICENSE", "COPYING", "NOTICE")):
                continue
            source = distribution.locate_file(relative)
            if source.is_file():
                # Keep nested license paths distinct (notably NumPy vendored code).
                resource_path = Path(str(relative))
                if resource_path.is_absolute() or ".." in resource_path.parts:
                    raise RuntimeError("Dependency license path leaves the release: " + name)
                target = directory / name / resource_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                inventory.append({"package": name, "version": distribution.version, "file": str(target.relative_to(resources))})
    python_license = next((path for path in (Path(sys.base_prefix) / "lib/python3.12/LICENSE.txt",
                                            Path(sys.base_prefix) / "LICENSE.txt") if path.is_file()), None)
    if python_license is not None:
        shutil.copyfile(python_license, directory / "Python.txt")
        inventory.append({"package": "Python", "version": sys.version.split()[0], "file": "licenses/Python.txt"})
    (directory / "inventory.json").write_text(json.dumps(inventory, indent=2))


def swift_cache_directory(compiler, sdk, version):
    """Do not reuse location-bound PCH files after a candidate is copied.

    Swift/Clang caches embed their absolute module-cache path. A release copy
    may retain the old build cache for diagnostics, but the next invocation
    must select a new namespace for its output location and toolchain.
    """
    identity = json.dumps({"output": str(OUTPUT.resolve()),
        "compiler": str(Path(compiler).resolve()), "sdk": str(Path(sdk).resolve()),
        "version": version.strip()}, sort_keys=True).encode()
    return OUTPUT / "swift-cache" / hashlib.sha256(identity).hexdigest()[:20]


def swift_options():
    compiler = Path(subprocess.check_output(["xcrun", "--find", "swiftc"], text=True).strip())
    sdk = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
    version = subprocess.check_output(["xcrun", "swiftc", "--version"], text=True, stderr=subprocess.STDOUT)
    include = compiler.parent.parent / "include/swift"
    old, current = include / "module.modulemap", include / "bridging.modulemap"
    expected = 'moduleSwiftBridging{header"bridging"export*}'
    declaration = lambda path: re.sub(r"\s+", "", re.sub(r"//[^\n]*", "", path.read_text()))
    cache = swift_cache_directory(compiler, sdk, version)
    cache.mkdir(parents=True, exist_ok=True)
    # Pin the SDK used in the cache identity. Explicit dependency builds let
    # independent SDK modules compile concurrently instead of serially on
    # every cold candidate build. Keep older toolchains on their supported
    # implicit mode, and do not share location-bound PCH files across outputs.
    flags = ["-module-cache-path", str(cache), "-sdk", sdk]
    compiler_help = subprocess.check_output([str(compiler), "-help-hidden"], text=True,
                                            stderr=subprocess.STDOUT)
    if re.search(r"(?m)^\s*-explicit-module-build(?:\s|$)", compiler_help):
        flags += ["-explicit-module-build", "-j", "4"]
    if old.exists() and current.exists() and declaration(old) == declaration(current) == expected:
        # Compiler-only overlay for the observed duplicate CLT declaration.
        # No SDK or system files are changed.
        empty = OUTPUT / "redundant-module.modulemap"
        empty.write_text("// redundant legacy module hidden for this invocation\n")
        overlay = OUTPUT / "swift-overlay.json"
        overlay.write_text(json.dumps({"version": 0, "roots": [{"type": "file", "name": str(old), "external-contents": str(empty)}]}))
        flags += ["-vfsoverlay", str(overlay), "-Xcc", "-ivfsoverlay", "-Xcc", str(overlay)]
    return flags


def macos_pdf(output=None):
    """Compile one background HTML worker for App, Skill and demo."""
    global OUTPUT
    previous=OUTPUT;OUTPUT=Path(output) if output is not None else OUTPUT
    try:
        directory=OUTPUT/'native-tools';directory.mkdir(parents=True,exist_ok=True)
        executable=directory/'ResearchHelperHTMLPDF'
        source=ROOT/'modules/artifactexporter/assets/macos/HTMLPDF.swift'
        command=['xcrun','swiftc','-parse-as-library','-swift-version','5','-O','-target','arm64-apple-macosx12.0',
            *swift_options(),str(source),'-framework','AppKit','-framework','WebKit','-o',str(executable)]
        result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
        (OUTPUT/'pdf-compiler.txt').write_text(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError('HTML转PDF组件编译失败：'+result.stderr[-1800:])
        return executable
    finally:OUTPUT=previous


def macos(output=None, *, pdf_renderer=None):
    global OUTPUT
    OUTPUT = Path(output) if output is not None else OUTPUT
    if sys.platform != "darwin":
        raise RuntimeError("macOS builder requires macOS")
    icons = verify_icons(ROOT)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bundle = OUTPUT / "ResearchHelper.app"
    resources = bundle / "Contents/Resources"
    binaries = bundle / "Contents/MacOS"
    resources.mkdir(parents=True, exist_ok=True)
    binaries.mkdir(parents=True, exist_ok=True)
    # Freeze the same flat runtime assembled for Skill and demo. PyInstaller
    # does not reliably infer development-only package path extensions.
    runtime = OUTPUT / 'runtime-source'
    progress('App 1/5', '冻结共享研究内核与Python运行时')
    copy_runtime(runtime / 'researchhelper')
    command = [str(PYTHON), "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
               "--name", "researchhelper-server", "--distpath", str(resources / "backend"),
               "--workpath", str(OUTPUT / "pyinstaller-work"), "--specpath", str(OUTPUT),
               '--paths', str(runtime), '--paths', str(ROOT), "--hidden-import", "pypdf", "--hidden-import", "docx", "--hidden-import", "xlrd", "--hidden-import", "reportlab.pdfbase.cidfonts", "--hidden-import", "openpyxl", "--collect-data", "reportlab"]
    command += ['--add-data', str(runtime / 'researchhelper/VERSION') + os.pathsep + 'researchhelper']
    command += tinyshare_build_options()
    for package in EXCLUDED_MODULES:
        command += ["--exclude-module", package]
    command.append(str(ROOT / 'products/app/backend/entry.py'))
    freeze_python_backend(command)
    progress('App 2/5', '复制界面、离线案例与依赖许可')
    copy_tree(ROOT / 'products/app/frontend', resources / 'app/web')
    copy_report_design(resources / 'app/web', root=ROOT)
    from shared_payload import copy_pdf_renderer
    copy_pdf_renderer(resources, pdf_renderer, root=ROOT)
    copy_tree(ROOT / 'products/app/data', resources / 'app/data')
    (resources / "assets/vendor").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "assets/vendor/echarts.min.js", resources / "assets/vendor/echarts.min.js")
    shutil.copyfile(ROOT / "assets/vendor/NOTICE.md", resources / "assets/vendor/NOTICE.md")
    licenses(resources)
    executable = binaries / "ResearchHelper"
    progress('App 3/5', '编译Swift原生壳')
    native = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5", "-O", "-target", "arm64-apple-macosx12.0",
                             *swift_options(), str(ROOT / 'products/app/desktop/macos/ResearchHelperApp.swift'),
                             "-framework", "AppKit", "-framework", "WebKit", "-o", str(executable)],
                            cwd=ROOT, text=True, capture_output=True)
    (OUTPUT / "native-compiler.txt").write_text(native.stdout + native.stderr)
    if native.returncode:
        raise RuntimeError(native.stderr)
    progress('App 4/5', '校验并复制同系列原生图标')
    shutil.copyfile(icons['icns'], resources / "ResearchHelper.icns")
    shutil.copyfile(icons['png'], resources / "ResearchHelperDock.png")
    info = {"CFBundleName": "ResearchHelper", "CFBundleDisplayName": "ResearchHelper", "CFBundleIdentifier": "io.researchhelper.app",
            "CFBundleExecutable": "ResearchHelper", "CFBundlePackageType": "APPL", "CFBundleVersion": VERSION,
            "CFBundleShortVersionString": VERSION, "CFBundleIconFile": "ResearchHelper.icns", "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0", "NSPrincipalClass": "NSApplication"}
    with (bundle / "Contents/Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    progress('App 5/5', '签名、制作DMG并只读挂载核验')
    return seal(bundle)


def seal(bundle):
    """Verify the signed DMG independently of Desktop's mutable metadata."""
    def content_identity(root):
        return {str(path.relative_to(root)):
                {"symlink": os.readlink(path)} if path.is_symlink() else
                {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in sorted(root.rglob("*")) if path.is_file() or path.is_symlink()}

    bundle = Path(bundle)
    if bundle.is_symlink() or bundle.resolve() != (OUTPUT / "ResearchHelper.app").resolve():
        raise ValueError("Signing is restricted to the generated ResearchHelper bundle")
    recovery = bundle.with_name("ResearchHelper.app.pre-seal-recovery")
    if recovery.exists() or recovery.is_symlink():
        raise FileExistsError("Previous signing recovery must be resolved before replacing " + str(recovery))
    with tempfile.TemporaryDirectory(prefix="researchhelper-sign-") as folder:
        stage = Path(folder)
        clean = stage / "ResearchHelper.app"
        shutil.copytree(bundle, clean, symlinks=True, copy_function=shutil.copy)
        subprocess.run(["xattr", "-cr", str(clean)], check=True)
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(clean)], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(clean)], check=True)
        expected_content = content_identity(clean)
        # A signed tree must be copied to an empty destination: merging through
        # existing library aliases can overwrite the canonical signed binaries.
        bundle.rename(recovery)
        try:
            shutil.copytree(clean, bundle, symlinks=True, copy_function=shutil.copy)
            # File providers can repeatedly attach FinderInfo on Desktop.
            # This tree records release bytes and links, not a durable signed
            # distribution. The actual distribution is verified below in DMG.
            if content_identity(bundle) != expected_content:
                raise RuntimeError("Desktop bundle content differs from the signed release tree")
            os.symlink("/Applications", stage / "Applications")
            disk = OUTPUT / f"ResearchHelper-{VERSION}-arm64.dmg"
            subprocess.run(["hdiutil", "create", "-volname", "ResearchHelper", "-srcfolder", str(stage),
                            "-fs", DMG_FILESYSTEM, "-format", "UDZO", "-ov", str(disk)], check=True)
            mount = stage / "verified-image"
            mount.mkdir()
            mounted = False
            try:
                subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount),
                                "-plist", str(disk)], check=True, capture_output=True)
                mounted = True
                distributed = mount / "ResearchHelper.app"
                subprocess.run(["codesign", "--verify", "--deep", "--strict", str(distributed)], check=True)
                if content_identity(distributed) != expected_content:
                    raise RuntimeError("Mounted distribution differs from the signed release tree")
            finally:
                if mounted:
                    subprocess.run(["hdiutil", "detach", str(mount)], check=True, capture_output=True)
        except BaseException:
            if bundle.is_symlink():
                bundle.unlink()
            elif bundle.exists():
                shutil.rmtree(bundle)
            recovery.rename(bundle)
            raise
        else:
            shutil.rmtree(recovery)
    manifest = {"version": VERSION, "platform": "macos", "runtime": runtime_identity(ROOT), "architecture": "arm64", "signing": "ad-hoc", "notarized": False,
                "dmg_filesystem": DMG_FILESYSTEM,
                "distribution_signature_verified": True, "signature_verified_from": "read-only mounted DMG",
                "desktop_tree_signed": False,
                "desktop_tree_limitation": "Desktop FileProvider may reattach FinderInfo; this tree certifies bytes and links only. Use the verified DMG for signed distribution.",
                "signed_content": expected_content,
                "offline_default": True, "network_policy": "authenticated-admin-consent",
                "python_embedded": True, "files": hashes(bundle)}
    (OUTPUT / "app-manifest.json").write_text(json.dumps(manifest, indent=2))
    return disk
