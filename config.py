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