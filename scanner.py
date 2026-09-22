r"""
Сканирование библиотек.

При добавлении / обнаружении файла:
  1. is_playable()       — если битый, едет в !Broken.
  2. needs_conversion()  — если контейнер/кодек плохой, конвертируем на месте.
  3. add_video()         — только после проверки и конвертации.

Оригинал при конвертации уезжает в !Duplicates.
Битые файлы уезжают в !Broken.
"""
import os
import cv2
import subprocess
import json
import threading

from models import (
    get_libraries, add_video, get_db_connection, delete_video, get_video_by_id,
)
from converter import (
    needs_conversion, convert_video_in_place, update_db_after_conversion,
    is_playable, move_to_broken,
)


VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.webp'}

_TMP_PREFIX = '.__conv_'

SCAN_TASKS = {}
SCAN_LOCK = threading.Lock()
SCAN_IN_PROGRESS = False
_RUN_LOCK = threading.Lock()


def _subprocess_kwargs():
    return dict(
        capture_output=True,
        encoding='utf-8',
        errors='replace',
    )


def is_scan_in_progress():
    with SCAN_LOCK:
        return SCAN_IN_PROGRESS


def get_scan_progress(task_id):
    with SCAN_LOCK:
        t = SCAN_TASKS.get(task_id)
        return dict(t) if t else None


def clear_scan_tasks():
    with SCAN_LOCK:
        SCAN_TASKS.clear()


