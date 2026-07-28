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
        ('image', 'image'),
        ('codex_pets', 'codex_pets'),
        ('packs', 'packs'),
        ('skins', 'skins'),
    ],
    hiddenimports=[
        'psutil',
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._win32',
        'pynput.mouse',
        'pynput.mouse._win32',
        'pynput._util.win32',
        'pystray',
        'pystray._win32',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'roach_settings',
        'phrase_packs',
        'desktop_chrome',
        'accountant_buddy',
        'story_mode',
        'llm_client',
        'presence_guard',
    ],
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
    # False: Windows 双击无黑框控制台（GitHub Actions / build_win.bat 同用此 spec）
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
