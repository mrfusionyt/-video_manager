"""Роуты PhotoClassifier, примонтированные под /pv."""
import os
import hashlib
import shutil

from flask import (
    render_template, request, jsonify, send_file, current_app, send_from_directory,
)

from . import photo_classifier_bp
from .state import (
    state, moves, moves_lock,
    load_config, save_config, scan_source, is_safe_name, ask_folder,
)


@photo_classifier_bp.route('/')
def index():
    return render_template(
        'photo_classifier.html',
        app_labels={
            'not_nude': state['labels'].get('not_nude') or 'Not Nude',
            'nude': state['labels'].get('nude') or 'Nude',
        },
    )


@photo_classifier_bp.route('/favicon.ico')
def favicon():
    # 1) локальная иконка addon/photo_classifier/img/4040.png
    local = os.path.join(os.path.dirname(__file__), 'img', '4040.png')
    if os.path.exists(local):
        return send_file(local, mimetype='image/png')

    # 2) иконка основного проекта image/video.png
    image_dir = current_app.config.get('IMAGE_DIR')
    if image_dir:
        for name in ('video.png', 'settings.png', 'icon1.png'):
            p = os.path.join(image_dir, name)
            if os.path.exists(p):
                return send_from_directory(image_dir, name)

    return '', 404


@photo_classifier_bp.route('/api/state')
def api_state():
    with moves_lock:
        undo_count = len(moves)
    return jsonify({
        'images': state['images'],
        'settings': {
            'source': state['source'] or '',
            'not_nude': state['targets']['not_nude'] or '',
            'nude': state['targets']['nude'] or '',
            'label_not_nude': state['labels']['not_nude'],
            'label_nude': state['labels']['nude'],
        },
        'undo_count': undo_count,
    })


@photo_classifier_bp.route('/api/restart', methods=['POST'])
def api_restart():
    load_config()
    scan_source()
    with moves_lock:
        undo_count = len(moves)
    return jsonify({
        'ok': True,
        'images': state['images'],
        'settings': {
            'source': state['source'] or '',
            'not_nude': state['targets']['not_nude'] or '',
            'nude': state['targets']['nude'] or '',
            'label_not_nude': state['labels']['not_nude'],
            'label_nude': state['labels']['nude'],
        },
        'undo_count': undo_count,
    })


@photo_classifier_bp.route('/api/pick-folder', methods=['POST'])
def api_pick_folder():
    data = request.get_json() or {}
    title = data.get('title') or 'Select folder'

    if state.get('tk_root') is None:
        return jsonify({
            'path': '',
            'error': 'Tkinter dialog not available. Type the path manually.',
        })

    try:
        path = ask_folder(title)
    except Exception as e:
        return jsonify({'path': '', 'error': str(e)})

    return jsonify({'path': path or ''})


@photo_classifier_bp.route('/api/save-settings', methods=['POST'])
def api_save_settings():
    data = request.get_json() or {}

    source = (data.get('source') or '').strip() or None
    not_nude = (data.get('not_nude') or '').strip() or None
    nude = (data.get('nude') or '').strip() or None

    label_nn = (data.get('label_not_nude') or '').strip() or 'Not Nude'
    label_n = (data.get('label_nude') or '').strip() or 'Nude'

    if source and not os.path.isdir(source):
        return jsonify({'ok': False, 'error': 'Source folder does not exist'})
    if not_nude and not os.path.isdir(not_nude):
        return jsonify({'ok': False, 'error': 'Folder "Not Nude" does not exist'})
    if nude and not os.path.isdir(nude):
        return jsonify({'ok': False, 'error': 'Folder "Nude" does not exist'})

    state['source'] = source
    state['targets']['not_nude'] = not_nude
    state['targets']['nude'] = nude
    state['labels']['not_nude'] = label_nn
    state['labels']['nude'] = label_n

    save_config()
    scan_source()

    with moves_lock:
        undo_count = len(moves)

    return jsonify({'ok': True, 'images': state['images'],
                    'undo_count': undo_count})


