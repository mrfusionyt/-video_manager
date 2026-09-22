r"""
Поиск дубликатов видео и картинок.

Двухуровневая схема:
  1) 5 ключевых кадров (10/30/50/70/90% длительности).
     Дубликат — если совпало МИНИМУМ 3 из 5 кадров.
     Сравнение хэшей — поэлементное: orig↔orig, mirror↔mirror и т.д.
     (раньше сравнивались все 16 комбинаций, что давало ложные).

  2) Chromaprint (fpcalc) аудио-фингерпринт.
     Для случая "5-мин видео внутри 10-мин" — нужен минимум
     AUDIO_MIN_RUN_SEC секунд непрерывного совпадения.

Кэш хэшей (hash_cache.json) живёт на диске — при повторных сканах
уже проверенные файлы не обрабатываются заново.
"""
import os
import json
import base64
import struct
import subprocess
import threading
import hashlib
import shutil
from functools import lru_cache

import numpy as np
import cv2
from PIL import Image, ImageOps
import imagehash

from models import get_all_videos, get_db_connection
from concurrent.futures import ThreadPoolExecutor, as_completed


# ===================================================================
#                     Конфигурация
# ===================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DUPLICATE_CACHE_FILE = os.path.join(BASE_DIR, 'duplicates_cache.json')
HASH_CACHE_FILE = os.path.join(BASE_DIR, 'hash_cache.json')

FPCALC_CANDIDATES = [
    os.path.join(BASE_DIR, '_dop', 'fpcalc.exe'),
    os.path.join(BASE_DIR, '_dop', 'fpcalc'),
    shutil.which('fpcalc'),
]

# --- Ключевые кадры ---
KEYFRAME_POSITIONS = [0.10, 0.30, 0.50, 0.70, 0.90]
# Минимум совпавших кадров из 5 (было фактически 1 из 1 → теперь 3 из 5)
KEYFRAMES_REQUIRED = 3

# --- Пороги pHash ---
HASH_THRESHOLD_STRICT = 4      # «это точно тот же кадр»
HASH_THRESHOLD_LOOSE  = 10     # «похоже»

# --- Аудио ---
AUDIO_BITS_TOLERANT    = 8     # было 10; снижено против ложных
AUDIO_MIN_RUN_SEC      = 10.0  # было 6; поднято против ложных
AUDIO_SUBPRINT_PER_SEC = 1.0 / 0.1234
FPCALC_MAX_LENGTH      = 600

# --- Длина ---
DURATION_RATIO_FRAGMENT = 1.35
DURATION_SAME_TOLERANCE = 0.10

THRESHOLD = 0     # legacy


# ===================================================================
#                     Глобальное состояние
# ===================================================================
DUPLICATE_GROUPS = []
scan_in_progress = False
scan_progress = {}
_lock = threading.Lock()


FPCALC_PATH = None
for cand in FPCALC_CANDIDATES:
    if cand and os.path.exists(cand):
        FPCALC_PATH = cand
        break


# ===================================================================
#                     Hash cache (persistent)
# ===================================================================
_HASH_CACHE = {}
_HASH_CACHE_LOCK = threading.Lock()
_HASH_CACHE_DIRTY = False


def _load_hash_cache():
    global _HASH_CACHE
    try:
        if os.path.exists(HASH_CACHE_FILE):
            with open(HASH_CACHE_FILE, 'r', encoding='utf-8') as f:
                _HASH_CACHE = json.load(f) or {}
    except Exception as e:
        print(f"[hash-cache] load failed: {e}")
        _HASH_CACHE = {}


def _save_hash_cache():
    global _HASH_CACHE_DIRTY
    with _HASH_CACHE_LOCK:
        if not _HASH_CACHE_DIRTY:
            return
        snapshot = dict(_HASH_CACHE)
        _HASH_CACHE_DIRTY = False
    try:
        with open(HASH_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f)
    except Exception as e:
        print(f"[hash-cache] save failed: {e}")


