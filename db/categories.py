"""CRUD для категорий и связи video_categories."""
from .connection import get_db_connection


def get_categories(mode=None, media_type=None):
    """
    Возвращает категории.
      mode        — 1 (female) | 2 (transgender) | None (все)
      media_type  — 'video' | 'image' | None (все)
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    where_parts = []
    params = []
    if mode is not None:
        where_parts.append('mode = ?')
        params.append(mode)
    if media_type is not None:
        where_parts.append('media_type = ?')
        params.append(media_type)
    where_sql = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''
    cursor.execute(
        f"SELECT id, name, mode, media_type FROM categories {where_sql} ORDER BY name",
        params
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def add_category(name, mode=1, media_type='video'):
    """Создаёт категорию. media_type: 'video' | 'image'."""
    if media_type not in ('video', 'image'):
        media_type = 'video'
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO categories (name, mode, media_type) VALUES (?, ?, ?)",
            (name, mode, media_type)
        )
        conn.commit()
        return cursor.lastrowid
    except Exception:
        return None
    finally:
        conn.close()


def delete_category(category_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    conn.commit()
    conn.close()


def delete_all_categories(mode, media_type=None):
    """Удаляет категории профиля. Если media_type задан — только этого типа."""
    conn = get_db_connection()
    cursor = conn.cursor()
    if media_type is not None:
        cursor.execute(
            "DELETE FROM categories WHERE mode = ? AND media_type = ?",
            (mode, media_type)
        )
    else:
        cursor.execute("DELETE FROM categories WHERE mode = ?", (mode,))
    conn.commit()
    conn.close()


def get_video_categories(video_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT c.id, c.name, c.mode, c.media_type
        FROM categories c
        JOIN video_categories vc ON c.id = vc.category_id
        WHERE vc.video_id = ?
    ''', (video_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_video_categories(video_id, category_ids):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM video_categories WHERE video_id = ?", (video_id,))
    for cat_id in category_ids:
        cursor.execute(
            "INSERT INTO video_categories (video_id, category_id) VALUES (?, ?)",
            (video_id, cat_id)
        )
    conn.commit()
    conn.close()