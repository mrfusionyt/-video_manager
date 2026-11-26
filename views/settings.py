"""
Настройки, скачивание VR, раздача VR-файлов, прогресс-стримы,
перезапуск сервера. Дубликаты — отдельная страница.

Категории разделены по media_type: video / image.
Поиск дубликатов: fast (video-only) / deep (video+audio),
ограничение по путям (scan_paths).
"""
import os
import sys
import json
import uuid
import time
import sqlite3
import threading
import subprocess
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from flask import (
    render_template, request, redirect, url_for, jsonify, Response,
)

from models import (
    add_category,
    delete_category,
    delete_all_categories,
    add_library,
    delete_library,
    get_stats,
    get_categories,
)
from scanner import (
    scan_libraries_async,
    get_scan_progress,
    is_scan_in_progress,
    request_scan_cancel,
)
from config import app_config, save_config
from .auth import auth_enabled, set_password, disable_password
from helpers.profiles import get_current_profile, profile_to_mode
from helpers.subprocess_utils import hidden_process_kwargs
from duplicate_finder import (
    find_duplicates_async,
    get_duplicate_groups,
    get_progress,
    request_duplicate_cancel,
)


def _duplicates_stats(groups):
    total_files = 0
    total_keep = 0
    total_move = 0
    total_size_keep = 0
    total_size_move = 0
    for g in groups:
        files = g.get('files', []) if isinstance(g, dict) else g
        if not files:
            continue
        best = max(files, key=lambda v: v.get('size', 0))
        total_files += len(files)
        total_keep += 1
        total_move += len(files) - 1
        total_size_keep += best.get('size', 0)
        for v in files:
            if v['id'] != best['id']:
                total_size_move += v.get('size', 0)
    return {
        'total_files': total_files,
        'total_keep': total_keep,
        'total_move': total_move,
        'total_size_keep': total_size_keep,
        'total_size_move': total_size_move,
    }


def _parse_scan_paths(raw_text):
    if not raw_text:
        return []
    result = []
    for line in raw_text.splitlines():
        p = line.strip().strip('"').strip("'")
        if p:
            result.append(p)
    return result


def _normalize_library_path(raw):
    """Accept a plain path, a quoted path or a file:// link."""
    path = (raw or '').strip()
    while len(path) >= 2 and path[0] == path[-1] and path[0] in ('"', "'"):
        path = path[1:-1].strip()
    if not path:
        return ''
    if path.lower().startswith('file://'):
        try:
            path = url2pathname(unquote(urlparse(path).path))
        except Exception:
            path = path.replace('file:///', '', 1)
    return os.path.normpath(os.path.expanduser(path))


RESTART_EXIT_CODE = 42


def _spawn_restart():
    if os.environ.get('VM_SUPERVISED_BY_BAT') == '1':
        print(f"[restart] supervised by run.bat, exiting with code {RESTART_EXIT_CODE}")
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        time.sleep(0.4)
        os._exit(RESTART_EXIT_CODE)

    try:
        if getattr(sys, 'frozen', False):
            cmd = [sys.executable] + list(sys.argv[1:])
        else:
            cmd = [sys.executable] + list(sys.argv)

        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base_dir = os.path.abspath(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )

        kwargs = hidden_process_kwargs(
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=base_dir,
        )
        if sys.platform == 'win32':
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            kwargs['creationflags'] |= DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

        proc = subprocess.Popen(cmd, **kwargs)
        print(f"[restart] new process spawned pid={proc.pid} cmd={cmd}")

        time.sleep(1.5)
        if proc.poll() is not None:
            print(f"[restart] new process died immediately (rc={proc.returncode})")
            return
    except Exception as e:
        print(f"[restart] failed to spawn new process: {e}")
        return

    print("[restart] exiting old process")
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)


