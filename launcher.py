"""
Десктоп-лаунчер VideoManager.
Запускает Flask в фоновом потоке, открывает окно Tkinter, добавляет
иконку в системный трей и умеет автообновляться с GitHub.
"""
import os
import re
import sys
import socket
import shutil
import threading
import webbrowser
import subprocess
import time
import zipfile
import queue
import hashlib
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox

from launcher_log import LOG_QUEUE, append_log, install_log_redirect

# Install before app / yt-dlp imports so their startup output reaches the log panel.
install_log_redirect()

import requests
from PIL import Image, ImageDraw
import pystray

import task_registry
from app import app as flask_app
from addon.photo_classifier import state as pv_state


# =====================================================================
# UPDATE SETTINGS
# =====================================================================
CURRENT_VERSION = '1.0.16'
GITHUB_USER = 'mrfusionyt'
GITHUB_REPO = '-video_manager'
AUTO_UPDATE_INTERVAL = 3600       # seconds between automatic checks
INSTALL_DIR = r'C:\!video_manager'  # where the compiled app installs updates
# =====================================================================

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ERROR_LOG = os.path.join(tempfile.gettempdir(), 'vm_launcher_error.log')


def _log_exception(exc_type, exc, tb):
    import traceback
    text = ''.join(traceback.format_exception(exc_type, exc, tb))
    try:
        with open(ERROR_LOG, 'a', encoding='utf-8') as f:
            f.write(text + '\n')
    except Exception:
        pass
    try:
        append_log('[fatal] ' + text.strip().splitlines()[-1])
    except Exception:
        pass


sys.excepthook = _log_exception
try:
    threading.excepthook = lambda args: _log_exception(
        args.exc_type, args.exc_value, args.exc_traceback)
except Exception:
    pass


tk_root = None
status_label = None
update_btn = None
update_progress_frame = None
update_progress_canvas = None
update_progress_percent = None
update_progress_detail = None
pending_update_url = None
pending_checksum_url = None
pending_version = None
pending_update_deferred = False
ignored_version = None
update_in_progress = False
tray_icon = None
local_url_global = ''
quit_requested = threading.Event()

log_text = None


def _version_key(v):
    parts = []
    for chunk in str(v or '').replace('-', '.').split('.'):
        parts.append(int(chunk) if chunk.isdigit() else 0)
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts) or (0,)


