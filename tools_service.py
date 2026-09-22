r"""
Сервисные функции для веб-страницы /tools.

Включает:
  • Поиск папок !Broken, !Good, !Duplicates
  • Диагностику файлов (декодирование через ffmpeg)
  • Перенос целых файлов из !Broken в !Good
  • Возврат файлов из !Good в библиотеку
  • Управление фоновыми задачами с прогрессом (SSE)
"""
import os
import re
import json
import shutil
import threading
import subprocess
import uuid
import time


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG = os.path.join(BASE_DIR, '_dop', 'ffmpeg.exe')
FFPROBE = os.path.join(BASE_DIR, '_dop', 'ffprobe.exe')
if not os.path.exists(FFMPEG):
    FFMPEG = shutil.which('ffmpeg') or 'ffmpeg'
if not os.path.exists(FFPROBE):
    FFPROBE = shutil.which('ffprobe') or 'ffprobe'


VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv',
              '.webm', '.m4v', '.ts', '.m2ts', '.vob', '.rm', '.rmvb'}

CRITICAL_MARKERS = [
    'invalid nal unit size',
    'error splitting the input into nal units',
    'missing picture in access unit',
    'no frame!',
    'corrupted macroblock',
]


# Глобальное состояние задач (для SSE)
TASKS = {}
TASK_LOCK = threading.Lock()


def _subprocess_kwargs():
    return dict(
        capture_output=True,
        encoding='utf-8',
        errors='replace',
    )


# ===================================================================
#                     Поиск папок
# ===================================================================
def find_dir(name):
    for drive in ('E:', 'D:', 'C:'):
        p = f'{drive}\\' + name
        if os.path.isdir(p):
            return p
    return None


def find_broken_dir():
    return find_dir('!Broken')


def find_good_dir():
    return find_dir('!Good')


def find_duplicates_dir():
    return find_dir('!Duplicates')


# ===================================================================
#                     Списки файлов
# ===================================================================
def list_videos(folder):
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder)):
        p = os.path.join(folder, name)
        if os.path.isfile(p) and os.path.splitext(name)[1].lower() in VIDEO_EXTS:
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            out.append({'name': name, 'path': p, 'size': size})
    return out


def folder_stats(folder):
    if not folder or not os.path.isdir(folder):
        return {'exists': False, 'path': folder, 'count': 0, 'size': 0}
    files = list_videos(folder)
    total = sum(f['size'] for f in files)
    return {
        'exists': True, 'path': folder,
        'count': len(files), 'size': total,
    }


# ===================================================================
#                     ffprobe
# ===================================================================
def probe_short(path):
    try:
        r = subprocess.run(
            [FFPROBE, '-v', 'quiet', '-print_format', 'json',
             '-show_streams', '-show_format', path],
            timeout=20, **_subprocess_kwargs(),
        )
        if r.returncode != 0 or not r.stdout:
            return None
        data = json.loads(r.stdout)
        v_codec = a_codec = None
        w = h = 0
        for s in data.get('streams', []):
            if s.get('codec_type') == 'video' and not v_codec:
                v_codec = s.get('codec_name', '?')
                w = int(s.get('width', 0) or 0)
                h = int(s.get('height', 0) or 0)
            elif s.get('codec_type') == 'audio' and not a_codec:
                a_codec = s.get('codec_name', '?')
        fmt = data.get('format', {})
        return {
            'video': v_codec or '—',
            'audio': a_codec or '—',
            'width': w,
            'height': h,
            'container': (fmt.get('format_name') or '?').lower(),
            'duration': int(float(fmt.get('duration', 0) or 0)),
        }
    except Exception:
        return None


# ===================================================================
#                     Полная проверка целостности
# ===================================================================
def analyze_file(path, timeout=1800):
    duration = 0
    info = probe_short(path)
    if info:
        duration = info['duration']

    cmd = [
        FFMPEG, '-v', 'info',
        '-an', '-sn', '-dn',
        '-i', path,
        '-f', 'null', '-',
    ]
    try:
        r = subprocess.run(cmd, timeout=timeout, **_subprocess_kwargs())
        stderr = r.stderr or ''
    except subprocess.TimeoutExpired as e:
        stderr = (e.stderr or '') if hasattr(e, 'stderr') else ''
        low = stderr.lower()
        errors = {m: low.count(m) for m in CRITICAL_MARKERS}
        return {
            'duration': duration, 'decoded': -1,
            'errors': errors, 'total_errors': sum(errors.values()),
            'status': 'TIMEOUT',
        }
    except Exception as e:
        return {
            'duration': duration, 'decoded': -1,
            'errors': {}, 'total_errors': 0,
            'status': 'ERROR', 'message': str(e),
        }

    times = re.findall(r'time=(\d+):(\d+):(\d+)\.(\d+)', stderr)
    decoded = 0
    if times:
        h, m, s, _ = times[-1]
        decoded = int(h) * 3600 + int(m) * 60 + int(s)

    low = stderr.lower()
    errors = {m: low.count(m) for m in CRITICAL_MARKERS}
    total = sum(errors.values())

    if total == 0:
        status = 'OK'
    elif duration > 0 and decoded >= duration * 0.85:
        status = 'PARTIAL'
    else:
        status = 'BROKEN'

    return {
        'duration': duration, 'decoded': decoded,
        'errors': errors, 'total_errors': total,
        'status': status,
    }


