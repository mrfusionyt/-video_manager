"""Роуты страницы Tools (обслуживание библиотеки)."""
import os
import json
import uuid
import time

from flask import (
    render_template, request, jsonify, Response,
)
from helpers.profiles import get_current_profile
from models import get_libraries
from tools_service import (
    find_broken_dir, find_good_dir, find_duplicates_dir,
    folder_stats, list_videos,
    start_diagnose_task, start_restore_task,
    get_task,
)


def register(app):

    @app.route('/tools')
    def tools_page():
        current_profile = get_current_profile()
        broken = find_broken_dir()
        good = find_good_dir()
        dups = find_duplicates_dir()

        broken_stats = folder_stats(broken) if broken else {
            'exists': False, 'path': None, 'count': 0, 'size': 0}
        good_stats = folder_stats(good) if good else {
            'exists': False, 'path': None, 'count': 0, 'size': 0}
        dups_stats = folder_stats(dups) if dups else {
            'exists': False, 'path': None, 'count': 0, 'size': 0}

        libraries = get_libraries() or []

        return render_template(
            'tools.html',
            current_profile=current_profile,
            broken=broken_stats,
            good=good_stats,
            duplicates=dups_stats,
            libraries=libraries,
            broken_files=list_videos(broken) if broken else [],
            good_files=list_videos(good) if good else [],
        )

    @app.route('/tools/diagnose', methods=['POST'])
    def tools_diagnose():
        mode = request.form.get('mode', 'check')
        source = find_broken_dir()
        if not source:
            return jsonify({'error': '!Broken not found'}), 400

        if mode == 'check':
            tid = start_diagnose_task(source, dest=None, move=False)
        else:
            drive = os.path.splitdrive(source)[0] + os.sep
            dest = os.path.join(drive, '!Good')
            include_partial = (mode == 'move_partial')
            tid = start_diagnose_task(
                source, dest=dest, move=True,
                include_partial=include_partial,
            )
        return jsonify({'task_id': tid})

    @app.route('/tools/restore', methods=['POST'])
    def tools_restore():
        good = find_good_dir()
        if not good:
            return jsonify({'error': '!Good not found'}), 400

        lib_id = request.form.get('library_id')
        libraries = get_libraries() or []
        target = None
        for lib in libraries:
            if str(lib.get('id')) == str(lib_id):
                target = lib['path']
                break
        if not target:
            return jsonify({'error': 'library not found'}), 400

        tid = start_restore_task(good, target)
        return jsonify({'task_id': tid})

    @app.route('/tools/scan', methods=['POST'])
    def tools_scan():
        from scanner import scan_libraries_async
        tid = str(uuid.uuid4())
        ok = scan_libraries_async(tid)
        if not ok:
            return jsonify({'error': 'scan already running'}), 400
        return jsonify({'task_id': tid})

    @app.route('/tools/progress/<task_id>')
    def tools_progress(task_id):
        def generate():
            while True:
                t = get_task(task_id)
                if t is None:
                    yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                    break
                yield f"data: {json.dumps(t)}\n\n"
                if t.get('status') in ('complete', 'error'):
                    break
                time.sleep(0.5)
        return Response(generate(), mimetype='text/event-stream')