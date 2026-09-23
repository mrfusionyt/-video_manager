r"""
Поиск дубликатов видео и картинок.

Гибридная схема:
  1) VIDEO: 1 кадр на 50% длительности → phash + 3 зеркальные версии.
     Совпало любое из 16 сочетаний → video-дубликат.
  2) AUDIO: Chromaprint через fpcalc (_dop/fpcalc.exe).
     Совпал непрерывный run >= AUDIO_MIN_RUN_SEC → audio-дубликат.

Группа помечается reason'ом: 'video' | 'audio' | 'video+audio'.
Поиск идёт по всем видео в БД.
"""
import os
import json
import base64
import struct
import shutil
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
HASH_CACHE_FILE      = os.path.join(BASE_DIR, 'hash_cache.json')

FPCALC_CANDIDATES = [
    os.path.join(BASE_DIR, '_dop', 'fpcalc.exe'),
    os.path.join(BASE_DIR, '_dop', 'fpcalc'),
    shutil.which('fpcalc'),
]

# ---------- Пороги ----------
THRESHOLD = 0                  # pHash: точное совпадение
AUDIO_BITS_TOLERANT = 10       # сколько бит может различаться в подписи
AUDIO_MIN_RUN_SEC   = 10.0     # минимум непрерывного совпадения, секунд
AUDIO_SUBPRINT_PER_SEC = 1.0 / 0.1234
FPCALC_MAX_LENGTH = 600        # макс. секунд, которые анализирует fpcalc


FPCALC_PATH = None
for cand in FPCALC_CANDIDATES:
    if cand and os.path.exists(cand):
        FPCALC_PATH = cand
        break
print(f"[dup] fpcalc path = {FPCALC_PATH}")


# ===================================================================
#                     Глобальное состояние
# ===================================================================
DUPLICATE_GROUPS = []
scan_in_progress = False
scan_progress = {}
_lock = threading.Lock()

_HASH_CACHE = {}
_HASH_CACHE_LOCK = threading.Lock()
_HASH_CACHE_DIRTY = False


def _load_hash_cache():
    global _HASH_CACHE
    try:
        if os.path.exists(HASH_CACHE_FILE):
            with open(HASH_CACHE_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f) or {}
            _HASH_CACHE = {k: v for k, v in raw.items()
                           if isinstance(v, dict) and 'hashes' in v}
            print(f"[dup] hash-cache loaded: {len(_HASH_CACHE)} из {len(raw)}")
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
        print(f"[dup] hash-cache saved: {len(snapshot)} entries")
    except Exception as e:
        print(f"[hash-cache] save failed: {e}")


def _cache_fresh(entry, filepath):
    if not entry or 'hashes' not in entry or 'audio_fp' not in entry:
        return False
    try:
        st = os.stat(filepath)
    except OSError:
        return False
    return entry.get('mtime') == st.st_mtime and entry.get('size') == st.st_size


# ===================================================================
#                     Video: кадр + 4 pHash
# ===================================================================
def _extract_frame(video_path, ratio=0.5):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[dup] cv2 cannot open: {video_path}")
        return None
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            ret, frame = cap.read()
            return frame if ret else None
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * ratio))
        ret, frame = cap.read()
        if not ret:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
        return frame if ret else None
    finally:
        cap.release()


def _hash_image(pil_img):
    return [
        str(imagehash.phash(pil_img)),
        str(imagehash.phash(ImageOps.mirror(pil_img))),
        str(imagehash.phash(ImageOps.flip(pil_img))),
        str(imagehash.phash(ImageOps.flip(ImageOps.mirror(pil_img)))),
    ]


def _process_video(video_path):
    frame = _extract_frame(video_path)
    if frame is None:
        return None
    try:
        pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        return _hash_image(pil_img)
    except Exception as e:
        print(f"[dup] video hash error {video_path}: {e}")
        return None


def _process_image(image_path):
    try:
        img = Image.open(image_path).convert('RGB')
        img.thumbnail((256, 256), Image.Resampling.LANCZOS)
        return _hash_image(img)
    except Exception as e:
        print(f"[dup] image hash error {image_path}: {e}")
        return None


# ===================================================================
#                     Audio: fpcalc
# ===================================================================
def _extract_audio_fingerprint(filepath):
    if not FPCALC_PATH:
        return None, 0
    try:
        r = subprocess.run(
            [FPCALC_PATH, '-raw', '-json',
             '-length', str(FPCALC_MAX_LENGTH), filepath],
            capture_output=True, text=True, timeout=180,
            encoding='utf-8', errors='replace',
        )
        if r.returncode != 0 or not r.stdout:
            return None, 0
        data = json.loads(r.stdout)
        fp_data = data.get('fingerprint')
        if not fp_data:
            return None, 0
        if isinstance(fp_data, list):
            fp = [int(x) & 0xFFFFFFFF for x in fp_data]
        else:
            raw = base64.b64decode(fp_data)
            n = len(raw) // 4
            fp = list(struct.unpack('<%dI' % n, raw[:n * 4]))
        return fp, float(data.get('duration', 0) or 0)
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


