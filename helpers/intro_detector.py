"""Leading splash / studio intro detection for local video files.

The detector is intentionally conservative. It analyses only the beginning of
a file (up to ``MAX_ANALYSIS_SECONDS``) with two cheap ffmpeg passes:

* low-resolution frames -> scene changes / static splash frames;
* mono PCM -> audio fade-in and silent intro.

It returns an estimated ``intro_end`` in seconds. Duplicate matching also uses
``intro_skip_seconds()``, which applies a safe heuristic for long videos even
when the detector did not find a clear boundary.
"""
import os
import subprocess

import numpy as np

from helpers.subprocess_utils import hidden_process_kwargs


DETECTOR_VERSION = 1

MAX_ANALYSIS_SECONDS = 150.0
FRAME_FPS = 2.0
FRAME_W = 32
FRAME_H = 18
AUDIO_RATE = 8000
AUDIO_WINDOW_SEC = 0.5

DEFAULT_SKIP_SEC = 30.0
LONG_VIDEO_SEC = 300.0
LONG_VIDEO_SKIP_SEC = 60.0
MAX_SKIP_RATIO = 0.25

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FFMPEG_PATH = None
_FFPROBE_PATH = None


def _binary(kind):
    global _FFMPEG_PATH, _FFPROBE_PATH
    if kind == 'ffmpeg' and _FFMPEG_PATH is not None:
        return _FFMPEG_PATH
    if kind == 'ffprobe' and _FFPROBE_PATH is not None:
        return _FFPROBE_PATH

    name = 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'
    if kind == 'ffprobe':
        name = 'ffprobe.exe' if os.name == 'nt' else 'ffprobe'
    candidate = os.path.join(_BASE_DIR, '_dop', name)
    found = candidate if os.path.exists(candidate) else kind

    if kind == 'ffmpeg':
        _FFMPEG_PATH = found
    else:
        _FFPROBE_PATH = found
    return found


def _run(cmd, timeout, binary_output=False):
    kwargs = hidden_process_kwargs(
        capture_output=True,
        text=False if binary_output else True,
        timeout=timeout,
    )
    return subprocess.run(cmd, check=False, **kwargs)


def probe_duration(filepath, fallback=0.0):
    """Return container duration in seconds, or ``fallback`` on failure."""
    try:
        r = _run([
            _binary('ffprobe'), '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            filepath,
        ], timeout=30)
        if r.returncode == 0 and r.stdout:
            value = float(str(r.stdout).strip().splitlines()[0])
            if value > 0:
                return value
    except Exception:
        pass
    return float(fallback or 0.0)


def _read_frames(filepath, window):
    """Return (times, frame_diff, brightness) sampled from the intro window."""
    try:
        r = _run([
            _binary('ffmpeg'), '-v', 'error',
            '-ss', '0', '-t', f'{window:.3f}', '-i', filepath,
            '-vf', f'fps={FRAME_FPS},scale={FRAME_W}:{FRAME_H},format=gray',
            '-f', 'rawvideo', 'pipe:1',
        ], timeout=180, binary_output=True)
        raw = r.stdout or b''
    except Exception:
        return None, None, None

    frame_size = FRAME_W * FRAME_H
    count = len(raw) // frame_size
    if count < 4:
        return None, None, None

    try:
        frames = np.frombuffer(raw[:count * frame_size], dtype=np.uint8)
        frames = frames.reshape(count, FRAME_H, FRAME_W).astype(np.float32)
    except Exception:
        return None, None, None

    diffs = np.abs(frames[1:] - frames[:-1]).mean(axis=(1, 2))
    brightness = frames.mean(axis=(1, 2))
    times = (np.arange(len(diffs)) + 1) / FRAME_FPS
    return times, diffs, brightness


def _read_audio_rms(filepath, window):
    """Return RMS per 0.5 s window. Empty array means no usable audio."""
    try:
        r = _run([
            _binary('ffmpeg'), '-v', 'error',
            '-ss', '0', '-t', f'{window:.3f}', '-i', filepath,
            '-vn', '-ac', '1', '-ar', str(AUDIO_RATE),
            '-f', 's16le', 'pipe:1',
        ], timeout=180, binary_output=True)
        raw = r.stdout or b''
    except Exception:
        return np.empty(0, dtype=np.float32)

    samples_per_win = int(AUDIO_RATE * AUDIO_WINDOW_SEC)
    usable = len(raw) // 2
    usable -= usable % samples_per_win
    if usable < samples_per_win * 2:
        return np.empty(0, dtype=np.float32)

    try:
        pcm = np.frombuffer(raw[:usable * 2], dtype='<i2').astype(np.float32)
        pcm /= 32768.0
        frames = pcm.reshape(-1, samples_per_win)
        return np.sqrt((frames * frames).mean(axis=1))
    except Exception:
        return np.empty(0, dtype=np.float32)


def _first_sustained(mask, run=3):
    """Index at which ``mask`` is true for ``run`` consecutive windows."""
    if mask.size < run:
        return None
    for i in range(mask.size - run + 1):
        if bool(np.all(mask[i:i + run])):
            return i
    return None