def _cache_entry_fresh(entry, filepath):
    if not entry:
        return False
    try:
        st = os.stat(filepath)
    except OSError:
        return False
    return (
        entry.get('mtime') == st.st_mtime
        and entry.get('size') == st.st_size
    )


# ===================================================================
#                     Работа с кадрами
# ===================================================================
def extract_frames_at_positions(filepath, positions):
    cap = cv2.VideoCapture(filepath)
    if not cap.isOpened():
        return []
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            ret, frame = cap.read()
            return [frame] if ret else []
        frames = []
        for pos in positions:
            target = max(0, int(total * pos))
            cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            ret, frame = cap.read()
            frames.append(frame if ret else None)
        return frames
    finally:
        cap.release()


def _hash_frame_with_flips(frame):
    """Возвращает 4 хэша: orig / mirror / flip / flip+mirror."""
    if frame is None:
        return None
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb)
    h_orig = str(imagehash.phash(img))
    h_h = str(imagehash.phash(ImageOps.mirror(img)))
    h_v = str(imagehash.phash(ImageOps.flip(img)))
    h_hv = str(imagehash.phash(ImageOps.flip(ImageOps.mirror(img))))
    return [h_orig, h_h, h_v, h_hv]


@lru_cache(maxsize=16384)
def _hex_to_hash(h):
    return imagehash.hex_to_hash(h)


def _hash_distance(h1, h2):
    try:
        return _hex_to_hash(h1) - _hex_to_hash(h2)
    except Exception:
        return 999


def _frames_match_strict(hashes_a, hashes_b, threshold):
    """
    Поэлементное сравнение: orig↔orig, mirror↔mirror, flip↔flip,
    flipmirror↔flipmirror.

    НЕ сравниваем orig↔mirror: это давало ложные срабатывания —
    два разных видео, одно из которых зеркало другого случайно, шли
    в одну группу.
    """
    if not hashes_a or not hashes_b:
        return False
    n = min(len(hashes_a), len(hashes_b))
    for i in range(n):
        h1 = hashes_a[i]
        h2 = hashes_b[i]
        if h1 and h2 and _hash_distance(h1, h2) <= threshold:
            return True
    return False


def _keyframes_overlap_count(cache_a, cache_b, threshold):
    """
    Считает, сколько из 5 позиций совпали (поэлементно).

    Если у одного из кэшей меньше 5 позиций (fallback на 1 кадр) —
    сравниваем только доступные.
    """
    ka = cache_a.get('keyframe_hashes') or []
    kb = cache_b.get('keyframe_hashes') or []
    if not ka or not kb:
        return 0
    n = min(len(ka), len(kb))
    count = 0
    for i in range(n):
        if _frames_match_strict(ka[i], kb[i], threshold):
            count += 1
    return count


# ===================================================================
#                     Аудио-фингерпринт
# ===================================================================
def extract_audio_fingerprint(filepath):
    if not FPCALC_PATH:
        return None, 0
    try:
        result = subprocess.run(
            [FPCALC_PATH, '-raw', '-json',
             '-length', str(FPCALC_MAX_LENGTH), filepath],
            capture_output=True, text=True, timeout=180,
            encoding='utf-8', errors='replace',
        )
        if result.returncode != 0 or not result.stdout:
            return None, 0
        data = json.loads(result.stdout)
        fp_data = data.get('fingerprint')
        if not fp_data:
            return None, 0

        if isinstance(fp_data, list):
            fp = [int(x) & 0xFFFFFFFF for x in fp_data]
        else:
            raw = base64.b64decode(fp_data)
            n = len(raw) // 4
            fp = list(struct.unpack('<%dI' % n, raw[:n * 4]))

        duration = float(data.get('duration', 0) or 0)
        return fp, duration
    except subprocess.TimeoutExpired:
        print(f"[dup] fpcalc timeout: {filepath}")
        return None, 0
    except Exception as e:
        print(f"[dup] fpcalc error: {e}")
        return None, 0


