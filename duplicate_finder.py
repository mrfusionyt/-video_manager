r"""
Поиск дубликатов видео и картинок — 1-в-1 логика из Finder.py.

Для каждого файла:
  • Извлекается ОДИН кадр на 50% длительности (для видео).
  • Считается phash + 3 зеркальные/перевёрнутые версии (4 хеша).
  • Сравниваются ВСЕ 16 комбинаций хешей двух файлов.
  • Дубликат — если хоть одна пара имеет hamming distance <= THRESHOLD.

Поиск идёт по всем видео в БД (все библиотеки).

Кэш хэшей (hash_cache.json) — по (mtime, size), на диске.
"""
import os
import json
import threading
import shutil
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import cv2
from PIL import Image, ImageOps
import imagehash

from models import get_all_videos, get_db_connection, get_video_by_id


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DUPLICATE_CACHE_FILE = os.path.join(BASE_DIR, 'duplicates_cache.json')
HASH_CACHE_FILE      = os.path.join(BASE_DIR, 'hash_cache.json')

# ---------- Порог pHash ----------
# 0 = точное совпадение (как в Finder.py).
# 1-2 — если хотите ловить лёгкую перекодировку.
THRESHOLD = 0


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
            # Оставляем только записи с новой схемой ('hashes')
            _HASH_CACHE = {k: v for k, v in raw.items()
                           if isinstance(v, dict) and 'hashes' in v}
            print(f"[dup] hash-cache loaded: {len(_HASH_CACHE)} "
                  f"(из {len(raw)} записей)")
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


def _cache_fresh(entry, filepath):
    if not entry or 'hashes' not in entry:
        return False
    try:
        st = os.stat(filepath)
    except OSError:
        return False
    return entry.get('mtime') == st.st_mtime and entry.get('size') == st.st_size


# ===================================================================
#                     Извлечение кадра и хеширование
# ===================================================================
def _extract_frame(video_path, ratio=0.5):
    """Один кадр на 50% длительности — точно как в Finder.py."""
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
    """4 хеша: orig / mirror / flip / flip+mirror — как в Finder.py."""
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
    else:
        hashes = _process_video(filepath)

    if not hashes:
        return None

    result = {
        'filepath': filepath,
        'media_type': media_type,
        'mtime': st.st_mtime,
        'size': st.st_size,
        'hashes': hashes,
    }
    with _HASH_CACHE_LOCK:
        _HASH_CACHE[filepath] = result
        global _HASH_CACHE_DIRTY
        _HASH_CACHE_DIRTY = True
    return result


# ===================================================================
#                     Сравнение — как в Finder.py
# ===================================================================
def _hashes_match(hashes_a, hashes_b, threshold):
    """ВСЕ 16 комбинаций (4×4)."""
    if not hashes_a or not hashes_b:
        return False
    objs_a = [imagehash.hex_to_hash(h) for h in hashes_a]
    objs_b = [imagehash.hex_to_hash(h) for h in hashes_b]
    for h1 in objs_a:
        for h2 in objs_b:
            if h1 - h2 <= threshold:
                return True
    return False


