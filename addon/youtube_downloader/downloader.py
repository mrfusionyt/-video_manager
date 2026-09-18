import os
import re
import sys
import json
import uuid
import time
import threading
import subprocess
from datetime import datetime

TASKS = {}
_lock = threading.Lock()


def _find_ffmpeg_dir():
    candidates = [
        # _dop в корне проекта (addon/youtube_downloader → ../.. → корень)
        os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '..', '..', '_dop'
        )),
        # _dop рядом с модулем (fallback)
        os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '_dop'
        )),
        # PyInstaller
        os.path.normpath(os.path.join(
            getattr(sys, '_MEIPASS', ''), '_dop'
        )),
    ]
    for p in candidates:
        if p and os.path.isfile(os.path.join(p, 'ffmpeg.exe')):
            return p
    return None


FFMPEG_DIR = _find_ffmpeg_dir()
PYTHON_EXE = sys.executable


# ===================================================================
#                     СОЗДАНИЕ ЗАДАЧИ
# ===================================================================
def create_task(url, mode, save_dir):
    task_id = uuid.uuid4().hex[:12]

    with _lock:
        TASKS[task_id] = {
            'id': task_id,
            'url': url,
            'mode': mode,
            'save_dir': save_dir,
            'base_save_dir': save_dir,
            'channel_folder': None,
            'status': 'starting',
            'returncode': None,
            'log': [],
            'created_at': datetime.now().isoformat(timespec='seconds'),
            'finished_at': None,
            'cancel_requested': False,
            'process': None,
            'started_at': time.time(),
            'current_video': None,
            'progress_percent': 0.0,
            'items_total': 0,
            'items_done': 0,
            'result_files': [],
        }

    thread = threading.Thread(target=_run_download, args=(task_id,), daemon=True)
    thread.start()
    return task_id


def get_task(task_id):
    with _lock:
        t = TASKS.get(task_id)
        if not t:
            return None
        result = {k: v for k, v in t.items() if k != 'process'}
        result['log'] = list(t['log'])
        result['result_files'] = list(t.get('result_files', []))
        return result


def get_all_tasks(limit=50):
    with _lock:
        items = []
        for t in TASKS.values():
            items.append({
                'id': t['id'],
                'url': t['url'],
                'mode': t['mode'],
                'save_dir': t['save_dir'],
                'channel_folder': t.get('channel_folder'),
                'status': t['status'],
                'created_at': t['created_at'],
                'finished_at': t['finished_at'],
                'log_count': len(t['log']),
                'current_video': t.get('current_video'),
                'items_total': t.get('items_total', 0),
                'items_done': t.get('items_done', 0),
                'progress_percent': t.get('progress_percent', 0.0),
                'result_files_count': len(t.get('result_files', [])),
            })
    items.sort(key=lambda x: x['created_at'], reverse=True)
    return items[:limit]


def get_log_since(task_id, offset=0):
    with _lock:
        t = TASKS.get(task_id)
        if not t:
            return None, 0
        return list(t['log'])[offset:], len(t['log'])


def get_state(task_id):
    with _lock:
        t = TASKS.get(task_id)
        if not t:
            return None
        return {
            'id': t['id'],
            'status': t['status'],
            'mode': t['mode'],
            'url': t['url'],
            'save_dir': t['save_dir'],
            'channel_folder': t.get('channel_folder'),
            'returncode': t['returncode'],
            'current_video': t.get('current_video'),
            'progress_percent': t.get('progress_percent', 0.0),
            'items_total': t.get('items_total', 0),
            'items_done': t.get('items_done', 0),
            'result_files': list(t.get('result_files', [])),
            'finished_at': t.get('finished_at'),
        }


def request_cancel(task_id):
    with _lock:
        t = TASKS.get(task_id)
        if not t:
            return 'not_found'
        if t['status'] in ('complete', 'error', 'cancelled'):
            return 'already_done'
        t['cancel_requested'] = True
        proc = t.get('process')
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        return 'ok'


def clear_all_tasks():
    with _lock:
        TASKS.clear()


def delete_task(task_id):
    with _lock:
        TASKS.pop(task_id, None)


# ===================================================================
#                     ПАРСИНГ ВЫВОДА YT-DLP
# ===================================================================
_DEST_RE = re.compile(r'\[download\]\s+Destination:\s+(.+)')
_MERGE_RE = re.compile(r'\[Merger\]\s+Merging formats into\s+"(.+)"')
_ALREADY_RE = re.compile(r'\[download\]\s+(.+)\s+has already been downloaded')
_PROGRESS_RE = re.compile(r'\[download\]\s+([\d.]+)%\s+of')
_ITEM_RE = re.compile(r'\[download\]\s+Downloading (?:item|video)\s+(\d+)\s+of\s+(\d+)')


