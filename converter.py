r"""
Конвертация несовместимых с браузером видео прямо в локальной папке.

Логика convert_video_in_place():
  1. is_playable(src)     — если битый, отказываемся.
  2. needs_conversion()   — если контейнер/кодек плохой, идём дальше.
  3. Делаем remux (быстро, без потери) ИЛИ re-encode (для плохого кодека).
  4. Проверяем is_playable(результата).
     Если remux дал неиграбельный результат — fallback на re-encode.
  5. Заменяем оригинал (он уезжает в !Duplicates).
  6. Обновляем запись в БД.

Битые файлы переносятся в <диск>:\!Broken\ (без подпапок).
Оригинал при конвертации переносится в <диск>:\!Duplicates\converted_<дата>.

Кэш ffprobe (probe_cache.json) живёт на диске — после перезапуска
сервера повторные вызовы ffprobe не делаются.
"""
import os
import json as _json
import shutil
import subprocess
import threading
import time
from datetime import datetime


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG_PATH = os.path.join(BASE_DIR, '_dop', 'ffmpeg.exe')
FFPROBE_PATH = os.path.join(BASE_DIR, '_dop', 'ffprobe.exe')
if not os.path.exists(FFMPEG_PATH):
    FFMPEG_PATH = shutil.which('ffmpeg') or 'ffmpeg'
if not os.path.exists(FFPROBE_PATH):
    FFPROBE_PATH = shutil.which('ffprobe') or 'ffprobe'


BAD_CODECS = {
    'hevc', 'h265', 'mpeg4', 'msmpeg4', 'msmpeg4v3',
    'wmv1', 'wmv2', 'wmv3', 'vc1', 'mpeg2video',
    'prores', 'dnxhd', 'theora', 'vp6', 'rv40',
}

BAD_FORMATS = {
    'mpegts', 'mpegtsraw', 'matroska', 'avi', 'asf', 'flv',
    'ogg', 'rm', 'realmedia',
}

GOOD_CODECS = {'h264', 'avc1', 'vp8', 'vp9', 'av1', 'mp4v'}
GOOD_FORMATS = {'mov', 'mp4', 'm4a', '3gp', '3g2', 'mj2', 'webm'}

BAD_EXTENSIONS = {
    '.mkv', '.avi', '.wmv', '.flv', '.ts', '.m2ts',
    '.vob', '.rm', '.rmvb', '.3gp',
}

# Точные фатальные маркеры (без ложных срабатываний на warnings)
_FATAL_MARKERS = (
    'invalid nal unit size',
    'error splitting the input into nal units',
    'missing picture in access unit',
    'no frame!',
    'corrupted macroblock',
)


# ===================================================================
#                     Persistent probe cache
# ===================================================================
_PROBE_CACHE_FILE = os.path.join(BASE_DIR, 'probe_cache.json')
_PROBE_CACHE = {}         # filepath -> (mtime, codec, format_name)
_PROBE_CACHE_LOCK = threading.Lock()
_PROBE_CACHE_DIRTY = False


