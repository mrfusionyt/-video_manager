"""File Transfer as a VideoManager blueprint (mounted under /transfer).

The original standalone project was a FastAPI app. This module keeps its
behaviour — upload files/folders, choose a destination on the server, avoid
name collisions and show recent files — but uses Flask and stores settings in
the common VideoManager app-data folder.

Uploads are chunked jobs: the browser keeps the selected files in IndexedDB
and sends one chunk at a time. The job state lives on the server, so progress
survives a page refresh and the transfer can be cancelled at any moment.
"""

import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import jsonify, render_template, request

from db.connection import APP_DATA_DIR

from . import file_transfer_bp

CONFIG_PATH = os.path.join(APP_DATA_DIR, 'file_transfer_config.json')
# Settings of the original standalone FileTransferServer, used only once.
LEGACY_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
JOBS_DIR = os.path.join(APP_DATA_DIR, 'file_transfer_jobs')

DEFAULT_DESTINATION = (
    r'C:\FileDrop' if os.name == 'nt'
    else os.path.join(os.path.expanduser('~'), 'FileDrop')
)

DEFAULTS = {
    'destination': DEFAULT_DESTINATION,
    'max_file_size': 0,
    'preserve_structure': True,
    'collision': 'rename',
}

_INVALID_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_COLLISION_MODES = ('rename', 'overwrite', 'skip')
CHUNK_SIZE = 1024 * 1024
MAX_CHUNK_SIZE = 8 * 1024 * 1024
JOB_TTL_SECONDS = 24 * 60 * 60
MAX_FILES_PER_JOB = 5000

_jobs = {}
_jobs_lock = threading.RLock()


def load_config():
    data = dict(DEFAULTS)
    saved = {}
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            saved = json.load(f) or {}
    except (OSError, ValueError, TypeError):
        pass

    # Keep the destination chosen in the old standalone app on first launch.
    # Credentials/port from that app are not needed inside VideoManager.
    if not saved:
        try:
            with open(LEGACY_CONFIG_PATH, 'r', encoding='utf-8') as f:
                legacy = json.load(f) or {}
            saved = {key: legacy[key] for key in DEFAULTS if key in legacy}
        except (OSError, ValueError, TypeError):
            pass

    for key in DEFAULTS:
        if key in saved:
            data[key] = saved[key]

    try:
        data['max_file_size'] = max(0, int(data['max_file_size'] or 0))
    except (TypeError, ValueError):
        data['max_file_size'] = 0
    data['preserve_structure'] = bool(data['preserve_structure'])
    if data['collision'] not in _COLLISION_MODES:
        data['collision'] = 'rename'
    return data


config = load_config()


def public_config():
    return {
        'destination': str(destination()),
        'max_file_size': int(config.get('max_file_size') or 0),
        'preserve_structure': bool(config.get('preserve_structure', True)),
        'collision': config.get('collision', 'rename'),
    }


def save_config():
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    tmp = CONFIG_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


def destination():
    path = Path(str(config.get('destination') or DEFAULT_DESTINATION)).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_part(part):
    """Keep Unicode names but remove characters Windows cannot use in a path."""
    part = _INVALID_CHARS.sub('_', str(part).strip()).rstrip(' .')
    if not part or part in ('.', '..'):
        return None
    return part


def safe_target(relative, root):
    """Resolve a relative upload path and make sure it stays inside root."""
    raw = str(relative or 'uploaded_file').replace('\\', '/')
    parts = [p for p in (safe_part(x) for x in raw.split('/')) if p]
    target = root.joinpath(*(parts or ['uploaded_file']))
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError('Invalid file path')
    return target


def collision_target(path, mode=None):
    mode = mode or config.get('collision', 'rename')
    if mode != 'rename' or not path.exists():
        return path
    n = 1
    while True:
        candidate = path.with_name(f'{path.stem} ({n}){path.suffix}')
        if not candidate.exists():
            return candidate
        n += 1