def _register_file(t, path):
    if not path:
        return
    save_dir = t['save_dir']
    try:
        if os.path.isabs(path):
            rel = os.path.relpath(path, save_dir)
        else:
            rel = path
        rel = rel.replace('\\', '/')
    except Exception:
        rel = os.path.basename(path)

    ext = os.path.splitext(rel)[1].lower()
    if ext not in ('.mp4', '.mkv', '.webm', '.mov', '.m4v',
                   '.jpg', '.jpeg', '.png', '.webp', '.gif'):
        return

    is_video = ext in ('.mp4', '.mkv', '.webm', '.mov', '.m4v')
    files = t.setdefault('result_files', [])
    for f in files:
        if f['name'] == rel:
            return
    files.append({'name': rel, 'is_video': is_video})


def _parse_line(task_id, line):
    with _lock:
        t = TASKS.get(task_id)
        if not t:
            return

        m = _PROGRESS_RE.search(line)
        if m:
            try:
                t['progress_percent'] = float(m.group(1))
            except Exception:
                pass

        m = _ITEM_RE.search(line)
        if m:
            try:
                t['items_done'] = max(0, int(m.group(1)) - 1)
                t['items_total'] = int(m.group(2))
            except Exception:
                pass

        m = _DEST_RE.search(line)
        if m:
            path = m.group(1).strip()
            t['current_video'] = os.path.basename(path)
            t['progress_percent'] = 0.0

        m = _ALREADY_RE.search(line)
        if m:
            path = m.group(1).strip()
            t['current_video'] = os.path.basename(path)
            _register_file(t, path)

        m = _MERGE_RE.search(line)
        if m:
            path = m.group(1).strip()
            t['current_video'] = os.path.basename(path)
            t['progress_percent'] = 100.0
            _register_file(t, path)


def _scan_new_files(task_id):
    with _lock:
        t = TASKS.get(task_id)
        if not t:
            return
        save_dir = t['save_dir']
        started = t.get('started_at', 0)
    if not save_dir or not os.path.isdir(save_dir):
        return
    try:
        for root, dirs, files in os.walk(save_dir):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in ('.mp4', '.mkv', '.webm', '.mov', '.m4v',
                               '.jpg', '.jpeg', '.png', '.webp', '.gif'):
                    continue
                full = os.path.join(root, fn)
                try:
                    mtime = os.path.getmtime(full)
                except Exception:
                    continue
                if started and mtime < started - 5:
                    continue
                with _lock:
                    t = TASKS.get(task_id)
                    if t:
                        _register_file(t, full)
    except Exception as e:
        print(f"[youtube] scan error: {e}")


# ===================================================================
#                     ОПРЕДЕЛЕНИЕ ИМЕНИ КАНАЛА
# ===================================================================
def _sanitize_name(name):
    if not name:
        return None
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = name.strip().strip('.')
    name = re.sub(r'\s+', ' ', name)
    if len(name) > 100:
        name = name[:100].rstrip()
    return name or None


def _try_get_channel_name(url):
    cmd = [
        PYTHON_EXE, '-m', 'yt_dlp',
        '--skip-download',
        '--playlist-end', '1',
        '--no-warnings',
        '--quiet',
        '--print', '%(channel|)s',
        '--print', '%(uploader|)s',
        '--print', '%(playlist_title|)s',
        '--print', '%(playlist_uploader|)s',
        url,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=90,
        )
    except subprocess.TimeoutExpired:
        print(f"[youtube] channel name: timeout for {url}")
        return None
    except Exception as e:
        print(f"[youtube] channel name error: {e}")
        return None

    if result.returncode != 0:
        print(f"[youtube] channel name rc={result.returncode}: {result.stderr[:200]}")
        return None

    for line in result.stdout.splitlines():
        line = line.strip()
        if line and line not in ('NA', 'null', 'None'):
            return line
    return None


def _get_channel_name(url, mode):
    candidates = []

    if '/shorts' in url:
        root = re.sub(r'/(shorts|videos|streams|playlists|featured)/?(?:[?#].*)?$',
                      '', url).rstrip('/')
        if root and root != url:
            candidates.append(root)

    candidates.append(url)

    for target in candidates:
        print(f"[youtube] trying channel name from: {target}")
        name = _try_get_channel_name(target)
        if name:
            print(f"[youtube] channel name = {name!r}")
            return name

    print(f"[youtube] could not determine channel name for {url}")
    return None


