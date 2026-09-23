r"""
Поиск дубликатов видео и картинок.

Для каждого видео:
  • 5 кадров на 10/30/50/70/90% длительности.
  • Для каждого кадра — phash + 3 зеркальные/перевёрнутые версии (4 хеша).
  • Chromaprint-фингерпринт аудиодорожки через fpcalc.

Пары объединяются через Union-Find:
  • video: любая из 16 комбинаций хешей двух кадров имеет hamming ≤ THRESHOLD;
  • audio: непрерывный run ≥ AUDIO_MIN_RUN_SEC (интро пропускается).

Для каждой группы сохраняются:
  • matches.video — список пар {a_id, b_id, pairs: [{pos_a, pos_b, hamming}]}
  • matches.audio — список пар {a_id, b_id, run_sec, start_a_sec, start_b_sec}

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
THRESHOLD = 0                  # pHash: точное совпадение (0 = identical)
AUDIO_BITS_TOLERANT = 10
AUDIO_MIN_RUN_SEC   = 10.0
AUDIO_SUBPRINT_PER_SEC = 1.0 / 0.1234
FPCALC_MAX_LENGTH = 600        # макс. секунд, которые анализирует fpcalc

# ---------- Пропуск интро ----------
# Первые N секунд аудиодорожки НЕ участвуют в сравнении.
# Защищает от ложных срабатываний на одинаковых интро/джинглах.
AUDIO_SKIP_INTRO_SEC = 30.0

# Позиции ключевых кадров (доли длительности)
KEYFRAME_POSITIONS = [0.10, 0.30, 0.50, 0.70, 0.90]


FPCALC_PATH = None
for cand in FPCALC_CANDIDATES:
    if cand and os.path.exists(cand):
        FPCALC_PATH = cand
        break
print(f"[dup] fpcalc path = {FPCALC_PATH}")
print(f"[dup] audio: skip first {AUDIO_SKIP_INTRO_SEC:.0f}s, "
      f"min run {AUDIO_MIN_RUN_SEC:.0f}s")


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
                           if isinstance(v, dict) and 'frames' in v}
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
    if not entry or 'frames' not in entry or 'audio_fp' not in entry:
        return False
    try:
        st = os.stat(filepath)
    except OSError:
        return False
    return entry.get('mtime') == st.st_mtime and entry.get('size') == st.st_size


# ===================================================================
#                     Кадры
# ===================================================================
def _extract_frames(video_path, positions):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[dup] cv2 cannot open: {video_path}")
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
            pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            out.append({'position': float(pos), 'hashes': _hash_image(pil_img)})
        except Exception as e:
            print(f"[dup] frame hash error {video_path} at {pos}: {e}")
    if not out:
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            try:
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total // 2))
                ret, frame = cap.read()
                if ret:
                    pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    out = [{'position': 0.5, 'hashes': _hash_image(pil_img)}]
            finally:
                cap.release()
    return out


def _process_image_frames(image_path):
    try:
        img = Image.open(image_path).convert('RGB')
        img.thumbnail((256, 256), Image.Resampling.LANCZOS)
        return [{'position': 0.5, 'hashes': _hash_image(img)}]
    except Exception as e:
        print(f"[dup] image hash error {image_path}: {e}")
        return []


# ===================================================================
#                     Аудио
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


def _find_longest_run_np(flags):
    """flags — np.array(int) 0/1.  Возвращает (length, start)."""
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


def _find_audio_match(fp_a, fp_b):
    """
    Возвращает dict с деталями совпадения или None.

    Первые AUDIO_SKIP_INTRO_SEC секунд отбрасываются с обеих дорожек
    (защита от одинаковых интро).  В start_a_sec / start_b_sec
    добавка возвращается обратно — чтобы Jump попадал в реальную
    позицию исходного видео.
    """
    if not fp_a or not fp_b:
        return None
    a = np.asarray(fp_a, dtype=np.uint32)
    b = np.asarray(fp_b, dtype=np.uint32)

    # ---- пропускаем интро ----
    skip_steps = int(AUDIO_SKIP_INTRO_SEC * AUDIO_SUBPRINT_PER_SEC)
    if skip_steps > 0:
        a = a[skip_steps:]
        b = b[skip_steps:]
    if len(a) < 40 or len(b) < 40:
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
    if np.count_nonzero(short_fp) < lb * 0.05:
        return None

    min_run_steps = max(20, int(AUDIO_MIN_RUN_SEC * AUDIO_SUBPRINT_PER_SEC))
    if min_run_steps > lb:
        min_run_steps = lb

    for shift in range(la - lb + 1):
        window = long_fp[shift:shift + lb]
        xor = np.bitwise_xor(window, short_fp)
        bits = _popcount_u32(xor)
        flags = (bits <= AUDIO_BITS_TOLERANT).astype(np.int32)
        run_len, run_start = _find_longest_run_np(flags)
        if run_len >= min_run_steps:
            if a_is_long:
                start_a_step = shift + run_start
                start_b_step = run_start
            else:
                start_a_step = run_start
                start_b_step = shift + run_start

            # добавляем обратно пропущенное интро
            start_a_sec = AUDIO_SKIP_INTRO_SEC + start_a_step / AUDIO_SUBPRINT_PER_SEC
            start_b_sec = AUDIO_SKIP_INTRO_SEC + start_b_step / AUDIO_SUBPRINT_PER_SEC

            return {
                'run_steps': int(run_len),
                'run_sec': float(run_len / AUDIO_SUBPRINT_PER_SEC),
                'shift': int(shift),
                'start_a_sec': float(start_a_sec),
                'start_b_sec': float(start_b_sec),
                'skipped_intro_sec': float(AUDIO_SKIP_INTRO_SEC),
            }
    return None


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
        frames = _process_image_frames(filepath)
        audio_fp = None
    else:
        frames = _process_video_frames(filepath)
        audio_fp, _ = _extract_audio_fingerprint(filepath)

    if not frames:
        return None

    result = {
        'filepath': filepath,
        'media_type': media_type,
        'mtime': st.st_mtime,
        'size': st.st_size,
        'frames': frames,
        'audio_fp': audio_fp,
    }
    with _HASH_CACHE_LOCK:
        _HASH_CACHE[filepath] = result
        global _HASH_CACHE_DIRTY
        _HASH_CACHE_DIRTY = True
    return result


# ===================================================================
#                     Сравнение пар
# ===================================================================
def _find_video_matches(frames_a, frames_b, threshold):
    """
    Возвращает список совпадений:
      [{'pos_a': 0.5, 'pos_b': 0.5, 'hamming': 0}, ...]
    """
    if not frames_a or not frames_b:
        return []
    matches = []
    objs_a = [(f['position'], [imagehash.hex_to_hash(h) for h in f['hashes']])
              for f in frames_a]
    objs_b = [(f['position'], [imagehash.hex_to_hash(h) for h in f['hashes']])
              for f in frames_b]

    for pos_a, hashes_a in objs_a:
        for pos_b, hashes_b in objs_b:
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


# ===================================================================
#                     Группировка
# ===================================================================
def _group_all(frames_by_path, audio_by_path, item_by_path, threshold):
    paths = list(frames_by_path.keys())
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

    video_matches_by_pair = {}
    audio_matches_by_pair = {}

    # ---------- video ----------
    for i in range(n):
        for j in range(i + 1, n):
            matches = _find_video_matches(
                frames_by_path[paths[i]],
                frames_by_path[paths[j]],
                threshold,
            )
            if matches:
                union(i, j)
                video_matches_by_pair[(i, j)] = matches

    # ---------- audio ----------
    audio_items = [(i, audio_by_path[p]) for i, p in enumerate(paths)
                   if audio_by_path.get(p)]
    for a_pos in range(len(audio_items)):
        i, fp_i = audio_items[a_pos]
        for b_pos in range(a_pos + 1, len(audio_items)):
            j, fp_j = audio_items[b_pos]
            res = _find_audio_match(fp_i, fp_j)
            if res:
                union(i, j)
                audio_matches_by_pair[(i, j)] = res

    # ---------- сборка групп ----------
    by_root = defaultdict(list)
    for i, path in enumerate(paths):
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
            if not a or not b:
                continue
            if a['id'] in group_ids and b['id'] in group_ids:
                video_list.append({
                    'a_id': a['id'],
                    'b_id': b['id'],
                    'pairs': pairs,
                })

        audio_list = []
        for (i, j), res in audio_matches_by_pair.items():
            a = item_by_path.get(paths[i])
            b = item_by_path.get(paths[j])
            if not a or not b:
                continue
            if a['id'] in group_ids and b['id'] in group_ids:
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
            'matches': {
                'video': video_list,
                'audio': audio_list,
            },
        })

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
        frames_by_path = {}
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
                    frames_by_path[item['filepath']] = entry['frames']
                    audio_by_path[item['filepath']] = entry.get('audio_fp')
                    item_by_path[item['filepath']] = item

        _save_hash_cache()
        print(f"[dup] hashed {len(frames_by_path)}/{total}")

        scan_progress[task_id]['message'] = 'Comparing pairs (video + audio)...'
        groups = _group_all(frames_by_path, audio_by_path,
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
            elif isinstance(g, list):
                files = [v for v in g
                         if isinstance(v, dict) and v.get('id') is not None]
                if len(files) >= 2:
                    DUPLICATE_GROUPS.append({
                        'files': files,
                        'reason': 'video',
                        'matches': {'video': [], 'audio': []},
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
    ok, msg = move_selected(all_ids)
    return ok, msg