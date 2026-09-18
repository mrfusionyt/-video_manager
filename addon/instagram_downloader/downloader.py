import os
import re
import json
import time
import random
import threading
import yt_dlp
from instagrapi import Client
from instagrapi.exceptions import LoginRequired
from . import db
from . import run_post_download_hook
from .browser_cookies import reopen_firefox

TASKS = {}
_lock = threading.Lock()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES_DIR = os.path.join(BASE_DIR, 'cookies')
os.makedirs(COOKIES_DIR, exist_ok=True)

CURRENT_USER_AGENT = (
    "Instagram 423.0.0.47.66 Android (33/13; 480dpi; 1080x2400; "
    "xiaomi; M2007J20CG; surya; qcom; en_US; 641123490)"
)

CURRENT_DEVICE = {
    "app_version": "423.0.0.47.66",
    "android_version": 33,
    "android_release": "13",
    "dpi": "480dpi",
    "resolution": "1080x2400",
    "manufacturer": "xiaomi",
    "device": "surya",
    "model": "M2007J20CG",
    "cpu": "qcom",
    "language": "en_US",
    "version_code": "641123490",
}


# ===================================================================
#                     ПОИСК FFMPEG
# ===================================================================
def _find_ffmpeg_dir():
    candidates = [
        # _dop в корне проекта (addon/instagram_downloader → ../.. → корень)
        os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '..', '..', '_dop'
        )),
        # _dop рядом с модулем (fallback)
        os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '_dop'
        )),
        # PyInstaller
        os.path.normpath(os.path.join(
            getattr(__import__('sys'), '_MEIPASS', ''), '_dop'
        )),
    ]
    for path in candidates:
        if path and os.path.isfile(os.path.join(path, 'ffmpeg.exe')):
            return path
    return None


FFMPEG_DIR = _find_ffmpeg_dir()
if FFMPEG_DIR:
    print(f"[downloader] ffmpeg найден: {FFMPEG_DIR}")
else:
    print("[downloader] ВНИМАНИЕ: ffmpeg не найден — видео и аудио могут скачиваться раздельно")


# ===================================================================
#                     ОТМЕНА И СТАТУСЫ
# ===================================================================
def request_cancel(task_id):
    with _lock:
        if task_id in TASKS:
            ev = TASKS[task_id].get('cancel_event')
            if ev is not None and not ev.is_set():
                ev.set()
                TASKS[task_id]['message'] = 'Cancelling...'
                return 'ok'
            return 'ok'
    task = db.get_task(task_id)
    if not task:
        return 'not_found'
    if task.get('status') in ('complete', 'error', 'cancelled'):
        return 'already_done'
    return 'lost'


def _is_cancelled(task_id):
    with _lock:
        if task_id in TASKS:
            ev = TASKS[task_id].get('cancel_event')
            return ev.is_set() if ev else False
    return False


def _interruptible_sleep(task_id, seconds):
    with _lock:
        ev = TASKS.get(task_id, {}).get('cancel_event')
    if ev is None:
        time.sleep(seconds)
        return False
    return ev.wait(timeout=seconds)


def get_progress(task_id):
    with _lock:
        if task_id in TASKS:
            p = dict(TASKS[task_id])
            p.pop('cancel_event', None)
            return p
    task = db.get_task(task_id)
    if not task:
        return None
    task['files'] = [
        f"{f['file_type']} #{f['idx']} — {f['filename'] or 'not downloaded'}"
        for f in db.get_task_files(task_id)
    ]
    return task


def get_task(task_id):
    with _lock:
        in_memory = task_id in TASKS and TASKS[task_id].get('status') in (
            'complete', 'cancelled', 'error'
        )
        if in_memory:
            t = dict(TASKS[task_id])
            t.pop('cancel_event', None)
            t['file_objects'] = db.get_task_files(task_id)
            return t
    task = db.get_task(task_id)
    if not task:
        return None
    task['file_objects'] = db.get_task_files(task_id)
    return task