def _popcount_u32(x):
    x = x.astype(np.uint32, copy=False)
    x = x - ((x >> np.uint32(1)) & np.uint32(0x55555555))
    x = (x & np.uint32(0x33333333)) + ((x >> np.uint32(2)) & np.uint32(0x33333333))
    x = (x + (x >> np.uint32(4))) & np.uint32(0x0F0F0F0F)
    x = (x * np.uint32(0x01010101)) >> np.uint32(24)
    return x


def _longest_true_run(flags):
    if not len(flags):
        return 0
    best = 0
    cur = 0
    for f in flags:
        if f:
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return best


def compare_audio_fingerprints(fp_a, fp_b):
    if not fp_a or not fp_b:
        return None

    a = np.asarray(fp_a, dtype=np.uint32)
    b = np.asarray(fp_b, dtype=np.uint32)

    if len(a) < len(b):
        a, b = b, a
        direction = 'a_inside_b'
    elif len(b) < len(a):
        direction = 'b_inside_a'
    else:
        direction = 'same'

    la, lb = len(a), len(b)
    best_run = 0
    best_shift = 0
    max_shift = la - lb

    for shift in range(max_shift + 1):
        window = a[shift:shift + lb]
        xor = np.bitwise_xor(window, b)
        bits = _popcount_u32(xor)
        flags = bits <= AUDIO_BITS_TOLERANT
        run = _longest_true_run(flags)
        if run > best_run:
            best_run = run
            best_shift = shift
            if run == lb:
                break

    return {
        'best_run_sec': best_run * (1.0 / AUDIO_SUBPRINT_PER_SEC),
        'best_run_steps': best_run,
        'best_shift': best_shift,
        'direction': direction,
    }


# ===================================================================
#                     Обработка одного медиа
# ===================================================================
def _process_media(item, cache_lock):
    filepath = item['filepath']
    if not os.path.exists(filepath):
        return None

    try:
        st = os.stat(filepath)
    except OSError:
        return None

    with _HASH_CACHE_LOCK:
        entry = _HASH_CACHE.get(filepath)
    if _cache_entry_fresh(entry, filepath):
        return entry

    media_type = item.get('media_type', 'video')
    result = {
        'filepath': filepath,
        'media_type': media_type,
        'mtime': st.st_mtime,
        'size': st.st_size,
    }

    if media_type == 'image':
        try:
            img = Image.open(filepath).convert('RGB')
            h = str(imagehash.phash(img))
            result['keyframe_hashes'] = [[h]]
        except Exception as e:
            print(f"[dup] Image hash error {filepath}: {e}")
            return None
        result['audio_fp'] = None
        with _HASH_CACHE_LOCK:
            _HASH_CACHE[filepath] = result
            global _HASH_CACHE_DIRTY
            _HASH_CACHE_DIRTY = True
        return result

    # --- Видео: 5 ключевых кадров ---
    frames = extract_frames_at_positions(filepath, KEYFRAME_POSITIONS)
    kf_hashes = [_hash_frame_with_flips(f) for f in frames]

    # fallback на 1 кадр в середине, если 5 не получились
    if not any(kf_hashes):
        cap = cv2.VideoCapture(filepath)
        if cap.isOpened():
            try:
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total // 2))
                ret, frame = cap.read()
                if ret:
                    kf_hashes = [_hash_frame_with_flips(frame)]
            finally:
                cap.release()

    result['keyframe_hashes'] = kf_hashes

    # --- Аудио-фингерпринт ---
    if FPCALC_PATH:
        fp, dur = extract_audio_fingerprint(filepath)
        if fp:
            result['audio_fp'] = fp
            result['audio_duration'] = dur
        else:
            result['audio_fp'] = None
    else:
        result['audio_fp'] = None

    with _HASH_CACHE_LOCK:
        _HASH_CACHE[filepath] = result
        _HASH_CACHE_DIRTY = True

    return result