def _clamp_progress(value):
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _blend_hex(first, second, ratio):
    a = [int(first[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(second[i:i + 2], 16) for i in (1, 3, 5)]
    return '#%02x%02x%02x' % tuple(
        int(round(a[i] + (b[i] - a[i]) * ratio)) for i in range(3))


def _show_update_progress():
    if not update_progress_frame or update_progress_frame.winfo_ismapped():
        return
    if update_btn and update_btn.winfo_ismapped():
        update_progress_frame.pack(before=update_btn, fill=tk.X, pady=(0, 6))
    else:
        update_progress_frame.pack(fill=tk.X, pady=(0, 6))


def _paint_update_progress(percent=None, state='active', detail=None):
    """Tk twin of the dark/red web progress card (called on the Tk thread)."""
    if not update_progress_canvas:
        return
    last = getattr(update_progress_canvas, '_vm_progress', 0)
    pct = _clamp_progress(last if percent is None else percent)
    state = state or 'active'
    if state == 'done':
        pct = 100
    update_progress_canvas._vm_progress = pct

    _show_update_progress()
    canvas = update_progress_canvas
    width = canvas.winfo_width()
    height = canvas.winfo_height()
    if width < 20:
        width = 440
    if height < 6:
        height = 10
    canvas.delete('all')
    canvas.create_rectangle(0, 0, width, height, fill='#27272a', outline='')

    if state == 'done':
        start_color, end_color = '#166534', '#4ade80'
    elif state == 'error':
        start_color, end_color = '#7f1d1d', '#ef4444'
    else:
        start_color, end_color = '#7f1d1d', '#ef4444'

    filled = int(width * pct / 100)
    if filled > 0:
        if state == 'active':
            for x in range(filled):
                ratio = x / max(1, filled - 1)
                canvas.create_rectangle(x, 0, x + 1, height,
                                        fill=_blend_hex(start_color, end_color, ratio),
                                        outline='')
        else:
            canvas.create_rectangle(0, 0, filled, height,
                                    fill=end_color, outline='')
        radius = min(height, filled) // 2
        if radius:
            canvas.create_oval(0, 0, radius * 2, height, fill=start_color, outline='')
            if filled > radius * 2:
                canvas.create_oval(filled - radius * 2, 0, filled, height,
                                   fill=end_color, outline='')

    if update_progress_percent:
        update_progress_percent.config(text=f'{pct}%')
        if state == 'done':
            update_progress_percent.config(fg='#4ade80')
        elif state == 'error':
            update_progress_percent.config(fg='#ff7b86')
        else:
            update_progress_percent.config(fg='#ff9aa4')
    if detail is not None and update_progress_detail:
        update_progress_detail.config(text=str(detail))


def set_update_ui(status=None, color='#ff596b', btn_text=None,
                  btn_state=None, show_btn=False, progress=None,
                  progress_state=None, progress_detail=None):
    """Thread-safe status/button/progress update for the launcher window."""
    def apply():
        if status_label and status is not None:
            status_label.config(text=status, fg=color)
        if update_btn:
            if btn_text is not None:
                update_btn.config(text=btn_text)
            if btn_state is not None:
                update_btn.config(state=btn_state)
            if show_btn and not update_btn.winfo_ismapped():
                update_btn.pack(fill=tk.X, padx=0, pady=(0, 6))
        if progress is not None or progress_state is not None:
            _paint_update_progress(progress,
                                   progress_state or 'active',
                                   progress_detail)
    if tk_root:
        try:
            tk_root.after(0, apply)
        except Exception:
            pass


def resolve_install_dir():
    """Target folder for an update: C:\\!video_manager, with a safe fallback."""
    candidates = []
    override = os.environ.get('VIDEOMANAGER_INSTALL_DIR', '').strip()
    if override:
        candidates.append(override)
    if os.name == 'nt':
        candidates.append(INSTALL_DIR)
    candidates.append(os.path.dirname(os.path.abspath(sys.executable)))

    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, 'vm_write_test.tmp')
            with open(probe, 'w', encoding='utf-8') as f:
                f.write('')
            os.remove(probe)
            return path
        except Exception as e:
            append_log(f'[update] folder {path} is not writable: {e}')
    return os.path.dirname(os.path.abspath(sys.executable))


def safe_extract(zf, dest):
    """Extract a zip without allowing ../ or absolute paths to escape dest."""
    dest_root = os.path.normpath(os.path.abspath(dest))
    members = []
    for info in zf.infolist():
        name = info.filename.replace('\\', '/')
        if name.startswith('/') or ':' in name.split('/')[0]:
            append_log(f'[update] skipped unsafe archive entry: {info.filename}')
            continue
        target = os.path.normpath(os.path.abspath(os.path.join(dest_root, name)))
        if target != dest_root and not target.startswith(dest_root + os.sep):
            append_log(f'[update] skipped unsafe archive entry: {info.filename}')
            continue
        members.append(info)
    if not members:
        raise RuntimeError('update archive has no usable files')
    zf.extractall(dest_root, members=members)


def _checksum_url_for(assets, zip_url):
    """The build publishes <zip>.sha256; older releases simply have none."""
    zip_name = os.path.basename(zip_url.split('?')[0]).lower()
    for asset in assets or []:
        name = str(asset.get('name') or '').lower()
        url = asset.get('browser_download_url') or ''
        if name == zip_name + '.sha256' or (
                name.endswith('.sha256') and 'onedir' in name and url):
            return url
    return zip_url + '.sha256'


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_expected_checksum(url):
    """Return the 64-hex digest from a .sha256 asset, or None if absent."""
    try:
        r = requests.get(url, timeout=(10, 60))
        if r.status_code == 404:
            append_log('[update] release has no checksum asset - skipping verification')
            return None
        r.raise_for_status()
        match = re.search(r'[0-9a-fA-F]{64}', r.text or '')
        if not match:
            raise RuntimeError('checksum asset is empty or malformed')
        return match.group(0).lower()
    except Exception as exc:
        if '404' in str(exc):
            return None
        raise RuntimeError(f'cannot read update checksum: {exc}')


def _active_update_blockers():
    """Active tasks must not be interrupted by an in-place update."""
    try:
        return task_registry.has_active_tasks()
    except Exception as exc:
        append_log(f'[update] task check failed: {exc}')
        return False, []


def _staging_dir(target_dir):
    """Stage next to the install folder when possible (same disk volume)."""
    parent = os.path.dirname(os.path.abspath(target_dir))
    candidates = [parent, tempfile.gettempdir()]
    for base in candidates:
        try:
            os.makedirs(base, exist_ok=True)
            probe = os.path.join(base, 'vm_stage_write_test.tmp')
            with open(probe, 'w', encoding='utf-8') as f:
                f.write('')
            os.remove(probe)
            return os.path.join(base, f'vm_update_stage_{os.getpid()}')
        except Exception as exc:
            append_log(f'[update] staging folder {base} is not writable: {exc}')
    return os.path.join(tempfile.gettempdir(), f'vm_update_stage_{os.getpid()}')


def resource_path(rel: str) -> str:
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, rel)
    return os.path.join(BASE_DIR, rel)


def get_local_ips():
    ips = []
    try:
        _, _, addrs = socket.gethostbyname_ex(socket.gethostname())
        for ip in addrs:
            if ip not in ips and not ip.startswith('127.') and ip != '0.0.0.0':
                ips.append(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        if ip not in ips and not ip.startswith('127.') and ip != '0.0.0.0':
            ips.append(ip)
    except Exception:
        pass
    return ips


def find_free_port(start=5000, end=5100):
    for port in range(start, end):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('0.0.0.0', port))
            return port
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError('No free port')


def load_app_icon_pil():
    for p in (resource_path('image/video.png'),
              resource_path('image/settings.png'),
              resource_path('image/icon1.png')):
        if os.path.exists(p):
            try:
                return Image.open(p)
            except Exception:
                pass
    img = Image.new('RGB', (64, 64), '#1a1a1a')
    d = ImageDraw.Draw(img)
    d.ellipse([8, 8, 56, 56], fill='#dc3545')
    d.polygon([(24, 20), (24, 44), (46, 32)], fill='white')
    return img


def show_window(icon=None, item=None):
    if tk_root:
        tk_root.after(0, lambda: (tk_root.deiconify(), tk_root.lift(), tk_root.focus_force()))


def hide_window(icon=None, item=None):
    if tk_root:
        tk_root.withdraw()


def tray_quit(icon, item):
    if icon:
        icon.stop()
    quit_requested.set()
    if tk_root:
        tk_root.after(0, tk_root.destroy)


