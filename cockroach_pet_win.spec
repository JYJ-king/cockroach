# -*- mode: python ; coding: utf-8 -*-
# 在 Windows 上打包:
#   pip install -r requirements.txt
#   pyinstaller --noconfirm cockroach_pet_win.spec

block_cipher = None

a = Analysis(
    ['cockroach_pet.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('cockroach.png', '.'),
        ('packs', 'packs'),
        ('skins', 'skins'),
    ],
    hiddenimports=['psutil', 'pynput', 'pystray', 'PIL', 'roach_settings', 'phrase_packs', 'desktop_chrome', 'accountant_buddy'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['AppKit', 'Foundation', 'objc'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='cockroach_pet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # True: 保留控制台，便于看快捷键提示 / 报错
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
