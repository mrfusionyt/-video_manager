# -*- coding: utf-8 -*-
"""
Одноразовый скрипт: перезаписывает views/settings.py правильным содержимым.
Запуск: venv\Scripts\python.exe fix_settings.py
"""
import os

CONTENT = '''"""
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
from helpers.subprocess_utils import hidden_process_kwargs
from duplicate_finder import (
    find_duplicates_async,
    get_duplicate_groups,
    get_progress,
)


def _duplicates_stats(groups):
    total_files = 0
    total_keep = 0
    total_move = 0
    total_size_keep = 0
    total_size_move = 0
    for g in groups:
        files = g.get(\'files\', []) if isinstance(g, dict) else g
        if not files:
            continue
        best = max(files, key=lambda v: v.get(\'size\', 0))
        total_files += len(files)
        total_keep += 1
        total_move += len(files) - 1
        total_size_keep += best.get(\'size\', 0)
        for v in files:
            if v[\'id\'] != best[\'id\']:
                total_size_move += v.get(\'size\', 0)
    return {
        \'total_files\': total_files,
        \'total_keep\': total_keep,
        \'total_move\': total_move,
        \'total_size_keep\': total_size_keep,
        \'total_size_move\': total_size_move,
    }


RESTART_EXIT_CODE = 42


def _spawn_restart():
    if os.environ.get(\'VM_SUPERVISED_BY_BAT\') == \'1\':
        print(f"[restart] supervised by run.bat, exiting with code {RESTART_EXIT_CODE}")
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        time.sleep(0.4)
        os._exit(RESTART_EXIT_CODE)
        return

    try:
        if getattr(sys, \'frozen\', False):
            cmd = [sys.executable] + list(sys.argv[1:])
        else:
            cmd = [sys.executable] + list(sys.argv)

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
        if sys.platform == \'win32\':
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            kwargs[\'creationflags\'] |= DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

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

    @app.route(\'/settings\', methods=[\'GET\', \'POST\'])
    def settings():
        current_profile = request.values.get(\'profile\') or get_current_profile()
        current_mode = profile_to_mode(current_profile)
        tab = request.values.get(\'tab\') or request.args.get(\'tab\') or \'libraries\'
        task_id = request.args.get(\'task_id\')

        if request.method == \'POST\':
            action = request.form.get(\'action\')
            if action == \'add_category\':
                name = request.form.get(\'name\')
                if name:
                    add_category(name, mode=current_mode)
            elif action == \'import_categories\':
                text = request.form.get(\'categories_text\', \'\')
                if text.strip():
                    names = [line.strip() for line in text.splitlines() if line.strip()]
                    for name in names:
                        try:
                            add_category(name, mode=current_mode)
                        except Exception:
                            pass
            elif action == \'delete_category\':
                cat_id = request.form.get(\'category_id\')
                if cat_id:
                    delete_category(cat_id)
            elif action == \'delete_all_categories\':
                delete_all_categories(current_mode)
            elif action == \'add_library\':
                path = (request.form.get(\'path\') or \'\').strip().strip(\'"\').strip("\'")
                if path and os.path.isdir(path):
                    try:
                        add_library(path, mode=current_mode)
                        new_task_id = str(uuid.uuid4())
                        if scan_libraries_async(new_task_id):
                            return redirect(url_for(\'settings\', tab=\'libraries\',
                                                    task_id=new_task_id,
                                                    profile=current_profile))
                    except sqlite3.IntegrityError:
                        pass
            elif action == \'delete_library\':
                lib_id = request.form.get(\'library_id\')
                if lib_id:
                    delete_library(lib_id)
                    return redirect(url_for(\'settings\', tab=\'libraries\',
                                            profile=current_profile))
            elif action == \'scan\':
                new_task_id = str(uuid.uuid4())
                started = scan_libraries_async(new_task_id)
                if not started:
                    return redirect(url_for(\'settings\', tab=\'libraries\',
                                            profile=current_profile))
                return redirect(url_for(\'settings\',
                                        tab=\'libraries\',
                                        task_id=new_task_id,
                                        profile=current_profile))
            elif action == \'save_ip\':
                ip = request.form.get(\'static_ip\', \'\').strip()
                app_config[\'static_ip\'] = ip
                save_config(app_config)
            elif action == \'start_duplicate_scan\':
                filters = request.form.getlist(\'media_types\')
                if not filters:
                    filters = [\'video\', \'image\']
                new_task_id = str(uuid.uuid4())
                find_duplicates_async(new_task_id, filters)
                return redirect(url_for(\'settings\', tab=\'duplicates\',
                                        task_id=new_task_id,
                                        profile=current_profile))
            return redirect(url_for(\'settings\', tab=tab, profile=current_profile))


        if tab == \'duplicates\':
            duplicates = get_duplicate_groups()
            stats = _duplicates_stats(duplicates)
            return render_template(\'duplicates.html\',
                                   duplicates=duplicates,
                                   task_id=task_id,
                                   **stats)
        elif tab == \'stats\':
            stats_female = get_stats(mode=1) or {
                \'total_videos\': 0, \'total_categories\': 0,
                \'total_size\': 0, \'avg_rating\': 0.0
            }
            stats_transgender = get_stats(mode=2) or {
                \'total_videos\': 0, \'total_categories\': 0,
                \'total_size\': 0, \'avg_rating\': 0.0
            }
            return render_template(\'stats.html\',
                                   stats_female=stats_female,
                                   stats_transgender=stats_transgender)
        else:
            return render_template(\'settings.html\',
                                   tab=tab,
                                   task_id=task_id,
                                   scan_running=is_scan_in_progress())

    @app.route(\'/restart_server\', methods=[\'POST\'])
    def restart_server():
        print("[restart] requested by client")
        supervised = os.environ.get(\'VM_SUPERVISED_BY_BAT\') == \'1\'
        print(f"[restart] supervised_by_bat={supervised}")
        threading.Thread(target=_spawn_restart, daemon=True).start()
        return jsonify({
            \'success\': True,
            \'supervised\': supervised,
            \'message\': \'Server is restarting...\',
        })

    @app.route(\'/scan_progress/<task_id>\')
    def scan_progress_stream(task_id):
        def generate():
            while True:
                progress = get_scan_progress(task_id)
                if progress is None:
                    yield f"data: {json.dumps({\'status\': \'not_found\'})}\\n\\n"
                    break
                yield f"data: {json.dumps(progress)}\\n\\n"
                if progress.get(\'status\') in (\'complete\', \'error\'):
                    break
                time.sleep(0.5)
        return Response(generate(), mimetype="text/event-stream")

    @app.route(\'/progress/<task_id>\')
    def progress_stream(task_id):
        def generate():
            while True:
                progress = get_progress(task_id)
                if progress is None:
                    yield f"data: {json.dumps({\'status\': \'not_found\'})}\\n\\n"
                    break
                yield f"data: {json.dumps(progress)}\\n\\n"
                if progress.get(\'status\') in (\'complete\', \'error\'):
                    break
                time.sleep(0.5)
        return Response(generate(), mimetype="text/event-stream")
'''


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(here, 'views', 'settings.py')

    if not os.path.isdir(os.path.join(here, 'views')):
        print("ERROR: папка 'views' не найдена рядом с этим скриптом.")
        print(f"Ожидалось: {os.path.join(here, 'views')}")
        input("Enter чтобы выйти...")
        return

    with open(target, 'w', encoding='utf-8', newline='\n') as f:
        f.write(CONTENT)

    print(f"OK: {target} перезаписан ({len(CONTENT.splitlines())} строк).")
    input("Enter чтобы выйти...")


if __name__ == '__main__':
    main()