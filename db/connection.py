"""Пути к БД и конфигу, соединение с SQLite."""
import os
import sys
import sqlite3


def get_app_data_dir():
    if sys.platform == 'win32':
        base = os.getenv('APPDATA', os.path.expanduser('~'))
    else:
        base = os.path.expanduser('~/.config')
    app_dir = os.path.join(base, 'VideoManager')
    if not os.path.exists(app_dir):
        os.makedirs(app_dir, exist_ok=True)
    return app_dir


APP_DATA_DIR = get_app_data_dir()
DB_PATH = os.path.join(APP_DATA_DIR, 'videos.db')
CONFIG_PATH = os.path.join(APP_DATA_DIR, 'config.json')


def get_db_connection():
    """
    Открывает соединение с SQLite в режиме WAL + busy_timeout.

    WAL: несколько читателей + один писатель одновременно.
    busy_timeout: если БД занята другим writer'ом — ждём до 30 сек,
                  а не падаем с 'database is locked'.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    # WAL — идемпотентно; ставится один раз на БД, но безвредно вызывать всегда
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        # Если БД уже в WAL — sqlite вернёт уже установленный режим.
        # Если файл занят другим процессом на этапе старта — пропускаем.
        pass
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn