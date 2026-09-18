"""
Генерация и хранение превью-кадров для видео.

Каждое превью — JPEG-файл <video_id>.jpg в APP_DATA_DIR/thumbnails/.
Генерируется через ffmpeg (берётся из _dop/ffmpeg.exe или из PATH).
Генерируется лениво при первом запросе /thumbnail/<id>.
"""
import os
import subprocess
from .connection import APP_DATA_DIR


THUMBNAILS_DIR = os.path.join(APP_DATA_DIR, 'thumbnails')

_FFMPEG_PATH = None


def _get_ffmpeg_path():
    """Ищет ffmpeg: сначала в _dop/, потом в PATH."""
    global _FFMPEG_PATH
    if _FFMPEG_PATH is not None:
        return _FFMPEG_PATH

    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(here)
    candidates = [
        os.path.join(project_root, '_dop', 'ffmpeg.exe'),
        os.path.join(project_root, '_dop', 'ffmpeg'),
    ]
    for c in candidates:
        if os.path.exists(c):
            _FFMPEG_PATH = c
            return c

    _FFMPEG_PATH = 'ffmpeg'  # fallback на системный PATH
    return _FFMPEG_PATH


def ensure_dir():
    if not os.path.exists(THUMBNAILS_DIR):
        os.makedirs(THUMBNAILS_DIR, exist_ok=True)


def get_thumbnail_path(video_id):
    return os.path.join(THUMBNAILS_DIR, f'{video_id}.jpg')


def has_thumbnail(video_id):
    return os.path.exists(get_thumbnail_path(video_id))


def delete_thumbnail(video_id):
    p = get_thumbnail_path(video_id)
    try:
        if os.path.exists(p):
            os.remove(p)
    except Exception as e:
        print(f"[thumbnails] delete error for {video_id}: {e}")


def generate_thumbnail(video_id, filepath, duration=0):
    """Генерирует JPEG-превью для видео. Возвращает True при успехе."""
    if not filepath or not os.path.exists(filepath):
        return False

    ensure_dir()
    out = get_thumbnail_path(video_id)

    # Точка захвата: 10% от длительности (не больше 10 сек, не меньше 1 сек)
    if duration and duration > 3:
        target_seconds = min(max(duration * 0.1, 1), 10)
    else:
        target_seconds = 1.0

    ffmpeg = _get_ffmpeg_path()

    cmd = [
        ffmpeg,
        '-y',
        '-ss', str(target_seconds),
        '-i', filepath,
        '-vframes', '1',
        '-vf', 'scale=480:-2',
        '-q:v', '4',
        out,
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and os.path.exists(out):
            return True
        err_tail = (result.stderr or '')[-200:]
        print(f"[thumbnails] ffmpeg rc={result.returncode} for {video_id}: {err_tail}")
        return False
    except subprocess.TimeoutExpired:
        print(f"[thumbnails] timeout for {video_id}")
        return False
    except FileNotFoundError:
        print(f"[thumbnails] ffmpeg not found at {ffmpeg}")
        return False
    except Exception as e:
        print(f"[thumbnails] error for {video_id}: {e}")
        return False


def generate_all_missing(get_all_videos_fn):
    """
    Прогревает кэш превью для всех видео, у которых их нет.
    Возвращает (generated, total).
    """
    ensure_dir()
    videos = get_all_videos_fn(mode=None)
    total = len(videos)
    generated = 0
    for v in videos:
        if has_thumbnail(v['id']):
            continue
        if generate_thumbnail(v['id'], v['filepath'], v.get('duration', 0)):
            generated += 1
    return generated, total


def clear_all_thumbnails():
    """Удаляет все превью. Возвращает количество удалённых."""
    if not os.path.exists(THUMBNAILS_DIR):
        return 0
    count = 0
    for f in os.listdir(THUMBNAILS_DIR):
        if f.endswith('.jpg'):
            try:
                os.remove(os.path.join(THUMBNAILS_DIR, f))
                count += 1
            except Exception:
                pass
    return count