def create_tray():
    global tray_icon
    try:
        image = load_app_icon_pil()
        menu = pystray.Menu(
            pystray.MenuItem('Show App', show_window, default=True),
            pystray.MenuItem('Open in Browser', lambda: webbrowser.open(local_url_global)),
            pystray.MenuItem('Restart Server', restart_from_tray),
            pystray.MenuItem('Set Access Password...', password_from_tray),
            pystray.MenuItem('Exit', tray_quit),
        )
        tray_icon = pystray.Icon('VideoManager', image, 'VideoManager', menu)
        threading.Thread(target=tray_icon.run, daemon=True).start()
    except Exception as e:
        print('[tray] failed:', e)


def check_for_updates(manual=False):
    """Check GitHub once; a newer release is installed automatically."""
    global ignored_version, update_in_progress, pending_update_deferred
    if GITHUB_USER == 'YOUR_NICKNAME' or GITHUB_REPO == 'YOUR_REPO':
        return False
    try:
        url = f'https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/releases/latest'
        r = requests.get(url, timeout=(5, 15),
                         headers={'Accept': 'application/vnd.github+json'})
        if r.status_code == 404:
            append_log('[update] no releases found yet')
            return False
        r.raise_for_status()
        data = r.json()
        latest_version = str(data.get('tag_name', '')).lstrip('v')

        if not latest_version:
            return False

        is_newer = _version_key(latest_version) > _version_key(CURRENT_VERSION)
        append_log(f'[update] latest v{latest_version} / current v{CURRENT_VERSION}')

        if is_newer and (manual or latest_version != ignored_version):
            download_url = None
            # Prefer the onedir archive asset (contains the .exe + libs) for auto-update.
            for asset in data.get('assets', []):
                name = (asset.get('name') or '').lower()
                if name.endswith('.zip') and 'onedir' in name:
                    download_url = asset['browser_download_url']
                    break
            if not download_url:
                for asset in data.get('assets', []):
                    if (asset.get('name') or '').lower().endswith('.zip'):
                        download_url = asset['browser_download_url']
                        break
            checksum_url = _checksum_url_for(data.get('assets', []), download_url or '')
            if not download_url:
                append_log('[update] release has no .zip asset')
                return False
            if update_in_progress:
                append_log('[update] newer version is already being installed')
                return True
            if tk_root:
                tk_root.after(0, lambda: show_update_indicator(
                    latest_version, download_url, checksum_url))
            else:
                show_update_indicator(latest_version, download_url, checksum_url)
            if getattr(sys, 'frozen', False):
                active, blockers = _active_update_blockers()
                if active:
                    pending_update_deferred = True
                    append_log(
                        f'[update] update to v{latest_version} deferred: '
                        f'{len(blockers)} active task(s): '
                        + ', '.join(str(t.get("kind")) for t in blockers[:5]))
                    set_update_ui(status=f'Update v{latest_version} ready - '
                                  'waiting for tasks', color='#ffc107',
                                  btn_text=f'Update to v{latest_version}',
                                  btn_state=tk.NORMAL, show_btn=True)
                else:
                    append_log(f'[update] auto-installing v{latest_version}')
                    start_update(download_url, latest_version, auto=True,
                                 checksum_url=checksum_url)
            else:
                append_log('[update] source run: auto-install needs the compiled .exe')
            return True
        return False
    except Exception as e:
        append_log(f'[update] check error: {e}')
        return False


def force_check_update():
    def do_check():
        global ignored_version, pending_update_url, pending_version
        global pending_checksum_url, pending_update_deferred
        ignored_version = None
        pending_update_url = None
        pending_checksum_url = None
        pending_version = None
        pending_update_deferred = False

        append_log('[update] manual check started')
        if status_label:
            tk_root.after(0, lambda: status_label.config(text='Checking...', fg='#ffffff'))

        found = check_for_updates(manual=True)

        if not found and tk_root:
            def show_latest_msg():
                append_log('[update] you are on the latest version')
                if status_label:
                    status_label.config(text='Latest version installed.', fg='#22c55e')
                def revert_msg():
                    if status_label and status_label.cget('text') == 'Latest version installed.':
                        status_label.config(text='Running.', fg='#e5e7eb')
                tk_root.after(20000, revert_msg)
            tk_root.after(0, show_latest_msg)
        elif not found and status_label:
            tk_root.after(0, lambda: status_label.config(text='Running.', fg='#e5e7eb'))
    threading.Thread(target=do_check, daemon=True).start()


def update_loop():
    # Give the window time to appear before the first network request.
    for _ in range(30):
        if quit_requested.is_set():
            return
        time.sleep(0.1)
    while not quit_requested.is_set():
        check_for_updates(manual=False)
        for tick in range(AUTO_UPDATE_INTERVAL):
            if quit_requested.is_set():
                break
            if tick % 10 == 0:
                maybe_start_deferred_update()
            time.sleep(1)


def maybe_start_deferred_update():
    """Install an update that was postponed because tasks were running."""
    global pending_update_deferred
    if not pending_update_deferred or update_in_progress:
        return
    if not getattr(sys, 'frozen', False) or not pending_update_url:
        return
    active, _ = _active_update_blockers()
    if active:
        return
    pending_update_deferred = False
    append_log(f'[update] tasks finished, installing v{pending_version}')
    start_update(pending_update_url, pending_version, auto=True,
                 checksum_url=pending_checksum_url, force=True)