def _apply_channel_folder(task_id, url, mode, base_save_dir):
    channel_name = _get_channel_name(url, mode)
    if not channel_name:
        _append_log(task_id, '⚠️ Could not determine channel name — using base folder')
        return base_save_dir

    folder = _sanitize_name(channel_name)
    if not folder:
        _append_log(task_id, '⚠️ Channel name is invalid after sanitizing — using base folder')
        return base_save_dir

    new_save_dir = os.path.join(base_save_dir, folder)
    try:
        os.makedirs(new_save_dir, exist_ok=True)
    except Exception as e:
        _append_log(task_id, f'⚠️ Cannot create channel folder "{folder}": {e}')
        return base_save_dir

    with _lock:
        if task_id in TASKS:
            TASKS[task_id]['save_dir'] = new_save_dir
            TASKS[task_id]['channel_folder'] = folder

    _append_log(task_id, f'📁 Channel folder: {folder}')
    return new_save_dir


# ===================================================================
#                     СБОР URL SHORTS
# ===================================================================
def _collect_shorts_urls(channel_url):
    if '/shorts' not in channel_url:
        channel_url = channel_url.rstrip('/') + '/shorts'

    cmd = [
        PYTHON_EXE, '-m', 'yt_dlp',
        '--flat-playlist',
        '--dump-json',
        '--no-warnings',
        '--quiet',
        channel_url,
    ]

    print(f"[youtube] collecting shorts: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("[youtube] shorts collection timed out")
        return []

    if result.returncode != 0:
        print(f"[youtube] shorts collection failed: {result.stderr[:300]}")
        return []

    urls = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            info = json.loads(line)
        except json.JSONDecodeError:
            continue
        video_id = info.get('id')
        if video_id:
            urls.append(f"https://www.youtube.com/watch?v={video_id}")

    print(f"[youtube] collected {len(urls)} shorts URLs")
    return urls


# ===================================================================
#                     СБОРКА КОМАНД YT-DLP
# ===================================================================
def _build_command_single(url, save_dir):
    output_template = os.path.join(save_dir, '%(id)s.%(ext)s')

    if FFMPEG_DIR:
        format_spec = 'bestvideo+bestaudio/best'
    else:
        format_spec = 'best[ext=mp4]'

    cmd = [
        PYTHON_EXE, '-m', 'yt_dlp',
        '-f', format_spec,
        '--merge-output-format', 'mp4',
        '--output', output_template,
        '--no-warnings',
        '--newline',
        '--no-playlist',
    ]
    if FFMPEG_DIR:
        cmd += ['--ffmpeg-location', FFMPEG_DIR]
    cmd.append(url)
    return cmd


def _build_command_playlist(url, save_dir, mode):
    output_template = os.path.join(save_dir, '%(id)s.%(ext)s')

    if FFMPEG_DIR:
        format_spec = 'bestvideo+bestaudio/best'
    else:
        format_spec = 'best[ext=mp4]'

    cmd = [
        PYTHON_EXE, '-m', 'yt_dlp',
        '-f', format_spec,
        '--merge-output-format', 'mp4',
        '--output', output_template,
        '--no-warnings',
        '--newline',
        '--yes-playlist',
    ]
    if FFMPEG_DIR:
        cmd += ['--ffmpeg-location', FFMPEG_DIR]
    cmd.append(url)
    return cmd


# ===================================================================
#                     ОСНОВНОЙ ЗАПУСК
# ===================================================================
def _append_log(task_id, line):
    with _lock:
        t = TASKS.get(task_id)
        if t is None:
            return
        if len(t['log']) > 5000:
            t['log'] = t['log'][-4000:]
        t['log'].append(line)


def _run_download(task_id):
    with _lock:
        t = TASKS.get(task_id)
        if not t:
            return
        url = t['url']
        mode = t['mode']
        base_save_dir = t.get('base_save_dir') or t['save_dir']

    try:
        os.makedirs(base_save_dir, exist_ok=True)
    except Exception as e:
        _append_log(task_id, f'❌ Cannot create save dir: {e}')
        with _lock:
            TASKS[task_id]['status'] = 'error'
            TASKS[task_id]['finished_at'] = datetime.now().isoformat(timespec='seconds')
        return

    with _lock:
        TASKS[task_id]['status'] = 'running'
    _append_log(task_id, '▶️ Detecting channel name…')

    save_dir = _apply_channel_folder(task_id, url, mode, base_save_dir)

    if mode == 'channel_shorts':
        _run_channel_shorts(task_id, url, save_dir)
        return

    cmd = _build_command_playlist(url, save_dir, mode)
    _append_log(task_id, f'▶️ {" ".join(cmd)}')

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
            bufsize=1, cwd=save_dir,
        )
        with _lock:
            TASKS[task_id]['process'] = proc

        for line in proc.stdout:
            line = line.rstrip('\n').rstrip('\r')
            if not line:
                continue
            _append_log(task_id, line)
            try:
                _parse_line(task_id, line)
            except Exception:
                pass

        proc.wait()
        with _lock:
            cancelled = TASKS[task_id].get('cancel_requested')
            TASKS[task_id]['returncode'] = proc.returncode

        try:
            _scan_new_files(task_id)
        except Exception:
            pass

        if cancelled:
            _append_log(task_id, '⏹️ Cancelled by user.')
            with _lock:
                TASKS[task_id]['status'] = 'cancelled'
                TASKS[task_id]['finished_at'] = datetime.now().isoformat(timespec='seconds')
            return

        if proc.returncode != 0:
            _append_log(task_id, f'❌ yt-dlp exited with code {proc.returncode}')
            with _lock:
                TASKS[task_id]['status'] = 'error'
                TASKS[task_id]['finished_at'] = datetime.now().isoformat(timespec='seconds')
        else:
            _append_log(task_id, '✅ Download finished successfully.')
            with _lock:
                TASKS[task_id]['status'] = 'complete'
                TASKS[task_id]['finished_at'] = datetime.now().isoformat(timespec='seconds')
                TASKS[task_id]['progress_percent'] = 100.0
    except Exception as e:
        _append_log(task_id, f'❌ Error: {e}')
        with _lock:
            TASKS[task_id]['status'] = 'error'
            TASKS[task_id]['finished_at'] = datetime.now().isoformat(timespec='seconds')
    finally:
        with _lock:
            if task_id in TASKS:
                TASKS[task_id]['process'] = None


