r"""
Поиск дубликатов — версия с частичным вложением (substring match).

Архитектура:
  • SQLite-кеш (hash_cache.db): files / video_frames / audio_fp /
    ignored_pairs.

  • Video fingerprint: 25 ключевых кадров (5%..95% с шагом),
    4 зеркала на кадр = 100 phash.

  • Логика video-пары:
      1) "full"   — покрытие обоих ≥ FULL_COVERAGE (0.8), hamming ≤ 2,
                    порядок кадров монотонный.
      2) "partial"— общая подпоследовательность ≥ MIN_SUBSEQ_FRAMES
                    (по умолчанию 6 из 25), порядок монотонный,
                    покрытие хотя бы одного < 0.8.
                    Это ловит "10-минутное содержит 7-минутное".

  • Audio: chromaprint → LSH → FFT → longest-run.
      run >= AUDIO_FULL_MIN_RUN_SEC (30s) — full
      run >= AUDIO_PARTIAL_MIN_RUN_SEC (60s) — partial

  • Blacklist пар применяется ко всем парам.

Прогресс-фазы: 0-40 хеширование, 40-45 video-index, 45-65 video verify,
65-70 audio LSH, 70-95 audio verify, 95-100 группировка.
"""
import os
import json
import base64
import struct
import shutil
import sqlite3
import threading
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import cv2
from PIL import Image, ImageOps
import imagehash

from models import get_all_videos, get_db_connection, get_video_by_id


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DUPLICATE_CACHE_FILE = os.path.join(BASE_DIR, 'duplicates_cache.json')
HASH_CACHE_DB        = os.path.join(BASE_DIR, 'hash_cache.db')

FPCALC_CANDIDATES = [
    os.path.join(BASE_DIR, '_dop', 'fpcalc.exe'),
    os.path.join(BASE_DIR, '_dop', 'fpcalc'),
    shutil.which('fpcalc'),
]

# ---------- Video fingerprint ----------
# 25 кадров от 5% до 95%. Шаг 3.75%. 4 зеркала на кадр = 100 phash.
KEYFRAME_POSITIONS = [round(0.05 + i * 0.0375, 4) for i in range(25)]

# ---------- FAST / DEEP thresholds ----------
# hamming ≤ этого значения = кадры совпали
FAST_MAX_HAMMING = 2

# FULL — классический дубликат (одно и то же видео).
# Покрытие: сколько ключевых кадров из A нашли в B / всего.
FULL_COVERAGE = 0.80

# PARTIAL — вложение (7 мин внутри 10 мин).
# Требуем минимум N последовательных совпавших кадров (в штуках из 25).
# 6 из 25 ≈ 24% длины. Для 7-минутного = 1.7 минуты. Для 10-минутного = 2.4 мин.
# Слишком мало → много ложных; слишком много → пропустим короткие вложения.
PARTIAL_MIN_SUBSEQ_FRAMES = 6

# Если у двух видео совпало меньше — вообще не считаем парой.
# Меньше этого = шум.
MIN_MATCHED_FRAMES = 3

# ---------- Audio thresholds ----------
AUDIO_SKIP_INTRO_SEC   = 30.0
AUDIO_SUBPRINT_PER_SEC = 1.0 / 0.1234
AUDIO_FPCALC_MAX_LENGTH = 900

AUDIO_FULL_MIN_RUN_SEC    = 30.0    # полный audio-match
AUDIO_PARTIAL_MIN_RUN_SEC = 60.0    # audio-вложение

AUDIO_BITS_TOLERANT = 8
AUDIO_PREFILTER_SAMPLES    = 40
AUDIO_PREFILTER_GOOD_RATIO = 0.40

AUDIO_LSH_BANDS       = 4
AUDIO_LSH_BAND_BITS   = 8
AUDIO_LSH_MIN_MATCHES = 3
AUDIO_LSH_MAX_POS_DELTA = 6

AUDIO_SILENCE_MIN_UNIQUE_RATIO     = 0.12
AUDIO_SILENCE_MIN_TRANSITION_RATIO = 0.12
AUDIO_SILENCE_MIN_MEAN_DIFF_BITS   = 2.5

AUDIO_FFT_PEAK_STD_RATIO = 5.0

# ---------- Параллелизм ----------
COMPARE_WORKERS = min(8, max(2, (os.cpu_count() or 4)))
HASH_WORKERS    = min(4, max(2, (os.cpu_count() or 4)))


FPCALC_PATH = None
for cand in FPCALC_CANDIDATES:
    if cand and os.path.exists(cand):
        FPCALC_PATH = cand
        break

print(f"[dup] fpcalc path = {FPCALC_PATH}")
print(f"[dup] video: {len(KEYFRAME_POSITIONS)} keyframes, "
      f"max_hamming={FAST_MAX_HAMMING}, "
      f"full_coverage>={FULL_COVERAGE}, "
      f"partial_min_subseq={PARTIAL_MIN_SUBSEQ_FRAMES}")
print(f"[dup] audio: full>={AUDIO_FULL_MIN_RUN_SEC:.0f}s, "
      f"partial>={AUDIO_PARTIAL_MIN_RUN_SEC:.0f}s")
print(f"[dup] workers: hash={HASH_WORKERS}, compare={COMPARE_WORKERS}")


# ===================================================================
#                     SQLite cache
# ===================================================================
_local = threading.local()


def _normalize_path(p):
    try:
        return os.path.normcase(os.path.abspath(p))
    except Exception:
        return p


