# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [
    ('templates', 'templates'),
    ('static', 'static'),
    ('image', 'image'),
    ('_dop', '_dop'),
    ('addon/youtube_downloader/templates', 'addon/youtube_downloader/templates'),
    ('addon/instagram_downloader/templates', 'addon/instagram_downloader/templates'),
    ('addon/photo_classifier/templates', 'addon/photo_classifier/templates'),
    ('addon/FileTransferServer/templates', 'addon/FileTransferServer/templates'),
    ('addon/FileTransferServer/static', 'addon/FileTransferServer/static'),
]
binaries = []
hiddenimports = ['flask', 'flask.json', 'flask.json.provider', 'werkzeug.security',
                 'werkzeug.utils', 'jinja2', 'sqlite3']
for package in ('playwright', 'cv2', 'PIL', 'imagehash', 'yt_dlp', 'Cryptodome', 'instagrapi'):
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