# ===================================================================
#                     Логика сравнения
# ===================================================================
def _same_length(item_a, item_b):
    da = item_a.get('duration') or 0
    db = item_b.get('duration') or 0
    if da <= 0 or db <= 0:
        return False
    ratio = max(da, db) / max(1, min(da, db))
    return ratio <= 1.0 + DURATION_SAME_TOLERANCE


def _fragment_length_ratio(item_a, item_b):
    da = item_a.get('duration') or 0
    db = item_b.get('duration') or 0
    if da <= 0 or db <= 0:
        return 1.0
    return max(da, db) / max(1, min(da, db))


def _is_duplicate_pair(meta_a, meta_b, cache_a, cache_b):
    """
    Возвращает (True, 'reason') или (False, None).

    Логика:
      1) Если длины примерно равны — сравниваем 5 keyframes.
         Дубликат ТОЛЬКО если совпало >= KEYFRAMES_REQUIRED (3 из 5).
      2) Если длины сильно различаются — сравниваем аудио-фингерпринты.
         Дубликат ТОЛЬКО если есть непрерывное совпадение >= 10 секунд.
      3) Fallback на keyframes для разных длин — тоже 3 из 5.
    """
    if not cache_a or not cache_b:
        return False, None

    media_a = cache_a.get('media_type', 'video')
    media_b = cache_b.get('media_type', 'video')

    # --- Картинки ---
    if media_a == 'image' and media_b == 'image':
        ha = (cache_a.get('keyframe_hashes') or [[None]])[0]
        hb = (cache_b.get('keyframe_hashes') or [[None]])[0]
        if _frames_match_strict(ha, hb, HASH_THRESHOLD_STRICT):
            return True, 'image-phash'
        return False, None

    if media_a != media_b:
        return False, None

    ratio = _fragment_length_ratio(meta_a, meta_b)

    # --- 1) Одинаковая длина: 5 keyframes, нужно 3+ совпадений ---
    if _same_length(meta_a, meta_b):
        matched = _keyframes_overlap_count(cache_a, cache_b, HASH_THRESHOLD_STRICT)
        if matched >= KEYFRAMES_REQUIRED:
            return True, f'keyframes {matched}/5'

    # --- 2) Разная длина: аудио-фингерпринт ---
    if ratio >= DURATION_RATIO_FRAGMENT:
        fp_a = cache_a.get('audio_fp')
        fp_b = cache_b.get('audio_fp')
        if fp_a and fp_b:
            res = compare_audio_fingerprints(fp_a, fp_b)
            if res and res['best_run_sec'] >= AUDIO_MIN_RUN_SEC:
                return True, (f'audio overlap {res["best_run_sec"]:.0f}s '
                              f'({res["direction"]})')

    # --- 3) Разная длина, аудио не сработало: fallback на keyframes ---
    # Нужны 3 из 5 совпадений — реже ложные, чем 1 из 1.
    if ratio >= DURATION_RATIO_FRAGMENT:
        ka = cache_a.get('keyframe_hashes') or []
        kb = cache_b.get('keyframe_hashes') or []
        # Считаем совпадения «всё-со-всем»: у разных длин позиции не совпадают
        common = 0
        for ha in ka:
            for hb in kb:
                if _frames_match_strict(ha, hb, HASH_THRESHOLD_STRICT):
                    common += 1
                    break
        if common >= KEYFRAMES_REQUIRED:
            return True, f'keyframe-overlap {common}/5'

    return False, None


