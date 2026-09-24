r"""
Поиск дубликатов видео и картинок — версия с SQLite-кешем, MIH и FFT.

Архитектура:
  • SQLite-кеш (hash_cache.db) — быстрая загрузка/сохранение,
    индексы для LSH-поиска кандидатов, WAL mode.
  • Video: 5 кадров × 4 зеркала. LSH-индекс через hash-index
    (THRESHOLD=0) или MIH 4×16 (THRESHOLD>0).
  • Audio: chromaprint → LSH 4×8-битные полосы, затем
    FFT cross-correlation для отсева, затем точный longest-run.
  • Skip audio для video-matched пар — экономит до 90% времени.
  • Приоритезация по размеру файла (опционально).

Прогресс-фазы:
  0-40%   хеширование (или загрузка из кеша)
  40-45%  подготовка video-индекса
  45-65%  video verification
  65-70%  audio LSH-индекс
  70-80%  audio FFT отсев
  80-95%  audio verification
  95-100% группировка
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

# ---------- Video ----------
THRESHOLD = 0
KEYFRAME_POSITIONS = [0.10, 0.30, 0.50, 0.70, 0.90]

# MIH для phash: 4 полосы по 16 бит.
MIH_BANDS = 4
MIH_BAND_BITS = 16
# Минимум совпавших полос в одном и том же кадре, чтобы пара
# считалась кандидатом (используется только при THRESHOLD > 0).
MIH_MIN_HITS = 2

# ---------- Audio ----------
AUDIO_BITS_TOLERANT = 10
AUDIO_MIN_RUN_SEC   = 10.0
AUDIO_SUBPRINT_PER_SEC = 1.0 / 0.1234
FPCALC_MAX_LENGTH = 600
AUDIO_SKIP_INTRO_SEC = 30.0

AUDIO_SILENCE_MIN_UNIQUE_RATIO     = 0.10
AUDIO_SILENCE_MIN_TRANSITION_RATIO = 0.10
AUDIO_SILENCE_MIN_MEAN_DIFF_BITS   = 2.0

AUDIO_PREFILTER_SAMPLES    = 40
AUDIO_PREFILTER_GOOD_RATIO = 0.30

# LSH
AUDIO_LSH_BANDS       = 4
AUDIO_LSH_BAND_BITS   = 8
AUDIO_LSH_MIN_MATCHES = 2
AUDIO_LSH_MAX_POS_DELTA = 8

# FFT prefilter: peak / std должен быть >= этого, чтобы считать,
# что между дорожками есть корреляция. Ниже — пропускаем без
# точной проверки.
FFT_PEAK_STD_RATIO = 4.0

# ---------- Параллелизм ----------
COMPARE_WORKERS = min(8, max(2, (os.cpu_count() or 4)))
HASH_WORKERS    = min(4, max(2, (os.cpu_count() or 4)))


FPCALC_PATH = None
for cand in FPCALC_CANDIDATES:
    if cand and os.path.exists(cand):
        FPCALC_PATH = cand
        break

print(f"[dup] fpcalc path = {FPCALC_PATH}")
print(f"[dup] audio: skip {AUDIO_SKIP_INTRO_SEC:.0f}s, run >= {AUDIO_MIN_RUN_SEC:.0f}s")
print(f"[dup] LSH audio {AUDIO_LSH_BANDS}x{AUDIO_LSH_BAND_BITS}bit, "
      f"min_hits={AUDIO_LSH_MIN_MATCHES}, FFT ratio={FFT_PEAK_STD_RATIO}")
print(f"[dup] workers: hash={HASH_WORKERS}, compare={COMPARE_WORKERS}, "
      f"threshold={THRESHOLD}")
print(f"[dup] mode: SQLite cache + MIH + FFT, skip audio for video-matched")


# ===================================================================
#                     SQLite cache
# ===================================================================
_local = threading.local()


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
    c.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files(path)")
    conn.commit()


def _get_or_create_file(path, mtime, size, media_type):
    """Возвращает (file_id, is_fresh).  is_fresh=False — надо пересчитать."""
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
    """Удаляет из кеша файлы, которых больше нет на диске."""
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
             '-length', str(FPCALC_MAX_LENGTH), filepath],
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
    """
    Быстрая проверка, есть ли между a и b корреляция.
    Использует FFT по popcount. Возвращает (True, peak_std_ratio)
    или (False, ratio).
    """
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
    return ratio >= FFT_PEAK_STD_RATIO, ratio


def _verify_audio_at_shift(a, b, shift_a, shift_b):
    """Точная проверка на заданном сдвиге: longest run."""
    if shift_a >= len(a) or shift_b >= len(b):
        return None
    la = len(a) - shift_a
    lb = len(b) - shift_b
    L = min(la, lb)
    if L < 40:
        return None
    wa = a[shift_a:shift_a + L]
    wb = b[shift_b:shift_b + L]
    xor = np.bitwise_xor(wa, wb)
    bits = _popcount_u32(xor)
    flags = (bits <= AUDIO_BITS_TOLERANT).astype(np.int32)
    run_len, run_start = _find_longest_run_np(flags)
    min_run_steps = max(20, int(AUDIO_MIN_RUN_SEC * AUDIO_SUBPRINT_PER_SEC))
    if run_len < min_run_steps:
        return None
    run_slice = wa[run_start:run_start + run_len]
    if _is_silence_like(run_slice):
        return None
    start_a_step = shift_a + run_start
    start_b_step = shift_b + run_start
    return {
        'run_steps': int(run_len),
        'run_sec': float(run_len / AUDIO_SUBPRINT_PER_SEC),
        'start_a_sec': float(AUDIO_SKIP_INTRO_SEC +
                             start_a_step / AUDIO_SUBPRINT_PER_SEC),
        'start_b_sec': float(AUDIO_SKIP_INTRO_SEC +
                             start_b_step / AUDIO_SUBPRINT_PER_SEC),
        'skipped_intro_sec': float(AUDIO_SKIP_INTRO_SEC),
    }


def _find_audio_match(a_raw, b_raw):
    """
    Точная проверка пары fingerprint'ов.

    Схема:
      1. Пропускаем интро, отсеиваем тишину.
      2. FFT-префильтр: если корреляции нет — сразу None.
      3. Полный перебор сдвигов с префильтром.
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

    # FFT-отсев: если корреляции нет, точный перебор не нужен.
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

    min_run_steps = max(20, int(AUDIO_MIN_RUN_SEC * AUDIO_SUBPRINT_PER_SEC))
    min_run_steps = min(min_run_steps, lb)
    prefilter_len = min(AUDIO_PREFILTER_SAMPLES, lb)
    short_head = short_fp[:prefilter_len]

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

            return {
                'run_steps': int(run_len),
                'run_sec': float(run_len / AUDIO_SUBPRINT_PER_SEC),
                'start_a_sec': float(AUDIO_SKIP_INTRO_SEC +
                                     start_a_step / AUDIO_SUBPRINT_PER_SEC),
                'start_b_sec': float(AUDIO_SKIP_INTRO_SEC +
                                     start_b_step / AUDIO_SUBPRINT_PER_SEC),
                'skipped_intro_sec': float(AUDIO_SKIP_INTRO_SEC),
                'fft_ratio': round(float(ratio), 2),
            }
    return None


