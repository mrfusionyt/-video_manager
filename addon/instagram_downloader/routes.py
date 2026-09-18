import os
import re
import uuid
import threading
from flask import (
    render_template, request, redirect, url_for,
    jsonify, abort, send_from_directory
)
from instagrapi import Client
from .downloader import (
    download_profile, get_progress, get_downloaded_files, get_task,
    request_cancel, TASKS, get_saved_sessions, get_session_path,
    rescan_task_files,
)
from .browser_cookies import get_instagram_cookies_dict, close_firefox
from . import db
from . import instagram_bp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SAVE_DIR = os.path.join(BASE_DIR, 'downloads')
COOKIES_DIR = os.path.join(BASE_DIR, 'cookies')
os.makedirs(DEFAULT_SAVE_DIR, exist_ok=True)
os.makedirs(COOKIES_DIR, exist_ok=True)

ITEMS_PER_PAGE = 15
db.init_db()

CURRENT_USER_AGENT_DIAG = (
    "Instagram 423.0.0.47.66 Android (33/13; 480dpi; 1080x2400; "
    "xiaomi; M2007J20CG; surya; qcom; en_US; 641123490)"
)
CURRENT_DEVICE_DIAG = {
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


@instagram_bp.route('/')
def index():
    return render_template('instagram_index.html',
                           recent_tasks=db.get_all_tasks(limit=10))


@instagram_bp.route('/start_download', methods=['POST'])
def start_download():
    profile_url = request.form.get('profile_url', '').strip()
    save_dir = request.form.get('save_dir', '').strip()
    media_type = request.form.get('media_type', 'both')

    if not profile_url:
        return render_template('instagram_index.html',
                               error='Profile URL обязателен.',
                               recent_tasks=db.get_all_tasks(limit=10))

    # --- Получаем cookies из Firefox ---
    try:
        cookies = get_instagram_cookies_dict()
    except Exception as e:
        return render_template('instagram_index.html',
                               error=f'Не удалось получить cookies из Firefox: {e}',
                               recent_tasks=db.get_all_tasks(limit=10))

    if not cookies or 'sessionid' not in cookies:
        return render_template('instagram_index.html',
                               error='В Firefox нет cookies Instagram. '
                                     'Войдите в Instagram в Firefox и попробуйте снова.',
                               recent_tasks=db.get_all_tasks(limit=10))

    print(f"[routes] cookies из Firefox: {sorted(cookies.keys())}")

    # --- Закрываем Firefox, чтобы Instagram не видел один sessionid из двух клиентов ---
    print("[routes] Закрываем Firefox...")
    close_firefox()

    # --- Определяем папку ---
    if not save_dir:
        match = re.search(r'instagram\.com/([^/?]+)', profile_url)
        if not match:
            match = re.search(r'^([A-Za-z0-9_.]+)$', profile_url.strip())
        profile_name = match.group(1) if match else 'profile'
        save_dir = os.path.join(DEFAULT_SAVE_DIR, profile_name)
    else:
        save_dir = os.path.abspath(save_dir)

    os.makedirs(save_dir, exist_ok=True)
    task_id = str(uuid.uuid4())

    thread = threading.Thread(
        target=download_profile,
        args=(task_id, profile_url, save_dir, cookies, media_type),
        daemon=True
    )
    thread.start()

    return redirect(url_for('instagram.progress', task_id=task_id))


@instagram_bp.route('/cancel/<task_id>', methods=['POST'])
def cancel_task(task_id):
    result = request_cancel(task_id)
    if result == 'ok':
        return jsonify({'success': True})
    if result == 'already_done':
        task = db.get_task(task_id)
        return jsonify({
            'success': False,
            'already_done': True,
            'error': 'Задача уже завершена.',
            'status': task.get('status') if task else None,
        })
    if result == 'lost':
        db.complete_task(task_id, 'cancelled',
                         '⚠️ Прервано (сервер был перезапущен)')
        return jsonify({
            'success': True,
            'note': 'Задача потеряна — помечена как отменённая.'
        })
    return jsonify({'success': False, 'error': 'Задача не найдена'}), 404


@instagram_bp.route('/tasks')
def tasks_list():
    return render_template('tasks.html', tasks=db.get_all_tasks(limit=200))


@instagram_bp.route('/task/<task_id>')
def task_view(task_id):
    task = db.get_task(task_id)
    if not task:
        abort(404)
    if task['status'] in ('complete', 'cancelled', 'error'):
        return redirect(url_for('instagram.downloaded_files', task_id=task_id))
    return redirect(url_for('instagram.progress', task_id=task_id))


@instagram_bp.route('/progress/<task_id>')
def progress(task_id):
    task = db.get_task(task_id)
    if not task:
        abort(404)
    return render_template('progress.html', task_id=task_id, task=task)


@instagram_bp.route('/progress_data/<task_id>')
def progress_data(task_id):
    p = get_progress(task_id)
    if p is None:
        return jsonify({'status': 'not_found'}), 404
    p_copy = dict(p)
    p_copy.pop('file_objects', None)
    p_copy.pop('cancel_event', None)
    return jsonify(p_copy)


@instagram_bp.route('/downloaded/<task_id>')
def downloaded_files(task_id):
    files = get_downloaded_files(task_id)
    if files is None:
        abort(404)
    return render_template('downloaded.html', files=files,
                           task_id=task_id, total=len(files),
                           items_per_page=ITEMS_PER_PAGE)


@instagram_bp.route('/downloaded/<task_id>/page/<int:page>')
def downloaded_page(task_id, page):
    task = get_task(task_id)
    if not task or task.get('status') not in ('complete', 'cancelled', 'error'):
        return jsonify({'error': 'Task not complete'}), 404
    items = task.get('file_objects', [])
    total = len(items)
    start = (page - 1) * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    return jsonify({
        'items': items[start:end], 'page': page,
        'has_more': end < total, 'total': total,
    })


@instagram_bp.route('/media/<task_id>/<path:filename>')
def media(task_id, filename):
    if '..' in filename or filename.startswith('/'):
        abort(400)
    task = db.get_task(task_id)
    if not task:
        abort(404)
    save_dir = task.get('save_dir')
    if not save_dir or not os.path.isdir(save_dir):
        abort(404)
    full = os.path.join(save_dir, filename)
    if os.path.isfile(full):
        return send_from_directory(save_dir, filename)
    basename = os.path.basename(filename)
    for root, dirs, files in os.walk(save_dir):
        if basename in files:
            rel = os.path.relpath(os.path.join(root, basename), save_dir)
            return send_from_directory(save_dir, rel)
    abort(404)


@instagram_bp.route('/rescan/<task_id>', methods=['POST'])
def rescan_task(task_id):
    task = db.get_task(task_id)
    if not task:
        return jsonify({'success': False, 'error': 'Task not found'}), 404
    try:
        added, total = rescan_task_files(task_id)
        return jsonify({'success': True, 'added': added, 'total': total})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@instagram_bp.route('/task/delete/<task_id>', methods=['POST'])
def delete_task_route(task_id):
    db.delete_task(task_id)
    return jsonify({'success': True})


@instagram_bp.route('/tasks/clear', methods=['POST'])
def clear_tasks_route():
    db.clear_all_tasks()
    return jsonify({'success': True})


@instagram_bp.route('/cookies_status')
def cookies_status():
    try:
        cookies = get_instagram_cookies_dict()
        return jsonify({
            'ok': True,
            'cookies': sorted(cookies.keys()),
            'has_sessionid': 'sessionid' in cookies,
        })
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e), 'cookies': []})