def _load_probe_cache():
    global _PROBE_CACHE
    try:
        if os.path.exists(_PROBE_CACHE_FILE):
            with open(_PROBE_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = _json.load(f) or {}
            loaded = {}
            for k, v in data.items():
                if isinstance(v, list) and len(v) == 3:
                    try:
                        loaded[k] = (float(v[0]), str(v[1]), str(v[2]))
                    except (ValueError, TypeError):
                        continue
            _PROBE_CACHE = loaded
    except Exception as e:
        print(f"[probe-cache] load failed: {e}")
        _PROBE_CACHE = {}


def _save_probe_cache():
    global _PROBE_CACHE_DIRTY
    with _PROBE_CACHE_LOCK:
        if not _PROBE_CACHE_DIRTY:
            return
        snapshot = {k: list(v) for k, v in _PROBE_CACHE.items()}
        _PROBE_CACHE_DIRTY = False
    try:
        with open(_PROBE_CACHE_FILE, 'w', encoding='utf-8') as f:
            _json.dump(snapshot, f)
    except Exception as e:
        print(f"[probe-cache] save failed: {e}")


def _subprocess_kwargs():
    return dict(
        capture_output=True,
        encoding='utf-8',
        errors='replace',
    )


def _probe_full(filepath):
    """Возвращает (codec_name, format_name). Кэшируется по mtime."""
    global _PROBE_CACHE_DIRTY

    try:
        st = os.stat(filepath)
        mtime = st.st_mtime
    except OSError:
        return '', ''

    with _PROBE_CACHE_LOCK:
        cached = _PROBE_CACHE.get(filepath)
        if cached and cached[0] == mtime:
            return cached[1], cached[2]

    codec = ''
    fmt = ''
    try:
        r = subprocess.run(
            [FFPROBE_PATH, '-v', 'error',
             '-select_streams', 'v:0',
             '-show_entries', 'stream=codec_name:format=format_name',
             '-of', 'json',
             filepath],
            timeout=20,
            **_subprocess_kwargs(),
        )
        if r.returncode == 0 and r.stdout:
            data = _json.loads(r.stdout)
            streams = data.get('streams', [])
            if streams:
                codec = (streams[0].get('codec_name', '') or '').lower()
            fmt = (data.get('format', {}).get('format_name', '') or '').lower()
    except Exception:
        pass

    with _PROBE_CACHE_LOCK:
        _PROBE_CACHE[filepath] = (mtime, codec, fmt)
        _PROBE_CACHE_DIRTY = True

    return codec, fmt


def probe_metadata(filepath):
    info = {
        'duration': 0, 'width': 0, 'height': 0,
        'codec': '', 'bitrate': 0, 'fps': 0.0,
        'orientation': 'horizontal',
    }
    try:
        r = subprocess.run(
            [FFPROBE_PATH, '-v', 'quiet', '-print_format', 'json',
             '-show_streams', '-show_format', filepath],
            timeout=30,
            **_subprocess_kwargs(),
        )
        if r.returncode != 0 or not r.stdout:
            return info
        data = _json.loads(r.stdout)
        streams = data.get('streams', [])
        v = next((s for s in streams if s.get('codec_type') == 'video'), None)
        if v:
            info['width'] = int(v.get('width', 0) or 0)
            info['height'] = int(v.get('height', 0) or 0)
            info['codec'] = (v.get('codec_name', '') or '').lower()
            fps_str = v.get('r_frame_rate', '0/1')
            if '/' in fps_str:
                n, d = fps_str.split('/')
                try:
                    info['fps'] = float(n) / float(d) if float(d) else 0.0
                except Exception:
                    info['fps'] = 0.0
            info['orientation'] = ('vertical' if info['height'] > info['width']
                                   else 'horizontal')
        fmt = data.get('format', {})
        info['duration'] = int(float(fmt.get('duration', 0) or 0))
        info['bitrate'] = int(fmt.get('bit_rate', 0) or 0)
    except Exception:
        pass
    return info


def is_playable(filepath, probe_seconds=10):
    """
    Возвращает (ok: bool, reason: str).

    True  — файл декодируется без фатальных ошибок за первые N секунд.
    False — есть фатальные маркеры (Invalid NAL и т.п.).
    """
    if not os.path.exists(filepath):
        return False, 'file not found'

    cmd = [
        FFMPEG_PATH, '-v', 'warning',
        '-an', '-sn', '-dn',
        '-i', filepath,
        '-t', str(probe_seconds),
        '-f', 'null', '-',
    ]
    try:
        r = subprocess.run(cmd, timeout=90, **_subprocess_kwargs())
    except subprocess.TimeoutExpired:
        return True, 'timeout (assumed ok)'
    except Exception as e:
        return False, f'spawn error: {e}'

    low = (r.stderr or '').lower()
    found = [m for m in _FATAL_MARKERS if m in low]

    if found:
        return False, ', '.join(found)
    return True, 'ok'


def needs_conversion(filepath, codec=None, format_name=None):
    """
    Возвращает (True, reason) или (False, None).
    reason: 'codec' | 'container' | 'extension'
    """
    ext = os.path.splitext(filepath)[1].lower()
    codec = (codec or '').lower()
    fmt = (format_name or '').lower()

    if ext in BAD_EXTENSIONS:
        return True, 'extension'

    if not codec or not fmt:
        probed_codec, probed_fmt = _probe_full(filepath)
        if not codec:
            codec = probed_codec
        if not fmt:
            fmt = probed_fmt

    if codec in BAD_CODECS:
        return True, 'codec'

    if fmt:
        parts = set(fmt.split(','))
        if parts & BAD_FORMATS and not (parts & GOOD_FORMATS):
            return True, 'container'

    return False, None


def _move_to_duplicates(src_path, keep_name=None):
    """Оригинал при конвертации уезжает сюда."""
    drive = os.path.splitdrive(src_path)[0] + os.sep
    dup_root = os.path.join(drive, '!Duplicates')
    os.makedirs(dup_root, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    sub = os.path.join(dup_root, f'converted_{ts}')
    os.makedirs(sub, exist_ok=True)
    name = keep_name or os.path.basename(src_path)
    dst = os.path.join(sub, name)
    base, ext = os.path.splitext(name)
    counter = 1
    while os.path.exists(dst):
        dst = os.path.join(sub, f'{base}_{counter}{ext}')
        counter += 1
    shutil.move(src_path, dst)
    return dst


def move_to_broken(src_path, keep_name=None):
    r"""Переносит битый файл в <диск>:\!Broken\ (одна папка, без подпапок)."""
    if not os.path.exists(src_path):
        return None

    drive = os.path.splitdrive(src_path)[0] + os.sep
    broken_root = os.path.join(drive, '!Broken')
    try:
        os.makedirs(broken_root, exist_ok=True)
    except OSError as e:
        print(f"[broken] cannot create !Broken dir: {e}")
        return None

    name = keep_name or os.path.basename(src_path)
    dst = os.path.join(broken_root, name)
    base, ext = os.path.splitext(name)
    counter = 1
    while os.path.exists(dst):
        dst = os.path.join(broken_root, f'{base}_{counter}{ext}')
        counter += 1

    try:
        shutil.move(src_path, dst)
    except Exception as e:
        print(f"[broken] move failed for {src_path}: {e}")
        return None

    return dst


def _cleanup_tmp(tmp):
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass


def _run_ffmpeg(cmd, timeout=7200):
    try:
        r = subprocess.run(cmd, timeout=timeout, **_subprocess_kwargs())
        return r.returncode == 0, (r.stderr or '')[-500:]
    except subprocess.TimeoutExpired:
        return False, 'timeout'
    except Exception as e:
        return False, str(e)


def _do_remux(src, tmp):
    """Быстрая перепаковка без перекодирования."""
    cmd = [
        FFMPEG_PATH, '-y', '-i', src,
        '-map', '0:v:0', '-map', '0:a:0?',
        '-c', 'copy', '-movflags', '+faststart', tmp,
    ]
    return _run_ffmpeg(cmd)


def _do_reencode(src, tmp):
    """Перекодирование видео в H.264, аудио — AAC."""
    cmd = [
        FFMPEG_PATH, '-y', '-i', src,
        '-map', '0:v:0', '-map', '0:a:0?',
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '192k', '-ac', '2',
        '-movflags', '+faststart', tmp,
    ]
    return _run_ffmpeg(cmd)


def convert_video_in_place(video):
    """
    Конвертирует видео прямо в его папке.

    Возвращает (True, new_video, message) или (False, None, error).
    При любой ошибке оригинал НЕ трогается.
    """
    src = video['filepath']
    if not os.path.exists(src):
        return False, None, 'file not found'

    ok, reason = is_playable(src)
    if not ok:
        return False, None, f'broken stream ({reason})'

    folder = os.path.dirname(src)
    base_name = os.path.splitext(video['filename'])[0]
    codec = (video.get('codec') or '').lower()

    if not codec:
        codec, _ = _probe_full(src)

    need_reencode = codec in BAD_CODECS
    dst_name = base_name + '.mp4'
    dst = os.path.join(folder, dst_name)

    same_path = (os.path.normcase(os.path.abspath(src))
                 == os.path.normcase(os.path.abspath(dst)))

    ts_ms = int(time.time() * 1000)
    tmp = os.path.join(
        folder,
        f'.__conv_{os.getpid()}_{ts_ms}_{base_name}.tmp.mp4'
    )

    try:
        if need_reencode:
            print(f"[convert] re-encode: "
                  f"{os.path.basename(src)} -> {dst_name}")
            ok_run, err = _do_reencode(src, tmp)
            mode_label = 're-encode'
        else:
            print(f"[convert] remux: "
                  f"{os.path.basename(src)} -> {dst_name}")
            ok_run, err = _do_remux(src, tmp)
            mode_label = 'remux'

        if ok_run and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            ok_play, why = is_playable(tmp)
            if not ok_play:
                print(f"[convert] {mode_label} result not playable "
                      f"({why}); retrying with re-encode")
                _cleanup_tmp(tmp)
                ok_run = False

        if not ok_run and not need_reencode:
            print(f"[convert] fallback to re-encode: {os.path.basename(src)}")
            ok_run, err = _do_reencode(src, tmp)
            mode_label = 're-encode'
            if ok_run and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                ok_play, why = is_playable(tmp)
                if not ok_play:
                    print(f"[convert] re-encode result not playable ({why})")
                    ok_run = False

        if not ok_run:
            _cleanup_tmp(tmp)
            return False, None, err or 'ffmpeg failed'

        if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            _cleanup_tmp(tmp)
            return False, None, 'empty output'

        moved_old = None

        if not same_path and os.path.exists(dst):
            try:
                _move_to_duplicates(dst, keep_name=os.path.basename(dst))
            except Exception as e:
                _cleanup_tmp(tmp)
                return False, None, f'cannot move existing dst: {e}'

        if same_path:
            try:
                moved_old = _move_to_duplicates(src, keep_name=video['filename'])
            except Exception as e:
                _cleanup_tmp(tmp)
                return False, None, f'cannot move original: {e}'

        try:
            os.replace(tmp, dst)
        except Exception as e:
            if moved_old:
                try:
                    shutil.move(moved_old, src)
                except Exception:
                    pass
            _cleanup_tmp(tmp)
            return False, None, f'cannot place new file: {e}'

        if not same_path:
            try:
                moved_old = _move_to_duplicates(src, keep_name=video['filename'])
            except Exception as e:
                print(f"[convert] warning: original not moved: {e}")

        meta = probe_metadata(dst)
        new_size = os.path.getsize(dst)

        new_video = {
            'filename': dst_name,
            'filepath': dst,
            'folder': folder,
            'size': new_size,
            'duration': meta['duration'],
            'width': meta['width'],
            'height': meta['height'],
            'codec': meta['codec'],
            'bitrate': meta['bitrate'],
            'fps': meta['fps'],
            'orientation': meta['orientation'],
        }

        moved_str = f'; old -> {moved_old}' if moved_old else ''
        msg = (f"{mode_label}: {video['filename']} -> {dst_name} "
               f"({new_size / (1024 * 1024):.1f} MB){moved_str}")
        print(f"[convert] OK {msg}")
        return True, new_video, msg

    except subprocess.TimeoutExpired:
        _cleanup_tmp(tmp)
        return False, None, 'timeout'
    except Exception as e:
        _cleanup_tmp(tmp)
        return False, None, str(e)


def update_db_after_conversion(video_id, new_video):
    from models import get_db_connection
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE videos SET
                filename = ?, filepath = ?, folder = ?, size = ?,
                duration = ?, width = ?, height = ?, codec = ?,
                bitrate = ?, fps = ?, orientation = ?
            WHERE id = ?
        ''', (
            new_video['filename'], new_video['filepath'], new_video['folder'],
            new_video['size'], new_video['duration'], new_video['width'],
            new_video['height'], new_video['codec'], new_video['bitrate'],
            new_video['fps'], new_video['orientation'], video_id,
        ))
        conn.commit()
    finally:
        conn.close()