def get_downloaded_files(task_id):
    task = get_task(task_id)
    if task and task.get('status') in ('complete', 'cancelled', 'error'):
        return task.get('file_objects', [])
    return None


def get_saved_sessions():
    return []


def get_session_path(username):
    return None


def _set_status(task_id, **kwargs):
    with _lock:
        if task_id in TASKS:
            TASKS[task_id].update(kwargs)
    db_fields = {}
    for key in ('status', 'progress', 'total', 'message'):
        if key in kwargs:
            db_fields[key] = kwargs[key]
    if db_fields:
        db.update_task(task_id, **db_fields)


# ===================================================================
#                     КЛИЕНТ INSTAGRAPI
# ===================================================================
def _create_client(cookies):
    try:
        cl = Client(public_transport="curl", public_transport_impersonate="chrome136")
    except Exception:
        cl = Client()
    cl.set_user_agent(CURRENT_USER_AGENT)
    cl.set_device(CURRENT_DEVICE)
    try:
        cl.login_by_sessionid(cookies['sessionid'])
        if hasattr(cl, 'set_cookies') and callable(cl.set_cookies):
            cl.set_cookies(cookies)
        if 'csrftoken' in cookies:
            cl.csrftoken = cookies['csrftoken']
    except Exception as e:
        print(f"[downloader] instagrapi login error: {e}")
        raise
    return cl


# ===================================================================
#                     YT-DLP ОПЦИИ
# ===================================================================
def _yt_dlp_opts(save_dir, cookie_file):
    opts = {
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'retries': 5,
        'fragment_retries': 5,
        'socket_timeout': 30,
        'nocheckcertificate': True,

        'format': (
            'best[ext=mp4][acodec!=none][vcodec!=none]'
            '/best[acodec!=none][vcodec!=none]'
            '/bestvideo[ext=mp4]+bestaudio[ext=m4a]'
            '/bestvideo+bestaudio'
            '/best'
        ),
        'merge_output_format': 'mp4',

        'outtmpl': os.path.join(save_dir, '%(id)s.%(ext)s'),
        'noplaylist': True,
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
            ),
        },
    }

    if cookie_file and os.path.exists(cookie_file):
        opts['cookiefile'] = cookie_file

    if FFMPEG_DIR:
        opts['ffmpeg_location'] = FFMPEG_DIR
    else:
        opts['format'] = 'best[ext=mp4]/best[acodec!=none][vcodec!=none]/best'
        print("[downloader] ffmpeg не найден, формат ограничен единым файлом")

    return opts


def _download_with_ytdlp(task_id, url, save_dir, cookie_file, idx):
    opts = _yt_dlp_opts(save_dir, cookie_file)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None, False

            prepared = ydl.prepare_filename(info)
            if not os.path.isfile(prepared):
                base, _ = os.path.splitext(prepared)
                for ext in ('.mp4', '.mkv', '.webm', '.mov'):
                    if os.path.isfile(base + ext):
                        prepared = base + ext
                        break

            if os.path.isfile(prepared):
                rel = os.path.relpath(prepared, save_dir)
                size_mb = os.path.getsize(prepared) / (1024 * 1024)
                print(f"[downloader] #{idx} downloaded: {os.path.basename(prepared)} "
                      f"({size_mb:.2f} MB)")
                return rel.replace('\\', '/'), True

            dir_name = os.path.dirname(prepared) or save_dir
            base_name = os.path.basename(base)
            leftovers = [f for f in os.listdir(dir_name) if f.startswith(base_name)]
            if leftovers:
                print(f"[downloader] #{idx} WARNING: leftover fragments "
                      f"(merge failed?): {leftovers}")

            return None, False

    except Exception as e:
        print(f"[downloader] yt-dlp error for #{idx}: {str(e)[:200]}")
        return None, False