def _group_by_hashes(hashes_by_path, threshold):
    items = list(hashes_by_path.items())
    groups = []
    used = set()

    if threshold == 0:
        hash_index = defaultdict(set)
        for path, hashes in items:
            for h in hashes:
                hash_index[h].add(path)

        for path_i, hashes_i in items:
            if path_i in used:
                continue
            group = [path_i]
            used.add(path_i)
            candidates = set()
            for h in hashes_i:
                candidates |= hash_index.get(h, set())
            candidates.discard(path_i)
            for path_j in candidates:
                if path_j in used:
                    continue
                if _hashes_match(hashes_i, hashes_by_path[path_j], threshold):
                    group.append(path_j)
                    used.add(path_j)
            if len(group) > 1:
                groups.append(group)
        return groups

    for i, (path_i, hashes_i) in enumerate(items):
        if path_i in used:
            continue
        group = [path_i]
        used.add(path_i)
        for j in range(i + 1, len(items)):
            path_j, hashes_j = items[j]
            if path_j in used:
                continue
            if _hashes_match(hashes_i, hashes_j, threshold):
                group.append(path_j)
                used.add(path_j)
        if len(group) > 1:
            groups.append(group)
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
                    item_by_path[item['filepath']] = item

        _save_hash_cache()
        print(f"[dup] hashed {len(hashes_by_path)}/{total}")

        scan_progress[task_id]['message'] = 'Grouping duplicates...'
        raw_groups = _group_by_hashes(hashes_by_path, THRESHOLD)
        print(f"[dup] raw groups: {len(raw_groups)}")

        # Превращаем пути в video-dict.  Гарантируем, что у каждого
        # элемента есть 'id' — иначе шаблон / move не сработает.
        groups = []
        for path_list in raw_groups:
            group = []
            for p in path_list:
                item = item_by_path.get(p)
                if not item:
                    continue
                if 'id' not in item or item['id'] is None:
                    print(f"[dup] WARN: item without id: {p}")
                    continue
                group.append(item)
            if len(group) > 1:
                groups.append(group)

        DUPLICATE_GROUPS = groups
        _save_groups(groups)

        scan_progress[task_id].update({
            'status': 'complete', 'progress': 100,
            'message': f'Found {len(groups)} duplicate groups'
        })
        print(f"[dup] complete: {len(groups)} groups")
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
        print(f"[dup] saved {len(groups)} groups to cache")
    except Exception as e:
        print(f"[dup] save groups failed: {e}")


# ===================================================================
#                     Геттеры
# ===================================================================
def get_duplicate_groups():
    """Возвращает список групп video-dict'ов."""
    global DUPLICATE_GROUPS

    if not DUPLICATE_GROUPS:
        try:
            with open(DUPLICATE_CACHE_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f) or []
        except (FileNotFoundError, json.JSONDecodeError):
            raw = []
        # Валидируем: группа — список dict'ов с 'id'
        DUPLICATE_GROUPS = []
        for group in raw:
            if not isinstance(group, list):
                continue
            valid = []
            for v in group:
                if isinstance(v, dict) and v.get('id') is not None:
                    valid.append(v)
            if len(valid) >= 2:
                DUPLICATE_GROUPS.append(valid)
        print(f"[dup] loaded {len(DUPLICATE_GROUPS)} groups from cache")

    if not DUPLICATE_GROUPS:
        return []

    # Синхронизируем с БД
    all_ids = set()
    for group in DUPLICATE_GROUPS:
        for v in group:
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
    for group in DUPLICATE_GROUPS:
        new_group = [v for v in group
                     if int(v.get('id', -1)) in existing_ids]
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
    """<диск>:\\!Duplicates\\<folder>_duplicates\\ — как в Finder.py."""
    drive = os.path.splitdrive(src_path)[0] + "\\"
    folder_name = os.path.basename(os.path.dirname(src_path)) or 'root'
    dest_dir = os.path.join(drive, "!Duplicates", f"{folder_name}_duplicates")
    os.makedirs(dest_dir, exist_ok=True)
    return dest_dir


def move_selected(video_ids):
    """Перемещает выбранные видео в !Duplicates. Удаляет из БД."""
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
            print(f"[dup] move error: {e}")

    kept = []
    for group in DUPLICATE_GROUPS:
        new_group = [v for v in group if int(v.get('id', -1)) not in target]
        if len(new_group) >= 2:
            kept.append(new_group)
    DUPLICATE_GROUPS = kept
    _save_groups(kept)

    if errors:
        return False, f"Moved {moved}, errors: {'; '.join(errors[:3])}"
    return True, f"Moved {moved} file(s)"


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

    to_move = [v['id'] for v in group if v['id'] != best['id']]
    ok, msg = move_selected(to_move)
    if ok:
        return True, f"{msg}. Kept: {best['filename']}"
    return False, msg


def move_all_groups(keep_best=True):
    groups = get_duplicate_groups()
    if not groups:
        return False, "No duplicate groups found"
    all_ids = []
    for group in groups:
        if not group:
            continue
        if keep_best:
            best = max(group, key=lambda v: v.get('size', 0))
        else:
            best = group[0]
        for v in group:
            if v['id'] != best['id']:
                all_ids.append(v['id'])
    ok, msg = move_selected(all_ids)
    return ok, msg