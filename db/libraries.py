"""CRUD для таблицы libraries."""
import os
from .connection import get_db_connection


def get_libraries(mode=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if mode is not None:
        cursor.execute("SELECT id, name, path, mode FROM libraries WHERE mode = ?", (mode,))
    else:
        cursor.execute("SELECT id, name, path, mode FROM libraries")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def add_library(path, name=None, mode=1):
    if name is None:
        name = os.path.basename(os.path.normpath(path))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO libraries (name, path, mode) VALUES (?, ?, ?)", (name, path, mode))
    conn.commit()
    conn.close()


def delete_library(library_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Сначала соберём id видео этой библиотеки, чтобы снести и их превью
    cursor.execute("SELECT id FROM videos WHERE library_id = ?", (library_id,))
    video_ids = [row['id'] for row in cursor.fetchall()]

    if video_ids:
        try:
            from .thumbnails import delete_thumbnail
            for vid in video_ids:
                delete_thumbnail(vid)
        except Exception:
            pass

    cursor.execute("DELETE FROM videos WHERE library_id = ?", (library_id,))
    cursor.execute("DELETE FROM libraries WHERE id = ?", (library_id,))
    conn.commit()
    conn.close()