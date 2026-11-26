# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

BASE = os.path.dirname(os.path.abspath(__file__))
# Unused helper shipped with some dev setups; ~44 MB of dead weight in onedir.
EXCLUDE_DOP_NAMES = {'bgutil-pot.exe'}

datas = [
    ('templates', 'templates'),
    ('static', 'static'),
    ('image', 'image'),
    ('addon/youtube_downloader/templates', 'addon/youtube_downloader/templates'),
    ('addon/instagram_downloader/templates', 'addon/instagram_downloader/templates'),
    ('addon/photo_classifier/templates', 'addon/photo_classifier/templates'),
    ('addon/FileTransferServer/templates', 'addon/FileTransferServer/templates'),
    ('addon/FileTransferServer/static', 'addon/FileTransferServer/static'),
]
for root, dirs, files in os.walk(os.path.join(BASE, '_dop')):
    dirs[:] = [d for d in dirs if d != '__pycache__']
    rel_root = os.path.relpath(root, BASE)
    for name in files:
        if name in EXCLUDE_DOP_NAMES:
            continue
        datas.append((os.path.join(root, name), rel_root))

binaries = []
hiddenimports = ['flask', 'flask.json', 'flask.json.provider', 'werkzeug.security',
                 'werkzeug.utils', 'jinja2', 'sqlite3']
for package in ('cv2', 'PIL', 'imagehash', 'yt_dlp', 'Cryptodome', 'instagrapi'):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception:
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden


a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VideoManager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='image/video.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VideoManager',
)