def detect_intro_end(filepath, duration=0.0):
    """Detect the end of a leading splash/intro.

    Returns a dict: ``end`` (seconds, 0 = no confident boundary),
    ``confidence`` (0..1), ``reason``.
    """
    result = {'end': 0.0, 'confidence': 0.0, 'reason': 'not analysed'}
    if not filepath or not os.path.exists(filepath):
        result['reason'] = 'missing file'
        return result

    duration = float(duration or 0.0) or probe_duration(filepath)
    if duration <= 0:
        duration = MAX_ANALYSIS_SECONDS

    window = min(duration, MAX_ANALYSIS_SECONDS)
    if window < 6:
        result['reason'] = 'too short'
        return result

    times, diffs, _brightness = _read_frames(filepath, window)
    if times is None or diffs.size < 4:
        result['reason'] = 'no frames'
        return result

    rms = _read_audio_rms(filepath, window)
    has_audio = rms.size >= 4

    # Static splash frames produce very small frame-to-frame differences.
    static_limit = max(2.5, float(np.percentile(diffs, 25)) * 1.5)
    static = diffs < static_limit
    scene_change = diffs > max(12.0, float(np.percentile(diffs, 90)) * 1.6)

    if has_audio:
        late = rms[int(len(rms) * 0.35):]
        noise_floor = float(np.percentile(late, 25)) if late.size else 0.0
        audio_threshold = max(0.012, noise_floor * 0.6,
                              float(np.percentile(rms, 75)) * 0.18)
        quiet = rms < audio_threshold
        # Align frame-based windows (2 fps) with 0.5 s audio windows.
        audio_active = ~quiet
        active_idx = _first_sustained(audio_active, run=4)
        visual_active_idx = _first_sustained(~static, run=3)
        candidates = [x for x in (active_idx, visual_active_idx) if x is not None]
        if not candidates:
            result['reason'] = 'no content transition'
            return result
        active_idx = min(candidates)

        # The intro is the last static/quiet window before sustained content.
        search_end = min(active_idx, static.size)
        intro_idx = None
        for i in range(search_end - 1, -1, -1):
            audio_i = min(int(times[i] / AUDIO_WINDOW_SEC), len(quiet) - 1)
            if static[i] and quiet[audio_i]:
                intro_idx = i
                break
        if intro_idx is None:
            for i in range(search_end - 1, -1, -1):
                audio_i = min(int(times[i] / AUDIO_WINDOW_SEC), len(quiet) - 1)
                if static[i] or quiet[audio_i]:
                    intro_idx = i
                    break
    else:
        active_idx = _first_sustained(~static, run=3)
        if active_idx is None:
            result['reason'] = 'no visual transition'
            return result
        intro_idx = None
        for i in range(active_idx - 1, -1, -1):
            if static[i]:
                intro_idx = i
                break

    if intro_idx is None and active_idx is not None and active_idx > 0:
        intro_idx = active_idx - 1
    if intro_idx is None:
        result['reason'] = 'no intro boundary'
        return result

    end = float(times[intro_idx] + (1.0 / FRAME_FPS))
    # A hard scene cut right after the intro is a strong signal.
    cut_idx = None
    for i in range(intro_idx, min(intro_idx + 4, scene_change.size)):
        if bool(scene_change[i]):
            cut_idx = i
            break
    if cut_idx is not None:
        end = max(end, float(times[cut_idx]))

    max_end = min(window, max(30.0, duration * 0.4))
    end = min(end, max_end)
    if end < 1.5:
        result['reason'] = 'boundary too early'
        return result

    confidence = 0.65
    if has_audio:
        confidence += 0.15
    if cut_idx is not None:
        confidence += 0.15
    if active_idx is not None and intro_idx >= max(0, active_idx - 2):
        confidence += 0.05

    result.update({
        'end': round(end, 2),
        'confidence': round(min(confidence, 0.95), 2),
        'reason': 'static/quiet intro with content transition',
    })
    return result


def intro_skip_seconds(video):
    """Seconds of the beginning to ignore for duplicate matching.

    Uses a detected intro when available. Long videos get a safe 60 s skip even
    without deep analysis, as requested: studio splashes there create false
    duplicate matches.
    """
    item = video or {}
    try:
        duration = float(item.get('duration') or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        detected = float(item.get('intro_end') or 0.0)
    except (TypeError, ValueError):
        detected = 0.0

    skip = max(DEFAULT_SKIP_SEC, detected)
    if duration >= LONG_VIDEO_SEC:
        skip = max(skip, LONG_VIDEO_SKIP_SEC)

    if duration > 0:
        skip = min(skip, duration * MAX_SKIP_RATIO)
    return max(0.0, float(skip))


def preview_skip_seconds(video):
    """Seek point for cover previews; 0 means the old 6/10% behaviour."""
    item = video or {}
    try:
        detected = float(item.get('intro_end') or 0.0)
    except (TypeError, ValueError):
        detected = 0.0
    try:
        duration = float(item.get('duration') or 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    if detected <= 0 and duration < LONG_VIDEO_SEC:
        return 0.0
    skip = detected + 2.0 if detected > 0 else 0.0
    if duration >= LONG_VIDEO_SEC:
        skip = max(skip, LONG_VIDEO_SKIP_SEC)
    if duration > 0:
        skip = min(skip, duration * MAX_SKIP_RATIO)
    return max(0.0, float(skip))