def _run_channel_shorts(task_id, url, save_dir):
    _append_log(task_id, f'▶️ Collecting shorts URLs from {url}...')

    urls = _collect_shorts_urls(url)
    if not urls:
        _append_log(task_id, '❌ No shorts URLs found. Channel may be private or YouTube blocked the request.')
        with _lock:
            TASKS[task_id]['status'] = 'error'
            TASKS[task_id]['finished_at'] = datetime.now().isoformat(timespec='seconds')
        return

    total = len(urls)
    _append_log(task_id, f'✅ Found {total} shorts')
    with _lock:
        TASKS[task_id]['items_total'] = total
        TASKS[task_id]['items_done'] = 0

    with _lock:
        folder_set = bool(TASKS[task_id].get('channel_folder'))
    if not folder_set:
        _append_log(task_id, '▶️ Retry channel name from first video URL...')
        name = _try_get_channel_name(urls[0])
        if name:
            folder = _sanitize_name(name)
            if folder:
                new_save_dir = os.path.join(
                    os.path.dirname(save_dir), folder
                )
                try:
                    os.makedirs(new_save_dir, exist_ok=True)
                    with _lock:
                        TASKS[task_id]['save_dir'] = new_save_dir
                        TASKS[task_id]['channel_folder'] = folder
                    save_dir = new_save_dir
                    _append_log(task_id, f'📁 Channel folder: {folder}')
                except Exception as e:
                    _append_log(task_id, f'⚠️ Cannot create channel folder: {e}')

    for idx, video_url in enumerate(urls, start=1):
        with _lock:
            if TASKS[task_id].get('cancel_requested'):
                _append_log(task_id, f'⏹️ Cancelled at {idx-1}/{total}')
                TASKS[task_id]['status'] = 'cancelled'
                TASKS[task_id]['finished_at'] = datetime.now().isoformat(timespec='seconds')
                return
            TASKS[task_id]['items_done'] = idx - 1
            TASKS[task_id]['progress_percent'] = 0.0

        _append_log(task_id, f'▶️ [{idx}/{total}] {video_url}')

        cmd = _build_command_single(video_url, save_dir)
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace',
                bufsize=1, cwd=save_dir,
            )
            with _lock:
                TASKS[task_id]['process'] = proc

            for line in proc.stdout:
                line = line.rstrip('\n').rstrip('\r')
                if not line:
                    continue
                _append_log(task_id, line)
                try:
                    _parse_line(task_id, line)
                except Exception:
                    pass

            proc.wait()
            with _lock:
                TASKS[task_id]['process'] = None

            if proc.returncode != 0:
                _append_log(task_id, f'⚠️ [{idx}/{total}] exited with code {proc.returncode}')
            else:
                _append_log(task_id, f'✅ [{idx}/{total}] done')

        except Exception as e:
            _append_log(task_id, f'❌ [{idx}/{total}] error: {e}')

    try:
        _scan_new_files(task_id)
    except Exception:
        pass

    with _lock:
        if TASKS[task_id].get('cancel_requested'):
            TASKS[task_id]['status'] = 'cancelled'
        else:
            TASKS[task_id]['status'] = 'complete'
            TASKS[task_id]['progress_percent'] = 100.0
        TASKS[task_id]['finished_at'] = datetime.now().isoformat(timespec='seconds')

    _append_log(task_id, f'✅ Finished. Total: {total}')