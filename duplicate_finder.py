r"""
Поиск дубликатов — версия с numpy-MIH, оптимизированной группировкой,
авто-очисткой битых/отсутствующих файлов и подробными логами.

Ключевое:
  • При старте отсутствующие на диске файлы удаляются из БД.
  • Битые файлы переносятся в !Broken.
  • VIDEO — 10 кадров × 2 зеркала (orig + mirror_h), LSH, full/partial.
  • PHOTO — 1 кадр × 2 зеркала, numpy-MIH + low-entropy фильтр.
  • Safety net: image с видео-расширением → принудительно video.
  • Группировка — O(matches + groups) через pre-indexing по корням.
  • AUDIO — chromaprint → LSH → FFT → longest-run.

Что убрано:
  • Вертикальные перевороты (flip, flip_hv) — редкий кейс, много ложных.
  • 25 кадров → 10 (декодирование — узкое место, экономия ~60%).
"""
import os
import json
import base64
import struct
import shutil
import sqlite3
import threading
import subprocess
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import cv2
from PIL import Image, ImageOps
import imagehash

from models import get_all_videos, get_db_connection, get_video_by_id, delete_video
from converter import move_to_broken
from helpers.subprocess_utils import hidden_process_kwargs


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DUPLICATE_CACHE_FILE = os.path.join(BASE_DIR, 'duplicates_cache.json')
HASH_CACHE_DB        = os.path.join(BASE_DIR, 'hash_cache.db')

FPCALC_CANDIDATES = [
    os.path.join(BASE_DIR, '_dop', 'fpcalc.exe'),
    os.path.join(BASE_DIR, '_dop', 'fpcalc'),
    shutil.which('fpcalc'),
]

# ---------- Video ----------
KEYFRAME_POSITIONS = [round(0.05 + i * 0.10, 4) for i in range(10)]

FAST_MAX_HAMMING = 2
DEEP_MAX_HAMMING = 6
DEEP_HASH_BLOCK_HEX = 4
DEEP_FULL_COVERAGE = 0.65
DEEP_PARTIAL_MIN_SUBSEQ_FRAMES = 2
FULL_COVERAGE = 0.80
PARTIAL_MIN_SUBSEQ_FRAMES = 3
MIN_MATCHED_FRAMES = 2
MAX_CANDIDATE_BUCKET = 250

# ---------- Photo ----------
IMAGE_MAX_HAMMING = 5
IMAGE_LSH_HAMMING = 5
IMAGE_BRUTE_FORCE_LIMIT = 3000
IMAGE_MIN_ENTROPY = 8.0

IMAGE_MIH_TRIU_THRESHOLD = 200
IMAGE_MIH_MAX_BUCKET_SIZE = 3000
IMAGE_MIH_MAX_PAIRS = 50_000_000

# ---------- Audio ----------
AUDIO_SKIP_INTRO_SEC   = 30.0
AUDIO_SKIP_VARIANTS    = (30.0, 0.0)
AUDIO_SUBPRINT_PER_SEC = 1.0 / 0.1234
AUDIO_FPCALC_MAX_LENGTH = 1800

AUDIO_FULL_MIN_RUN_SEC    = 30.0

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

COMPARE_WORKERS = min(8, max(2, (os.cpu_count() or 4)))
HASH_WORKERS    = min(4, max(2, (os.cpu_count() or 4)))

SQL_PARAM_CHUNK = 500

NUM_HASHES = 2

_VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv',
               '.webm', '.m4v', '.ts', '.m2ts', '.mpg', '.mpeg'}


FPCALC_PATH = None
for cand in FPCALC_CANDIDATES:
    if cand and os.path.exists(cand):
        FPCALC_PATH = cand
        break

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
    result = []
    for r in rows:
        hashes = [h for h in (r['phash_orig'], r['phash_mirror']) if h]
        if not hashes:
            continue
        result.append({
            'position': r['position'],
            'hashes': hashes,
        })
    return result if result else None