def _has_run_of_length(flags, length):
    if length <= 0 or length > len(flags):
        return False
    cs = np.concatenate(([0], np.cumsum(flags, dtype=np.int32)))
    return bool(np.any(cs[length:] - cs[:-length] >= length))


def _audio_match(fp_a, fp_b):
    """True, если есть непрерывный совпадающий run >= AUDIO_MIN_RUN_SEC."""
    if not fp_a or not fp_b:
        return False, 0
    a = np.asarray(fp_a, dtype=np.uint32)
    b = np.asarray(fp_b, dtype=np.uint32)
    if len(a) < len(b):
        a, b = b, a
    la, lb = len(a), len(b)
    if lb < 40:
        return False, 0
    # Защита от тишины / константной дорожки
    if np.count_nonzero(b) < lb * 0.05:
        return False, 0

    min_run_steps = int(AUDIO_MIN_RUN_SEC * AUDIO_SUBPRINT_PER_SEC)
    min_run_steps = max(20, min(min_run_steps, lb))

    max_shift = la - lb
    for shift in range(max_shift + 1):
        window = a[shift:shift + lb]
        xor = np.bitwise_xor(window, b)
        bits = _popcount_u32(xor)
        flags = (bits <= AUDIO_BITS_TOLERANT).astype(np.int32)
        # Быстрая проверка через cumsum — есть ли run нужной длины
        if _has_run_of_length(flags, min_run_steps):
            return True, min_run_steps
    return False, 0


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
    if _cache_fresh(entry, filepath):
        return entry

    media_type = item.get('media_type', 'video')
    if media_type == 'image':
        hashes = _process_image(filepath)
        audio_fp = None
    else:
        hashes = _process_video(filepath)
        audio_fp, _ = _extract_audio_fingerprint(filepath)

    if not hashes:
        return None

    result = {
        'filepath': filepath,
        'media_type': media_type,
        'mtime': st.st_mtime,
        'size': st.st_size,
        'hashes': hashes,
        'audio_fp': audio_fp,
    }
    with _HASH_CACHE_LOCK:
        _HASH_CACHE[filepath] = result
        global _HASH_CACHE_DIRTY
        _HASH_CACHE_DIRTY = True
    return result


# ===================================================================
#                     Сравнение
# ===================================================================
def _hashes_match(hashes_a, hashes_b, threshold):
    if not hashes_a or not hashes_b:
        return False
    objs_a = [imagehash.hex_to_hash(h) for h in hashes_a]
    objs_b = [imagehash.hex_to_hash(h) for h in hashes_b]
    for h1 in objs_a:
        for h2 in objs_b:
            if h1 - h2 <= threshold:
                return True
    return False


def _group_all(hashes_by_path, audio_by_path, item_by_path, threshold):
    """
    Union-Find: объединяем пары, у которых совпало video ИЛИ audio.
    Возвращает список dict'ов: {'files': [...], 'reason': 'video|audio|video+audio'}
    """
    paths = list(hashes_by_path.keys())
    n = len(paths)
    idx = {p: i for i, p in enumerate(paths)}
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

    # ---- 1) Video matches (быстрый путь через hash index) ----
    hash_index = defaultdict(set)
    for i, path in enumerate(paths):
        for h in hashes_by_path[path]:
            hash_index[h].add(i)

    video_pairs = set()
    for i, path_i in enumerate(paths):
        cands = set()
        for h in hashes_by_path[path_i]:
            cands |= hash_index.get(h, set())
        cands.discard(i)
        for j in cands:
            if (j, i) in video_pairs or (i, j) in video_pairs:
                continue
            if _hashes_match(hashes_by_path[path_i],
                             hashes_by_path[paths[j]], threshold):
                union(i, j)
                video_pairs.add((i, j))

    # ---- 2) Audio matches ----
    audio_candidates = [(i, p) for i, p in enumerate(paths)
                        if audio_by_path.get(p)]
    audio_pairs = set()
    for a_pos in range(len(audio_candidates)):
        i, p_i = audio_candidates[a_pos]
        for b_pos in range(a_pos + 1, len(audio_candidates)):
            j, p_j = audio_candidates[b_pos]
            if find(i) == find(j) and (i, j) in video_pairs:
                continue  # уже дубликаты по видео
            ok, _ = _audio_match(audio_by_path[p_i], audio_by_path[p_j])
            if ok:
                union(i, j)
                audio_pairs.add((min(i, j), max(i, j)))

    # ---- 3) Сборка групп ----
    by_root = defaultdict(list)
    for i, path in enumerate(paths):
        by_root[find(i)].append(path)

    groups = []
    for root, group_paths in by_root.items():
        if len(group_paths) < 2:
            continue
        # reason: смотрим, что реально совпало
        has_video = False
        has_audio = False
        for ii in range(len(group_paths)):
            for jj in range(ii + 1, len(group_paths)):
                i = idx[group_paths[ii]]
                j = idx[group_paths[jj]]
                pair = (min(i, j), max(i, j))
                if pair in video_pairs or (j, i) in video_pairs:
                    has_video = True
                if pair in audio_pairs:
                    has_audio = True
        if has_video and has_audio:
            reason = 'video+audio'
        elif has_video:
            reason = 'video'
        else:
            reason = 'audio'

        files = [item_by_path[p] for p in group_paths if p in item_by_path]
        files = [f for f in files if f.get('id') is not None]
        if len(files) >= 2:
            groups.append({'files': files, 'reason': reason})

    return groups


