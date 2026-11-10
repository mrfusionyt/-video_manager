"""
Глобальный конфиг приложения.
Хранится один экземпляр app_config — импортируется во все вьюхи.
"""
import os
import json
from models import CONFIG_PATH


def load_config():
    default = {
        'last_folder': '',
        'dark_mode': True,
        'threshold': 0,
        'static_ip': ''
    }
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            for k in default:
                if k not in data:
                    data[k] = default[k]
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_config(config):
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: Could not save config: {e}")


# Singleton. Вьюхи мутируют эту переменную — изменения общие.
app_config = load_config()
DARK_MODE = app_config.get('dark_mode', True)

ITEMS_PER_PAGE = 15

MODE_LABELS = {
    1: 'Female',
    2: 'Transgender',
}


# ============================================================
#                  QUALITY THRESHOLDS
# ============================================================
# Классификация видео по разрешению (height — это высота кадра;
# для вертикальных видео важнее height, для горизонтальных — тоже
# height: 1080p = 1080 по вертикали).
#
#   LOW    — height <  QUALITY_LOW_HEIGHT (по умолчанию < 720)
#   HD     — 720 .. 1080 (не помечается)
#   FHD    — 1080 .. 1440 (не помечается)
#   QHD    — 1440 .. 2160 (не помечается)
#   UHD_4K — height >= QUALITY_4K_HEIGHT (по умолчанию >= 2160)
#
# Меняй пороги здесь, если хочется помечать другую границу.
QUALITY_LOW_HEIGHT = 720
QUALITY_4K_HEIGHT  = 2160

# Подписи и css-классы для бейджей. Порядок: от лучшего к худшему.
QUALITY_LABELS = {
    '4k':  {'label': '4K',  'cls': 'quality-4k'},
    'low': {'label': 'Low', 'cls': 'quality-low'},
}


def video_quality_tag(width, height):
    """
    Возвращает '4k' | 'low' | None для видео по разрешению.
    Ориентируемся на height (минимальная сторона для вертикальных,
    но так как поворот обычно 90°, используем max(w, h)).

    Логика:
      * если max(w, h) >= 2160 → '4k'
      * если min(w, h) < 720  → 'low'  (низкое разрешение)
      * иначе → None

    Примеры:
      1920x1080 → None
      3840x2160 → '4k'
      2160x3840 → '4k'
      1280x720  → None
      640x360   → 'low'
      848x480   → 'low'
      1080x1920 → None
    """
    try:
        w = int(width or 0)
        h = int(height or 0)
    except (TypeError, ValueError):
        return None

    if w <= 0 and h <= 0:
        return None

    short = min(w, h) if (w and h) else max(w, h)
    long_side = max(w, h)

    if long_side >= QUALITY_4K_HEIGHT:
        return '4k'
    if short and short < QUALITY_LOW_HEIGHT:
        return 'low'
    return None


def video_is_low(width, height):
    """True, если видео считается низким по разрешению."""
    return video_quality_tag(width, height) == 'low'


# ---------- Folder covers ----------
FOLDER_COVERS_FILE = os.path.join(os.path.dirname(CONFIG_PATH), 'folder_covers.json')


def load_folder_covers():
    try:
        with open(FOLDER_COVERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_folder_covers(covers):
    with open(FOLDER_COVERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(covers, f, indent=2)