# ===================================================================
#                     Обработка одного медиа
# ===================================================================
def _process_media(item):
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
        audio_fp = _load_audio_fp(file_id) if media_type == 'video' else None
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

    # Пересчёт
    if media_type == 'image':
        frames = _process_image_frames(path)
        audio_fp = None
    else:
        frames = _process_video_frames(path)
        audio_fp = _extract_audio_fingerprint(path)

    if not frames:
        return None

    _save_video_frames(file_id, frames)
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
#                     Video verification
# ===================================================================
def _precompute_video_hashes(frames_by_path, paths):
    out = []
    for p in paths:
        out.append([
            (f['position'], [imagehash.hex_to_hash(h) for h in f['hashes']])
            for f in frames_by_path[p]
        ])
    return out


def _verify_video_pair(ha, hb, threshold):
    matches = []
    for pos_a, hashes_a in ha:
        for pos_b, hashes_b in hb:
            best = 999
            for h1 in hashes_a:
                for h2 in hashes_b:
                    d = h1 - h2
                    if d < best:
                        best = d
            if best <= threshold:
                matches.append({
                    'pos_a': float(pos_a),
                    'pos_b': float(pos_b),
                    'hamming': int(best),
                })
    return matches


def _video_candidates(paths, hash_objs, threshold):
    """
    THRESHOLD=0: hash-index по всем 20 хешам на файл (5 кадров × 4 зеркала).
    THRESHOLD>0: MIH 4×16 по orig-хешу каждого кадра.
    """
    n = len(paths)
    pairs = set()
    if threshold == 0:
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
    else:
        # MIH: (frame_idx, band_idx, band_val) -> [file_idx]
        bucket = defaultdict(list)
        mask = (1 << MIH_BAND_BITS) - 1
        for i in range(n):
            for frame_idx, (_, hashes) in enumerate(hash_objs[i]):
                try:
                    phash_int = int(hashes[0], 16)
                except (ValueError, TypeError):
                    continue
                for band_idx in range(MIH_BANDS):
                    shift = band_idx * MIH_BAND_BITS
                    bv = (phash_int >> shift) & mask
                    bucket[(frame_idx, band_idx, bv)].append(i)
        pair_hits = defaultdict(int)
        for key, files in bucket.items():
            if len(files) < 2:
                continue
            frame_idx = key[0]
            uniq = sorted(set(files))
            for a in range(len(uniq)):
                for b in range(a + 1, len(uniq)):
                    pair_hits[(uniq[a], uniq[b], frame_idx)] += 1
        for (fi, fj, _), hits in pair_hits.items():
            if hits >= MIH_MIN_HITS:
                pairs.add((fi, fj))
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
               item_by_path, threshold):
    paths = list(frames_by_path.keys())
    n = len(paths)
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

    # ---------- VIDEO ----------
    _set_progress(task_id, 40, 0, 0, 'Preparing video index...')
    hash_objs = _precompute_video_hashes(frames_by_path, paths)

    _set_progress(task_id, 42, 0, 0, 'Building video candidate index...')
    video_pairs = _video_candidates(paths, hash_objs, threshold)
    print(f"[dup] video candidates: {len(video_pairs)}")

    video_matches_by_pair = {}
    total_vp = len(video_pairs)
    done_v = 0
    if total_vp:
        _set_progress(task_id, 45, 0, total_vp, 'Verifying video pairs...')
        pairs_list = list(video_pairs)
        with ThreadPoolExecutor(max_workers=COMPARE_WORKERS) as ex:
            futures = [
                ex.submit(_verify_video_pair,
                          hash_objs[i], hash_objs[j], threshold)
                for (i, j) in pairs_list
            ]
            for fut, (i, j) in zip(futures, pairs_list):
                m = fut.result()
                if m:
                    union(i, j)
                    video_matches_by_pair[(i, j)] = m
                done_v += 1
                if done_v % 200 == 0 or done_v == total_vp:
                    pct = 45 + int(done_v / total_vp * 20)
                    _set_progress(task_id, pct, done_v, total_vp,
                                  f'Video verify {done_v}/{total_vp}')
    print(f"[dup] video matched: {len(video_matches_by_pair)} / {total_vp}")

    # ---------- AUDIO ----------
    _set_progress(task_id, 65, 0, 0, 'Building audio LSH index...')
    audio_pairs_all = _audio_lsh_candidates(paths, audio_by_path)
    print(f"[dup] audio candidates (LSH raw): {len(audio_pairs_all)}")

    audio_pairs = {p for p in audio_pairs_all if find(p[0]) != find(p[1])}
    skipped = len(audio_pairs_all) - len(audio_pairs)
    print(f"[dup] audio after skip video-matched: {len(audio_pairs)} "
          f"(skipped {skipped})")

    audio_matches_by_pair = {}
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
                if res and find(i) != find(j):
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

        video_list = []
        for (i, j), pairs in video_matches_by_pair.items():
            a = item_by_path.get(paths[i])
            b = item_by_path.get(paths[j])
            if a and b and a['id'] in group_ids and b['id'] in group_ids:
                video_list.append({
                    'a_id': a['id'],
                    'b_id': b['id'],
                    'pairs': pairs,
                })

        audio_list = []
        for (i, j), res in audio_matches_by_pair.items():
            a = item_by_path.get(paths[i])
            b = item_by_path.get(paths[j])
            if a and b and a['id'] in group_ids and b['id'] in group_ids:
                audio_list.append({
                    'a_id': a['id'],
                    'b_id': b['id'],
                    'run_sec': res['run_sec'],
                    'start_a_sec': res['start_a_sec'],
                    'start_b_sec': res['start_b_sec'],
                    'skipped_intro_sec': res.get('skipped_intro_sec', 0),
                })

        if video_list and audio_list:
            reason = 'video+audio'
        elif video_list:
            reason = 'video'
        elif audio_list:
            reason = 'audio'
        else:
            continue

        groups.append({
            'files': group_files,
            'reason': reason,
            'matches': {'video': video_list, 'audio': audio_list},
        })

    return groups