def settings_snapshot():
    return {
        'destination': str(destination()),
        'max_file_size': int(config.get('max_file_size') or 0),
        'preserve_structure': bool(config.get('preserve_structure', True)),
        'collision': config.get('collision', 'rename'),
    }


def _iso(ts):
    return datetime.fromtimestamp(ts).isoformat(timespec='seconds')


def save_job(job):
    path = os.path.join(job['dir'], 'job.json')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(job, f, ensure_ascii=False)
    os.replace(tmp, path)


def cleanup_jobs():
    """Drop finished/abandoned jobs older than one day."""
    now = time.time()
    with _jobs_lock:
        for job_id, job in list(_jobs.items()):
            if now - job['updated'] > JOB_TTL_SECONDS:
                _jobs.pop(job_id, None)
                shutil.rmtree(job['dir'], ignore_errors=True)
        try:
            for entry in os.scandir(JOBS_DIR):
                if entry.is_dir() and entry.name not in _jobs:
                    if now - entry.stat().st_mtime > JOB_TTL_SECONDS:
                        shutil.rmtree(entry.path, ignore_errors=True)
        except OSError:
            pass


def get_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            return job
        path = os.path.join(JOBS_DIR, job_id, 'job.json')
        if not os.path.isfile(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                job = json.load(f)
            if not job.get('dir') or not os.path.isdir(job['dir']):
                return None
            _jobs[job_id] = job
            return job
        except (OSError, ValueError, TypeError):
            return None


def job_payload(job):
    files = []
    for f in job['files']:
        status = f['status']
        if status == 'pending' and f['received'] > 0:
            status = 'uploading'
        files.append({
            'index': f['index'],
            'name': f['name'],
            'path': f['path'],
            'size': f['size'],
            'received': f['received'],
            'chunks_received': f['chunks_received'],
            'status': status,
            'error': f.get('error'),
        })

    total = int(job['total_bytes'])
    received = sum(int(f['received']) for f in job['files'])
    return {
        'job_id': job['id'],
        'status': job['status'],
        'total_bytes': total,
        'received_bytes': received,
        'percent': min(100, round(received * 100 / total)) if total else 100,
        'files': files,
        'uploaded': job['uploaded'],
        'skipped': job['skipped'],
        'errors': job['errors'],
        'started': _iso(job['started']),
        'updated': _iso(job['updated']),
    }


def finalize_job(job):
    """Move completed chunks into the destination folder."""
    root = Path(job['destination'])
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        job['status'] = 'error'
        job['errors'].append({'file': '', 'error': f'Cannot use destination: {e}'})
        return

    for f in job['files']:
        if not f['accept'] or f['status'] in ('skipped', 'error'):
            continue
        temp = Path(f['temp']) if f['temp'] else None
        if temp is None or not temp.exists():
            f['status'] = 'error'
            job['errors'].append({'file': f['name'], 'error': 'Uploaded data is missing'})
            continue
        try:
            target = safe_target(f['path'], root)
            if job['collision'] == 'skip' and target.exists():
                f['status'] = 'skipped'
                job['skipped'].append(f['path'])
                continue
            target = collision_target(target, job['collision'])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(temp), str(target))
            job['uploaded'].append({
                'name': target.name,
                'path': str(target),
                'size': target.stat().st_size,
                'time': datetime.now().isoformat(timespec='seconds'),
            })
            f['status'] = 'done'
        except Exception as e:
            f['status'] = 'error'
            job['errors'].append({'file': f['name'], 'error': str(e)})
        finally:
            if temp is not None:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass

    job['status'] = 'completed'
    job['updated'] = time.time()
    save_job(job)


def maybe_finalize(job):
    for f in job['files']:
        if f['accept'] and f['status'] not in ('done', 'error', 'skipped'):
            return
    finalize_job(job)


@file_transfer_bp.route('/')
def index():
    return render_template('file_transfer.html', transfer_config=public_config())