# ===================================================================
#                     Управление задачами
# ===================================================================
def new_task(name):
    tid = str(uuid.uuid4())
    with TASK_LOCK:
        TASKS[tid] = {
            'name': name,
            'status': 'running',
            'total': 0, 'processed': 0,
            'progress': 0,
            'current': '',
            'message': 'Initializing...',
            'result': None,
            'started_at': time.time(),
        }
    return tid


def update_task(tid, **kwargs):
    with TASK_LOCK:
        t = TASKS.get(tid)
        if not t:
            return
        t.update(kwargs)


def get_task(tid):
    with TASK_LOCK:
        t = TASKS.get(tid)
        return dict(t) if t else None


def _unique_path(dst_dir, name):
    dst = os.path.join(dst_dir, name)
    base, ext = os.path.splitext(name)
    i = 1
    while os.path.exists(dst):
        dst = os.path.join(dst_dir, f'{base}_{i}{ext}')
        i += 1
    return dst


# ===================================================================
#                     Задача: диагностика + перенос
# ===================================================================
def start_diagnose_task(source, dest=None, move=False, include_partial=False):
    tid = new_task('diagnose' if move else 'check')
    thread = threading.Thread(
        target=_diagnose_worker,
        args=(tid, source, dest, move, include_partial),
        daemon=True,
    )
    thread.start()
    return tid


def _diagnose_worker(tid, source, dest, move, include_partial):
    try:
        files = list_videos(source)
        total = len(files)
        update_task(tid, total=total, message=f'Scanning {total} files...')

        if move and dest:
            os.makedirs(dest, exist_ok=True)

        results = []
        moved = 0
        errors = 0

        for i, f in enumerate(files, 1):
            path = f['path']
            name = f['name']

            update_task(tid, current=name,
                        message=f'Analyzing {name}...')

            res = analyze_file(path)
            st = res['status']
            results.append({
                'name': name,
                'size': f['size'],
                'status': st,
                'decoded': res['decoded'],
                'duration': res['duration'],
                'errors': res['total_errors'],
            })

            should_move = (st == 'OK') or (st == 'PARTIAL' and include_partial)
            if move and dest and should_move:
                try:
                    dst = _unique_path(dest, name)
                    shutil.move(path, dst)
                    moved += 1
                except Exception as e:
                    errors += 1
                    print(f"[tools] move failed for {name}: {e}")

            update_task(tid, processed=i,
                        progress=int(i / total * 100),
                        message=f'{i}/{total}: {name} → {st}')

        update_task(
            tid,
            status='complete',
            progress=100,
            message=f'Done. Moved: {moved}, errors: {errors}',
            result={'results': results, 'moved': moved, 'errors': errors},
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        update_task(tid, status='error', message=str(e))


# ===================================================================
#                     Задача: возврат в библиотеку
# ===================================================================
def start_restore_task(good_dir, library_path):
    tid = new_task('restore')
    thread = threading.Thread(
        target=_restore_worker,
        args=(tid, good_dir, library_path),
        daemon=True,
    )
    thread.start()
    return tid


def _restore_worker(tid, good_dir, library_path):
    try:
        files = list_videos(good_dir)
        total = len(files)
        update_task(tid, total=total,
                    message=f'Restoring {total} files...')

        os.makedirs(library_path, exist_ok=True)
        moved = 0
        errors = 0

        for i, f in enumerate(files, 1):
            name = f['name']
            try:
                dst = _unique_path(library_path, name)
                shutil.move(f['path'], dst)
                moved += 1
                update_task(tid, current=name,
                            message=f'{i}/{total}: {name} → OK')
            except Exception as e:
                errors += 1
                update_task(tid, current=name,
                            message=f'{i}/{total}: {name} → error: {e}')
            update_task(tid, processed=i,
                        progress=int(i / total * 100))

        update_task(
            tid, status='complete', progress=100,
            message=f'Done. Moved: {moved}, errors: {errors}',
            result={'moved': moved, 'errors': errors},
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        update_task(tid, status='error', message=str(e))