# ===================================================================
#                     Асинхронный запуск
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
        _load_hash_cache()

        all_items = get_all_videos()
        print(f"[dup] total in DB: {len(all_items)}")

        if filters:
            allowed = set(filters)
            all_items = [v for v in all_items
                         if v.get('media_type', 'video') in allowed]
        print(f"[dup] after filter: {len(all_items)}")

        total = len(all_items)
        scan_progress[task_id]['total'] = total
        scan_progress[task_id]['message'] = f'Scanning {total} files...'

        if total == 0:
            DUPLICATE_GROUPS = []
            _save_groups([])
            scan_progress[task_id].update({
                'status': 'complete', 'progress': 100,
                'message': 'No files to scan.',
            })
            return

        cache_lock = threading.Lock()
        processed = 0
        max_workers = min(4, os.cpu_count() or 1)
        hashes_by_path = {}
        audio_by_path = {}
        item_by_path = {}

        def _job(item):
            return item, _process_media(item, cache_lock)

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_job, it) for it in all_items]
            for fut in as_completed(futures):
                item, entry = fut.result()
                processed += 1
                scan_progress[task_id]['progress'] = int(processed / total * 100)
                scan_progress[task_id]['processed'] = processed
                name = os.path.basename(item['filepath']) if item else '...'
                scan_progress[task_id]['message'] = \
                    f'Processing {processed}/{total} ({name})'
                if entry:
                    hashes_by_path[item['filepath']] = entry['hashes']
                    audio_by_path[item['filepath']] = entry.get('audio_fp')
                    item_by_path[item['filepath']] = item

        _save_hash_cache()
        print(f"[dup] hashed {len(hashes_by_path)}/{total}")

        scan_progress[task_id]['message'] = 'Comparing pairs (video + audio)...'
        groups = _group_all(hashes_by_path, audio_by_path,
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
            # новый формат
            if isinstance(g, dict) and 'files' in g:
                files = [v for v in g['files']
                         if isinstance(v, dict) and v.get('id') is not None]
                if len(files) >= 2:
                    DUPLICATE_GROUPS.append({
                        'files': files,
                        'reason': g.get('reason', 'video'),
                    })
            # старый формат — list of files
            elif isinstance(g, list):
                files = [v for v in g
                         if isinstance(v, dict) and v.get('id') is not None]
                if len(files) >= 2:
                    DUPLICATE_GROUPS.append({
                        'files': files,
                        'reason': 'video',
                    })
        print(f"[dup] loaded {len(DUPLICATE_GROUPS)} groups from cache")

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
    for g in DUPLICATE_GROUPS:
        new_files = [v for v in g['files']
                     if int(v.get('id', -1)) in existing_ids]
        if len(new_files) >= 2:
            if len(new_files) != len(g['files']):
                changed = True
            cleaned.append({'files': new_files, 'reason': g['reason']})
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
        cursor = conn.cursor()
        cursor.execute("DELETE FROM videos WHERE id = ?", (video_id,))
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
            print(f"[dup] moved: {src} -> {dest}")
        except Exception as e:
            errors.append(f"{os.path.basename(src)}: {e}")

    kept = []
    for g in DUPLICATE_GROUPS:
        new_files = [v for v in g['files']
                     if int(v.get('id', -1)) not in target]
        if len(new_files) >= 2:
            kept.append({'files': new_files, 'reason': g['reason']})
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
    ok, msg = move_selected(all_ids)
    return ok, msg