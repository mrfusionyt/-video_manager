"""Список VR-видео в папке vr_downloads."""
import os
from models import CONFIG_PATH


def get_vr_downloads():
    vr_dir = os.path.join(os.path.dirname(CONFIG_PATH), 'vr_downloads')
    if not os.path.exists(vr_dir):
        os.makedirs(vr_dir)
    files = []
    for f in os.listdir(vr_dir):
        if f.endswith(('.mp4', '.mkv', '.webm')):
            filepath = os.path.join(vr_dir, f)
            size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
            files.append({'name': f, 'path': filepath, 'size': size})
    return files