def _get_conn():
    conn = getattr(_local, 'conn', None)
    if conn is None:
        conn = sqlite3.connect(HASH_CACHE_DB, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 60000")
        conn.execute("PRAGMA foreign_keys = ON")
        _local.conn = conn
    return conn


def _init_db():
    conn = _get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            mtime REAL,
            size INTEGER,
            media_type TEXT DEFAULT 'video'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS video_frames (
            file_id INTEGER NOT NULL,
            frame_idx INTEGER NOT NULL,
            position REAL,
            phash_orig TEXT,
            phash_mirror TEXT,
            phash_flip TEXT,
            phash_hv TEXT,
            PRIMARY KEY (file_id, frame_idx),
            FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS audio_fp (
            file_id INTEGER PRIMARY KEY,
            fp_blob BLOB,
            fp_len INTEGER,
            FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS ignored_pairs (
            path_a TEXT NOT NULL,
            path_b TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT,
            PRIMARY KEY (path_a, path_b)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files(path)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ignored_a ON ignored_pairs(path_a)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ignored_b ON ignored_pairs(path_b)")
    conn.commit()


def _get_or_create_file(path, mtime, size, media_type):
    conn = _get_conn()
    c = conn.cursor()
    c.execute("SELECT id, mtime, size FROM files WHERE path = ?", (path,))
    row = c.fetchone()
    if row is None:
        c.execute(
            "INSERT INTO files(path, mtime, size, media_type) VALUES (?, ?, ?, ?)",
            (path, mtime, size, media_type),
        )
        conn.commit()
        return c.lastrowid, False
    if row['mtime'] != mtime or row['size'] != size:
        c.execute(
            "UPDATE files SET mtime = ?, size = ?, media_type = ? WHERE id = ?",
            (mtime, size, media_type, row['id']),
        )
        conn.commit()
        return row['id'], False
    return row['id'], True


def _load_video_frames(file_id):
    c = _get_conn().cursor()
    c.execute("""
        SELECT frame_idx, position,
               phash_orig, phash_mirror, phash_flip, phash_hv
        FROM video_frames WHERE file_id = ?
        ORDER BY frame_idx
    """, (file_id,))
    rows = c.fetchall()
    if not rows:
        return None
    return [
        {
            'position': r['position'],
            'hashes': [r['phash_orig'], r['phash_mirror'],
                       r['phash_flip'], r['phash_hv']],
        }
        for r in rows
    ]


def _save_video_frames(file_id, frames):
    conn = _get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM video_frames WHERE file_id = ?", (file_id,))
    for idx, f in enumerate(frames):
        h = f['hashes']
        c.execute("""
            INSERT INTO video_frames
                (file_id, frame_idx, position,
                 phash_orig, phash_mirror, phash_flip, phash_hv)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (file_id, idx, f['position'], h[0], h[1], h[2], h[3]))
    conn.commit()


def _load_audio_fp(file_id):
    c = _get_conn().cursor()
    c.execute("SELECT fp_blob FROM audio_fp WHERE file_id = ?", (file_id,))
    row = c.fetchone()
    if not row or not row['fp_blob']:
        return None
    raw = row['fp_blob']
    n = len(raw) // 4
    if n == 0:
        return None
    return list(struct.unpack('<%dI' % n, raw[:n * 4]))


def _save_audio_fp(file_id, fp):
    conn = _get_conn()
    c = conn.cursor()
    if fp is None:
        c.execute("DELETE FROM audio_fp WHERE file_id = ?", (file_id,))
    else:
        raw = struct.pack('<%dI' % len(fp), *fp)
        c.execute("""
            INSERT OR REPLACE INTO audio_fp (file_id, fp_blob, fp_len)
            VALUES (?, ?, ?)
        """, (file_id, raw, len(fp)))
    conn.commit()


def _cleanup_db(current_paths):
    conn = _get_conn()
    c = conn.cursor()
    c.execute("SELECT id, path FROM files")
    rows = c.fetchall()
    current = set(current_paths)
    removed = 0
    for r in rows:
        if r['path'] not in current:
            c.execute("DELETE FROM files WHERE id = ?", (r['id'],))
            removed += 1
    if removed:
        conn.commit()
        print(f"[dup] db cleanup: removed {removed} stale entries")


# ===================================================================
#                     Blacklist
# ===================================================================
def _pair_key(p_a, p_b):
    a = _normalize_path(p_a)
    b = _normalize_path(p_b)
    return (a, b) if a < b else (b, a)


def mark_pair_not_duplicate(path_a, path_b, note=''):
    if not path_a or not path_b:
        return False, "Empty path"
    a, b = _pair_key(path_a, path_b)
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("""
            INSERT OR REPLACE INTO ignored_pairs (path_a, path_b, note, created_at)
            VALUES (?, ?, ?, ?)
        """, (a, b, note or '', _now_iso()))
        conn.commit()
    finally:
        pass
    return True, "Ignored"


def unmark_pair_not_duplicate(path_a, path_b):
    if not path_a or not path_b:
        return False
    a, b = _pair_key(path_a, path_b)
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute(
            "DELETE FROM ignored_pairs WHERE path_a = ? AND path_b = ?",
            (a, b),
        )
        conn.commit()
    finally:
        pass
    return True


def list_ignored_pairs():
    _init_db()
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("""
            SELECT path_a, path_b, note, created_at
            FROM ignored_pairs
            ORDER BY created_at DESC
        """)
        return [dict(r) for r in c.fetchall()]
    finally:
        pass


def clear_ignored_pairs():
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM ignored_pairs")
        conn.commit()
    finally:
        pass


def _load_blacklist():
    _init_db()
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT path_a, path_b FROM ignored_pairs")
        return {(r['path_a'], r['path_b']) for r in c.fetchall()}
    finally:
        pass


def _now_iso():
    import datetime
    return datetime.datetime.now().isoformat()


# ===================================================================
#                     Глобальное состояние
# ===================================================================
DUPLICATE_GROUPS = []
scan_in_progress = False
scan_progress = {}
_lock = threading.Lock()


def _set_progress(task_id, progress, processed, total, message):
    t = scan_progress.get(task_id)
    if t is None:
        return
    t['progress'] = max(0, min(100, int(progress)))
    t['processed'] = int(processed)
    t['total'] = int(total)
    t['message'] = message


# ===================================================================
#                     Фильтрация по scan_paths
# ===================================================================
def _normalize_scan_paths(paths):
    if not paths:
        return []
    normalized = []
    seen = set()
    for p in paths:
        if not p:
            continue
        if isinstance(p, str):
            p = p.strip().strip('"').strip("'")
        if not p:
            continue
        try:
            p_abs = os.path.abspath(os.path.normpath(p))
            p_norm = os.path.normcase(p_abs)
        except Exception:
            continue
        p_norm_sep = p_norm.rstrip(os.sep) + os.sep
        if p_norm_sep in seen:
            continue
        seen.add(p_norm_sep)
        normalized.append(p_norm_sep)
    return normalized


def _filter_videos_by_scan_paths(videos, scan_paths):
    norm_paths = _normalize_scan_paths(scan_paths)
    if not norm_paths:
        return videos

    result = []
    for v in videos:
        fp = v.get('filepath')
        if not fp:
            continue
        try:
            fp_norm = os.path.normcase(os.path.abspath(fp))
        except Exception:
            continue
        for base_sep in norm_paths:
            base_no_sep = base_sep.rstrip(os.sep)
            if fp_norm == base_no_sep or fp_norm.startswith(base_sep):
                result.append(v)
                break
    return result


# ===================================================================
#                     Видео: кадры
# ===================================================================
def _extract_frames(video_path, positions):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        out = []
        if total <= 0:
            ret, frame = cap.read()
            if ret:
                out.append((positions[0], frame))
            return out
        for pos in positions:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(total * pos)))
            ret, frame = cap.read()
            if ret:
                out.append((pos, frame))
        return out
    finally:
        cap.release()


def _hash_image(pil_img):
    return [
        str(imagehash.phash(pil_img)),
        str(imagehash.phash(ImageOps.mirror(pil_img))),
        str(imagehash.phash(ImageOps.flip(pil_img))),
        str(imagehash.phash(ImageOps.flip(ImageOps.mirror(pil_img)))),
    ]


def _process_video_frames(video_path):
    raw = _extract_frames(video_path, KEYFRAME_POSITIONS)
    out = []
    for pos, frame in raw:
        try:
            pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            out.append({'position': float(pos), 'hashes': _hash_image(pil)})
        except Exception:
            pass
    if not out:
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            try:
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total // 2))
                ret, frame = cap.read()
                if ret:
                    pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    out = [{'position': 0.5, 'hashes': _hash_image(pil)}]
            finally:
                cap.release()
    return out


def _process_image_frames(image_path):
    try:
        img = Image.open(image_path).convert('RGB')
        img.thumbnail((256, 256), Image.Resampling.LANCZOS)
        return [{'position': 0.5, 'hashes': _hash_image(img)}]
    except Exception:
        return []


# ===================================================================
#                     Аудио
# ===================================================================
def _extract_audio_fingerprint(filepath):
    if not FPCALC_PATH:
        return None
    try:
        r = subprocess.run(
            [FPCALC_PATH, '-raw', '-json',
             '-length', str(AUDIO_FPCALC_MAX_LENGTH), filepath],
            capture_output=True, text=True, timeout=180,
            encoding='utf-8', errors='replace',
        )
        if r.returncode != 0 or not r.stdout:
            return None
        data = json.loads(r.stdout)
        fp_data = data.get('fingerprint')
        if not fp_data:
            return None
        if isinstance(fp_data, list):
            return [int(x) & 0xFFFFFFFF for x in fp_data]
        raw = base64.b64decode(fp_data)
        n = len(raw) // 4
        return list(struct.unpack('<%dI' % n, raw[:n * 4]))
    except Exception:
        return None


def _popcount_u32(x):
    x = x.astype(np.uint32, copy=False)
    x = x - ((x >> np.uint32(1)) & np.uint32(0x55555555))
    x = (x & np.uint32(0x33333333)) + ((x >> np.uint32(2)) & np.uint32(0x33333333))
    x = (x + (x >> np.uint32(4))) & np.uint32(0x0F0F0F0F)
    x = (x * np.uint32(0x01010101)) >> np.uint32(24)
    return x


def _find_longest_run_np(flags):
    if len(flags) == 0:
        return 0, 0
    padded = np.concatenate(([0], flags.astype(np.int8), [0]))
    diffs = np.diff(padded)
    starts = np.where(diffs == 1)[0]
    ends = np.where(diffs == -1)[0]
    if len(starts) == 0:
        return 0, 0
    lengths = ends - starts
    idx = int(np.argmax(lengths))
    return int(lengths[idx]), int(starts[idx])


def _is_silence_like(fp):
    if fp is None:
        return True
    arr = np.asarray(fp, dtype=np.uint32)
    n = len(arr)
    if n < 20:
        return True
    unique_ratio = len(np.unique(arr)) / n
    if n > 1:
        transitions = np.sum(arr[1:] != arr[:-1])
        transition_ratio = transitions / (n - 1)
        xor = np.bitwise_xor(arr[1:], arr[:-1])
        mean_diff_bits = float(np.mean(_popcount_u32(xor)))
    else:
        transition_ratio = 0.0
        mean_diff_bits = 0.0
    if (unique_ratio < AUDIO_SILENCE_MIN_UNIQUE_RATIO and
            transition_ratio < AUDIO_SILENCE_MIN_TRANSITION_RATIO):
        return True
    if mean_diff_bits < AUDIO_SILENCE_MIN_MEAN_DIFF_BITS:
        return True
    return False


def _fft_has_correlation(a, b):
    if len(a) < 40 or len(b) < 40:
        return False, 0.0
    a_f = _popcount_u32(np.asarray(a, dtype=np.uint32)).astype(np.float32)
    b_f = _popcount_u32(np.asarray(b, dtype=np.uint32)).astype(np.float32)
    a_f -= a_f.mean()
    b_f -= b_f.mean()
    n = len(a_f) + len(b_f)
    fa = np.fft.rfft(a_f, n)
    fb = np.fft.rfft(b_f, n)
    corr = np.fft.irfft(fa * np.conj(fb), n)
    abs_corr = np.abs(corr)
    peak = float(abs_corr.max())
    std = float(abs_corr.std())
    if std < 1e-9:
        return False, 0.0
    ratio = peak / std
    return ratio >= AUDIO_FFT_PEAK_STD_RATIO, ratio


def _find_audio_match(a_raw, b_raw):
    """
    Возвращает dict с run_sec / start_*_sec / kind ('full'|'partial').
    kind='full'    — run_sec >= AUDIO_FULL_MIN_RUN_SEC
    kind='partial' — run_sec >= AUDIO_PARTIAL_MIN_RUN_SEC
    """
    if not a_raw or not b_raw:
        return None
    a = np.asarray(a_raw, dtype=np.uint32)
    b = np.asarray(b_raw, dtype=np.uint32)

    skip_steps = int(AUDIO_SKIP_INTRO_SEC * AUDIO_SUBPRINT_PER_SEC)
    if skip_steps > 0:
        a = a[skip_steps:]
        b = b[skip_steps:]
    if len(a) < 40 or len(b) < 40:
        return None
    if _is_silence_like(a) or _is_silence_like(b):
        return None

    has_corr, ratio = _fft_has_correlation(a, b)
    if not has_corr:
        return None

    if len(a) >= len(b):
        long_fp, short_fp = a, b
        a_is_long = True
    else:
        long_fp, short_fp = b, a
        a_is_long = False

    la, lb = len(long_fp), len(short_fp)
    if lb < 40:
        return None

    min_run_partial_steps = max(20, int(AUDIO_PARTIAL_MIN_RUN_SEC * AUDIO_SUBPRINT_PER_SEC))
    min_run_partial_steps = min(min_run_partial_steps, lb)
    prefilter_len = min(AUDIO_PREFILTER_SAMPLES, lb)
    short_head = short_fp[:prefilter_len]

    best_match = None
    for shift in range(la - lb + 1):
        head_window = long_fp[shift:shift + prefilter_len]
        xor_h = np.bitwise_xor(head_window, short_head)
        bits_h = _popcount_u32(xor_h)
        if float(np.mean(bits_h <= AUDIO_BITS_TOLERANT)) < AUDIO_PREFILTER_GOOD_RATIO:
            continue

        window = long_fp[shift:shift + lb]
        xor = np.bitwise_xor(window, short_fp)
        bits = _popcount_u32(xor)
        flags = (bits <= AUDIO_BITS_TOLERANT).astype(np.int32)
        run_len, run_start = _find_longest_run_np(flags)
        if run_len >= min_run_partial_steps:
            if a_is_long:
                run_slice = long_fp[shift + run_start:shift + run_start + run_len]
            else:
                run_slice = short_fp[run_start:run_start + run_len]
            if _is_silence_like(run_slice):
                continue

            if a_is_long:
                start_a_step = shift + run_start
                start_b_step = run_start
            else:
                start_a_step = run_start
                start_b_step = shift + run_start

            run_sec = float(run_len / AUDIO_SUBPRINT_PER_SEC)
            if run_sec >= AUDIO_FULL_MIN_RUN_SEC:
                kind = 'full'
            else:
                kind = 'partial'

            candidate = {
                'run_steps': int(run_len),
                'run_sec': run_sec,
                'kind': kind,
                'start_a_sec': float(AUDIO_SKIP_INTRO_SEC +
                                     start_a_step / AUDIO_SUBPRINT_PER_SEC),
                'start_b_sec': float(AUDIO_SKIP_INTRO_SEC +
                                     start_b_step / AUDIO_SUBPRINT_PER_SEC),
                'skipped_intro_sec': float(AUDIO_SKIP_INTRO_SEC),
                'fft_ratio': round(float(ratio), 2),
            }

            # приоритет: full > partial, потом больше run_sec
            def _rank(m):
                return (0 if m['kind'] == 'full' else 1, -m['run_sec'])
            if best_match is None or _rank(candidate) < _rank(best_match):
                best_match = candidate
            if best_match and best_match['kind'] == 'full':
                # уже нашли full — не ищем дальше
                break

    return best_match


# ===================================================================
#                     Обработка одного медиа
# ===================================================================
def _process_media(item, include_audio=True):
    path = item['filepath']
    if not os.path.exists(path):
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None

    media_type = item.get('media_type', 'video')
    file_id, is_fresh = _get_or_create_file(
        path, st.st_mtime, st.st_size, media_type)

    if is_fresh:
        frames = _load_video_frames(file_id)
        audio_fp = _load_audio_fp(file_id) if (media_type == 'video' and include_audio) else None
        if frames:
            return {
                'file_id': file_id,
                'filepath': path,
                'media_type': media_type,
                'size': st.st_size,
                'frames': frames,
                'audio_fp': audio_fp,
                'from_cache': True,
            }

    if media_type == 'image':
        frames = _process_image_frames(path)
        audio_fp = None
    else:
        frames = _process_video_frames(path)
        if include_audio:
            audio_fp = _extract_audio_fingerprint(path)
        else:
            audio_fp = None

    if not frames:
        return None

    _save_video_frames(file_id, frames)
    if include_audio:
        _save_audio_fp(file_id, audio_fp)

    return {
        'file_id': file_id,
        'filepath': path,
        'media_type': media_type,
        'size': st.st_size,
        'frames': frames,
        'audio_fp': audio_fp,
        'from_cache': False,
    }


# ===================================================================
#                     Video verification (full + partial)
# ===================================================================
def _frame_best_distance(hashes_a, hashes_b):
    """Минимальный hamming между двумя наборами по 4 зеркала (16 сравнений)."""
    best = 999
    for h1 in hashes_a:
        for h2 in hashes_b:
            d = h1 - h2
            if d < best:
                best = d
    return best


def _video_match_matrix(ha, hb, max_hamming=FAST_MAX_HAMMING):
    """
    Возвращает матрицу совпадений между кадрами A и B.
    matched[i][j] = hamming если <= max_hamming, иначе None.
    Плюс массив best_j[i] = индекс лучшего кадра B для кадра A.
    """
    n_a = len(ha)
    n_b = len(hb)
    matched = [[None] * n_b for _ in range(n_a)]
    for i in range(n_a):
        pos_a, hashes_a = ha[i]
        for j in range(n_b):
            pos_b, hashes_b = hb[j]
            d = _frame_best_distance(hashes_a, hashes_b)
            if d <= max_hamming:
                matched[i][j] = d
    return matched


def _classify_video_pair(ha, hb):
    """
    ha, hb — списки (position, [4 imagehash]).
    Возвращает dict:
      {
        'kind': 'full' | 'partial',
        'coverage_a': float,       # доля кадров A, нашедших пару в B
        'coverage_b': float,
        'matched_count': int,      # уникальных совпавших кадров
        'subseq_frames': int,      # длина наибольшей монотонной подпоследовательности
        'pairs': [...],            # по одному на каждый совпавший кадр A
      }
    или None, если пара не подходит.

    Идея:
      * Полное совпадение: каждый A[i] находит пару B[j] c hamming ≤ 2,
        порядок монотонный. coverage_a ≈ coverage_b ≈ 1.
      * Частичное: A короче B, но существует монотонная
        подпоследовательность длины ≥ PARTIAL_MIN_SUBSEQ_FRAMES
        с hamming ≤ 2. Тогда A "вложено" в B.
    """
    n_a = len(ha)
    n_b = len(hb)
    if n_a == 0 or n_b == 0:
        return None

    matched = _video_match_matrix(ha, hb, FAST_MAX_HAMMING)

    # Для каждого кадра A — лучший j и его hamming
    # (это "жадное" соответствие: A[i] → B[best_j])
    greedy_pairs = []  # (i, j, hamming)
    for i in range(n_a):
        best_j = -1
        best_d = 999
        for j in range(n_b):
            d = matched[i][j]
            if d is not None and d < best_d:
                best_d = d
                best_j = j
        if best_j >= 0:
            greedy_pairs.append((i, best_j, best_d))

    # Монотонная подпоследовательность в greedy_pairs:
    # ищем такую, где i и j одновременно возрастают.
    # Для этого: сортируем по i (уже), потом LIS по j.
    if not greedy_pairs:
        return None

    # LIS по j (с сохранением индексов)
    j_seq = [p[1] for p in greedy_pairs]
    n = len(j_seq)
    # dp[k] = длина LIS, заканчивающейся на k
    dp = [1] * n
    parent = [-1] * n
    for k in range(n):
        for m in range(k):
            if j_seq[m] < j_seq[k] and dp[m] + 1 > dp[k]:
                dp[k] = dp[m] + 1
                parent[k] = m
    best_len = max(dp)
    best_end = dp.index(best_len)

    # восстановление цепочки
    chain = []
    k = best_end
    while k != -1:
        chain.append(greedy_pairs[k])
        k = parent[k]
    chain.reverse()

    subseq_len = len(chain)
    matched_count = len(greedy_pairs)

    coverage_a = matched_count / n_a
    coverage_b = len(set(p[1] for p in greedy_pairs)) / n_b

    # FULL: покрытие обоих >= FULL_COVERAGE и subseq_len достаточно
    if (coverage_a >= FULL_COVERAGE and
            coverage_b >= FULL_COVERAGE and
            subseq_len >= MIN_MATCHED_FRAMES):
        return {
            'kind': 'full',
            'coverage_a': round(coverage_a, 3),
            'coverage_b': round(coverage_b, 3),
            'matched_count': matched_count,
            'subseq_frames': subseq_len,
            'pairs': [
                {'pos_a': float(ha[i][0]),
                 'pos_b': float(hb[j][0]),
                 'hamming': int(d)}
                for (i, j, d) in chain
            ],
        }

    # PARTIAL: длинная монотонная цепочка, но покрытие < FULL_COVERAGE
    if subseq_len >= PARTIAL_MIN_SUBSEQ_FRAMES:
        return {
            'kind': 'partial',
            'coverage_a': round(coverage_a, 3),
            'coverage_b': round(coverage_b, 3),
            'matched_count': matched_count,
            'subseq_frames': subseq_len,
            'pairs': [
                {'pos_a': float(ha[i][0]),
                 'pos_b': float(hb[j][0]),
                 'hamming': int(d)}
                for (i, j, d) in chain
            ],
        }

    return None


def _precompute_video_hashes(frames_by_path, paths):
    out = []
    for p in paths:
        out.append([
            (f['position'], [imagehash.hex_to_hash(h) for h in f['hashes']])
            for f in frames_by_path[p]
        ])
    return out


def _video_candidates(paths, hash_objs):
    """
    Пары-кандидаты по ЛЮБОМУ совпадению phash.

    Больше кандидатов (25 кадров × 4 зеркала = 100 phash), но
    финальное решение — в _classify_video_pair, который отсекает
    всё, что не full/partial.
    """
    n = len(paths)
    pairs = set()
    index = defaultdict(list)
    for i in range(n):
        for _, hashes in hash_objs[i]:
            for h in hashes:
                index[h].append(i)
    for _, files in index.items():
        if len(files) < 2:
            continue
        uniq = sorted(set(files))
        for a in range(len(uniq)):
            for b in range(a + 1, len(uniq)):
                pairs.add((uniq[a], uniq[b]))
    return pairs


# ===================================================================
#                     Audio LSH
# ===================================================================
def _audio_lsh_candidates(paths, audio_by_path):
    n = len(paths)
    skip_steps = int(AUDIO_SKIP_INTRO_SEC * AUDIO_SUBPRINT_PER_SEC)
    mask = (1 << AUDIO_LSH_BAND_BITS) - 1
    bucket = defaultdict(list)

    for i in range(n):
        fp = audio_by_path.get(paths[i])
        if not fp:
            continue
        arr = np.asarray(fp, dtype=np.uint32)
        if skip_steps > 0:
            arr = arr[skip_steps:]
        if len(arr) < 40:
            continue
        if _is_silence_like(arr):
            continue
        for band in range(AUDIO_LSH_BANDS):
            shift = band * AUDIO_LSH_BAND_BITS
            band_vals = (arr >> np.uint32(shift)) & np.uint32(mask)
            seen = {}
            for pos, bv in enumerate(band_vals):
                bv_i = int(bv)
                if bv_i not in seen:
                    seen[bv_i] = pos
            for bv_i, pos in seen.items():
                bucket[(band, bv_i)].append((i, pos))

    pair_hits = defaultdict(int)
    for _, items in bucket.items():
        if len(items) < 2:
            continue
        for a in range(len(items)):
            fi, pi = items[a]
            for b in range(a + 1, len(items)):
                fj, pj = items[b]
                if fi == fj:
                    continue
                if abs(pi - pj) > AUDIO_LSH_MAX_POS_DELTA:
                    continue
                lo, hi = (fi, fj) if fi < fj else (fj, fi)
                pair_hits[(lo, hi)] += 1

    return {p for p, c in pair_hits.items()
            if c >= AUDIO_LSH_MIN_MATCHES}


# ===================================================================
#                     Группировка
# ===================================================================
def _group_all(task_id, frames_by_path, audio_by_path,
               item_by_path, mode='deep'):
    paths = list(frames_by_path.keys())
    n = len(paths)
    parent = list(range(n))

    print(f"[dup] === GROUPING START (mode={mode}) ===")
    print(f"[dup] files with frames: {n}")

    blacklist = _load_blacklist()
    print(f"[dup] ignored_pairs loaded: {len(blacklist)}")

    def is_ignored(i, j):
        return _pair_key(paths[i], paths[j]) in blacklist

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # ---------- VIDEO ----------
    _set_progress(task_id, 40, 0, 0, 'Preparing video index...')
    hash_objs = _precompute_video_hashes(frames_by_path, paths)

    _set_progress(task_id, 42, 0, 0, 'Building video candidate index...')
    video_pairs = _video_candidates(paths, hash_objs)
    print(f"[dup] video candidates (raw): {len(video_pairs)}")

    video_pairs = {p for p in video_pairs if not is_ignored(p[0], p[1])}
    print(f"[dup] video candidates after blacklist: {len(video_pairs)}")

    video_matches_by_pair = {}
    total_vp = len(video_pairs)
    done_v = 0
    rejected = 0
    if total_vp:
        _set_progress(task_id, 45, 0, total_vp, 'Verifying video pairs...')
        pairs_list = list(video_pairs)
        with ThreadPoolExecutor(max_workers=COMPARE_WORKERS) as ex:
            futures = [
                ex.submit(_classify_video_pair,
                          hash_objs[i], hash_objs[j])
                for (i, j) in pairs_list
            ]
            for fut, (i, j) in zip(futures, pairs_list):
                res = fut.result()
                if res:
                    union(i, j)
                    video_matches_by_pair[(i, j)] = res
                else:
                    rejected += 1
                done_v += 1
                if done_v % 200 == 0 or done_v == total_vp:
                    pct = 45 + int(done_v / total_vp * 20)
                    _set_progress(task_id, pct, done_v, total_vp,
                                  f'Video verify {done_v}/{total_vp}')
    print(f"[dup] video matched: {len(video_matches_by_pair)} / {total_vp} "
          f"(rejected {rejected} loose pairs)")

    # ---------- AUDIO (только в deep) ----------
    audio_matches_by_pair = {}
    if mode == 'deep':
        _set_progress(task_id, 65, 0, 0, 'Building audio LSH index...')
        audio_pairs_all = _audio_lsh_candidates(paths, audio_by_path)
        print(f"[dup] audio candidates (LSH raw): {len(audio_pairs_all)}")

        audio_pairs = {p for p in audio_pairs_all if not is_ignored(p[0], p[1])}
        print(f"[dup] audio after blacklist: {len(audio_pairs)}")

        total_ap = len(audio_pairs)
        done_a = 0
        if total_ap:
            _set_progress(task_id, 70, 0, total_ap, 'Audio verify (FFT)...')
            pairs_list = list(audio_pairs)
            with ThreadPoolExecutor(max_workers=COMPARE_WORKERS) as ex:
                futures = [
                    ex.submit(_find_audio_match,
                              audio_by_path[paths[i]],
                              audio_by_path[paths[j]])
                    for (i, j) in pairs_list
                ]
                for fut, (i, j) in zip(futures, pairs_list):
                    res = fut.result()
                    if res:
                        union(i, j)
                        audio_matches_by_pair[(i, j)] = res
                    done_a += 1
                    if done_a % 100 == 0 or done_a == total_ap:
                        pct = 70 + int(done_a / total_ap * 25)
                        _set_progress(task_id, pct, done_a, total_ap,
                                      f'Audio verify {done_a}/{total_ap}')
        print(f"[dup] audio matched: {len(audio_matches_by_pair)} / {total_ap}")

    # ---------- СБОРКА ГРУПП ----------
    _set_progress(task_id, 95, 0, 0, 'Grouping results...')

    by_root = defaultdict(list)
    for i in range(n):
        by_root[find(i)].append(i)

    groups = []
    for root, idxs in by_root.items():
        if len(idxs) < 2:
            continue
        group_files = []
        for i in idxs:
            item = item_by_path.get(paths[i])
            if item and item.get('id') is not None:
                group_files.append(item)
        if len(group_files) < 2:
            continue
        group_ids = {f['id'] for f in group_files}

        # Собираем video-совпадения
        video_list = []
        for (i, j), res in video_matches_by_pair.items():
            a = item_by_path.get(paths[i])
            b = item_by_path.get(paths[j])
            if a and b and a['id'] in group_ids and b['id'] in group_ids:
                video_list.append({
                    'a_id': a['id'],
                    'b_id': b['id'],
                    'kind': res['kind'],
                    'coverage_a': res['coverage_a'],
                    'coverage_b': res['coverage_b'],
                    'matched_count': res['matched_count'],
                    'subseq_frames': res['subseq_frames'],
                    'pairs': res['pairs'],
                })

        audio_list = []
        for (i, j), res in audio_matches_by_pair.items():
            a = item_by_path.get(paths[i])
            b = item_by_path.get(paths[j])
            if a and b and a['id'] in group_ids and b['id'] in group_ids:
                audio_list.append({
                    'a_id': a['id'],
                    'b_id': b['id'],
                    'kind': res.get('kind', 'full'),
                    'run_sec': res['run_sec'],
                    'start_a_sec': res['start_a_sec'],
                    'start_b_sec': res['start_b_sec'],
                    'skipped_intro_sec': res.get('skipped_intro_sec', 0),
                })

        # Классификация группы: full / partial / mixed
        has_full = any(v['kind'] == 'full' for v in video_list) or \
                   any(a['kind'] == 'full' for a in audio_list)
        has_partial = any(v['kind'] == 'partial' for v in video_list) or \
                      any(a['kind'] == 'partial' for a in audio_list)

        if video_list and audio_list:
            reason = 'video+audio'
        elif video_list:
            reason = 'video'
        elif audio_list:
            reason = 'audio'
        else:
            continue

        if has_full and has_partial:
            kind = 'mixed'
        elif has_full:
            kind = 'full'
        else:
            kind = 'partial'

        groups.append({
            'files': group_files,
            'reason': reason,
            'kind': kind,
            'matches': {'video': video_list, 'audio': audio_list},
        })

    return groups


# ===================================================================
#                     Запуск
# ===================================================================
def find_duplicates_async(task_id, filters=None, scan_paths=None, mode='deep'):
    global scan_in_progress, scan_progress
    if mode not in ('fast', 'deep'):
        mode = 'deep'

    with _lock:
        if scan_in_progress:
            print("[dup] scan already running")
            return False
        scan_in_progress = True
        scan_progress[task_id] = {
            'status': 'scanning', 'progress': 0, 'total': 0,
            'processed': 0, 'message': 'Initializing...',
            'mode': mode,
        }
    threading.Thread(target=_find_duplicates_worker,
                     args=(task_id, filters, scan_paths, mode),
                     daemon=True).start()
    print(f"[dup] started task_id={task_id} filters={filters} "
          f"paths={scan_paths} mode={mode}")
    return True


def _find_duplicates_worker(task_id, filters, scan_paths=None, mode='deep'):
    global DUPLICATE_GROUPS, scan_in_progress
    try:
        print(f"\n{'=' * 70}")
        print(f"[dup] === WORKER START (mode={mode}) ===")
        print(f"[dup] task_id:     {task_id}")
        print(f"[dup] filters:     {filters}")
        print(f"[dup] scan_paths:  {scan_paths}")
        print(f"{'=' * 70}")

        _init_db()

        all_items = get_all_videos()
        print(f"[dup] total in DB: {len(all_items)}")

        if mode == 'fast':
            if filters:
                filters = [f for f in filters if f == 'video']
            if not filters:
                filters = ['video']

        if filters:
            allowed = set(filters)
            before = len(all_items)
            all_items = [v for v in all_items
                         if v.get('media_type', 'video') in allowed]
            print(f"[dup] after media_type filter {filters}: "
                  f"{len(all_items)} (removed {before - len(all_items)})")

        if scan_paths:
            before = len(all_items)
            all_items = _filter_videos_by_scan_paths(all_items, scan_paths)
            print(f"[dup] after path filter: {len(all_items)} "
                  f"(removed {before - len(all_items)})")

        missing = [v['filepath'] for v in all_items
                   if not os.path.exists(v.get('filepath', ''))]
        if missing:
            print(f"[dup] WARNING: {len(missing)} files in DB missing on disk")

        total = len(all_items)
        _set_progress(task_id, 0, 0, total, f'Scanning {total} files...')

        if total == 0:
            DUPLICATE_GROUPS = []
            _save_groups([])
            scan_progress[task_id].update({
                'status': 'complete', 'progress': 100,
                'message': 'No files to scan.',
            })
            return

        _cleanup_db([v['filepath'] for v in all_items])

        processed = 0
        cached_count = 0
        frames_by_path = {}
        audio_by_path = {}

        include_audio = (mode == 'deep')

        with ThreadPoolExecutor(max_workers=HASH_WORKERS) as ex:
            futures = [ex.submit(_process_media, it, include_audio)
                       for it in all_items]
            for fut in as_completed(futures):
                try:
                    entry = fut.result()
                except Exception as e:
                    print(f"[dup] hash err: {e}")
                    entry = None
                processed += 1
                pct = int(processed / total * 40)
                _set_progress(task_id, pct, processed, total,
                              f'Hashing {processed}/{total}')
                if entry:
                    fp = entry['filepath']
                    frames_by_path[fp] = entry['frames']
                    if include_audio:
                        audio_by_path[fp] = entry.get('audio_fp')
                    if entry.get('from_cache'):
                        cached_count += 1

        item_by_path = {it['filepath']: it for it in all_items}
        print(f"[dup] hashed {len(frames_by_path)}/{total} "
              f"(cached: {cached_count}, fresh: {total - cached_count})")

        if include_audio:
            with_audio = sum(1 for v in audio_by_path.values() if v)
            print(f"[dup] files with audio fingerprint: "
                  f"{with_audio}/{len(audio_by_path)}")

        groups = _group_all(task_id, frames_by_path, audio_by_path,
                            item_by_path, mode=mode)
        print(f"[dup] found {len(groups)} groups")

        DUPLICATE_GROUPS = groups
        _save_groups(groups)

        scan_progress[task_id].update({
            'status': 'complete', 'progress': 100,
            'message': f'Found {len(groups)} duplicate groups ({mode})',
        })

        print(f"[dup] === RESULT: {len(groups)} groups ({mode}) ===")
        for gi, g in enumerate(groups[:5]):
            print(f"[dup]   group #{gi+1}: {len(g['files'])} files, "
                  f"reason={g['reason']}, kind={g['kind']}")
        print(f"{'=' * 70}\n")

    except Exception as e:
        import traceback
        traceback.print_exc()
        scan_progress[task_id]['status'] = 'error'
        scan_progress[task_id]['message'] = str(e)
    finally:
        with _lock:
            scan_in_progress = False


def _save_groups(groups):
    try:
        with open(DUPLICATE_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(groups, f,
                      default=lambda o: o if isinstance(o, (dict, list)) else str(o),
                      indent=2)
        print(f"[dup] saved {len(groups)} groups")
    except Exception as e:
        print(f"[dup] save groups failed: {e}")


# ===================================================================
#                     Геттеры
# ===================================================================
def _rebuild_groups_from_matches(group):
    files = group.get('files', [])
    if len(files) < 2:
        return []
    matches = group.get('matches', {}) or {}
    video_matches = matches.get('video', []) or []
    audio_matches = matches.get('audio', []) or []

    path_by_id = {}
    for f in files:
        path_by_id[f['id']] = f.get('filepath', '')
    id_list = [f['id'] for f in files]
    idx = {vid: i for i, vid in enumerate(id_list)}
    n = len(id_list)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    blacklist = _load_blacklist()
    used_video_pairs = []
    used_audio_pairs = []

    def is_ignored_by_id(a_id, b_id):
        pa = path_by_id.get(a_id, '')
        pb = path_by_id.get(b_id, '')
        if not pa or not pb:
            return False
        return _pair_key(pa, pb) in blacklist

    for m in video_matches:
        a_id = m.get('a_id')
        b_id = m.get('b_id')
        if a_id in idx and b_id in idx:
            if not is_ignored_by_id(a_id, b_id):
                union(idx[a_id], idx[b_id])
                used_video_pairs.append(m)
    for m in audio_matches:
        a_id = m.get('a_id')
        b_id = m.get('b_id')
        if a_id in idx and b_id in idx:
            if not is_ignored_by_id(a_id, b_id):
                union(idx[a_id], idx[b_id])
                used_audio_pairs.append(m)

    by_root = defaultdict(list)
    for i, vid in enumerate(id_list):
        by_root[find(i)].append(vid)

    result = []
    for root, vids in by_root.items():
        if len(vids) < 2:
            continue
        vids_set = set(vids)
        sub_files = [f for f in files if f['id'] in vids_set]
        sub_video = [m for m in used_video_pairs
                     if m['a_id'] in vids_set and m['b_id'] in vids_set]
        sub_audio = [m for m in used_audio_pairs
                     if m['a_id'] in vids_set and m['b_id'] in vids_set]
        if sub_video and sub_audio:
            r = 'video+audio'
        elif sub_video:
            r = 'video'
        elif sub_audio:
            r = 'audio'
        else:
            continue

        has_full = any(v.get('kind') == 'full' for v in sub_video) or \
                   any(a.get('kind') == 'full' for a in sub_audio)
        has_partial = any(v.get('kind') == 'partial' for v in sub_video) or \
                      any(a.get('kind') == 'partial' for a in sub_audio)
        if has_full and has_partial:
            kind = 'mixed'
        elif has_full:
            kind = 'full'
        else:
            kind = 'partial'

        result.append({
            'files': sub_files,
            'reason': r,
            'kind': kind,
            'matches': {'video': sub_video, 'audio': sub_audio},
        })
    return result


def get_duplicate_groups():
    global DUPLICATE_GROUPS
    if not DUPLICATE_GROUPS:
        try:
            with open(DUPLICATE_CACHE_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f) or []
        except (FileNotFoundError, json.JSONDecodeError):
            raw = []
        DUPLICATE_GROUPS = []
        for g in raw:
            if isinstance(g, dict) and 'files' in g:
                files = [v for v in g['files']
                         if isinstance(v, dict) and v.get('id') is not None]
                if len(files) >= 2:
                    DUPLICATE_GROUPS.append({
                        'files': files,
                        'reason': g.get('reason', 'video'),
                        'kind': g.get('kind', 'full'),
                        'matches': g.get('matches', {'video': [], 'audio': []}),
                    })
    if not DUPLICATE_GROUPS:
        return []

    all_ids = set()
    for g in DUPLICATE_GROUPS:
        for v in g['files']:
            try:
                all_ids.add(int(v['id']))
            except (KeyError, TypeError, ValueError):
                pass
    if not all_ids:
        DUPLICATE_GROUPS = []
        _save_groups([])
        return []

    conn = get_db_connection()
    try:
        c = conn.cursor()
        placeholders = ','.join('?' * len(all_ids))
        c.execute(f'SELECT id FROM videos WHERE id IN ({placeholders})',
                  tuple(all_ids))
        existing = {row['id'] for row in c.fetchall()}
    finally:
        conn.close()

    cleaned = []
    for g in DUPLICATE_GROUPS:
        new_files = [v for v in g['files']
                     if int(v.get('id', -1)) in existing]
        if len(new_files) >= 2:
            cleaned.append({
                'files': new_files,
                'reason': g['reason'],
                'kind': g.get('kind', 'full'),
                'matches': g.get('matches', {'video': [], 'audio': []}),
            })

    final = []
    for g in cleaned:
        final.extend(_rebuild_groups_from_matches(g))

    if len(final) != len(DUPLICATE_GROUPS):
        DUPLICATE_GROUPS = final
        _save_groups(final)
    else:
        DUPLICATE_GROUPS = final

    return DUPLICATE_GROUPS


def get_progress(task_id):
    return scan_progress.get(task_id, None)


# ===================================================================
#                     Перемещение
# ===================================================================
def _unique_dst(dst_dir, name):
    dst = os.path.join(dst_dir, name)
    base, ext = os.path.splitext(name)
    i = 1
    while os.path.exists(dst):
        dst = os.path.join(dst_dir, f'{base}_{i}{ext}')
        i += 1
    return dst


def _remove_from_db(video_id):
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        conn.commit()
    finally:
        conn.close()


def _duplicates_dir_for(src_path):
    drive = os.path.splitdrive(src_path)[0] + "\\"
    folder_name = os.path.basename(os.path.dirname(src_path)) or 'root'
    dest_dir = os.path.join(drive, "!Duplicates", f"{folder_name}_duplicates")
    os.makedirs(dest_dir, exist_ok=True)
    return dest_dir


def move_selected(video_ids):
    global DUPLICATE_GROUPS
    if not video_ids:
        return False, "No video_ids"
    try:
        video_ids = [int(x) for x in video_ids]
    except (TypeError, ValueError):
        return False, "Invalid video_ids"
    target = set(video_ids)
    moved = 0
    errors = []
    for vid in video_ids:
        v = get_video_by_id(vid)
        if not v:
            continue
        src = v['filepath']
        if not os.path.exists(src):
            errors.append(f"{os.path.basename(src)}: not found")
            continue
        try:
            dest_dir = _duplicates_dir_for(src)
            dest = _unique_dst(dest_dir, os.path.basename(src))
            shutil.move(src, dest)
            _remove_from_db(vid)
            moved += 1
        except Exception as e:
            errors.append(f"{os.path.basename(src)}: {e}")
    kept = []
    for g in DUPLICATE_GROUPS:
        new_files = [v for v in g['files']
                     if int(v.get('id', -1)) not in target]
        if len(new_files) >= 2:
            kept.append({
                'files': new_files,
                'reason': g['reason'],
                'kind': g.get('kind', 'full'),
                'matches': g.get('matches', {'video': [], 'audio': []}),
            })
    DUPLICATE_GROUPS = kept
    _save_groups(kept)
    if errors:
        return False, f"Moved {moved}, errors: {'; '.join(errors[:3])}"
    return True, f"Moved {moved} file(s)"


def move_group(group_index, keep_best=True):
    groups = get_duplicate_groups()
    if group_index < 0 or group_index >= len(groups):
        return False, "Group not found"
    files = groups[group_index]['files']
    if len(files) < 2:
        return False, "Not a duplicate group"
    best = max(files, key=lambda v: v.get('size', 0)) if keep_best else files[0]
    to_move = [v['id'] for v in files if v['id'] != best['id']]
    ok, msg = move_selected(to_move)
    if ok:
        return True, f"{msg}. Kept: {best['filename']}"
    return False, msg


def move_all_groups(keep_best=True):
    groups = get_duplicate_groups()
    if not groups:
        return False, "No duplicate groups found"
    all_ids = []
    for g in groups:
        files = g['files']
        if not files:
            continue
        best = max(files, key=lambda v: v.get('size', 0)) if keep_best else files[0]
        for v in files:
            if v['id'] != best['id']:
                all_ids.append(v['id'])
    return move_selected(all_ids)


# ===================================================================
#                     Blacklist API
# ===================================================================
def mark_group_ignored(video_ids, note=''):
    if not video_ids or len(video_ids) < 2:
        return False, "Need >= 2 files", 0
    paths = []
    for vid in video_ids:
        v = get_video_by_id(int(vid))
        if v and v.get('filepath'):
            paths.append(v['filepath'])
    if len(paths) < 2:
        return False, "Files not found", 0

    count = 0
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            ok, _ = mark_pair_not_duplicate(paths[i], paths[j], note)
            if ok:
                count += 1

    global DUPLICATE_GROUPS
    DUPLICATE_GROUPS = []
    get_duplicate_groups()

    return True, f"Ignored {count} pairs", count


def mark_pair_by_ids(id_a, id_b, note=''):
    a = get_video_by_id(int(id_a))
    b = get_video_by_id(int(id_b))
    if not a or not b:
        return False, "File not found"
    ok, msg = mark_pair_not_duplicate(a['filepath'], b['filepath'], note)
    global DUPLICATE_GROUPS
    DUPLICATE_GROUPS = []
    get_duplicate_groups()
    return ok, msg


def unmark_pair_by_paths(path_a, path_b):
    ok = unmark_pair_not_duplicate(path_a, path_b)
    global DUPLICATE_GROUPS
    DUPLICATE_GROUPS = []
    get_duplicate_groups()
    return ok