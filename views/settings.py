"""
Настройки, скачивание VR, раздача VR-файлов, прогресс-стримы,
перезапуск сервера.
"""
import os
import sys
import json
import uuid
import time
import sqlite3
import mimetypes
import threading
import subprocess

import yt_dlp
from flask import (
    render_template, request, redirect, url_for, jsonify, abort,
    send_file, Response,
)

from models import (
    add_category,
    delete_category,
    delete_all_categories,
    add_library,
    delete_library,
    get_stats,
    CONFIG_PATH,
)
from scanner import (
    scan_libraries,
    scan_libraries_async,
    get_scan_progress,
    is_scan_in_progress,
)
from config import app_config, save_config
from helpers.profiles import get_current_profile, profile_to_mode
from helpers.vr_downloads import get_vr_downloads
from duplicate_finder import (
    find_duplicates_async,
    get_duplicate_groups,
    get_progress,
)


def _duplicates_stats(groups):
    """Считает total_* для шаблона."""
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


# ===================================================================
#                     RESTART SERVER
# ===================================================================
#  Как работает:
#
#  A) Если приложение запущено через run.bat — bat экспортирует
#     VM_SUPERVISED_BY_BAT=1. В этом случае мы просто делаем
#     os._exit(42), а bat сам поднимает python заново в том же окне.
#     Это самый чистый сценарий: сервер живёт в том же cmd,
#     пользователь видит логи, фоновых "висящих" python не остаётся.
#
#  B) Если приложение запущено вручную (`python app.py` или .exe) —
#     переменной нет. Тогда запускаем новый процесс через Popen
#     (DETACHED_PROCESS) и убиваем старый. Работает и в dev, и в EXE.
#
RESTART_EXIT_CODE = 42