@instagram_bp.route('/diagnose')
def diagnose():
    profile = request.args.get('profile', '').strip()

    try:
        cookies = get_instagram_cookies_dict()
    except Exception as e:
        return jsonify({'ok': False, 'error': f'cookies: {e}'}), 500

    if not cookies or 'sessionid' not in cookies:
        return jsonify({'ok': False, 'error': 'no sessionid in cookies'}), 400

    try:
        cl = Client(public_transport="curl", public_transport_impersonate="chrome136")
        cl.set_user_agent(CURRENT_USER_AGENT_DIAG)
        cl.set_device(CURRENT_DEVICE_DIAG)
        cl.login_by_sessionid(cookies['sessionid'])
        if hasattr(cl, 'set_cookies') and callable(cl.set_cookies):
            cl.set_cookies(cookies)
        if 'csrftoken' in cookies:
            cl.csrftoken = cookies['csrftoken']

        info = cl.account_info()
        result = {
            'ok': True,
            'logged_in_as': getattr(info, 'username', '?'),
            'pk': str(getattr(info, 'pk', '?')),
            'cookies_keys': sorted(cookies.keys()),
        }

        if profile:
            try:
                uid = cl.user_id_from_username(profile)
                result['profile_user_id'] = str(uid)

                uinfo = cl.user_info(uid)
                result['profile_is_private'] = bool(getattr(uinfo, 'is_private', False))
                result['profile_media_count'] = getattr(uinfo, 'media_count', None)
                result['profile_full_name'] = getattr(uinfo, 'full_name', None)

                try:
                    posts = cl.user_medias(uid, amount=0)
                    result['user_medias_count'] = len(posts)
                except Exception as e:
                    result['user_medias_error'] = str(e)[:200]

                try:
                    clips = cl.user_clips(uid, amount=0)
                    result['user_clips_count'] = len(clips)
                except Exception as e:
                    result['user_clips_error'] = str(e)[:200]

            except Exception as e:
                result['profile_error'] = str(e)[:200]

        return jsonify(result)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500