# ===================================================================
#                     Асинхронный запуск
# ===================================================================
def find_duplicates_async(task_id, filters=None):
    global scan_in_progress, scan_progress
    with _lock:
        if scan_in_progress:
            print("[DEBUG] Scan already in progress, ignoring new request.")
            return
        scan_in_progress = True
        scan_progress[task_id] = {
            'status': 'scanning', 'progress': 0, 'total': 0,
            'processed': 0, 'message': 'Initializing...'
        }
    thread = threading.Thread(target=_find_duplicates_worker,
                              args=(task_id, filters), daemon=True)
    thread.start()
    print(f"[DEBUG] Started duplicate scan with task_id={task_id}")


def _find_duplicates_worker(task_id, filters):
    global DUPLICATE_GROUPS, scan_in_progress, scan_progress
    try:
        print(f"[DEBUG] Worker started for task_id={task_id}")

        _load_hash_cache()

        all_items = get_all_videos()
        if filters:
            allowed = set(filters)
            all_items = [v for v in all_items
                         if v.get('media_type', 'video') in allowed]

        total = len(all_items)
        scan_progress[task_id]['total'] = total
        scan_progress[task_id]['message'] = f'Scanning {total} files...'
        print(f"[DEBUG] Total media to scan: {total}")

        if total == 0:
            DUPLICATE_GROUPS = []
            _save_groups([])
            scan_progress[task_id]['status'] = 'complete'
            scan_progress[task_id]['progress'] = 100
            scan_progress[task_id]['message'] = 'No files to scan.'
            return

        cache_lock = threading.Lock()
        processed = 0
        max_workers = min(4, os.cpu_count() or 1)
        by_path = {}

        def _job(item):
            return item, _process_media(item, cache_lock)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_job, it) for it in all_items]
            for fut in as_completed(futures):
                item, entry = fut.result()
                processed += 1
                progress = int((processed / total) * 100)
                scan_progress[task_id]['progress'] = progress
                scan_progress[task_id]['processed'] = processed
                if entry:
                    by_path[item['filepath']] = entry
                name = os.path.basename(item['filepath']) if item else '...'
                scan_progress[task_id]['message'] = \
                    f'Processing {processed}/{total} ({name})'
                if processed % 10 == 0:
                    print(f"[DEBUG] Progress: {progress}% ({processed}/{total})")

        _save_hash_cache()

        scan_progress[task_id]['message'] = 'Comparing pairs...'
        items = list(all_items)
        n = len(items)
        used = set()
        groups = []

        for i in range(n):
            if i in used:
                continue
            item_a = items[i]
            entry_a = by_path.get(item_a['filepath'])
            if not entry_a:
                continue

            group = [item_a]
            used.add(i)

            for j in range(i + 1, n):
                if j in used:
                    continue
                item_b = items[j]
                entry_b = by_path.get(item_b['filepath'])
                if not entry_b:
                    continue
                ok, reason = _is_duplicate_pair(item_a, item_b, entry_a, entry_b)
                if ok:
                    print(f"[dup] {os.path.basename(item_a['filepath'])} == "
                          f"{os.path.basename(item_b['filepath'])} ({reason})")
                    group.append(item_b)
                    used.add(j)

            if len(group) > 1:
                groups.append(group)

        DUPLICATE_GROUPS = groups
        _save_groups(groups)

        scan_progress[task_id]['status'] = 'complete'
        scan_progress[task_id]['progress'] = 100
        scan_progress[task_id]['message'] = f'Found {len(groups)} duplicate groups'
        print(f"[DEBUG] Scan complete, found {len(groups)} groups.")
    except Exception as e:
        import traceback
        traceback.print_exc()
        scan_progress[task_id]['status'] = 'error'
        scan_progress[task_id]['message'] = str(e)
        print(f"[ERROR] Worker error: {e}")
    finally:
        with _lock:
            scan_in_progress = False