def register(app):

    @app.route('/settings', methods=['GET', 'POST'])
    def settings():
        current_profile = request.values.get('profile') or get_current_profile()
        current_mode = profile_to_mode(current_profile)
        tab = request.values.get('tab') or request.args.get('tab') or 'libraries'
        task_id = request.args.get('task_id')

        if request.method == 'POST':
            action = request.form.get('action')

            form_media_type = request.form.get('media_type', 'video')
            if form_media_type not in ('video', 'image'):
                form_media_type = 'video'

            if action == 'add_category':
                name = request.form.get('name')
                if name:
                    add_category(name, mode=current_mode,
                                 media_type=form_media_type)

            elif action == 'import_categories':
                text = request.form.get('categories_text', '')
                if text.strip():
                    names = [line.strip() for line in text.splitlines()
                             if line.strip()]
                    for name in names:
                        try:
                            add_category(name, mode=current_mode,
                                         media_type=form_media_type)
                        except Exception:
                            pass

            elif action == 'delete_category':
                cat_id = request.form.get('category_id')
                if cat_id:
                    delete_category(cat_id)

            elif action == 'delete_all_categories':
                delete_all_categories(current_mode,
                                      media_type=form_media_type)

            elif action == 'add_library':
                path = _normalize_library_path(request.form.get('path'))
                if not path:
                    return redirect(url_for('settings', tab='libraries',
                                            profile=current_profile,
                                            error='Enter a folder path first.'))
                if not os.path.isdir(path):
                    return redirect(url_for('settings', tab='libraries',
                                            profile=current_profile,
                                            error='Folder not found: ' + path))
                try:
                    add_library(path, mode=current_mode)
                except sqlite3.IntegrityError:
                    return redirect(url_for('settings', tab='libraries',
                                            profile=current_profile,
                                            error='This library is already added.'))
                # Do not block the POST with a full scan: start it in the
                # background so the same progress bar used by Scan shows up.
                new_task_id = str(uuid.uuid4())
                started = scan_libraries_async(new_task_id)
                if started:
                    return redirect(url_for('settings', tab='libraries',
                                            task_id=new_task_id,
                                            profile=current_profile))
                return redirect(url_for('settings', tab='libraries',
                                        profile=current_profile))

            elif action == 'delete_library':
                lib_id = request.form.get('library_id')
                if lib_id:
                    delete_library(lib_id)
                    return redirect(url_for('settings', tab='libraries',
                                            profile=current_profile))

            elif action == 'scan':
                new_task_id = str(uuid.uuid4())
                started = scan_libraries_async(new_task_id)
                if not started:
                    return redirect(url_for('settings', tab='libraries',
                                            profile=current_profile))
                return redirect(url_for('settings',
                                        tab='libraries',
                                        task_id=new_task_id,
                                        profile=current_profile))

            elif action == 'deep_scan':
                new_task_id = str(uuid.uuid4())
                started = scan_libraries_async(new_task_id, deep=True)
                if not started:
                    return redirect(url_for('settings', tab='libraries',
                                            profile=current_profile))
                return redirect(url_for('settings',
                                        tab='libraries',
                                        task_id=new_task_id,
                                        profile=current_profile))

            elif action == 'save_ip':
                ip = request.form.get('static_ip', '').strip()
                app_config['static_ip'] = ip
                save_config(app_config)

            elif action == 'set_password':
                password = request.form.get('password', '')
                confirm = request.form.get('password_confirm', '')
                if not password:
                    return redirect(url_for('settings', tab='security',
                                            profile=current_profile,
                                            error='Enter a password first.'))
                if password != confirm:
                    return redirect(url_for('settings', tab='security',
                                            profile=current_profile,
                                            error='Passwords do not match.'))
                set_password(password)
                return redirect(url_for('settings', tab='security',
                                        profile=current_profile, saved='1'))

            elif action == 'disable_password':
                disable_password()
                return redirect(url_for('settings', tab='security',
                                        profile=current_profile, saved='1'))

            elif action == 'start_duplicate_scan':
                filters = request.form.getlist('media_types')
                if not filters:
                    filters = ['video', 'image']
                scan_paths = _parse_scan_paths(
                    request.form.get('scan_paths', ''))
                mode = request.form.get('mode', 'deep')
                if mode not in ('fast', 'deep'):
                    mode = 'deep'
                new_task_id = str(uuid.uuid4())
                started = find_duplicates_async(new_task_id, filters,
                                                scan_paths or None, mode=mode)
                if not started:
                    return redirect(url_for(
                        'duplicates_page', profile=current_profile,
                        error='A duplicate scan is already running.'))
                return redirect(url_for('duplicates_page',
                                        task_id=new_task_id,
                                        profile=current_profile))

            if tab == 'categories':
                return redirect(url_for('settings',
                                        tab='categories',
                                        media_type=form_media_type,
                                        profile=current_profile))
            return redirect(url_for('settings', tab=tab, profile=current_profile))

        if tab == 'duplicates':
            return redirect(url_for('duplicates_page', profile=current_profile))

        if tab == 'categories':
            cat_media_type = request.args.get('media_type', 'video')
            if cat_media_type not in ('video', 'image'):
                cat_media_type = 'video'

            categories_video = get_categories(mode=current_mode,
                                              media_type='video')
            categories_image = get_categories(mode=current_mode,
                                              media_type='image')

            return render_template(
                'settings.html',
                tab='categories',
                categories_video=categories_video,
                categories_image=categories_image,
                cat_media_type=cat_media_type,
                scan_running=is_scan_in_progress(),
            )

        if tab == 'stats':
            stats_female = get_stats(mode=1) or {
                'total_videos': 0, 'total_categories': 0,
                'total_size': 0, 'avg_rating': 0.0
            }
            stats_transgender = get_stats(mode=2) or {
                'total_videos': 0, 'total_categories': 0,
                'total_size': 0, 'avg_rating': 0.0
            }
            return render_template('stats.html',
                                   stats_female=stats_female,
                                   stats_transgender=stats_transgender)

        return render_template('settings.html',
                               tab=tab,
                               task_id=task_id,
                               error=request.args.get('error'),
                               saved=request.args.get('saved'),
                               password_enabled=auth_enabled(),
                               scan_running=is_scan_in_progress())

    # ================================================================
    #                    DUPLICATES — отдельная страница
    # ================================================================
    @app.route('/duplicates', methods=['GET', 'POST'])
    def duplicates_page():
        current_profile = request.values.get('profile') or get_current_profile()
        task_id = request.args.get('task_id')

        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'start_duplicate_scan':
                filters = request.form.getlist('media_types')
                if not filters:
                    filters = ['video', 'image']
                scan_paths = _parse_scan_paths(
                    request.form.get('scan_paths', ''))
                mode = request.form.get('mode', 'deep')
                if mode not in ('fast', 'deep'):
                    mode = 'deep'
                new_task_id = str(uuid.uuid4())
                started = find_duplicates_async(new_task_id, filters,
                                                scan_paths or None, mode=mode)
                if not started:
                    return redirect(url_for(
                        'duplicates_page', profile=current_profile,
                        error='A duplicate scan is already running.'))
                return redirect(url_for('duplicates_page',
                                        task_id=new_task_id,
                                        profile=current_profile))
            return redirect(url_for('duplicates_page', profile=current_profile))

        duplicates = get_duplicate_groups()
        stats = _duplicates_stats(duplicates)
        return render_template('duplicates.html',
                               duplicates=duplicates,
                               task_id=task_id,
                               **stats)

    # ================================================================
    #                    RESTART SERVER
    # ================================================================
    @app.route('/restart_server', methods=['POST'])
    def restart_server():
        print("[restart] requested by client")
        supervised = os.environ.get('VM_SUPERVISED_BY_BAT') == '1'
        print(f"[restart] supervised_by_bat={supervised}")
        threading.Thread(target=_spawn_restart, daemon=True).start()
        return jsonify({
            'success': True,
            'supervised': supervised,
            'message': 'Server is restarting...',
        })

    @app.route('/scan_progress/<task_id>')
    def scan_progress_stream(task_id):
        def generate():
            while True:
                progress = get_scan_progress(task_id)
                if progress is None:
                    yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                    break
                yield f"data: {json.dumps(progress)}\n\n"
                if progress.get('status') in ('complete', 'error', 'cancelled'):
                    break
                time.sleep(0.5)
        return Response(generate(), mimetype="text/event-stream")

    @app.route('/scan_progress/<task_id>/abort', methods=['POST'])
    def scan_progress_abort(task_id):
        result = request_scan_cancel(task_id)
        if result == 'not_found':
            return jsonify({'success': False, 'error': 'Task not found'}), 404
        return jsonify({'success': True, 'status': 'cancelling'})

    @app.route('/progress/<task_id>/abort', methods=['POST'])
    def progress_abort(task_id):
        result = request_duplicate_cancel(task_id)
        if result == 'not_found':
            return jsonify({'success': False, 'error': 'Task not found'}), 404
        return jsonify({'success': True, 'status': 'cancelling'})

    @app.route('/progress/<task_id>')
    def progress_stream(task_id):
        def generate():
            while True:
                progress = get_progress(task_id)
                if progress is None:
                    yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                    break
                yield f"data: {json.dumps(progress)}\n\n"
                if progress.get('status') in ('complete', 'error', 'cancelled'):
                    break
                time.sleep(0.5)
        return Response(generate(), mimetype="text/event-stream")