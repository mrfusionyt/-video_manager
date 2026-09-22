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

    _FFMPEG_PATH = 'ffmpeg'
    return _FFMPEG_PATH


def _subprocess_kwargs():
    """Общие параметры для subprocess.run.

    encoding + errors критично важны на Windows: без них Python
    читает вывод как cp1252 и падает на кириллических путях.
    """
    return dict(
        capture_output=True,
        encoding='utf-8',
        errors='replace',
    )


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


def _run_ffmpeg(cmd, video_id, timeout=30):
    """Запускает ffmpeg, возвращает (success, stderr_tail)."""
    try:
        result = subprocess.run(cmd, timeout=timeout, **_subprocess_kwargs())
    except subprocess.TimeoutExpired:
        print(f"[thumbnails] timeout for {video_id}")
        return False, 'timeout'
    except FileNotFoundError:
        print(f"[thumbnails] ffmpeg not found at {cmd[0]}")
        return False, 'ffmpeg not found'
    except Exception as e:
        print(f"[thumbnails] ffmpeg spawn error for {video_id}: {e}")
        return False, str(e)

    if result.returncode == 0:
        return True, ''

    err_tail = (result.stderr or '')[-300:].strip()
    return False, err_tail


def generate_thumbnail(video_id, filepath, duration=0):
    """
    Генерирует JPEG-превью для видео. Возвращает True при успехе.

    Стратегия:
      1. Если задана duration — берём кадр на 10% (1..10 сек).
         Быстрый -ss ДО -i (keyframe seek).
      2. Если первый вариант упал (битые timestamps, mpegts-контейнер) —
         повторяем с -fflags +genpts+igndts и увеличенными probesize/analyzeduration,
         но уже без -ss (берём первый кадр).
    """
    if not filepath or not os.path.exists(filepath):
        return False

    ensure_dir()
    out = get_thumbnail_path(video_id)

    # Убираем предыдущий обломок, если был
    try:
        if os.path.exists(out):
            os.remove(out)
    except OSError:
        pass

    # Точка захвата: 10% от длительности (1..10 сек)
    if duration and duration > 3:
        target_seconds = min(max(duration * 0.1, 1), 10)
    else:
        target_seconds = 1.0

    ffmpeg = _get_ffmpeg_path()

    # -------- Попытка 1: быстрый seek --------
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
    ok, err = _run_ffmpeg(cmd, video_id, timeout=30)
    if ok and os.path.exists(out) and os.path.getsize(out) > 0:
        return True

    # -------- Попытка 2: для битых контейнеров (mpegts и т.п.) --------
    # -fflags +genpts+igndts — чинит битые timestamps
    # -analyzeduration / -probesize — увеличиваем анализ входа
    # -ss убран — берём первый доступный кадр
    print(f"[thumbnails] retry with mpegts options for {video_id} (prev rc: {err[:80]})")
    cmd2 = [
        ffmpeg,
        '-y',
        '-fflags', '+genpts+igndts',
        '-analyzeduration', '100M',
        '-probesize', '100M',
        '-i', filepath,
        '-vframes', '1',
        '-vf', 'scale=480:-2',
        '-q:v', '4',
        out,
    ]
    ok2, err2 = _run_ffmpeg(cmd2, video_id, timeout=60)
    if ok2 and os.path.exists(out) and os.path.getsize(out) > 0:
        return True

    # -------- Попытка 3: то же, но с seek на 1 сек --------
    cmd3 = [
        ffmpeg,
        '-y',
        '-fflags', '+genpts+igndts',
        '-analyzeduration', '100M',
        '-probesize', '100M',
        '-ss', '1',
        '-i', filepath,
        '-vframes', '1',
        '-vf', 'scale=480:-2',
        '-q:v', '4',
        out,
    ]
    ok3, err3 = _run_ffmpeg(cmd3, video_id, timeout=60)
    if ok3 and os.path.exists(out) and os.path.getsize(out) > 0:
        return True

    # Всё упало — логируем, но НЕ крашимся
    print(f"[thumbnails] ffmpeg failed for {video_id}: {err2[:200]}")
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