def _spawn_restart():
    """
    Инициирует перезапуск сервера.

    Вызывается в фоновом потоке, чтобы сначала успеть отдать HTTP-ответ
    клиенту. Сам процесс завершается мгновенно.
    """
    # -------- Сценарий A: под управлением run.bat ----------------------
    if os.environ.get('VM_SUPERVISED_BY_BAT') == '1':
        print(f"[restart] supervised by run.bat, exiting with code "
              f"{RESTART_EXIT_CODE}")
        # Даём буферам шанс сброситься
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        # Небольшая пауза, чтобы HTTP-ответ успел уйти
        time.sleep(0.4)
        os._exit(RESTART_EXIT_CODE)
        return  # pragma: no cover

    # -------- Сценарий B: запуск вручную (dev или EXE) -----------------
    try:
        if getattr(sys, 'frozen', False):
            # PyInstaller EXE: [app.exe] + переданные аргументы
            cmd = [sys.executable] + list(sys.argv[1:])
        else:
            # dev: [python.exe] + [app.py]
            cmd = [sys.executable] + list(sys.argv)

        # Абсолютный cwd, чтобы app.py нашёлся при любом рабочем каталоге
        base_dir = os.path.abspath(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

        kwargs = {
            'close_fds': True,
            'stdin': subprocess.DEVNULL,
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.DEVNULL,
            'cwd': base_dir,
        }
        if sys.platform == 'win32':
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            kwargs['creationflags'] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

        proc = subprocess.Popen(cmd, **kwargs)
        print(f"[restart] new process spawned pid={proc.pid} cmd={cmd}")

        # Даём новому процессу время подняться и проверить, что он жив
        time.sleep(1.5)
        if proc.poll() is not None:
            print(f"[restart] new process died immediately "
                  f"(rc={proc.returncode}), NOT killing old server")
            return  # оставляем старый сервер работать
    except Exception as e:
        print(f"[restart] failed to spawn new process: {e}")
        return  # не убиваем текущий сервер, если не удалось запустить новый

    # -------- Убиваем старый процесс -----------------------------------
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
            if action == 'add_category':
                name = request.form.get('name')
                if name:
                    add_category(name, mode=current_mode)
            elif action == 'import_categories':
                text = request.form.get('categories_text', '')
                if text.strip():
                    names = [line.strip() for line in text.splitlines() if line.strip()]
                    for name in names:
                        try:
                            add_category(name, mode=current_mode)
                        except Exception:
                            pass
            elif action == 'delete_category':
                cat_id = request.form.get('category_id')
                if cat_id:
                    delete_category(cat_id)
            elif action == 'delete_all_categories':
                delete_all_categories(current_mode)
            elif action == 'add_library':
                path = request.form.get('path')
                if path and os.path.isdir(path):
                    try:
                        add_library(path, mode=current_mode)
                        scan_libraries()
                    except sqlite3.IntegrityError:
                        pass
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
            elif action == 'save_ip':
                ip = request.form.get('static_ip', '').strip()
                app_config['static_ip'] = ip
                save_config(app_config)
            elif action == 'start_duplicate_scan':
                filters = request.form.getlist('media_types')
                if not filters:
                    filters = ['video', 'image']
                new_task_id = str(uuid.uuid4())
                find_duplicates_async(new_task_id, filters)
                return redirect(url_for('settings', tab='duplicates',
                                        task_id=new_task_id,
                                        profile=current_profile))
            return redirect(url_for('settings', tab=tab, profile=current_profile))

        if tab == 'duplicates':
            duplicates = get_duplicate_groups()
            stats = _duplicates_stats(duplicates)
            return render_template('duplicates.html',
                                   duplicates=duplicates,
                                   task_id=task_id,
                                   **stats)
        elif tab == 'stats':
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
        elif tab == 'download_vr':
            return render_template('download_vr.html', files=get_vr_downloads())
        else:
            return render_template('settings.html',
                                   tab=tab,
                                   task_id=task_id,
                                   scan_running=is_scan_in_progress())

    # ------------------------------------------------------------------
    #                    RESTART SERVER
    # ------------------------------------------------------------------
    @app.route('/restart_server', methods=['POST'])
    def restart_server():
        """
        Перезапускает Flask-сервер.

        Ответ отдаём сразу — клиент сам ждёт, пока сервер вернётся
        (polling HEAD / каждые 0.9 секунды из JS).
        """
        print("[restart] requested by client")
        supervised = os.environ.get('VM_SUPERVISED_BY_BAT') == '1'
        print(f"[restart] supervised_by_bat={supervised}")
        threading.Thread(target=_spawn_restart, daemon=True).start()
        return jsonify({
            'success': True,
            'supervised': supervised,
            'message': 'Server is restarting…',
        })

    @app.route('/scan_progress/<task_id>')
    def scan_progress_stream(task_id):
        def generate():
            import time
            while True:
                progress = get_scan_progress(task_id)
                if progress is None:
                    yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                    break
                yield f"data: {json.dumps(progress)}\n\n"
                if progress.get('status') in ('complete', 'error'):
                    break
                time.sleep(0.5)
        return Response(generate(), mimetype="text/event-stream")

    @app.route('/download_vr', methods=['GET', 'POST'])
    def download_vr():
        if request.method == 'POST':
            url = request.form.get('url')
            download_path = request.form.get('download_path', '').strip()

            if not url:
                return render_template('download_vr.html',
                                       error='URL is required',
                                       files=get_vr_downloads())

            base_dir = os.path.dirname(CONFIG_PATH)
            if download_path and os.path.exists(download_path) and os.path.isdir(download_path):
                save_dir = download_path
            else:
                save_dir = os.path.join(base_dir, 'vr_downloads')
                if not os.path.exists(save_dir):
                    os.makedirs(save_dir)

            dop_dir = os.path.join(base_dir, '_dop')
            if os.path.exists(dop_dir):
                os.environ['PATH'] = dop_dir + os.pathsep + os.environ.get('PATH', '')

            ffmpeg_available = os.path.exists(os.path.join(dop_dir, 'ffmpeg.exe'))
            deno_available = os.path.exists(os.path.join(dop_dir, 'deno.exe'))

            ydl_opts = {
                'outtmpl': os.path.join(save_dir, '%(title)s.%(ext)s'),
                'quiet': True,
                'extractor_args': {'youtube': {'player_client': ['default', '-web_safari']}},
                'socket_timeout': 30,
                'retries': 10,
            }
            if ffmpeg_available:
                ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
                ydl_opts['ffmpeg_location'] = dop_dir
            else:
                ydl_opts['format'] = 'best[ext=mp4]'

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    message = f"Downloaded: {info.get('title', 'video')}"
                    if not ffmpeg_available:
                        message += " (ffmpeg not found)"
                    if not deno_available:
                        message += " (Deno not found)"
                    return render_template('download_vr.html',
                                           success=message,
                                           files=get_vr_downloads())
            except Exception as e:
                return render_template('download_vr.html',
                                       error=str(e),
                                       files=get_vr_downloads())
        return render_template('download_vr.html', files=get_vr_downloads())

    @app.route('/vr_download/<filename>')
    def vr_download_file(filename):
        vr_dir = os.path.join(os.path.dirname(CONFIG_PATH), 'vr_downloads')
        filepath = os.path.join(vr_dir, filename)
        if not os.path.exists(filepath):
            abort(404)
        mimetype, _ = mimetypes.guess_type(filepath)
        if not mimetype:
            mimetype = 'video/mp4'
        return send_file(filepath, mimetype=mimetype, as_attachment=False)

    @app.route('/progress/<task_id>')
    def progress_stream(task_id):
        def generate():
            import time
            while True:
                progress = get_progress(task_id)
                if progress is None:
                    yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                    break
                yield f"data: {json.dumps(progress)}\n\n"
                if progress.get('status') in ('complete', 'error'):
                    break
                time.sleep(0.5)
        return Response(generate(), mimetype="text/event-stream")