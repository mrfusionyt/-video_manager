"""
Глобальное состояние PhotoClassifier.
Вынесено отдельно, чтобы launcher.py мог подцепить Tkinter-диалоги,
а routes.py — читать/писать фолдеры и файлы.
"""
import os
import json
import queue
import threading
from collections import deque
from pathlib import Path

from PIL import Image


SUPPORTED = ('.jpg', '.jpeg', '.png', '.gif', '.bmp',
             '.webp', '.tiff', '.tif', '.ico', '.ppm', '.pgm')

CONFIG_PATH = Path.home() / '.photo_classifier_config.json'

state = {
    'source': None,
    'targets': {'not_nude': None, 'nude': None},
    'labels': {'not_nude': 'Not Nude', 'nude': 'Nude'},
    'images': [],
    'tk_root': None,          # set by launcher.py when Tkinter is available
}

moves = deque(maxlen=10)
moves_lock = threading.Lock()

# Диалоги выбора папок: launcher.py запускает tk_pump,
# который читает dialog_req и пишет в dialog_res.
dialog_req = queue.Queue()
dialog_res = queue.Queue()


def load_config():
    if not CONFIG_PATH.exists():
        return
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        state['source'] = data.get('source') or None
        state['targets']['not_nude'] = data.get('not_nude') or None
        state['targets']['nude'] = data.get('nude') or None
        state['labels']['not_nude'] = data.get('label_not_nude') or 'Not Nude'
        state['labels']['nude'] = data.get('label_nude') or 'Nude'
    except Exception:
        pass


def save_config():
    try:
        CONFIG_PATH.write_text(json.dumps({
            'source': state['source'],
            'not_nude': state['targets']['not_nude'],
            'nude': state['targets']['nude'],
            'label_not_nude': state['labels']['not_nude'],
            'label_nude': state['labels']['nude'],
        }, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass


def scan_source():
    src = state['source']
    if not src or not os.path.isdir(src):
        state['images'] = []
        return
    try:
        names = os.listdir(src)
    except OSError:
        state['images'] = []
        return

    import re

    def key(f):
        return [int(t) if t.isdigit() else t.lower()
                for t in re.split(r'(\d+)', f)]

    state['images'] = sorted(
        [f for f in names if f.lower().endswith(SUPPORTED)],
        key=key,
    )


def is_safe_name(name):
    return (bool(name) and '/' not in name and '\\' not in name
            and '..' not in name and ':' not in name)


def ask_folder(title):
    """Синхронно запрашивает папку через Tkinter-диалог.
    Если Tkinter не запущен — сразу возвращает ''."""
    if state.get('tk_root') is None:
        return ''
    dialog_req.put(title)
    try:
        return dialog_res.get(timeout=300)
    except queue.Empty:
        return ''