def show_update_indicator(latest_version, download_url, checksum_url=None):
    """UI + pending state only; deferral is decided by the caller."""
    global pending_update_url, pending_version, pending_checksum_url
    pending_update_url = download_url
    pending_checksum_url = checksum_url
    pending_version = latest_version
    append_log(f'[update] new version v{latest_version} available')
    if update_btn and not update_btn.winfo_ismapped():
        update_btn.config(text=f'Update to v{latest_version}', state=tk.NORMAL)
        update_btn.pack(fill=tk.X, padx=0, pady=(0, 6))
    if status_label:
        status_label.config(text=f'Update v{latest_version} available!', fg='#ff596b')
    if tray_icon:
        try:
            tray_icon.title = f'VideoManager - update v{latest_version}'
        except Exception:
            pass


def start_update(download_url, target_version=None, auto=False,
                 checksum_url=None, force=False):
    """Download and install in the background; the button stays as a retry."""
    global update_in_progress, pending_update_url, pending_version
    global pending_checksum_url, pending_update_deferred
    if update_in_progress:
        append_log('[update] install already in progress, skipping')
        return
    if not getattr(sys, 'frozen', False):
        if tk_root:
            tk_root.after(0, lambda: messagebox.showinfo(
                'Info', 'Auto-update works only in the compiled .exe.', parent=tk_root))
        return
    if not force:
        active, blockers = _active_update_blockers()
        if active:
            names = ', '.join(str(t.get('kind')) for t in blockers[:5])
            if auto:
                pending_update_deferred = True
                append_log(f'[update] deferring install: active tasks ({names})')
                set_update_ui(
                    status=f'Update v{target_version or "new"} ready - '
                           'waiting for tasks', color='#ffc107',
                    btn_state=tk.NORMAL, show_btn=True)
                return
            def ask_then_start():
                ok = messagebox.askyesno(
                    'Tasks are running',
                    f'{len(blockers)} background task(s) are still running '
                    f'({names}). Updating now may interrupt them.\n\n'
                    'Update anyway?', parent=tk_root)
                if ok:
                    start_update(download_url, target_version, auto=False,
                                 checksum_url=checksum_url, force=True)
            if tk_root:
                tk_root.after(0, ask_then_start)
            return
    pending_update_deferred = False
    update_in_progress = True
    pending_update_url = download_url
    pending_checksum_url = checksum_url
    if target_version:
        pending_version = target_version
    ver = target_version or 'new'
    verb = 'Downloading' if auto else 'Updating'
    append_log(f'[update] {"automatic" if auto else "manual"} update to v{ver} started')
    set_update_ui(status=f'{verb} v{ver}...', color='#ff596b',
                  btn_text=f'{verb} v{ver}...', btn_state=tk.DISABLED,
                  show_btn=True, progress=0, progress_state='active',
                  progress_detail=f'Preparing v{ver}...')
    threading.Thread(target=download_and_update,
                     args=(download_url, ver, auto, checksum_url),
                     daemon=True).start()


def trigger_update():
    if not pending_update_url:
        return
    start_update(pending_update_url, pending_version, auto=False,
                 checksum_url=pending_checksum_url)


def _find_payload_root(folder):
    """Zip may contain either files at root or a single top-level directory."""
    exe_name = 'VideoManager.exe'
    if os.path.isfile(os.path.join(folder, exe_name)):
        return folder
    subdirs = [d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))]
    for d in subdirs:
        if os.path.isfile(os.path.join(folder, d, exe_name)):
            return os.path.join(folder, d)
    return folder


