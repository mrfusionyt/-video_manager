"""File Transfer as a VideoManager blueprint (mounted under /transfer).

The original standalone project was a FastAPI app. This module keeps its
behaviour — upload files/folders, choose a destination on the server, avoid
name collisions and show recent files — but uses Flask and stores settings in
the common VideoManager app-data folder.
"""

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from flask import jsonify, render_template, request

from db.connection import APP_DATA_DIR

from . import file_transfer_bp

CONFIG_PATH = os.path.join(APP_DATA_DIR, 'file_transfer_config.json')
# Settings of the original standalone FileTransferServer, used only once.
LEGACY_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')

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


def collision_target(path):
    if config.get('collision') != 'rename' or not path.exists():
        return path
    n = 1
    while True:
        candidate = path.with_name(f'{path.stem} ({n}){path.suffix}')
        if not candidate.exists():
            return candidate
        n += 1


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


@file_transfer_bp.route('/api/upload', methods=['POST'])
def upload_files():
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No files selected'}), 400

    paths = request.form.getlist('paths')
    root = destination()
    limit = int(config.get('max_file_size') or 0)
    preserve = bool(config.get('preserve_structure', True))

    uploaded = []
    skipped = []
    errors = []

    for i, upload in enumerate(files):
        temp_path = None
        try:
            rel = paths[i] if i < len(paths) and paths[i] else (upload.filename or 'uploaded_file')
            if not preserve:
                rel = Path(str(rel).replace('\\', '/')).name
            target = safe_target(rel, root)
            target.parent.mkdir(parents=True, exist_ok=True)

            if config.get('collision') == 'skip' and target.exists():
                skipped.append(str(rel))
                continue

            target = collision_target(target)
            # Write to a temporary file first, so a failed/limited upload
            # never leaves a truncated file at the destination.
            fd, temp_name = tempfile.mkstemp(
                prefix=target.name + '.', suffix='.part', dir=str(target.parent)
            )
            temp_path = Path(temp_name)
            size = 0
            with os.fdopen(fd, 'wb') as out:
                while True:
                    chunk = upload.stream.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if limit and size > limit:
                        raise ValueError('File exceeds the size limit')
                    out.write(chunk)

            os.replace(temp_path, target)
            temp_path = None
            uploaded.append({
                'name': target.name,
                'path': str(target),
                'size': size,
                'time': datetime.now().isoformat(timespec='seconds'),
            })
        except Exception as e:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            errors.append({'file': upload.filename, 'error': str(e)})
        finally:
            try:
                upload.close()
            except Exception:
                pass

    return jsonify({
        'success': not errors,
        'uploaded': uploaded,
        'skipped': skipped,
        'errors': errors,
    })


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
