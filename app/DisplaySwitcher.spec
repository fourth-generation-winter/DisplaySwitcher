# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['display_switcher_app.py'],
    pathex=[],
    binaries=[('C:/Users/feign/.workbuddy/binaries/python/envs/default/Lib/site-packages/wx/WebView2Loader.dll', '.')],
    datas=[('webroot', 'webroot')],
    hiddenimports=['wx', 'wx.html2'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DisplaySwitcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['webroot/assets/DisplaySwitcher.ico'],
)
