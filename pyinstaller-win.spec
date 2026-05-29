# PyInstaller spec for the Windows tray app (PyInstaller 6.x).
# Build: pyinstaller --noconfirm pyinstaller-win.spec

a = Analysis(
    ["bridge_app_win.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=["server", "bridge_common"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WebCLIBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
