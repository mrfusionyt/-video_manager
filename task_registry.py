"""
Единый реестр фоновых задач.

Состояние хранится в SQLite (таблица tasks), поэтому задачи переживают
рестарт сервера. In-memory handlers нужны только для доставки сигнала
отмены в уже работающий воркер.
"""
import json
import threading
from datetime import datetime

from db.connection import get_db_connection


ACTIVE_STATUSES = {
    'running', 'starting', 'queued', 'scanning', 'downloading',
    'uploading', 'processing', 'converting', 'cancelling',
}
TERMINAL_STATUSES = {
    'complete', 'completed', 'done', 'finished',
    'error', 'failed', 'cancelled', 'canceled', 'aborted',
}

_FIELDS = {
    'kind', 'status', 'progress', 'processed', 'total', 'message',
    'current', 'mode', 'cancel_requested', 'payload', 'result',
    'created_at', 'updated_at', 'finished_at',
}
_json_fields = {'payload', 'result'}
_int_fields = {'progress', 'processed', 'total', 'cancel_requested'}

_handlers = {}
_handlers_lock = threading.RLock()


def _now():
    return datetime.now().isoformat(timespec='seconds')


def _encode(value):
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _decode(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _row_to_dict(row):
    if row is None:
        return None
    data = dict(row)
    for key in _json_fields:
        if key in data:
            data[key] = _decode(data[key])
    return data


def create(task_id, kind, status='running', **fields):
    """Create or replace a task row. Existing external task ids are preserved."""
    if not task_id:
        raise ValueError('task_id is required')
    values = {
        'id': str(task_id),
        'kind': str(kind or 'task'),
        'status': str(status or 'running'),
        'created_at': _now(),
        'updated_at': _now(),
        'finished_at': None,
        'progress': 0,
        'processed': 0,
        'total': 0,
        'message': '',
        'current': '',
        'mode': '',
        'cancel_requested': 0,
        'payload': None,
        'result': None,
    }
    for key, value in fields.items():
        if key in _FIELDS:
            values[key] = value
    for key in _json_fields:
        if values.get(key) is not None:
            values[key] = _encode(values[key])
    conn = get_db_connection()
    try:
        cols = ', '.join(values.keys())
        marks = ', '.join('?' for _ in values)
        conn.execute(
            f"INSERT OR REPLACE INTO tasks ({cols}) VALUES ({marks})",
            tuple(values.values()),
        )
        conn.commit()
    finally:
        conn.close()
    return get(task_id)


def update(task_id, **fields):
    """Update selected fields. Returns the current task dict or None."""
    if not task_id or not fields:
        return get(task_id)
    values = {}
    for key, value in fields.items():
        if key not in _FIELDS:
            continue
        if key in _json_fields and value is not None:
            value = _encode(value)
        if key in _int_fields and value is not None:
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = 0
        values[key] = value
    values['updated_at'] = _now()
    if str(values.get('status') or '') in TERMINAL_STATUSES:
        values['finished_at'] = _now()
    conn = get_db_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM tasks WHERE id = ?", (str(task_id),)
        ).fetchone()
        if not existing:
            return None
        sets = ', '.join(f'{k} = ?' for k in values)
        conn.execute(
            f"UPDATE tasks SET {sets} WHERE id = ?",
            tuple(values.values()) + (str(task_id),),
        )
        conn.commit()
    finally:
        conn.close()
    return get(task_id)


def get(task_id):
    if not task_id:
        return None
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (str(task_id),)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row)


def list_tasks(limit=100, active_only=False):
    conn = get_db_connection()
    try:
        if active_only:
            marks = ', '.join('?' for _ in ACTIVE_STATUSES)
            rows = conn.execute(
                f"SELECT * FROM tasks WHERE status IN ({marks}) "
                "ORDER BY updated_at DESC LIMIT ?",
                tuple(ACTIVE_STATUSES) + (int(limit),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(row) for row in rows]


def is_active(task):
    if not task:
        return False
    return str(task.get('status') or '') in ACTIVE_STATUSES


def has_active_tasks():
    """Return (active, brief list) for the updater's safety check."""
    tasks = list_tasks(limit=200, active_only=True)
    brief = [
        {
            'id': t.get('id'),
            'kind': t.get('kind'),
            'status': t.get('status'),
            'message': t.get('message') or '',
        }
        for t in tasks
    ]
    return bool(brief), brief


def register_cancel_handler(task_id, handler):
    with _handlers_lock:
        _handlers[str(task_id)] = handler


def unregister_cancel_handler(task_id):
    with _handlers_lock:
        _handlers.pop(str(task_id), None)


def request_cancel(task_id):
    """Idempotent cancel request compatible with vm_progress.js."""
    task = get(task_id)
    if not task:
        return 'not_found'
    if not is_active(task):
        return 'already_done'
    with _handlers_lock:
        handler = _handlers.get(str(task_id))
    if handler:
        update(task_id, status='cancelling', cancel_requested=1,
               message=task.get('message') or 'Cancelling...')
        try:
            handler()
        except Exception as e:
            print(f'[tasks] cancel handler error for {task_id}: {e}')
            update(task_id, status='error',
                   message=f'Cancel failed: {str(e)[:200]}')
    else:
        # No live worker: nothing can drain a 'cancelling' state, so close it.
        update(task_id, status='cancelled', cancel_requested=1,
               message=task.get('message') or 'Cancelled.')
    return 'ok'


def is_cancel_requested(task_id):
    task = get(task_id)
    if not task:
        return False
    return bool(task.get('cancel_requested')) or str(task.get('status')) == 'cancelling'


def mark_stale_on_startup():
    """Previous process died: active rows can no longer be running."""
    conn = get_db_connection()
    try:
        marks = ', '.join('?' for _ in ACTIVE_STATUSES)
        now = _now()
        conn.execute(
            f"UPDATE tasks SET status = 'cancelled', cancel_requested = 0, "
            f"message = 'Interrupted by restart', updated_at = ?, finished_at = ? "
            f"WHERE status IN ({marks})",
            (now, now) + tuple(ACTIVE_STATUSES),
        )
        conn.commit()
    finally:
        conn.close()
    with _handlers_lock:
        _handlers.clear()