@photo_classifier_bp.route('/api/image')
def api_image():
    name = request.args.get('name', '')
    thumb = request.args.get('thumb', '0') == '1'

    if not is_safe_name(name) or not state['source']:
        return 'bad', 400
    path = os.path.join(state['source'], name)
    if not os.path.isfile(path):
        return 'not found', 404

    if thumb:
        try:
            img = __import__('PIL.Image', fromlist=['Image']).Image.open(path)
            if img.width <= 1920 and img.height <= 1920:
                return send_file(path)

            cache_dir = os.path.join(state['source'], '.cache')
            os.makedirs(cache_dir, exist_ok=True)

            h = hashlib.md5(path.encode('utf-8')).hexdigest()
            cache_path = os.path.join(cache_dir, h + '.jpg')

            if os.path.exists(cache_path):
                return send_file(cache_path, mimetype='image/jpeg')

            img.thumbnail((1920, 1920), __import__('PIL.Image', fromlist=['Image']).Image.BILINEAR)
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            img.save(cache_path, format='JPEG', quality=80)
            return send_file(cache_path, mimetype='image/jpeg')
        except Exception:
            return send_file(path)

    return send_file(path)


@photo_classifier_bp.route('/api/info')
def api_info():
    name = request.args.get('name', '')
    if not is_safe_name(name) or not state['source']:
        return jsonify({'ok': False})

    path = os.path.join(state['source'], name)
    if not os.path.isfile(path):
        return jsonify({'ok': False})

    size_mb = os.path.getsize(path) / (1024 * 1024)
    try:
        from PIL import Image
        with Image.open(path) as img:
            w, h = img.size
        return jsonify({'ok': True, 'size': f'{size_mb:.1f} MB', 'res': f'{w}x{h}'})
    except Exception:
        return jsonify({'ok': True, 'size': f'{size_mb:.1f} MB', 'res': '?'})


@photo_classifier_bp.route('/api/classify', methods=['POST'])
def api_classify():
    data = request.get_json() or {}
    name = data.get('name', '')
    category = data.get('category', '')

    if category not in state['targets']:
        return jsonify({'ok': False, 'error': 'Unknown category'})
    target = state['targets'][category]
    if not target:
        return jsonify({'ok': False, 'error': 'Folder not selected in settings'})
    if not os.path.isdir(target):
        return jsonify({'ok': False, 'error': 'Folder does not exist'})
    if not is_safe_name(name) or not state['source']:
        return jsonify({'ok': False, 'error': 'Invalid file name'})

    src = os.path.join(state['source'], name)
    if not os.path.isfile(src):
        return jsonify({'ok': False, 'error': 'File not found'})

    dst = os.path.join(target, name)
    base, ext = os.path.splitext(name)
    i = 1
    while os.path.exists(dst):
        dst = os.path.join(target, f'{base}_{i}{ext}')
        i += 1

    try:
        shutil.move(src, dst)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

    with moves_lock:
        moves.append({'src': src, 'dst': dst, 'name': name})
        undo_count = len(moves)

    scan_source()
    return jsonify({'ok': True, 'images': state['images'],
                    'undo_count': undo_count})


@photo_classifier_bp.route('/api/undo', methods=['POST'])
def api_undo():
    with moves_lock:
        if not moves:
            return jsonify({'ok': False, 'error': 'No operations to undo'})
        m = moves.pop()

    src = m['src']
    dst = m['dst']

    if not os.path.isfile(dst):
        with moves_lock:
            undo_count = len(moves)
        return jsonify({'ok': False, 'error': 'File already moved or deleted',
                        'undo_count': undo_count})

    src_dir = os.path.dirname(src)
    if not os.path.isdir(src_dir):
        with moves_lock:
            undo_count = len(moves)
        return jsonify({'ok': False, 'error': 'Source folder no longer exists',
                        'undo_count': undo_count})

    target_path = src
    if os.path.exists(target_path):
        base, ext = os.path.splitext(src)
        i = 1
        while os.path.exists(f'{base}_undo{i}{ext}'):
            i += 1
        target_path = f'{base}_undo{i}{ext}'

    try:
        shutil.move(dst, target_path)
    except Exception as e:
        with moves_lock:
            moves.append(m)
        return jsonify({'ok': False, 'error': str(e)})

    scan_source()
    with moves_lock:
        undo_count = len(moves)
    return jsonify({'ok': True, 'images': state['images'],
                    'undo_count': undo_count})


@photo_classifier_bp.route('/api/delete', methods=['POST'])
def api_delete():
    data = request.get_json() or {}
    name = data.get('name', '')

    if not is_safe_name(name) or not state['source']:
        return jsonify({'ok': False, 'error': 'Invalid file name'})

    path = os.path.join(state['source'], name)
    if not os.path.isfile(path):
        return jsonify({'ok': False, 'error': 'File not found'})

    try:
        try:
            from send2trash import send2trash
            send2trash(path)
        except ImportError:
            os.remove(path)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

    scan_source()
    with moves_lock:
        undo_count = len(moves)
    return jsonify({'ok': True, 'images': state['images'],
                    'undo_count': undo_count})