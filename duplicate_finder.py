import os
import threading
import hashlib
import json
import shutil
import cv2
from PIL import Image, ImageOps
import imagehash
from models import get_all_videos, get_db_connection
from concurrent.futures import ThreadPoolExecutor, as_completed

DUPLICATE_CACHE_FILE = os.path.join(os.path.dirname(__file__), 'duplicates_cache.json')
DUPLICATE_GROUPS = []
scan_in_progress = False
scan_progress = {}
_lock = threading.Lock()
THRESHOLD = 0

def compute_hash_from_frame(frame):
    pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    hash_orig = imagehash.phash(pil_img)
    img_h = ImageOps.mirror(pil_img)
    hash_h = imagehash.phash(img_h)
    img_v = ImageOps.flip(pil_img)
    hash_v = imagehash.phash(img_v)
    img_hv = ImageOps.flip(img_h)
    hash_hv = imagehash.phash(img_hv)
    return [str(hash_orig), str(hash_h), str(hash_v), str(hash_hv)]

def extract_frame_from_video(filepath, target_ratio=0.5):
    cap = cv2.VideoCapture(filepath)
    if not cap.isOpened():
        return None
    try:
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            ret, frame = cap.read()
            return frame if ret else None
        target_frame = int(total_frames * target_ratio)
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ret, frame = cap.read()
        if not ret:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
        return frame if ret else None
    finally:
        cap.release()

def get_preview(filepath):
    frame = extract_frame_from_video(filepath)
    if frame is None:
        return None
    pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    pil_img.thumbnail((200, 200))
    return pil_img

def find_duplicates_async(task_id, filters=None):
    global scan_in_progress, scan_progress
    with _lock:
        if scan_in_progress:
            print("[DEBUG] Scan already in progress, ignoring new request.")
            return
        scan_in_progress = True
        scan_progress[task_id] = {'status': 'scanning', 'progress': 0, 'total': 0, 'processed': 0, 'message': 'Initializing...'}
    thread = threading.Thread(target=_find_duplicates_worker, args=(task_id, filters), daemon=True)
    thread.start()
    print(f"[DEBUG] Started duplicate scan with task_id={task_id}")

def _find_duplicates_worker(task_id, filters):
    global DUPLICATE_GROUPS, scan_in_progress, scan_progress
    try:
        print(f"[DEBUG] Worker started for task_id={task_id}")
        videos = get_all_videos()
        if filters:
            allowed_types = set(filters)
            videos = [v for v in videos if v.get('media_type', 'video') in allowed_types]
        videos = [v for v in videos if v.get('media_type') == 'video']
        total = len(videos)
        scan_progress[task_id]['total'] = total
        scan_progress[task_id]['message'] = f'Scanning {total} videos...'
        print(f"[DEBUG] Total videos to scan: {total}")

        if total == 0:
            DUPLICATE_GROUPS = []
            with open(DUPLICATE_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump([], f)
            scan_progress[task_id]['status'] = 'complete'
            scan_progress[task_id]['progress'] = 100
            scan_progress[task_id]['message'] = 'No video files to scan.'
            print("[DEBUG] No videos, scan complete.")
            return

        hash_map = {}
        previews = {}
        processed = 0
        max_workers = min(4, os.cpu_count() or 1)

        def process_video(video):
            filepath = video['filepath']
            if not os.path.exists(filepath):
                return None, None, None
            frame = extract_frame_from_video(filepath)
            if frame is None:
                return None, None, None
            hashes = compute_hash_from_frame(frame)
            preview = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            preview.thumbnail((200, 200))
            return video, hashes, preview

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_video, video): video for video in videos}
            for future in as_completed(futures):
                video, hashes, preview = future.result()
                processed += 1
                progress = int((processed / total) * 100)
                scan_progress[task_id]['progress'] = progress
                scan_progress[task_id]['processed'] = processed
                if video and hashes:
                    filepath = video['filepath']
                    hash_map[filepath] = hashes
                    previews[filepath] = preview
                scan_progress[task_id]['message'] = f'Processing {processed}/{total} ({os.path.basename(video["filepath"]) if video else "..."})'
                if processed % 10 == 0:
                    print(f"[DEBUG] Progress: {progress}% ({processed}/{total})")

        items = list(hash_map.items())
        groups = {}
        used = set()
        for i, (path_i, hashes_i) in enumerate(items):
            if path_i in used:
                continue
            group = [path_i]
            used.add(path_i)
            hash_objs_i = [imagehash.hex_to_hash(h) for h in hashes_i]
            for j, (path_j, hashes_j) in enumerate(items):
                if j <= i or path_j in used:
                    continue
                hash_objs_j = [imagehash.hex_to_hash(h) for h in hashes_j]
                is_duplicate = False
                for h1 in hash_objs_i:
                    for h2 in hash_objs_j:
                        if h1 - h2 <= THRESHOLD:
                            is_duplicate = True
                            break
                    if is_duplicate:
                        break
                if is_duplicate:
                    group.append(path_j)
                    used.add(path_j)
            if len(group) > 1:
                groups[hashes_i[0]] = group

        duplicate_groups = []
        for hash_val, paths in groups.items():
            group_videos = []
            for p in paths:
                for v in videos:
                    if v['filepath'] == p:
                        group_videos.append(v)
                        break
            if group_videos:
                duplicate_groups.append(group_videos)

        DUPLICATE_GROUPS = duplicate_groups
        with open(DUPLICATE_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(duplicate_groups, f, default=lambda o: o if isinstance(o, dict) else str(o), indent=2)

        scan_progress[task_id]['status'] = 'complete'
        scan_progress[task_id]['progress'] = 100
        scan_progress[task_id]['message'] = f'Found {len(duplicate_groups)} duplicate groups'
        print(f"[DEBUG] Scan complete, found {len(duplicate_groups)} groups.")
    except Exception as e:
        scan_progress[task_id]['status'] = 'error'
        scan_progress[task_id]['message'] = str(e)
        print(f"[ERROR] Worker error: {e}")
    finally:
        with _lock:
            scan_in_progress = False
            print("[DEBUG] scan_in_progress reset to False.")

def get_duplicate_groups():
    global DUPLICATE_GROUPS
    if DUPLICATE_GROUPS:
        return DUPLICATE_GROUPS
    try:
        with open(DUPLICATE_CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return []

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
    with open(DUPLICATE_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(new_groups, f, default=lambda o: o if isinstance(o, dict) else str(o), indent=2)
    global DUPLICATE_GROUPS
    DUPLICATE_GROUPS = new_groups

    return True, f"Moved {len(moved)} files, kept best: {best['filename']}"

def move_all_groups(keep_best=True):
    groups = get_duplicate_groups()
    if not groups:
        return False, "No duplicate groups found"
    total_moved = 0
    errors = []
    for idx in range(len(groups)-1, -1, -1):
        success, msg = move_group(idx, keep_best)
        if success:
            total_moved += 1
        else:
            errors.append(f"Group {idx+1}: {msg}")
    if errors:
        return False, f"Moved {total_moved} groups, errors: {'; '.join(errors)}"
    else:
        return True, f"All {total_moved} groups moved successfully."

def get_progress(task_id):
    return scan_progress.get(task_id, None)