def download_and_update(download_url, target_version=None, auto=False,
                        checksum_url=None):
    """Download -> verify -> extract -> install into C:\\!video_manager -> restart."""
    global update_in_progress
    ver = target_version or 'new'
    try:
        tmp = tempfile.gettempdir()
        zip_path = os.path.join(tmp, 'vm_update.zip')
        target_dir = resolve_install_dir()
        update_dir = _staging_dir(target_dir)
        append_log(f'[update] install folder: {target_dir}')

        r = requests.get(download_url, stream=True, timeout=(10, 120))
        r.raise_for_status()
        total = int(r.headers.get('content-length') or 0)
        done = 0
        next_report = 10
        last_stage = -1
        with open(zip_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if not total:
                    continue
                pct = done * 100 // total
                # The download is the first 80% of the bar; installing the
                # remaining stages fills the last 20%.
                stage = min(80, pct * 80 // 100)
                if pct >= next_report:
                    append_log(f'[update] downloaded {pct}%')
                    next_report += 10
                if stage != last_stage:
                    last_stage = stage
                    set_update_ui(status=f'Update v{ver}: {pct}%',
                                  btn_text=f'Downloading v{ver} {pct}%',
                                  progress=stage, progress_state='active',
                                  progress_detail=f'Downloading v{ver}: {pct}%')
        append_log(f'[update] archive saved ({done // 1048576} MB)')
        if checksum_url:
            set_update_ui(status=f'Verifying v{ver}...', progress=84,
                          progress_state='active',
                          progress_detail='Checking download checksum...')
            expected = _fetch_expected_checksum(checksum_url)
            if expected:
                actual = _sha256_file(zip_path)
                append_log(f'[update] sha256 expected={expected} actual={actual}')
                if actual != expected:
                    raise RuntimeError(
                        'checksum mismatch: the downloaded archive is not the '
                        'one published by the release')
        set_update_ui(status=f'Preparing v{ver}...', progress=86,
                      progress_state='active', progress_detail='Preparing files...')

        if os.path.exists(update_dir):
            shutil.rmtree(update_dir, ignore_errors=True)
        os.makedirs(update_dir)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            safe_extract(zf, update_dir)
        set_update_ui(status=f'Verifying v{ver}...', progress=92,
                      progress_state='active', progress_detail='Verifying package...')

        src_root = _find_payload_root(update_dir)
        if not os.path.isfile(os.path.join(src_root, 'VideoManager.exe')):
            raise RuntimeError('VideoManager.exe not found inside the update archive')

        target_exe = os.path.join(target_dir, 'VideoManager.exe')
        bat_path = os.path.join(tmp, 'vm_update.bat')
        log_path = os.path.join(tmp, 'vm_update.log')
        pid = os.getpid()
        # Copying over a running exe is impossible on Windows. A detached
        # batch waits for this PID to disappear, closes any remaining
        # instances that still hold the files, copies and relaunches.
        # Same writable parent as the staging folder (backup/rollback volume).
        backup_dir = update_dir + '_backup'
        with open(bat_path, 'w', encoding='utf-8') as f:
            f.write(f'''@echo off
chcp 65001 >nul
set "LOG={log_path}"
set "TASKFILE={tmp}\\vm_update_tasks.txt"
set "TARGET={target_dir}"
set "SRC={src_root}"
set "STAGE={update_dir}"
set "BACKUP={backup_dir}"
set "EXE={target_exe}"
set "ZIP={zip_path}"
echo [%DATE% %TIME%] update to v{ver} started, pid {pid} >> "%LOG%"
set /a WAITS=0
:wait_pid
rem NB: a tasklist|findstr pipe hangs when cmd runs detached without a console.
rem Write the process list to a file and search that file instead.
tasklist /FI "PID eq {pid}" /NH > "%TASKFILE%"
findstr /C:" {pid} " "%TASKFILE%" >nul
if errorlevel 1 goto install
set /a WAITS+=1
if %WAITS% GEQ 120 (
  echo [%DATE% %TIME%] timeout, forcing exit >> "%LOG%"
  taskkill /PID {pid} /F >> "%LOG%" 2>&1
  goto install
)
ping -n 2 127.0.0.1 >nul
goto wait_pid
:install
del "%TASKFILE%" >nul 2>&1
if not exist "%TARGET%" mkdir "%TARGET%"
echo [%DATE% %TIME%] closing running VideoManager instances >> "%LOG%"
rem The updating process is gone, but another launcher/service instance may
rem still hold the destination exe and DLLs. Close all of them before copying.
taskkill /F /IM VideoManager.exe >> "%LOG%" 2>&1
ping -n 3 127.0.0.1 >nul

rem Keep user data (databases, config, cookies) before overwriting anything.
if exist "%BACKUP%" rmdir /s /q "%BACKUP%" >nul 2>&1
echo [%DATE% %TIME%] backing up user data to %BACKUP% >> "%LOG%"
robocopy "%TARGET%" "%BACKUP%" *.db *.db-wal *.db-shm *.json *.txt /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NC /NS /NP >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 goto rollback

echo [%DATE% %TIME%] copying staged build to %TARGET% >> "%LOG%"
robocopy "%SRC%" "%TARGET%" /E /IS /IT /R:5 /W:1 /NFL /NDL /NJH /NJS /NC /NS /NP >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 goto rollback
if not exist "%EXE%" (
  echo [%DATE% %TIME%] VideoManager.exe missing after copy, rolling back >> "%LOG%"
  goto rollback
)

echo [%DATE% %TIME%] update installed successfully >> "%LOG%"
rmdir /s /q "%STAGE%" >nul 2>&1
rmdir /s /q "%BACKUP%" >nul 2>&1
del "%ZIP%" >nul 2>&1
echo [%DATE% %TIME%] starting %EXE% >> "%LOG%"
start "" "%EXE%"
del "%~f0"
exit /b 0

:rollback
echo [%DATE% %TIME%] update failed (rc=%RC%), restoring user data >> "%LOG%"
robocopy "%BACKUP%" "%TARGET%" *.db *.db-wal *.db-shm *.json *.txt /E /IS /IT /R:1 /W:1 /NFL /NDL /NJH /NJS /NC /NS /NP >> "%LOG%" 2>&1
echo [%DATE% %TIME%] rollback done, NOT starting the updated build >> "%LOG%"
rmdir /s /q "%STAGE%" >nul 2>&1
del "%ZIP%" >nul 2>&1
del "%~f0"
exit /b 1
''')
        append_log('[update] installing to ' + target_dir + ', app will restart')
        set_update_ui(status=f'Installing v{ver}...', color='#ff596b',
                      btn_text=f'Installing v{ver}...', btn_state=tk.DISABLED,
                      progress=97, progress_state='active',
                      progress_detail='Installing and restarting...')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'DETACHED_PROCESS', 0)
        subprocess.Popen(['cmd', '/c', bat_path], creationflags=flags, close_fds=True)
        set_update_ui(status=f'Update v{ver} installed - restarting...',
                      color='#4ade80', progress=100, progress_state='done',
                      progress_detail=f'Restarting with v{ver}...')
        quit_requested.set()
        if tray_icon:
            try:
                tray_icon.stop()
            except Exception:
                pass
        if tk_root:
            tk_root.after(500, tk_root.destroy)
    except Exception as e:
        update_in_progress = False
        append_log(f'[update] failed: {e}')
        set_update_ui(status='Update failed - see log', color='#ff596b',
                      btn_text=f'Retry update v{ver}', btn_state=tk.NORMAL,
                      show_btn=True, progress_state='error',
                      progress_detail='Update failed - retry when ready')
        if not auto and tk_root:
            tk_root.after(0, lambda: messagebox.showerror(
                'Update Error', f'Failed to update: {e}', parent=tk_root))


def drain_log():
    """Move queued log lines into the Text widget (called from the Tk loop)."""
    global log_text
    if log_text is None or quit_requested.is_set():
        return
    lines = []
    try:
        while len(lines) < 250:
            lines.append(LOG_QUEUE.get_nowait())
    except queue.Empty:
        pass
    if lines:
        at_bottom = log_text.yview()[1] >= 0.995
        log_text.configure(state='normal')
        log_text.insert('end', '\n'.join(lines) + '\n')
        try:
            total = int(log_text.index('end-1c').split('.')[0])
            if total > 700:
                log_text.delete('1.0', f'{total - 700}.0')
        except Exception:
            pass
        log_text.configure(state='disabled')
        if at_bottom:
            log_text.see('end')
    tk_root.after(150, drain_log)


def tk_pump():
    if quit_requested.is_set():
        try:
            tk_root.destroy()
        except Exception:
            pass
        return
    try:
        title = pv_state.dialog_req.get_nowait()
    except Exception:
        tk_root.after(100, tk_pump)
        return
    try:
        path = filedialog.askdirectory(title=title, parent=tk_root) or ''
    except Exception:
        path = ''
    pv_state.dialog_res.put(path)
    tk_root.after(100, tk_pump)


def copy_to_clipboard(text: str, feedback_label: tk.Label):
    try:
        tk_root.clipboard_clear()
        tk_root.clipboard_append(text)
        tk_root.update()
        if feedback_label is not None:
            old = feedback_label.cget('text')
            feedback_label.config(text='✓', fg='#22c55e')
            tk_root.after(1200, lambda: feedback_label.config(text=old, fg='#6b7280'))
    except Exception as e:
        print('[copy] failed:', e)


def make_url_row(parent, label_text: str, url: str):
    row = tk.Frame(parent, bg='#141417')
    row.pack(fill=tk.X, pady=1)

    if label_text:
        lbl = tk.Label(row, text=label_text, bg='#141417', fg='#9ca3af',
                       font=('Segoe UI', 8), width=12, anchor='w')
        lbl.pack(side=tk.LEFT)

    entry = tk.Entry(row, font=('Consolas', 9), bg='#1f1f24',
                     fg='#fca5a5', readonlybackground='#1f1f24',
                     relief='flat', bd=0, highlightthickness=1,
                     highlightbackground='#333', highlightcolor='#dc3545',
                     insertbackground='#fff')
    entry.insert(0, url)
    entry.config(state='readonly')
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4), ipady=2)

    fb = tk.Label(row, text='⎘', bg='#141417', fg='#6b7280',
                  font=('Segoe UI', 12), cursor='hand2', width=3)
    fb.pack(side=tk.LEFT)

    def on_copy(_):
        copy_to_clipboard(url, fb)

    fb.bind('<Button-1>', on_copy)
    entry.bind('<Control-c>', on_copy)