@file_transfer_bp.route('/api/config')
def get_config():
    return jsonify(public_config())


@file_transfer_bp.route('/api/config', methods=['POST'])
def update_config():
    data = request.get_json(silent=True) or {}
    updated = dict(config)

    if 'destination' in data:
        raw = str(data['destination']).strip()
        if not raw:
            return jsonify({'error': 'Destination path cannot be empty'}), 400
        try:
            path = Path(raw).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            if not path.is_dir():
                raise ValueError('Not a directory')
            updated['destination'] = str(path.resolve())
        except Exception as e:
            return jsonify({'error': f'Cannot use path: {e}'}), 400

    if 'max_file_size' in data:
        try:
            updated['max_file_size'] = max(0, int(data['max_file_size']))
        except (TypeError, ValueError):
            return jsonify({'error': 'Invalid file size limit'}), 400

    if 'preserve_structure' in data:
        updated['preserve_structure'] = bool(data['preserve_structure'])

    if 'collision' in data:
        if data['collision'] not in _COLLISION_MODES:
            return jsonify({'error': 'Invalid collision setting'}), 400
        updated['collision'] = data['collision']

    config.update(updated)
    save_config()
    return jsonify({'success': True, 'config': public_config()})


@file_transfer_bp.route('/api/upload/start', methods=['POST'])
def start_upload():
    data = request.get_json(silent=True) or {}
    items = data.get('files') or []
    if not isinstance(items, list) or not items:
        return jsonify({'error': 'No files selected'}), 400
    if len(items) > MAX_FILES_PER_JOB:
        return jsonify({'error': f'Too many files (limit {MAX_FILES_PER_JOB})'}), 400

    cleanup_jobs()
    snap = settings_snapshot()
    root = Path(snap['destination'])
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return jsonify({'error': f'Cannot use destination: {e}'}), 400

    job_id = secrets.token_urlsafe(16)
    job_dir = os.path.join(JOBS_DIR, job_id)
    try:
        os.makedirs(job_dir, exist_ok=True)
    except OSError as e:
        return jsonify({'error': f'Cannot create job: {e}'}), 500

    now = time.time()
    job = {
        'id': job_id,
        'dir': job_dir,
        'status': 'uploading',
        'started': now,
        'updated': now,
        'total_bytes': 0,
        'files': [],
        'uploaded': [],
        'skipped': [],
        'errors': [],
        'destination': snap['destination'],
        'collision': snap['collision'],
        'preserve_structure': snap['preserve_structure'],
        'max_file_size': snap['max_file_size'],
    }

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or 'uploaded_file')
        try:
            rel = safe_target(item.get('path') or name, root).relative_to(root).as_posix()
            size = max(0, int(item.get('size') or 0))
        except (ValueError, TypeError):
            job['errors'].append({'file': name, 'error': 'Invalid file path or size'})
            continue

        accept = True
        status = 'pending'
        error = None
        if snap['max_file_size'] and size > snap['max_file_size']:
            accept = False
            status = 'error'
            error = 'File exceeds the size limit'
            job['errors'].append({'file': name, 'error': error})
        elif snap['collision'] == 'skip' and safe_target(rel, root).exists():
            accept = False
            status = 'skipped'
            job['skipped'].append(rel)

        temp = None
        if accept:
            try:
                fd, temp = tempfile.mkstemp(prefix=f'file{i}.', suffix='.part', dir=job_dir)
                os.close(fd)
                if size == 0:
                    status = 'done'
                job['total_bytes'] += size
            except OSError as e:
                accept = False
                status = 'error'
                error = f'Cannot prepare upload: {e}'
                temp = None
                job['errors'].append({'file': name, 'error': error})

        job['files'].append({
            'index': i,
            'name': name,
            'path': rel,
            'size': size,
            'received': 0,
            'chunks_received': 0,
            'temp': temp,
            'status': status,
            'error': error,
            'accept': accept,
        })

    if not job['files']:
        return jsonify({'error': 'No valid files selected'}), 400

    with _jobs_lock:
        _jobs[job_id] = job
        maybe_finalize(job)
        save_job(job)
        payload = job_payload(job)

    return jsonify({
        'job_id': job_id,
        'chunk_size': CHUNK_SIZE,
        'status': payload['status'],
    })