# ===================================================================
#                     СБОР МЕДИА ЧЕРЕЗ INSTAGRAPI
# ===================================================================
def _collect_profile_media(cl, profile_name, is_reels_only):
    try:
        user_id = cl.user_id_from_username(profile_name)
    except Exception as e:
        raise RuntimeError(
            f'Profile @{profile_name} not found: {str(e)[:150]}'
        )
    print(f"[downloader] user_id for @{profile_name} = {user_id}")

    media_items = []

    if is_reels_only:
        try:
            clips = cl.user_clips(user_id, amount=0)
            print(f"[downloader] user_clips: {len(clips)} items")
            for m in clips:
                media_items.append({
                    'url': f"https://www.instagram.com/reel/{m.code}/",
                    'item_type': 'Reel',
                    'pk': str(m.pk),
                })
        except Exception as e:
            print(f"[downloader] user_clips error: {e}")
    else:
        try:
            posts = cl.user_medias(user_id, amount=0)
            print(f"[downloader] user_medias: {len(posts)} items")
            for m in posts:
                is_reel = getattr(m, 'product_type', None) == 'clips'
                kind = 'Reel' if is_reel else 'Post'
                if is_reel:
                    url = f"https://www.instagram.com/reel/{m.code}/"
                else:
                    url = f"https://www.instagram.com/p/{m.code}/"
                media_items.append({
                    'url': url,
                    'item_type': kind,
                    'pk': str(m.pk),
                })
        except Exception as e:
            print(f"[downloader] user_medias error: {e}")

    seen = set()
    result = []
    for item in media_items:
        if item['pk'] in seen:
            continue
        seen.add(item['pk'])
        result.append(item)

    print(f"[downloader] Total after dedup: {len(result)}")
    return result