def _save_groups(groups):
    try:
        with open(DUPLICATE_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(groups, f,
                      default=lambda o: o if isinstance(o, dict) else str(o),
                      indent=2)
    except Exception as e:
        print(f"[dup] Failed to save groups: {e}")


# ===================================================================
#                     Геттеры
# ===================================================================
def get_duplicate_groups():
    global DUPLICATE_GROUPS

    if not DUPLICATE_GROUPS:
        try:
            with open(DUPLICATE_CACHE_FILE, 'r', encoding='utf-8') as f:
                DUPLICATE_GROUPS = json.load(f) or []
        except (FileNotFoundError, json.JSONDecodeError):
            DUPLICATE_GROUPS = []

    if not DUPLICATE_GROUPS:
        return []

    all_ids = set()
    for group in DUPLICATE_GROUPS:
        for v in group:
            vid = v.get('id') if isinstance(v, dict) else None
            if vid is not None:
                all_ids.add(int(vid))

    if not all_ids:
        return []

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        placeholders = ','.join('?' * len(all_ids))
        cursor.execute(
            f'SELECT id FROM videos WHERE id IN ({placeholders})',
            tuple(all_ids)
        )
        existing_ids = {row['id'] for row in cursor.fetchall()}
    finally:
        conn.close()

    cleaned = []
    changed = False
    for group in DUPLICATE_GROUPS:
        new_group = [v for v in group if int(v.get('id', -1)) in existing_ids]
        if len(new_group) >= 2:
            cleaned.append(new_group)
            if len(new_group) != len(group):
                changed = True
        else:
            changed = True

    if changed:
        DUPLICATE_GROUPS = cleaned
        _save_groups(cleaned)

    return DUPLICATE_GROUPS


def get_progress(task_id):
    return scan_progress.get(task_id, None)


# ===================================================================
#                     Перемещение дубликатов
# ===================================================================
def move_group(group_index, keep_best=True):
    groups = get_duplicate_groups()
    if group_index < 0 or group_index >= len(groups):
        return False, "Group not found"
    group = groups[group_index]
    if len(group) < 2:
        return False, "Not a duplicate group"

    if keep_best:
        best = max(group, key=lambda v: v.get('size', 0))
    else:
        best = group[0]

    first_path = group[0]['filepath']
    drive = os.path.splitdrive(first_path)[0] + "\\"
    root_duplicates = os.path.join(drive, "!Duplicates")
    if not os.path.exists(root_duplicates):
        os.makedirs(root_duplicates)

    group_hash = hashlib.md5(str(group).encode()).hexdigest()[:8]
    dup_folder = os.path.join(root_duplicates, f"duplicates_{group_hash}")
    if not os.path.exists(dup_folder):
        os.makedirs(dup_folder)

    moved = []
    for video in group:
        if video['id'] == best['id']:
            continue
        src = video['filepath']
        if not os.path.exists(src):
            continue
        dest = os.path.join(dup_folder, os.path.basename(src))
        base, ext = os.path.splitext(dest)
        counter = 1
        while os.path.exists(dest):
            dest = f"{base}_{counter}{ext}"
            counter += 1
        try:
            shutil.move(src, dest)
            moved.append((src, dest))
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM videos WHERE id = ?", (video['id'],))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Move error: {e}")
            return False, str(e)

    new_groups = [g for i, g in enumerate(groups) if i != group_index]
    global DUPLICATE_GROUPS
    DUPLICATE_GROUPS = new_groups
    _save_groups(new_groups)

    return True, f"Moved {len(moved)} files, kept best: {best['filename']}"


def move_all_groups(keep_best=True):
    groups = get_duplicate_groups()
    if not groups:
        return False, "No duplicate groups found"
    total_moved = 0
    errors = []
    for idx in range(len(groups) - 1, -1, -1):
        success, msg = move_group(idx, keep_best)
        if success:
            total_moved += 1
        else:
            errors.append(f"Group {idx+1}: {msg}")
    if errors:
        return False, f"Moved {total_moved} groups, errors: {'; '.join(errors)}"
    else:
        return True, f"All {total_moved} groups moved successfully."