@file_transfer_bp.route('/api/upload/<job_id>/chunk', methods=['POST'])
def upload_chunk(job_id):
    job = get_job(job_id)
    if job is None:
        return jsonify({'error': 'Upload job not found'}), 404

    with _jobs_lock:
        if job['status'] != 'uploading':
            return jsonify({
                'error': 'Upload job is not active',
                'status': job['status'],
            }), 409

        index_raw = request.headers.get('X-File-Index')
        chunk_raw = request.headers.get('X-Chunk-Index')
        if index_raw is None or chunk_raw is None:
            index_raw = request.form.get('index', -1)
            chunk_raw = request.form.get('chunk', -1)
        try:
            index = int(index_raw)
            chunk_index = int(chunk_raw)
        except (TypeError, ValueError):
            return jsonify({'error': 'Invalid chunk index'}), 400

        if index < 0 or index >= len(job['files']):
            return jsonify({'error': 'Invalid file index'}), 400
        file_state = job['files'][index]
        if not file_state['accept']:
            return jsonify({'error': 'This file is not being uploaded'}), 409

        if chunk_index < file_state['chunks_received']:
            return jsonify(job_payload(job))  # already received after a refresh
        if chunk_index != file_state['chunks_received']:
            return jsonify({'error': 'Chunk out of order'}), 409

        data = request.get_data()
        if not data:
            return jsonify({'error': 'Empty chunk'}), 400
        if len(data) > MAX_CHUNK_SIZE:
            return jsonify({'error': 'Chunk is too large'}), 413
        if file_state['received'] + len(data) > file_state['size']:
            return jsonify({'error': 'Chunk exceeds the declared file size'}), 400

        try:
            with open(file_state['temp'], 'ab') as out:
                out.write(data)
        except OSError as e:
            return jsonify({'error': f'Cannot save chunk: {e}'}), 500

        file_state['received'] += len(data)
        file_state['chunks_received'] += 1
        job['updated'] = time.time()
        if file_state['received'] >= file_state['size']:
            file_state['status'] = 'done'

        maybe_finalize(job)
        save_job(job)
        return jsonify(job_payload(job))


@file_transfer_bp.route('/api/upload/<job_id>/status')
def upload_status(job_id):
    job = get_job(job_id)
    if job is None:
        return jsonify({'error': 'Upload job not found'}), 404
    with _jobs_lock:
        return jsonify(job_payload(job))


@file_transfer_bp.route('/api/upload/<job_id>/cancel', methods=['POST'])
def cancel_upload(job_id):
    job = get_job(job_id)
    if job is None:
        return jsonify({'error': 'Upload job not found'}), 404

    with _jobs_lock:
        if job['status'] not in ('completed', 'cancelled', 'error'):
            job['status'] = 'cancelled'
            job['updated'] = time.time()
            for f in job['files']:
                if f.get('temp'):
                    try:
                        Path(f['temp']).unlink(missing_ok=True)
                    except OSError:
                        pass
            save_job(job)
        return jsonify(job_payload(job))


@file_transfer_bp.route('/api/history')
def history():
    try:
        entries = []
        for path in destination().rglob('*'):
            if not path.is_file():
                continue
            stat = path.stat()
            entries.append({
                'name': path.name,
                'path': str(path),
                'size': stat.st_size,
                'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(timespec='seconds'),
            })
        entries.sort(key=lambda x: x['modified'], reverse=True)
        return jsonify(entries[:100])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@file_transfer_bp.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'destination': str(destination()),
        'time': datetime.now().isoformat(timespec='seconds'),
    })
