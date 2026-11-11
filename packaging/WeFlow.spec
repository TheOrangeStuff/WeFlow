# PyInstaller spec: one-folder build; a .app bundle on macOS.
#   pyinstaller --noconfirm packaging/WeFlow.spec
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
VERSION = "1.0.0"

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "WeFlow" / "assets" / "icons"), "WeFlow/assets/icons")],
    hiddenimports=[
        "keyring.backends.macOS",
        "keyring.backends.Windows",
        "keyring.backends.SecretService",
        "flickrapi",
        "WeFlow.core._builtin_keys",
    ],
    excludes=["tkinter", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = str(ROOT / "packaging" / ("WeFlow.icns" if sys.platform == "darwin" else "WeFlow.ico"))
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WeFlow",
    console=False,
    icon=icon,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="WeFlow", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="WeFlow.app",
        icon=icon,
        bundle_identifier="com.weflow.app",
        version=VERSION,
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
            "NSRemovableVolumesUsageDescription": "WeFlow copies photos from your memory card.",
            "LSApplicationCategoryType": "public.app-category.photography",
        },
    )