class RoundedButton(tk.Canvas):
    """Canvas button styled like the unified app buttons (tint + red border, stronger red on hover)."""
    def __init__(self,parent,text,command,primary=False,width=130,height=34):
        super().__init__(parent,width=width,height=height,bg=parent.cget('bg'),highlightthickness=0,bd=0,cursor='hand2')
        self.text=text; self.command=command; self.primary=primary; self.w=width; self.h=height
        self.normal='#2e1e22'; self.hover='#4b3248'
        self.fg='#e6e7ea'
        self.bind('<Configure>',lambda e:self.draw(self.normal)); self.bind('<Enter>',lambda e:self.draw(self.hover)); self.bind('<Leave>',lambda e:self.draw(self.normal)); self.bind('<Button-1>',lambda e:self.command())
        self.draw(self.normal)
    def draw(self,color):
        self.delete('all'); r=7; w=self.w; h=self.h
        self.create_rectangle(r,0,w-r,h,fill=color,outline=color); self.create_rectangle(0,r,w,h-r,fill=color,outline=color)
        for x,y in [(r,r),(w-r,r),(r,h-r),(w-r,h-r)]: self.create_oval(x-r,y-r,x+r,y+r,fill=color,outline=color)
        self.create_text(w/2,h/2,text=self.text,fill=self.fg,font=('Segoe UI',9,'bold' if self.primary else 'normal'))

