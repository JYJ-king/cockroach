# -*- mode: python ; coding: utf-8 -*-
# macOS 可选打包（一般直接 python3 运行即可）:
#   pip3 install -r requirements-mac.txt pyinstaller
#   pyinstaller --noconfirm cockroach_pet.spec

a = Analysis(
    ['cockroach_pet.py'],
    pathex=[],
    binaries=[],
    datas=[('cockroach.png', '.'), ('image', 'image'), ('codex_pets', 'codex_pets')],
    hiddenimports=[],
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
    name='cockroach_pet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
app = BUNDLE(
    exe,
    name='cockroach_pet.app',
    icon=None,
    bundle_identifier='local.cockroach.pet',
)