# ===================================================================
#                     Запуск
# ===================================================================
def find_duplicates_async(task_id, filters=None):
    global scan_in_progress, scan_progress
    with _lock:
        if scan_in_progress:
            print("[dup] scan already running")
            return False
        scan_in_progress = True
        scan_progress[task_id] = {
            'status': 'scanning', 'progress': 0, 'total': 0,
            'processed': 0, 'message': 'Initializing...'
        }
    threading.Thread(target=_find_duplicates_worker,
                     args=(task_id, filters), daemon=True).start()
    print(f"[dup] started task_id={task_id} filters={filters}")
    return True


def _find_duplicates_worker(task_id, filters):
    global DUPLICATE_GROUPS, scan_in_progress
    try:
        print(f"[dup] worker start")
        _init_db()

        all_items = get_all_videos()
        print(f"[dup] total in DB: {len(all_items)}")
        if filters:
            allowed = set(filters)
            all_items = [v for v in all_items
                         if v.get('media_type', 'video') in allowed]
        print(f"[dup] after filter: {len(all_items)}")

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

        # Удаляем устаревшие записи из кеша
        _cleanup_db([v['filepath'] for v in all_items])

        processed = 0
        cached_count = 0
        frames_by_path = {}
        audio_by_path = {}

        with ThreadPoolExecutor(max_workers=HASH_WORKERS) as ex:
            futures = [ex.submit(_process_media, it) for it in all_items]
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
                    audio_by_path[fp] = entry.get('audio_fp')
                    if entry.get('from_cache'):
                        cached_count += 1

        item_by_path = {it['filepath']: it for it in all_items}
        print(f"[dup] hashed {len(frames_by_path)}/{total} "
              f"(cached: {cached_count}, fresh: {total - cached_count})")

        groups = _group_all(task_id, frames_by_path, audio_by_path,
                            item_by_path, THRESHOLD)
        print(f"[dup] found {len(groups)} groups")

        DUPLICATE_GROUPS = groups
        _save_groups(groups)

        scan_progress[task_id].update({
            'status': 'complete', 'progress': 100,
            'message': f'Found {len(groups)} duplicate groups'
        })
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
    changed = False
    for g in DUPLICATE_GROUPS:
        new_files = [v for v in g['files']
                     if int(v.get('id', -1)) in existing]
        if len(new_files) >= 2:
            if len(new_files) != len(g['files']):
                changed = True
            cleaned.append({
                'files': new_files,
                'reason': g['reason'],
                'matches': g.get('matches', {'video': [], 'audio': []}),
            })
        else:
            changed = True
    if changed:
        DUPLICATE_GROUPS = cleaned
        _save_groups(cleaned)
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