"""
Генерация и хранение превью-кадров для видео.

Каждое главное превью — JPEG-файл <video_id>.jpg в APP_DATA_DIR/thumbnails/.
Генерируется через ffmpeg (берётся из _dop/ffmpeg.exe или из PATH).
Генерируется лениво при первом запросе /thumbnail/<id>.

Кадры для ручного выбора хранятся в thumbnails/candidates/ и не влияют
на главное превью, пока пользователь не выберет один из них.
"""
import os
import subprocess

from helpers.subprocess_utils import hidden_process_kwargs
from helpers.intro_detector import preview_skip_seconds
from .connection import APP_DATA_DIR


THUMBNAILS_DIR = os.path.join(APP_DATA_DIR, 'thumbnails')
CANDIDATES_DIR = os.path.join(THUMBNAILS_DIR, 'candidates')

# Показ превью начинается не с начала файла, чтобы не попадать в интро.
PREVIEW_START_SEC = 60.0
PREVIEW_CANDIDATE_COUNT = 6

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
    """Общие параметры для subprocess.run без мигающего терминала."""
    return hidden_process_kwargs(capture_output=True)


def ensure_dir():
    os.makedirs(THUMBNAILS_DIR, exist_ok=True)
    os.makedirs(CANDIDATES_DIR, exist_ok=True)


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


def _source_mtime(filepath):
    try:
        return int(os.path.getmtime(filepath)) if filepath else 0
    except OSError:
        return 0


def get_preview_frame_path(video_id, seconds, filepath=None):
    """Кэш одного кадра для модалки выбора главного превью."""
    stamp = int(round(float(seconds or 0.0)))
    mtime = _source_mtime(filepath)
    return os.path.join(CANDIDATES_DIR, f'{video_id}_{stamp}_{mtime}.jpg')


def has_preview_frame(video_id, seconds, filepath=None):
    return os.path.exists(get_preview_frame_path(video_id, seconds, filepath))


def delete_preview_frames(video_id):
    """Чистим кандидатов конкретного видео после смены превью/файла."""
    prefix = f'{video_id}_'
    try:
        if not os.path.isdir(CANDIDATES_DIR):
            return 0
        removed = 0
        for name in os.listdir(CANDIDATES_DIR):
            if name.startswith(prefix) and name.endswith('.jpg'):
                try:
                    os.remove(os.path.join(CANDIDATES_DIR, name))
                    removed += 1
                except OSError:
                    pass
        return removed
    except Exception as e:
        print(f"[thumbnails] candidate cleanup error for {video_id}: {e}")
        return 0


def preview_candidate_times(duration=0):
    """Равномерные точки от 1 минуты до конца ролика (6 кадров)."""
    try:
        duration = float(duration or 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    count = PREVIEW_CANDIDATE_COUNT
    if duration > PREVIEW_START_SEC + 2:
        start = PREVIEW_START_SEC
    elif duration > 4:
        start = min(PREVIEW_START_SEC, max(1.0, duration * 0.25))
    else:
        start = 1.0

    if duration > start + 4:
        end = duration - 2.0
    else:
        # Длительность неизвестна или видео очень короткое — всё равно даём выбор.
        start = PREVIEW_START_SEC if duration <= 0 else start
        end = start + 30.0 * (count - 1)

    if duration > 0:
        end = min(end, max(start + 0.5, duration - 1.0))
    if end <= start:
        end = start + 1.0
    step = (end - start) / float(count - 1)
    # Candidate URLs and saved preview times are whole seconds, so dedupe
    # rounded values (important for very short videos).
    times = sorted(set(int(round(start + i * step)) for i in range(count)))
    return times or [1]


def choose_preview_seconds(duration=0, intro_end=0, preview_at=0):
    """Выбирает секунду кадра: ручная точка или авто (не раньше 1 минуты)."""
    try:
        duration = float(duration or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        intro_end = float(intro_end or 0.0)
    except (TypeError, ValueError):
        intro_end = 0.0
    try:
        preview_at = float(preview_at or 0.0)
    except (TypeError, ValueError):
        preview_at = 0.0

    if preview_at > 0:
        target = preview_at
    elif duration > PREVIEW_START_SEC + 1:
        skip = preview_skip_seconds({
            'intro_end': intro_end, 'duration': duration})
        target = max(skip, PREVIEW_START_SEC)
    elif duration > 4:
        target = min(PREVIEW_START_SEC, max(1.0, duration * 0.25))
    else:
        target = 1.0

    if duration > 0:
        target = min(target, max(1.0, duration - 1.0))
    return round(max(1.0, float(target)), 2)


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


def _capture_frame(video_id, filepath, target, out):
    """Достаёт один кадр в out, перебирая запасные варианты для битых контейнеров."""
    if not filepath or not os.path.exists(filepath):
        return False

    ensure_dir()
    out_dir = os.path.dirname(out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    try:
        if os.path.exists(out):
            os.remove(out)
    except OSError:
        pass

    ffmpeg = _get_ffmpeg_path()

    # -------- Попытка 1: быстрый seek --------
    cmd = [
        ffmpeg,
        '-y',
        '-ss', str(target),
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

    # -------- Попытка 3: тот же seek, но с восстановленными timestamps --------
    cmd3 = [
        ffmpeg,
        '-y',
        '-fflags', '+genpts+igndts',
        '-analyzeduration', '100M',
        '-probesize', '100M',
        '-ss', str(target),
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


def generate_thumbnail(video_id, filepath, duration=0, intro_end=0, preview_at=0):
    """Генерирует главное JPEG-превью для видео. Возвращает True при успехе."""
    target = choose_preview_seconds(duration, intro_end, preview_at)
    return _capture_frame(video_id, filepath, target, get_thumbnail_path(video_id))


def generate_preview_frame(video_id, filepath, seconds, duration=0):
    """Генерирует один кадр для модалки выбора главного превью."""
    target = choose_preview_seconds(duration=duration, preview_at=seconds)
    out = get_preview_frame_path(video_id, target, filepath)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return True
    return _capture_frame(video_id, filepath, target, out)


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
        if generate_thumbnail(v['id'], v['filepath'], v.get('duration', 0),
                              v.get('intro_end', 0), v.get('preview_at', 0)):
            generated += 1
    return generated, total


def clear_all_thumbnails():
    """Удаляет все превью. Возвращает количество удалённых."""
    if not os.path.exists(THUMBNAILS_DIR):
        return 0
    count = 0
    for f in os.listdir(THUMBNAILS_DIR):
        p = os.path.join(THUMBNAILS_DIR, f)
        if os.path.isfile(p) and f.endswith('.jpg'):
            try:
                os.remove(p)
                count += 1
            except Exception:
                pass
        elif os.path.isdir(p):
            try:
                for name in os.listdir(p):
                    if name.endswith('.jpg'):
                        os.remove(os.path.join(p, name))
                        count += 1
            except Exception:
                pass
    return count