def get_media_info(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    if ext in VIDEO_EXTENSIONS:
        media_type = 'video'
        base_dir = os.path.dirname(os.path.abspath(__file__))
        ffprobe_path = os.path.join(base_dir, '_dop', 'ffprobe.exe')
        if os.path.exists(ffprobe_path):
            try:
                cmd = [ffprobe_path, '-v', 'quiet', '-print_format', 'json',
                       '-show_streams', '-show_format', filepath]
                result = subprocess.run(cmd, check=False, **_subprocess_kwargs())
                if result.returncode == 0 and result.stdout:
                    data = json.loads(result.stdout)
                    stream = data['streams'][0] if data.get('streams') else {}
                    width = int(stream.get('width', 0))
                    height = int(stream.get('height', 0))
                    fps_str = stream.get('r_frame_rate', '0/1')
                    if '/' in fps_str:
                        num, den = fps_str.split('/')
                        fps = float(num) / float(den) if float(den) != 0 else 0.0
                    else:
                        fps = float(fps_str)
                    codec = stream.get('codec_name', '')
                    bitrate = int(data['format'].get('bit_rate', 0) or 0)
                    duration = int(float(data['format'].get('duration', 0) or 0))
                    fmt_name = (data['format'].get('format_name', '') or '').lower()
                    orientation = 'vertical' if height > width else 'horizontal'
                    return {
                        'duration': duration,
                        'orientation': orientation,
                        'media_type': media_type,
                        'width': width, 'height': height,
                        'fps': fps, 'codec': codec, 'bitrate': bitrate,
                        'format_name': fmt_name,
                    }
            except Exception as e:
                print(f"FFprobe error for {filepath}: {e}")

        cap = cv2.VideoCapture(filepath)
        if not cap.isOpened():
            return None
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            duration = int(frame_count / fps) if fps > 0 else 0
            orientation = 'vertical' if height > width else 'horizontal'
            return {
                'duration': duration,
                'orientation': orientation,
                'media_type': media_type,
                'width': width, 'height': height,
                'fps': fps, 'codec': '', 'bitrate': 0,
                'format_name': '',
            }
        finally:
            cap.release()
    elif ext in IMAGE_EXTENSIONS:
        return {
            'duration': 0, 'orientation': 'horizontal', 'media_type': 'image',
            'width': 0, 'height': 0, 'fps': 0, 'codec': '', 'bitrate': 0,
            'format_name': '',
        }
    else:
        return {
            'duration': 0, 'orientation': 'horizontal', 'media_type': 'unknown',
            'width': 0, 'height': 0, 'fps': 0, 'codec': '', 'bitrate': 0,
            'format_name': '',
        }


def update_video_metadata(video_id, info):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE videos SET
                duration = ?, orientation = ?, media_type = ?,
                width = ?, height = ?, codec = ?, bitrate = ?, fps = ?
            WHERE id = ?
        ''', (
            info['duration'], info['orientation'], info['media_type'],
            info['width'], info['height'], info['codec'], info['bitrate'],
            info['fps'], video_id
        ))
        conn.commit()
    finally:
        conn.close()


def _update_video_folder(video_id, folder):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE videos SET folder = ? WHERE id = ?",
                       (folder, video_id))
        conn.commit()
    finally:
        conn.close()


def _is_temp_file(filename):
    lower = filename.lower()
    if lower.startswith(_TMP_PREFIX):
        return True
    if lower.endswith('.tmp.mp4'):
        return True
    return False


def _scan_impl(progress_cb=None):
    acquired = _RUN_LOCK.acquire(blocking=False)
    if not acquired:
        print("[scan] another scan is already running, skipping.")
        if progress_cb:
            progress_cb(0, 0, 'Another scan is running')
        return
    try:
        _scan_impl_locked(progress_cb)
    finally:
        _RUN_LOCK.release()


def _scan_impl_locked(progress_cb=None):
    libraries = get_libraries()
    if not libraries:
        print("No libraries to scan.")
        if progress_cb:
            progress_cb(0, 0, 'No libraries')
        return

    all_files = []
    for lib in libraries:
        lib_id = lib['id']
        lib_path = lib['path']
        lib_mode = lib.get('mode', 1)
        if not os.path.isdir(lib_path):
            print(f"[scan] skip non-existent library: {lib_path}")
            continue
        for root, dirs, files in os.walk(lib_path):
            # Исключаем служебные папки
            dirs[:] = [d for d in dirs
                       if d not in ('!Duplicates', '!Broken', '!Good')]
            for file in files:
                if _is_temp_file(file):
                    continue
                ext = os.path.splitext(file)[1].lower()
                if ext in VIDEO_EXTENSIONS or ext in IMAGE_EXTENSIONS:
                    full_path = os.path.normpath(os.path.join(root, file))
                    all_files.append((lib_id, lib_mode, full_path, file))

    total = len(all_files)
    print(f"[scan] Enumerated {total} files across {len(libraries)} libraries")
    if progress_cb:
        progress_cb(0, total, 'Enumerated. Starting...')

    existing_map = {}
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, filepath, width, height, folder FROM videos")
        for row in cursor.fetchall():
            existing_map[row['filepath']] = {
                'id': row['id'],
                'width': row['width'] or 0,
                'height': row['height'] or 0,
                'folder': row['folder'] or '',
            }
    finally:
        conn.close()

    found_paths = set()
    attempted = set()

    for i, (lib_id, lib_mode, full_path, filename) in enumerate(all_files, start=1):
        found_paths.add(full_path)
        folder = os.path.dirname(full_path)
        existing_row = existing_map.get(full_path)

        try:
            is_video = os.path.splitext(full_path)[1].lower() in VIDEO_EXTENSIONS

            # --- Шаг 1. Проверка целостности видео ---
            if is_video and full_path not in attempted:
                attempted.add(full_path)
                ok, reason = is_playable(full_path)
                if not ok:
                    print(f"[scan] broken video: {filename} ({reason})")

                    if existing_row:
                        try:
                            delete_video(existing_row['id'])
                            existing_map.pop(full_path, None)
                        except Exception as e:
                            print(f"[scan] delete error for "
                                  f"{existing_row['id']}: {e}")

                    dest = move_to_broken(full_path, keep_name=filename)
                    if dest:
                        print(f"[scan] moved to !Broken: {dest}")
                        found_paths.discard(full_path)
                    else:
                        print(f"[scan] WARNING: could not move "
                              f"{filename} to !Broken")

                    if progress_cb and (i % 5 == 0 or i == total):
                        progress_cb(i, total, filename)
                    continue

            if existing_row:
                # --- Существующая запись ---
                video_id = existing_row['id']
                if existing_row['folder'] != folder:
                    _update_video_folder(video_id, folder)

                vid_obj = get_video_by_id(video_id)
                need_meta = (existing_row['width'] == 0
                             or existing_row['height'] == 0)

                if vid_obj:
                    need, reason = needs_conversion(
                        full_path, vid_obj.get('codec')
                    )
                    if need:
                        print(f"[scan] bad {reason}: {filename}")
                        ok, new_video, msg = convert_video_in_place(vid_obj)
                        if ok:
                            update_db_after_conversion(video_id, new_video)
                            found_paths.discard(full_path)
                            found_paths.add(new_video['filepath'])
                            existing_map.pop(full_path, None)
                            existing_map.pop(new_video['filepath'], None)
                        else:
                            print(f"[scan] convert FAILED: {msg}")
                    elif need_meta:
                        info = get_media_info(full_path)
                        if info:
                            update_video_metadata(video_id, info)
                elif need_meta:
                    info = get_media_info(full_path)
                    if info:
                        update_video_metadata(video_id, info)

            else:
                # --- Новый файл ---
                size = (os.path.getsize(full_path)
                        if os.path.exists(full_path) else 0)
                info = get_media_info(full_path)
                if info:
                    original_path = full_path

                    # --- Шаг 2. Конвертация ДО добавления в БД ---
                    need, reason = needs_conversion(
                        full_path, info.get('codec'),
                        info.get('format_name'),
                    )
                    if need:
                        print(f"[scan] bad {reason}: {filename}")
                        temp = {
                            'id': None,
                            'filename': filename,
                            'filepath': full_path,
                            'codec': info.get('codec', ''),
                        }
                        ok, new_video, msg = convert_video_in_place(temp)
                        if ok:
                            full_path = new_video['filepath']
                            filename = new_video['filename']
                            folder = new_video['folder']
                            size = new_video['size']
                            for k in ('duration', 'width', 'height', 'codec',
                                      'bitrate', 'fps', 'orientation'):
                                info[k] = new_video[k]
                            found_paths.discard(original_path)
                            found_paths.add(full_path)
                        else:
                            print(f"[scan] convert FAILED (will retry later): "
                                  f"{msg}")

                    # --- Шаг 3. Добавляем в БД ---
                    add_video(
                        lib_id, filename, full_path,
                        duration=info['duration'],
                        size=size,
                        orientation=info['orientation'],
                        media_type=info['media_type'],
                        width=info['width'], height=info['height'],
                        codec=info['codec'], bitrate=info['bitrate'],
                        fps=info['fps'],
                        mode=lib_mode,
                        folder=folder,
                    )
                else:
                    print(f"[scan] Failed to read: {filename}")
        except Exception as e:
            print(f"[scan] Error processing {filename}: {e}")

        if progress_cb and (i % 5 == 0 or i == total):
            progress_cb(i, total, filename)

    for filepath, row in existing_map.items():
        if os.path.normpath(filepath) not in found_paths:
            print(f"[scan] Removing missing: {filepath}")
            try:
                delete_video(row['id'])
            except Exception as e:
                print(f"[scan] delete error for {row['id']}: {e}")

    print("[scan] Scan complete.")


def scan_libraries(progress_cb=None):
    _scan_impl(progress_cb=progress_cb)


def scan_libraries_async(task_id):
    global SCAN_IN_PROGRESS
    with SCAN_LOCK:
        if SCAN_IN_PROGRESS:
            SCAN_TASKS[task_id] = {
                'status': 'error', 'progress': 0, 'total': 0,
                'processed': 0, 'message': 'Scan already in progress',
            }
            return False
        SCAN_IN_PROGRESS = True
        SCAN_TASKS[task_id] = {
            'status': 'scanning', 'progress': 0, 'total': 0,
            'processed': 0, 'message': 'Initializing...',
        }

    def _cb(processed, total, message):
        with SCAN_LOCK:
            t = SCAN_TASKS.get(task_id)
            if not t:
                return
            t['processed'] = processed
            t['total'] = total
            t['progress'] = int((processed / total) * 100) if total > 0 else 0
            t['message'] = message

    def _worker():
        global SCAN_IN_PROGRESS
        try:
            _scan_impl(progress_cb=_cb)
            with SCAN_LOCK:
                t = SCAN_TASKS.get(task_id)
                if t:
                    t['status'] = 'complete'
                    t['progress'] = 100
                    t['message'] = f"Done. {t.get('processed', 0)} files processed."
        except Exception as e:
            print(f"[scan] worker error: {e}")
            with SCAN_LOCK:
                t = SCAN_TASKS.get(task_id)
                if t:
                    t['status'] = 'error'
                    t['message'] = f"Error: {str(e)[:300]}"
        finally:
            with SCAN_LOCK:
                SCAN_IN_PROGRESS = False

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return True