def _save_video_frames(file_id, frames):
    conn = _get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM video_frames WHERE file_id = ?", (file_id,))
    for idx, f in enumerate(frames):
        h = f['hashes']
        h0 = h[0] if len(h) > 0 else ''
        h1 = h[1] if len(h) > 1 else ''
        c.execute("""
            INSERT INTO video_frames
                (file_id, frame_idx, position,
                 phash_orig, phash_mirror, phash_flip, phash_hv)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (file_id, idx, f['position'], h0, h1, '', ''))
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
    # Отключаем внутреннюю многопоточность OpenCV во избежание дедлоков на Windows
    cv2.setNumThreads(0)
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

        try:
            gray = np.asarray(img.convert('L'), dtype=np.float32)
            std = float(gray.std())
            if std < IMAGE_MIN_ENTROPY:
                return []
        except Exception:
            pass

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
            **hidden_process_kwargs(capture_output=True, text=True, timeout=180),
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


def _find_audio_match_skip(a_raw, b_raw, skip_intro_sec=AUDIO_SKIP_INTRO_SEC):
    if not a_raw or not b_raw:
        return None
    a = np.asarray(a_raw, dtype=np.uint32)
    b = np.asarray(b_raw, dtype=np.uint32)

    skip_steps = int(skip_intro_sec * AUDIO_SUBPRINT_PER_SEC)
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

    # Accept runs from the full-search minimum (30 s) and label longer ones
    # as partial. The old code required 60 s before any match was accepted.
    min_run_steps = max(20, int(AUDIO_FULL_MIN_RUN_SEC * AUDIO_SUBPRINT_PER_SEC))
    min_run_steps = min(min_run_steps, lb)
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
        if run_len >= min_run_steps:
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
                'start_a_sec': float(skip_intro_sec +
                                     start_a_step / AUDIO_SUBPRINT_PER_SEC),
                'start_b_sec': float(skip_intro_sec +
                                     start_b_step / AUDIO_SUBPRINT_PER_SEC),
                'skipped_intro_sec': float(skip_intro_sec),
                'fft_ratio': round(float(ratio), 2),
            }

            def _rank(m):
                return (0 if m['kind'] == 'full' else 1, -m['run_sec'])
            if best_match is None or _rank(candidate) < _rank(best_match):
                best_match = candidate
            if best_match and best_match['kind'] == 'full':
                break

    return best_match


def _find_audio_match(a_raw, b_raw):
    """Try both intro offsets; return the strongest match found."""
    best = None
    for skip_sec in AUDIO_SKIP_VARIANTS:
        try:
            candidate = _find_audio_match_skip(a_raw, b_raw, skip_sec)
        except Exception as e:
            print(f"[dup] audio match skip={skip_sec:g} failed: {e}", flush=True)
            continue
        if not candidate:
            continue
        rank = (0 if candidate.get('kind') == 'full' else 1,
                -float(candidate.get('run_sec') or 0))
        best_rank = (0 if best and best.get('kind') == 'full' else 1,
                     -float(best.get('run_sec') or 0)) if best else (2, 0)
        if rank < best_rank:
            best = candidate
    return best


# ===================================================================
#                     Обработка одного медиа
# ===================================================================
def _process_media(item, include_audio=True):
    path = item['filepath']
    filename = item['filename']
    
    if not os.path.exists(path):
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None

    media_type = item.get('media_type', 'video')

    _ext = os.path.splitext(path)[1].lower()
    if media_type == 'image' and _ext in _VIDEO_EXTS:
        print(f"[dup] WARNING: 'photo' with video extension {_ext}: {path}")
        media_type = 'video'
        item = dict(item)
        item['media_type'] = 'video'

    print(f"[dup]   [START] {filename}", flush=True)
    file_id, is_fresh = _get_or_create_file(
        path, st.st_mtime, st.st_size, media_type)

    if is_fresh:
        frames = _load_video_frames(file_id)
        audio_fp = _load_audio_fp(file_id) if (media_type == 'video' and include_audio) else None
        if frames:
            # A fast scan caches frames but not audio. Without this backfill a
            # later deep scan sees the file as fresh and never calls fpcalc,
            # so the audio phase has zero candidates.
            if (include_audio and media_type == 'video' and not audio_fp and
                    FPCALC_PATH):
                audio_fp = _extract_audio_fingerprint(path)
                if audio_fp:
                    _save_audio_fp(file_id, audio_fp)
                    print(f"[dup]   [AUDIO BACKFILL] {filename}", flush=True)
                else:
                    print(f"[dup]   [AUDIO BACKFILL FAILED] {filename}", flush=True)
            print(f"[dup]   [CACHED] {filename}", flush=True)
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
        print(f"[dup]   [FRAMES OK] {filename}", flush=True)
        if include_audio:
            audio_fp = _extract_audio_fingerprint(path)
            print(f"[dup]   [AUDIO OK] {filename}", flush=True)
        else:
            audio_fp = None

    if not frames:
        print(f"[dup]   [NO FRAMES] {filename}", flush=True)
        return None

    _save_video_frames(file_id, frames)
    if include_audio:
        _save_audio_fp(file_id, audio_fp)

    print(f"[dup]   [DONE] {filename}", flush=True)
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
#                     PHOTO: numpy MIH
# ===================================================================
def _is_image_item(item):
    return (item or {}).get('media_type') == 'image'


def _popcount64_np(x):
    x = x.astype(np.uint64, copy=False)
    x = x - ((x >> np.uint64(1)) & np.uint64(0x5555555555555555))
    x = (x & np.uint64(0x3333333333333333)) + \
        ((x >> np.uint64(2)) & np.uint64(0x3333333333333333))
    x = (x + (x >> np.uint64(4))) & np.uint64(0x0F0F0F0F0F0F0F0F)
    x = (x * np.uint64(0x0101010101010101)) >> np.uint64(56)
    return x.astype(np.uint8)


def _image_mih_candidates_np(hashes_np, lsh_hamming,
                             triu_threshold=IMAGE_MIH_TRIU_THRESHOLD,
                             max_bucket_size=IMAGE_MIH_MAX_BUCKET_SIZE,
                             max_pairs=IMAGE_MIH_MAX_PAIRS):
    n = len(hashes_np)
    if n < 2:
        return (np.empty(0, dtype=np.int32),
                np.empty(0, dtype=np.int32))

    num_blocks = lsh_hamming + 1
    base_bits = 64 // num_blocks
    extra = 64 - base_bits * num_blocks

    shifts = []
    masks = []
    shift = 0
    for b in range(num_blocks):
        bits = base_bits + (1 if b < extra else 0)
        shifts.append(shift)
        masks.append((1 << bits) - 1)
        shift += bits

    all_i = []
    all_j = []
    pairs_total = 0
    skipped_huge = 0
    skipped_by_limit = 0
    limit_hit = False

    t_start = time.time()
    m_slots = hashes_np.shape[1]

    for b_idx in range(num_blocks):
        shift_val = shifts[b_idx]
        mask_val = masks[b_idx]

        for m in range(m_slots):
            col = (hashes_np[:, m] >> np.uint64(shift_val)) & np.uint64(mask_val)

            order = np.argsort(col, kind='stable')
            sorted_col = col[order]

            if n > 1:
                changes = np.empty(n, dtype=bool)
                changes[0] = True
                changes[1:] = sorted_col[1:] != sorted_col[:-1]
            else:
                changes = np.array([True])

            group_starts = np.where(changes)[0]
            group_ends = np.concatenate((group_starts[1:], [n]))
            sizes = group_ends - group_starts

            multi_mask = sizes >= 2
            if not multi_mask.any():
                continue

            multi_starts = group_starts[multi_mask]
            multi_ends = group_ends[multi_mask]
            multi_sizes = sizes[multi_mask]

            small = multi_sizes <= triu_threshold
            for gs, ge in zip(multi_starts[small], multi_ends[small]):
                size = int(ge - gs)
                file_ids = order[gs:ge]
                ii, jj = np.triu_indices(size, k=1)
                fi = file_ids[ii]
                fj = file_ids[jj]
                lo = np.minimum(fi, fj).astype(np.int32)
                hi = np.maximum(fi, fj).astype(np.int32)
                all_i.append(lo)
                all_j.append(hi)
                pairs_total += len(lo)

            medium = (multi_sizes > triu_threshold) & (multi_sizes <= max_bucket_size)
            for gs, ge in zip(multi_starts[medium], multi_ends[medium]):
                if limit_hit and (ge - gs) > 5:
                    skipped_by_limit += 1
                    continue
                file_ids = order[gs:ge]
                fi = file_ids[:-1]
                fj = file_ids[1:]
                lo = np.minimum(fi, fj).astype(np.int32)
                hi = np.maximum(fi, fj).astype(np.int32)
                all_i.append(lo)
                all_j.append(hi)
                pairs_total += len(lo)

                if pairs_total > max_pairs:
                    limit_hit = True

            huge = multi_sizes > max_bucket_size
            if huge.any():
                skipped_huge += int(huge.sum())

        elapsed = time.time() - t_start
        print(f"[dup] image MIH block {b_idx + 1}/{num_blocks}: "
              f"pairs={pairs_total}, skipped_huge={skipped_huge}, "
              f"skipped_by_limit={skipped_by_limit}, "
              f"elapsed={elapsed:.1f}s")

    if not all_i:
        return (np.empty(0, dtype=np.int32),
                np.empty(0, dtype=np.int32))

    all_i_cat = np.concatenate(all_i)
    all_j_cat = np.concatenate(all_j)

    if n < 2 ** 31:
        codes = (all_i_cat.astype(np.int64) * np.int64(n)
                 + all_j_cat.astype(np.int64))
    else:
        codes = (all_i_cat.astype(np.uint64) * np.uint64(n)
                 + all_j_cat.astype(np.uint64))

    unique_codes = np.unique(codes)
    if n < 2 ** 31:
        unique_i = (unique_codes // n).astype(np.int32)
        unique_j = (unique_codes % n).astype(np.int32)
    else:
        unique_i = (unique_codes // np.uint64(n)).astype(np.int32)
        unique_j = (unique_codes % np.uint64(n)).astype(np.int32)

    return unique_i, unique_j


def _image_bruteforce_np(hashes_np, max_hamming):
    n = len(hashes_np)
    all_pairs = []
    batch_size = 200
    m_slots = hashes_np.shape[1]

    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        b = end - start
        batch = hashes_np[start:end]
        rest = hashes_np[start:]
        n_rest = len(rest)

        min_hamming = np.full((b, n_rest), 255, dtype=np.uint8)
        min_ma = np.zeros((b, n_rest), dtype=np.uint8)
        min_mb = np.zeros((b, n_rest), dtype=np.uint8)

        for ka in range(m_slots):
            a_col = batch[:, ka]
            for kb in range(m_slots):
                b_col = rest[:, kb]
                xor = np.bitwise_xor(a_col[:, None], b_col[None, :])
                pc = _popcount64_np(xor)
                better = pc < min_hamming
                min_hamming = np.where(better, pc, min_hamming)
                min_ma = np.where(better, ka, min_ma)
                min_mb = np.where(better, kb, min_mb)

        valid = min_hamming <= max_hamming
        triu = np.triu(np.ones_like(valid, dtype=bool), k=1)
        final = valid & triu

        li_arr, lj_arr = np.where(final)
        for k in range(len(li_arr)):
            li = int(li_arr[k])
            lj = int(lj_arr[k])
            all_pairs.append((
                start + li,
                start + lj,
                int(min_hamming[li, lj]),
                int(min_ma[li, lj]),
                int(min_mb[li, lj]),
            ))

        print(f"[dup]   image brute: {end}/{n} "
              f"(matches: {len(all_pairs)})")

    return all_pairs


def _image_verify_candidates_np(hashes_np, i_arr, j_arr, max_hamming):
    if len(i_arr) == 0:
        return []

    all_pairs = []
    batch_size = 200000
    total = len(i_arr)
    m_slots = hashes_np.shape[1]

    t_start = time.time()

    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        bi = i_arr[start:end]
        bj = j_arr[start:end]

        h_i = hashes_np[bi]
        h_j = hashes_np[bj]

        min_hamming = np.full(len(bi), 255, dtype=np.uint8)
        min_ma = np.zeros(len(bi), dtype=np.uint8)
        min_mb = np.zeros(len(bi), dtype=np.uint8)

        for ka in range(m_slots):
            a_col = h_i[:, ka]
            for kb in range(m_slots):
                b_col = h_j[:, kb]
                xor = np.bitwise_xor(a_col, b_col)
                pc = _popcount64_np(xor)
                better = pc < min_hamming
                min_hamming = np.where(better, pc, min_hamming)
                min_ma = np.where(better, ka, min_ma)
                min_mb = np.where(better, kb, min_mb)

        valid = min_hamming <= max_hamming
        valid_idx = np.where(valid)[0]
        if len(valid_idx):
            for k in valid_idx:
                k = int(k)
                all_pairs.append((
                    int(bi[k]),
                    int(bj[k]),
                    int(min_hamming[k]),
                    int(min_ma[k]),
                    int(min_mb[k]),
                ))

        elapsed = time.time() - t_start
        print(f"[dup]   image verify: {end}/{total} "
              f"(matched: {len(all_pairs)}, elapsed={elapsed:.1f}s)")

    return all_pairs


def _image_pair_matches(paths, frames_by_path, item_by_path,
                        max_hamming=IMAGE_MAX_HAMMING,
                        lsh_hamming=IMAGE_LSH_HAMMING):
    image_indices = [
        i for i, p in enumerate(paths)
        if _is_image_item(item_by_path.get(p))
    ]
    n = len(image_indices)
    if n < 2:
        return {}

    print(f"[dup] image phase: {n} photos")

    hashes_np = np.zeros((n, NUM_HASHES), dtype=np.uint64)
    valid = np.zeros(n, dtype=bool)

    skipped_no_frames = 0
    skipped_bad_hex = 0
    non_zero_count = 0

    for k, i in enumerate(image_indices):
        frames = frames_by_path.get(paths[i])
        if not frames:
            skipped_no_frames += 1
            continue
        hashes_list = frames[0].get('hashes') or []
        if len(hashes_list) < NUM_HASHES:
            skipped_no_frames += 1
            continue
        ok = False
        for m in range(NUM_HASHES):
            hx = hashes_list[m]
            if not hx:
                continue
            try:
                val = int(hx, 16) if isinstance(hx, str) else int(hx)
            except (ValueError, TypeError):
                skipped_bad_hex += 1
                continue
            hashes_np[k, m] = np.uint64(val)
            if val != 0:
                non_zero_count += 1
            ok = True
        if ok:
            valid[k] = True

    print(f"[dup] image hashes: non-zero={non_zero_count}, "
          f"skipped_no_frames={skipped_no_frames}, "
          f"skipped_bad_hex={skipped_bad_hex}")

    valid_k = np.where(valid)[0]
    hashes_np = hashes_np[valid_k]
    n_valid = len(valid_k)
    print(f"[dup] image phase: {n_valid} valid photos "
          f"({n - n_valid} skipped)")

    if n_valid < 2:
        return {}

    if n_valid <= IMAGE_BRUTE_FORCE_LIMIT:
        print(f"[dup] image mode: brute-force (n={n_valid})")
        all_pairs = _image_bruteforce_np(hashes_np, max_hamming)
    else:
        print(f"[dup] image mode: MIH "
              f"(n={n_valid}, lsh_hamming={lsh_hamming})")
        t0 = time.time()
        i_arr, j_arr = _image_mih_candidates_np(
            hashes_np, lsh_hamming,
            triu_threshold=IMAGE_MIH_TRIU_THRESHOLD,
            max_bucket_size=IMAGE_MIH_MAX_BUCKET_SIZE,
            max_pairs=IMAGE_MIH_MAX_PAIRS,
        )
        t_mih = time.time() - t0
        print(f"[dup] image MIH done: {len(i_arr)} unique candidates "
              f"in {t_mih:.1f}s")

        t0 = time.time()
        all_pairs = _image_verify_candidates_np(
            hashes_np, i_arr, j_arr, max_hamming)
        t_verify = time.time() - t0
        print(f"[dup] image verify done: {len(all_pairs)} matched "
              f"in {t_verify:.1f}s")

    result = {}
    for (local_i, local_j, hamming, ma, mb) in all_pairs:
        i_path = image_indices[valid_k[local_i]]
        j_path = image_indices[valid_k[local_j]]
        result[(i_path, j_path)] = {
            'hamming': hamming,
            'mirror_a': ma,
            'mirror_b': mb,
        }

    total_pairs = n_valid * (n_valid - 1) // 2
    print(f"[dup] image matched: {len(result)} / {total_pairs}")
    return result


# ===================================================================
#                     Video verification
# ===================================================================
def _frame_best_distance(hashes_a, hashes_b):
    best = 999
    for h1 in hashes_a:
        for h2 in hashes_b:
            d = h1 - h2
            if d < best:
                best = d
    return best


def _video_match_matrix(ha, hb, max_hamming=FAST_MAX_HAMMING):
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


def _classify_video_pair(ha, hb, mode='fast'):
    n_a = len(ha)
    n_b = len(hb)
    if n_a == 0 or n_b == 0:
        return None

    deep = mode == 'deep'
    max_hamming = DEEP_MAX_HAMMING if deep else FAST_MAX_HAMMING
    full_coverage = DEEP_FULL_COVERAGE if deep else FULL_COVERAGE
    partial_min = (DEEP_PARTIAL_MIN_SUBSEQ_FRAMES if deep
                   else PARTIAL_MIN_SUBSEQ_FRAMES)
    matched = _video_match_matrix(ha, hb, max_hamming)

    greedy_pairs = []
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

    if not greedy_pairs:
        return None

    j_seq = [p[1] for p in greedy_pairs]
    n = len(j_seq)
    dp = [1] * n
    parent = [-1] * n
    for k in range(n):
        for m in range(k):
            if j_seq[m] < j_seq[k] and dp[m] + 1 > dp[k]:
                dp[k] = dp[m] + 1
                parent[k] = m
    best_len = max(dp)
    best_end = dp.index(best_len)

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

    if (coverage_a >= full_coverage and
            coverage_b >= full_coverage and
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

    if subseq_len >= partial_min:
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


def _video_candidates(paths, hash_objs, image_path_set, mode='fast'):
    """Exact-hash candidates, plus block keys in deep mode.

    Exact equality alone misses re-encoded clips where every sampled frame
    differs by one or two bits, because the tolerance is applied only after a
    candidate pair has been found. Block keys let those pairs reach the
    verifier while keeping the index small.
    """
    n = len(paths)
    pairs = set()
    index = defaultdict(list)
    block_index = defaultdict(list) if mode == 'deep' else None

    for i in range(n):
        if paths[i] in image_path_set:
            continue
        for _, hashes in hash_objs[i]:
            for h in hashes:
                index[h].append(i)
                if block_index is None:
                    continue
                text = str(h)
                for block_no in range(0, len(text), DEEP_HASH_BLOCK_HEX):
                    block = text[block_no:block_no + DEEP_HASH_BLOCK_HEX]
                    if len(block) == DEEP_HASH_BLOCK_HEX:
                        block_index[(block_no, block)].append(i)

    for buckets in (index, block_index):
        if not buckets:
            continue
        for _, files in buckets.items():
            if len(files) < 2:
                continue
            uniq = sorted(set(files))
            if len(uniq) > MAX_CANDIDATE_BUCKET:
                # A bucket this large is almost always a degenerate hash
                # (black frames, static intro) and would explode pairwise work.
                continue
            for a in range(len(uniq)):
                for b in range(a + 1, len(uniq)):
                    pairs.add((uniq[a], uniq[b]))
    return pairs


# ===================================================================
#                     Audio LSH
# ===================================================================
def _audio_lsh_candidates(paths, audio_by_path, image_path_set):
    n = len(paths)
    mask = (1 << AUDIO_LSH_BAND_BITS) - 1
    bucket = defaultdict(list)

    for i in range(n):
        if paths[i] in image_path_set:
            continue
        fp = audio_by_path.get(paths[i])
        if not fp:
            continue
        arr_all = np.asarray(fp, dtype=np.uint32)
        for skip_sec in AUDIO_SKIP_VARIANTS:
            skip_steps = int(skip_sec * AUDIO_SUBPRINT_PER_SEC)
            arr = arr_all[skip_steps:] if skip_steps > 0 else arr_all
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
                    bucket[(skip_sec, band, bv_i)].append((i, pos))

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

    image_path_set = set()
    for p in paths:
        item = item_by_path.get(p) or {}
        if _is_image_item(item):
            image_path_set.add(p)

    n_images = len(image_path_set)
    n_videos = n - n_images
    print(f"[dup] videos: {n_videos}, photos: {n_images}")

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
    video_pairs = _video_candidates(paths, hash_objs, image_path_set, mode=mode)
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
                          hash_objs[i], hash_objs[j], mode)
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

    # ---------- PHOTO ----------
    _set_progress(task_id, 60, 0, 0, 'Comparing photos...')
    image_matches_by_pair = {}
    if n_images >= 2:
        raw_image_matches = _image_pair_matches(
            paths, frames_by_path, item_by_path)
        for (i, j), res in raw_image_matches.items():
            if is_ignored(i, j):
                continue
            image_matches_by_pair[(i, j)] = res
            union(i, j)
        print(f"[dup] image matched after blacklist: "
              f"{len(image_matches_by_pair)}")

    # ---------- AUDIO ----------
    audio_matches_by_pair = {}
    if mode == 'deep':
        _set_progress(task_id, 65, 0, 0, 'Building audio LSH index...')
        audio_pairs_all = _audio_lsh_candidates(
            paths, audio_by_path, image_path_set)
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

    print(f"[dup] pre-indexing matches by root...")

    video_by_root = defaultdict(list)
    for (i, j), res in video_matches_by_pair.items():
        video_by_root[find(i)].append((i, j, res))

    image_by_root = defaultdict(list)
    for (i, j), res in image_matches_by_pair.items():
        image_by_root[find(i)].append((i, j, res))

    audio_by_root = defaultdict(list)
    for (i, j), res in audio_matches_by_pair.items():
        audio_by_root[find(i)].append((i, j, res))

    by_root = defaultdict(list)
    for i in range(n):
        by_root[find(i)].append(i)

    print(f"[dup] roots: {len(by_root)}, "
          f"video matches: {len(video_matches_by_pair)}, "
          f"image matches: {len(image_matches_by_pair)}, "
          f"audio matches: {len(audio_matches_by_pair)}")

    groups = []
    done_g = 0
    total_g = len(by_root)
    for root, idxs in by_root.items():
        done_g += 1
        if done_g % 500 == 0 or done_g == total_g:
            print(f"[dup] grouping: {done_g}/{total_g}")

        if len(idxs) < 2:
            continue
        group_files = []
        for i in idxs:
            item = item_by_path.get(paths[i])
            if item and item.get('id') is not None:
                group_files.append(item)
        if len(group_files) < 2:
            continue

        video_list = []
        for (i, j, res) in video_by_root.get(root, []):
            a = item_by_path.get(paths[i])
            b = item_by_path.get(paths[j])
            if a and b:
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

        image_list = []
        for (i, j, res) in image_by_root.get(root, []):
            a = item_by_path.get(paths[i])
            b = item_by_path.get(paths[j])
            if a and b:
                image_list.append({
                    'a_id': a['id'],
                    'b_id': b['id'],
                    'kind': 'image',
                    'hamming': res['hamming'],
                    'mirror_a': res['mirror_a'],
                    'mirror_b': res['mirror_b'],
                })

        audio_list = []
        for (i, j, res) in audio_by_root.get(root, []):
            a = item_by_path.get(paths[i])
            b = item_by_path.get(paths[j])
            if a and b:
                audio_list.append({
                    'a_id': a['id'],
                    'b_id': b['id'],
                    'kind': res.get('kind', 'full'),
                    'run_sec': res['run_sec'],
                    'start_a_sec': res['start_a_sec'],
                    'start_b_sec': res['start_b_sec'],
                    'skipped_intro_sec': res.get('skipped_intro_sec', 0),
                })

        if video_list and audio_list:
            reason = 'video+audio'
        elif video_list:
            reason = 'video'
        elif image_list:
            reason = 'image'
        elif audio_list:
            reason = 'audio'
        else:
            continue

        if image_list and not video_list and not audio_list:
            kind = 'image'
        else:
            has_full = any(v['kind'] == 'full' for v in video_list) or \
                       any(a['kind'] == 'full' for a in audio_list)
            has_partial = any(v['kind'] == 'partial' for v in video_list) or \
                          any(a['kind'] == 'partial' for a in audio_list)
            if has_full and has_partial:
                kind = 'mixed'
            elif has_full:
                kind = 'full'
            elif has_partial:
                kind = 'partial'
            else:
                kind = 'image'

        groups.append({
            'files': group_files,
            'reason': reason,
            'kind': kind,
            'matches': {
                'video': video_list,
                'audio': audio_list,
                'image': image_list,
            },
        })

    print(f"[dup] grouped {len(groups)} groups")
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

        valid_items = []
        missing_count = 0
        for v in all_items:
            if not os.path.exists(v.get('filepath', '')):
                missing_count += 1
                delete_video(v['id'])
            else:
                valid_items.append(v)
        if missing_count:
            print(f"[dup] removed {missing_count} missing files from DB")
        all_items = valid_items

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
            futures = {ex.submit(_process_media, it, include_audio): it
                       for it in all_items}
            for fut in as_completed(futures):
                item = futures[fut]
                entry = None
                try:
                    entry = fut.result()
                except Exception as e:
                    print(f"[dup] hash err: {e}", flush=True)

                processed += 1
                pct = int(processed / total * 40)
                _set_progress(task_id, pct, processed, total,
                              f'Hashing {processed}/{total}')

                if entry is None:
                    path = item['filepath']
                    if os.path.exists(path):
                        print(f"[dup] file broken, moving to !Broken: {path}", flush=True)
                        try:
                            move_to_broken(path, keep_name=item['filename'])
                            delete_video(item['id'])
                        except Exception as e:
                            print(f"[dup] failed to move broken file: {e}", flush=True)
                else:
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
    image_matches = matches.get('image', []) or []

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
    used_image_pairs = []

    def is_ignored_by_id(a_id, b_id):
        pa = path_by_id.get(a_id, '')
        pb = path_by_id.get(b_id, '')
        if not pa or not pb:
            return False
        return _pair_key(pa, pb) in blacklist

    for m in video_matches:
        a_id, b_id = m.get('a_id'), m.get('b_id')
        if a_id in idx and b_id in idx and not is_ignored_by_id(a_id, b_id):
            union(idx[a_id], idx[b_id])
            used_video_pairs.append(m)
    for m in audio_matches:
        a_id, b_id = m.get('a_id'), m.get('b_id')
        if a_id in idx and b_id in idx and not is_ignored_by_id(a_id, b_id):
            union(idx[a_id], idx[b_id])
            used_audio_pairs.append(m)
    for m in image_matches:
        a_id, b_id = m.get('a_id'), m.get('b_id')
        if a_id in idx and b_id in idx and not is_ignored_by_id(a_id, b_id):
            union(idx[a_id], idx[b_id])
            used_image_pairs.append(m)

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
        sub_image = [m for m in used_image_pairs
                     if m['a_id'] in vids_set and m['b_id'] in vids_set]
        if sub_video and sub_audio:
            r = 'video+audio'
        elif sub_video:
            r = 'video'
        elif sub_image:
            r = 'image'
        elif sub_audio:
            r = 'audio'
        else:
            continue

        if sub_image and not sub_video and not sub_audio:
            kind = 'image'
        else:
            has_full = any(v.get('kind') == 'full' for v in sub_video) or \
                       any(a.get('kind') == 'full' for a in sub_audio)
            has_partial = any(v.get('kind') == 'partial' for v in sub_video) or \
                          any(a.get('kind') == 'partial' for a in sub_audio)
            if has_full and has_partial:
                kind = 'mixed'
            elif has_full:
                kind = 'full'
            elif has_partial:
                kind = 'partial'
            else:
                kind = 'image'

        result.append({
            'files': sub_files,
            'reason': r,
            'kind': kind,
            'matches': {
                'video': sub_video,
                'audio': sub_audio,
                'image': sub_image,
            },
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
                        'matches': g.get('matches', {
                            'video': [], 'audio': [], 'image': []
                        }),
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

    existing = set()
    ids_list = list(all_ids)
    conn = get_db_connection()
    try:
        c = conn.cursor()
        for i in range(0, len(ids_list), SQL_PARAM_CHUNK):
            chunk = ids_list[i:i + SQL_PARAM_CHUNK]
            placeholders = ','.join('?' * len(chunk))
            c.execute(f'SELECT id FROM videos WHERE id IN ({placeholders})',
                      tuple(chunk))
            existing.update(row['id'] for row in c.fetchall())
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
                'matches': g.get('matches', {
                    'video': [], 'audio': [], 'image': []
                }),
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
    task = scan_progress.get(task_id)
    return dict(task) if task is not None else None


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
                'matches': g.get('matches', {
                    'video': [], 'audio': [], 'image': []
                }),
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