# ===================================================================
#                     ОСНОВНАЯ ФУНКЦИЯ
# ===================================================================
def download_profile(task_id, profile_url, save_dir, cookies=None, media_type='both'):
    if not cookies or 'sessionid' not in cookies:
        raise RuntimeError("No cookies. Fetch them from Firefox.")

    is_reels_only = '/reels/' in profile_url

    match = re.search(r'instagram\.com/([^/?]+)', profile_url)
    if not match:
        match = re.search(r'^([A-Za-z0-9_.]+)$', profile_url.strip())
    profile_name = match.group(1) if match else 'profile'

    db.create_task(task_id, profile_url, profile_name, save_dir, None, media_type)

    cancel_event = threading.Event()
    with _lock:
        TASKS[task_id] = {
            'status': 'starting',
            'progress': 0,
            'total': 0,
            'message': 'Initializing...',
            'files': [],
            'cancel_event': cancel_event,
        }

    def finish(status, message):
        with _lock:
            if task_id in TASKS:
                TASKS[task_id]['status'] = status
                TASKS[task_id]['message'] = message
                if status == 'complete':
                    TASKS[task_id]['progress'] = 100
        db.complete_task(task_id, status, message)
        if status == 'complete':
            run_post_download_hook()

    # Save cookies to a temp file for yt-dlp
    os.makedirs(COOKIES_DIR, exist_ok=True)
    cookie_file = os.path.join(COOKIES_DIR, f'cookies_{task_id[:8]}.txt')
    try:
        with open(cookie_file, 'w', encoding='utf-8') as f:
            f.write('# Netscape HTTP Cookie File\n')
            for name, value in cookies.items():
                f.write(f".instagram.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n")
        print(f"[downloader] cookies saved: {cookie_file} ({len(cookies)} items)")
    except Exception as e:
        print(f"[downloader] failed to save cookies: {e}")
        cookie_file = None

    try:
        _set_status(task_id, message='[AUTH] Authorizing...')
        try:
            cl = _create_client(cookies)
            print("[downloader] instagrapi authorized")
        except Exception as e:
            finish('error',
                   f'Authorization failed: {e}. '
                   f'Make sure you are logged in to Instagram in Firefox.')
            return

        if _is_cancelled(task_id):
            finish('cancelled', 'Cancelled')
            return

        _set_status(task_id, message='[SEARCH] Collecting media list...')
        try:
            media_items = _collect_profile_media(cl, profile_name, is_reels_only)
        except RuntimeError as e:
            finish('error', str(e))
            return
        except Exception as e:
            finish('error', f'Failed to collect media: {str(e)[:200]}')
            return

        if media_type == 'video':
            media_items = [m for m in media_items if m['item_type'] == 'Reel' or
                           m['url'].find('/reel/') != -1]
        elif media_type == 'image':
            media_items = [m for m in media_items if m['item_type'] == 'Post' and
                           m['url'].find('/p/') != -1]

        total_items = len(media_items)
        print(f"[downloader] Total to download: {total_items}")

        if total_items == 0:
            finish('error',
                   f'No media found for @{profile_name} '
                   f'(after media_type filter).')
            return

        _set_status(task_id, total=total_items, status='downloading',
                    message=f'Found {total_items}. Downloading...')

        downloaded_strings = []
        failed_items = []

        for idx, item in enumerate(media_items, start=1):
            if _is_cancelled(task_id):
                finish('cancelled', f'Cancelled. Processed {idx-1} of {total_items}.')
                return

            url = item['url']
            item_type = item['item_type']
            pk = item['pk']

            rel_path, ok = _download_with_ytdlp(task_id, url, save_dir, cookie_file, idx)

            filename = os.path.basename(rel_path) if rel_path else None
            is_video = item_type == 'Reel' or (
                rel_path and rel_path.lower().endswith(('.mp4', '.mov', '.webm', '.mkv'))
            )

            db.add_task_file(task_id, idx, rel_path, item_type,
                             'video' if is_video else 'image',
                             is_video, pk, ok=ok)

            if ok:
                downloaded_strings.append(f'{item_type} #{idx} — {filename}')
            else:
                downloaded_strings.append(f'{item_type} #{idx} — download failed')
                failed_items.append({'idx': idx, 'url': url})

            progress = int((idx / total_items) * 100)
            _set_status(task_id, progress=progress,
                        message=f'Downloaded {idx}/{total_items}',
                        files=downloaded_strings)

            if _interruptible_sleep(task_id, 1.0 + random.uniform(0, 0.5)):
                finish('cancelled', f'Cancelled. Processed {idx} of {total_items}.')
                return

        success = total_items - len(failed_items)
        summary = f'Done! {success}/{total_items}.'
        if failed_items:
            summary += f' Failed: {len(failed_items)}.'

        with _lock:
            if task_id in TASKS:
                TASKS[task_id]['files'] = downloaded_strings
        finish('complete', summary)

    except Exception as e:
        finish('error', f'Error: {str(e)[:300]}')
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except Exception:
                pass
        print("[downloader] Reopening Firefox...")
        reopen_firefox()


# ===================================================================
#                     ПЕРЕСКАНИРОВАНИЕ
# ===================================================================
def rescan_task_files(task_id):
    task = db.get_task(task_id)
    if not task:
        return 0, 0
    save_dir = task.get('save_dir')
    if not save_dir or not os.path.isdir(save_dir):
        return 0, 0
    existing = {f['filename'] for f in db.get_task_files(task_id) if f.get('filename')}
    found = []
    for root, dirs, files in os.walk(save_dir):
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext in ('.mp4', '.mov', '.webm', '.mkv', '.m4v',
                       '.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff'):
                found.append(fn)
    found = sorted(set(found))
    added = 0
    for i, fn in enumerate(found, start=1):
        if fn in existing:
            continue
        ext = os.path.splitext(fn)[1].lower()
        is_video = ext in ('.mp4', '.mov', '.webm', '.mkv', '.m4v')
        db.add_task_file(task_id, 100000 + i, fn, 'File',
                         'video' if is_video else 'image', is_video, '', ok=True)
        added += 1
    return added, len(found)