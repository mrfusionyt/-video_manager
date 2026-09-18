import os
from urllib.parse import unquote
from flask import (
    render_template, request, redirect, url_for, jsonify, abort, send_from_directory
)
from . import youtube_bp
from . import downloader as dl

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SAVE_DIR = os.path.join(BASE_DIR, 'downloads')
os.makedirs(DEFAULT_SAVE_DIR, exist_ok=True)


@youtube_bp.route('/')
def index():
    return render_template('youtube_index.html',
                           default_save_dir=DEFAULT_SAVE_DIR,
                           recent_tasks=dl.get_all_tasks(limit=10))


@youtube_bp.route('/start', methods=['POST'])
def start():
    url = request.form.get('url', '').strip()
    mode = request.form.get('mode', 'video')
    save_dir = request.form.get('save_dir', '').strip() or DEFAULT_SAVE_DIR

    if mode not in ('video', 'playlist', 'channel_shorts'):
        mode = 'video'

    if not url:
        return render_template('youtube_index.html',
                               error='URL is required.',
                               default_save_dir=DEFAULT_SAVE_DIR,
                               recent_tasks=dl.get_all_tasks(limit=10))

    save_dir = os.path.abspath(save_dir)
    try:
        os.makedirs(save_dir, exist_ok=True)
    except Exception as e:
        return render_template('youtube_index.html',
                               error=f'Cannot create save dir: {e}',
                               default_save_dir=DEFAULT_SAVE_DIR,
                               recent_tasks=dl.get_all_tasks(limit=10))

    task_id = dl.create_task(url, mode, save_dir)
    return redirect(url_for('youtube.progress', task_id=task_id))


@youtube_bp.route('/progress/<task_id>')
def progress(task_id):
    task = dl.get_task(task_id)
    if not task:
        abort(404)
    return render_template('youtube_progress.html', task=task)


@youtube_bp.route('/state/<task_id>')
def state(task_id):
    s = dl.get_state(task_id)
    if s is None:
        return jsonify({'error': 'not_found'}), 404
    return jsonify(s)


@youtube_bp.route('/log/<task_id>')
def log(task_id):
    try:
        offset = int(request.args.get('offset', 0))
    except ValueError:
        offset = 0
    lines, total = dl.get_log_since(task_id, offset)
    if lines is None:
        return jsonify({'error': 'not_found'}), 404
    task = dl.get_task(task_id)
    return jsonify({
        'lines': lines,
        'total': total,
        'status': task['status'] if task else 'unknown',
        'returncode': task['returncode'] if task else None,
        'finished_at': task['finished_at'] if task else None,
    })


@youtube_bp.route('/media/<task_id>/<path:filename>')
def media(task_id, filename):
    if '..' in filename or filename.startswith('/'):
        abort(400)
    task = dl.get_task(task_id)
    if not task:
        abort(404)
    save_dir = task.get('save_dir')
    if not save_dir or not os.path.isdir(save_dir):
        abort(404)

    # Декодируем имя файла из URL
    decoded = unquote(filename)

    # 1) Прямой путь
    full = os.path.join(save_dir, decoded)
    if os.path.isfile(full):
        return send_from_directory(save_dir, decoded)

    # 2) Поиск по basename (учитываем эмодзи и пробелы)
    basename = os.path.basename(decoded)
    for root, dirs, files in os.walk(save_dir):
        for fn in files:
            if fn == basename or fn == decoded:
                rel = os.path.relpath(os.path.join(root, fn), save_dir)
                return send_from_directory(save_dir, rel)

    # 3) Нормализация Unicode (NFC/NFD)
    import unicodedata
    basename_nfc = unicodedata.normalize('NFC', basename)
    basename_nfd = unicodedata.normalize('NFD', basename)
    for root, dirs, files in os.walk(save_dir):
        for fn in files:
            fn_nfc = unicodedata.normalize('NFC', fn)
            fn_nfd = unicodedata.normalize('NFD', fn)
            if fn_nfc == basename_nfc or fn_nfd == basename_nfd:
                rel = os.path.relpath(os.path.join(root, fn), save_dir)
                return send_from_directory(save_dir, rel)

    abort(404)


@youtube_bp.route('/cancel/<task_id>', methods=['POST'])
def cancel(task_id):
    result = dl.request_cancel(task_id)
    if result == 'ok':
        return jsonify({'success': True})
    if result == 'already_done':
        return jsonify({'success': False, 'error': 'Task already finished'})
    return jsonify({'success': False, 'error': 'Task not found'}), 404


@youtube_bp.route('/tasks')
def tasks_list():
    return render_template('youtube_tasks.html',
                           tasks=dl.get_all_tasks(limit=200))


@youtube_bp.route('/task/<task_id>')
def task_view(task_id):
    task = dl.get_task(task_id)
    if not task:
        abort(404)
    return redirect(url_for('youtube.progress', task_id=task_id))


@youtube_bp.route('/task/delete/<task_id>', methods=['POST'])
def delete_task(task_id):
    dl.delete_task(task_id)
    return jsonify({'success': True})


@youtube_bp.route('/tasks/clear', methods=['POST'])
def clear_tasks():
    dl.clear_all_tasks()
    return jsonify({'success': True})