def show_password_dialog():
    """Styled Tk modal matching the web Security tab."""
    dialog = tk.Toplevel(tk_root)
    dialog.title('VideoManager — Access password')
    dialog.configure(bg='#191a1f')
    dialog.resizable(False, False)
    dialog.transient(tk_root)
    dialog.grab_set()
    dialog.geometry('400x262')
    dialog.update_idletasks()
    x = tk_root.winfo_rootx() + (tk_root.winfo_width() - 400) // 2
    y = tk_root.winfo_rooty() + (tk_root.winfo_height() - 262) // 2
    dialog.geometry(f'+{x}+{y}')

    tk.Label(dialog, text='Set access password', bg='#191a1f', fg='#f4f4f5',
             font=('Segoe UI', 13, 'bold')).pack(anchor='w', padx=20, pady=(18, 4))
    tk.Label(dialog,
             text='Required only for LAN / Tailscale clients. This PC stays open.',
             bg='#191a1f', fg='#aeb0b8', font=('Segoe UI', 9),
             wraplength=355, justify='left').pack(anchor='w', padx=20)

    def field(label, show=''):
        tk.Label(dialog, text=label, bg='#191a1f', fg='#9ca3af',
                 font=('Segoe UI', 9)).pack(anchor='w', padx=20, pady=(10, 2))
        entry = tk.Entry(dialog, show=show, bg='#1f1f24', fg='#f4f4f5',
                         insertbackground='#fff', relief='flat', bd=0,
                         highlightthickness=1, highlightbackground='#3a3a42',
                         highlightcolor='#dc3545', font=('Segoe UI', 10))
        entry.pack(fill=tk.X, padx=20, ipady=5)
        return entry

    pwd = field('New password', '*')
    confirm = field('Confirm password', '*')
    result = tk.Label(dialog, text='', bg='#191a1f', fg='#ff9aa4',
                      font=('Segoe UI', 9), wraplength=355, justify='left')
    result.pack(anchor='w', padx=20, pady=(8, 0))
    pwd.focus_set()

    def save():
        value = pwd.get()
        if not value:
            result.config(text='Enter a password first.')
            return
        if value != confirm.get():
            result.config(text='Passwords do not match.')
            return
        def post():
            try:
                r = requests.post(
                    local_url_global + '/api/auth/password',
                    json={'password': value, 'password_confirm': confirm.get()},
                    timeout=5)
                data = r.json() if r.ok else {}
                if r.ok and data.get('success'):
                    dialog.after(0, lambda: (
                        result.config(text='Password saved.', fg='#4ade80'),
                        dialog.after(1200, dialog.destroy)))
                else:
                    msg = data.get('error') or f'Server error: {r.status_code}'
                    dialog.after(0, lambda: result.config(text=msg))
            except Exception as exc:
                dialog.after(0, lambda: result.config(text=f'Request failed: {exc}'))
        threading.Thread(target=post, daemon=True).start()

    bar = tk.Frame(dialog, bg='#191a1f')
    bar.pack(fill=tk.X, side='bottom', padx=18, pady=14)
    RoundedButton(bar, 'Cancel', dialog.destroy, False, 92, 34).pack(side='right', padx=(8, 0))
    RoundedButton(bar, 'Save password', save, True, 130, 34).pack(side='right')
    dialog.bind('<Escape>', lambda e: dialog.destroy())
    dialog.bind('<Return>', lambda e: save())


def password_from_tray(icon=None, item=None):
    if tk_root:
        tk_root.after(0, show_password_dialog)


def show_restart_dialog():
    dialog=tk.Toplevel(tk_root); dialog.title('Restart VideoManager'); dialog.configure(bg='#191a1f'); dialog.resizable(False,False); dialog.transient(tk_root); dialog.grab_set()
    dialog.geometry('360x178'); dialog.update_idletasks()
    x=tk_root.winfo_rootx()+(tk_root.winfo_width()-360)//2; y=tk_root.winfo_rooty()+(tk_root.winfo_height()-178)//2; dialog.geometry(f'+{x}+{y}')
    tk.Label(dialog,text='Restart VideoManager?',bg='#191a1f',fg='#f4f4f5',font=('Segoe UI',13,'bold')).pack(anchor='w',padx=20,pady=(18,6))
    tk.Label(dialog,text='The server will briefly go offline and then start again.',bg='#191a1f',fg='#aeb0b8',font=('Segoe UI',9),wraplength=315,justify='left').pack(anchor='w',padx=20)
    bar=tk.Frame(dialog,bg='#191a1f'); bar.pack(fill='x',side='bottom',padx=18,pady=16)
    RoundedButton(bar,'Cancel',dialog.destroy,False,92,34).pack(side='right',padx=(8,0))
    def confirm():
        dialog.destroy()
        if status_label: status_label.config(text='Restarting...',fg='#ff596b')
        def request_restart():
            try: requests.post(local_url_global+'/restart_server',timeout=3)
            except Exception as exc: print('[restart] request failed:',exc)
        threading.Thread(target=request_restart,daemon=True).start()
    RoundedButton(bar,'Restart Server',confirm,True,126,34).pack(side='right')
    dialog.bind('<Escape>',lambda e:dialog.destroy()); dialog.bind('<Return>',lambda e:confirm())

def restart_from_tray(icon=None,item=None):
    if tk_root: tk_root.after(0,show_restart_dialog)


def main():
    global tk_root, status_label, update_btn, local_url_global, tray_icon, log_text
    global update_progress_frame, update_progress_canvas
    global update_progress_percent, update_progress_detail

    install_log_redirect()

    port = find_free_port(5000, 5100)
    local_url = f'http://127.0.0.1:{port}'
    local_url_global = local_url
    lan_ips = get_local_ips()
    shown_ips = lan_ips[:5]

    # Window height follows the number of network rows; extra room is reserved
    # for the update progress card so the log never gets clipped.
    window_height = 384 + 26 * len(shown_ips)
    tk_root = tk.Tk()
    tk_root.title('VideoManager')
    tk_root.geometry(f'470x{window_height}')
    tk_root.minsize(440, 300)
    tk_root.configure(bg='#18181b')
    tk_root.report_callback_exception = lambda exc, val, tb: _log_exception(exc, val, tb)

    try:
        icon_img = Image.open(resource_path('image/video.png'))
        icon_img.thumbnail((64, 64))
        from PIL import ImageTk
        tk_icon = ImageTk.PhotoImage(icon_img)
        tk_root.iconphoto(True, tk_icon)
        tk_root._icon_ref = tk_icon
    except Exception as e:
        print('[launcher] icon failed:', e)

    pv_state.state['tk_root'] = tk_root
    pv_state.load_config()
    pv_state.scan_source()

    # Main frame without expand, so no empty space appears
    root_frame = tk.Frame(tk_root, bg='#18181b')
    root_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

    head = tk.Frame(root_frame, bg='#18181b')
    head.pack(fill=tk.X, pady=(0, 4))

    status_label = tk.Label(head, text='Running.', bg='#18181b',
                            fg='#e5e7eb', font=('Segoe UI', 11, 'bold'),
                            anchor='w')
    status_label.pack(side=tk.LEFT)

    tk.Label(head, text=f'v{CURRENT_VERSION}', bg='#18181b', fg='#6b7280',
             font=('Segoe UI', 8), anchor='e').pack(side=tk.RIGHT)

    update_frame = tk.Frame(root_frame, bg='#18181b')
    update_frame.pack(fill=tk.X)

    # Hidden until an update starts; mirrors the web progress card.
    update_progress_frame = tk.Frame(
        update_frame, bg='#141417', padx=8, pady=6,
        highlightthickness=1, highlightbackground='#2a2a31')
    progress_head = tk.Frame(update_progress_frame, bg='#141417')
    progress_head.pack(fill=tk.X, pady=(0, 4))
    tk.Label(progress_head, text='Update progress', bg='#141417',
             fg='#e5e7eb', font=('Segoe UI', 9, 'bold'),
             anchor='w').pack(side=tk.LEFT)
    update_progress_percent = tk.Label(progress_head, text='0%', bg='#141417',
                                       fg='#ff9aa4', font=('Segoe UI', 9, 'bold'),
                                       anchor='e')
    update_progress_percent.pack(side=tk.RIGHT)
    update_progress_canvas = tk.Canvas(update_progress_frame, height=10,
                                       bg='#27272a', highlightthickness=0,
                                       bd=0)
    update_progress_canvas.pack(fill=tk.X)
    update_progress_detail = tk.Label(update_progress_frame,
                                      text='Initializing...', bg='#141417',
                                      fg='#9ca3af', font=('Segoe UI', 8),
                                      anchor='w')
    update_progress_detail.pack(fill=tk.X, pady=(5, 0))

    update_btn = tk.Button(update_frame, text='', command=trigger_update,
                           bg='#3a1f24', fg='#fca5a5',
                           activebackground='#4b3248', activeforeground='#ffffff',
                           relief='flat', bd=0, highlightthickness=1,
                           highlightbackground='#7a2630',
                           font=('Segoe UI', 9, 'bold'), cursor='hand2',
                           padx=10, pady=5)

    urls_frame = tk.Frame(root_frame, bg='#141417', padx=6, pady=4)
    urls_frame.pack(fill=tk.X, pady=(0, 6))

    make_url_row(urls_frame, 'On this PC:', local_url)

    if shown_ips:
        tk.Label(urls_frame, text='Network:',
                 bg='#141417', fg='#6b7280',
                 font=('Segoe UI', 8), anchor='w').pack(fill=tk.X, pady=(4, 0))
        for ip in shown_ips:
            make_url_row(urls_frame, '', f'http://{ip}:{port}')

    # ---- live log panel -------------------------------------------------
    log_wrap = tk.Frame(root_frame, bg='#141417',
                        highlightthickness=1, highlightbackground='#26262c')
    log_wrap.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

    log_text = tk.Text(log_wrap, height=7, bg='#101013', fg='#9ca3af',
                       insertbackground='#e6e7ea', relief='flat', bd=0,
                       font=('Consolas', 8), wrap='word', padx=6, pady=4,
                       state='disabled')
    log_scroll = tk.Scrollbar(log_wrap, command=log_text.yview, width=10,
                              bg='#1b1b20', troughcolor='#101013', relief='flat', bd=0,
                              activebackground='#2b2b33')
    log_text.configure(yscrollcommand=log_scroll.set)
    log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # One-line info above the buttons
    info_label = tk.Label(root_frame, text=f'VideoManager — running: {local_url}',
                         bg='#18181b', fg='#6b7280', font=('Segoe UI', 8), anchor='w')
    info_label.pack(fill=tk.X, pady=(0, 2))

    btn_bar = tk.Frame(root_frame, bg='#18181b')
    btn_bar.pack(fill=tk.X, pady=(6, 0))

    append_log(f'VideoManager v{CURRENT_VERSION} starting...')
    append_log(f'Local URL: {local_url}')
    for ip in shown_ips:
        append_log(f'Network URL: http://{ip}:{port}')

    def mk_btn(text, cmd, primary=False, width=130):
        return RoundedButton(btn_bar, text, cmd, primary=primary, width=width, height=34)

    mk_btn('Check Updates', force_check_update, width=118).pack(side=tk.LEFT, padx=(0, 7))
    mk_btn('Restart Server', show_restart_dialog, width=122).pack(side=tk.LEFT, padx=(0, 7))
    mk_btn('Password', show_password_dialog, width=100).pack(side=tk.LEFT, padx=(0, 7))
    mk_btn('Open Browser', lambda: webbrowser.open(local_url_global), primary=True, width=118).pack(side=tk.RIGHT)

    tk_root.protocol('WM_DELETE_WINDOW', hide_window)

    def run_flask():
        try:
            flask_app.run(host='0.0.0.0', port=port,
                          debug=False, use_reloader=False, threaded=True)
        except Exception as e:
            print('[flask] error:', e)

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=update_loop, daemon=True).start()

    create_tray()
    tk_root.after(100, tk_pump)
    tk_root.after(200, drain_log)

    try:
        tk_root.mainloop()
    except KeyboardInterrupt:
        print('\nShutting down...')
        quit_requested.set()
    finally:
        if tray_icon:
            try:
                tray_icon.stop()
            except Exception:
                pass
        try:
            tk_root.destroy()
        except Exception:
            pass


if __name__ == '__main__':
    main()


    