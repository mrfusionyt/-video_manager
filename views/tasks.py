"""Единый просмотр и отмена фоновых задач."""
import json
import time

from flask import Response, jsonify, render_template

from task_registry import (
    TERMINAL_STATUSES, get, has_active_tasks, list_tasks, request_cancel,
)


def register(app):
    @app.route('/tasks')
    def tasks_page():
        return render_template('tasks.html', tasks=list_tasks(limit=200))

    @app.route('/api/tasks')
    def api_tasks():
        return jsonify({'tasks': list_tasks(limit=200)})

    @app.route('/api/active_tasks')
    def api_active_tasks():
        active, tasks = has_active_tasks()
        return jsonify({'active': active, 'tasks': tasks})

    @app.route('/tasks/<task_id>/events')
    def task_events(task_id):
        def generate():
            last_sent = None
            while True:
                state = get(task_id)
                if state is None:
                    state = {'status': 'not_found', 'progress': 0,
                             'processed': 0, 'total': 0,
                             'message': 'Task not found'}
                    yield f'data: {json.dumps(state, ensure_ascii=False)}\n\n'
                    break
                if state != last_sent:
                    yield f'data: {json.dumps(state, ensure_ascii=False)}\n\n'
                    last_sent = state
                if str(state.get('status')) in TERMINAL_STATUSES:
                    break
                time.sleep(0.5)

        return Response(generate(), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache',
                                 'X-Accel-Buffering': 'no'})

    @app.route('/tasks/<task_id>/cancel', methods=['POST'])
    def task_cancel(task_id):
        result = request_cancel(task_id)
        state = get(task_id) or {}
        return jsonify({
            'success': result in ('ok', 'already_done'),
            'status': state.get('status') or result,
            'result': result,
        })
