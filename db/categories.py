"""CRUD для категорий и связи video_categories."""
from .connection import get_db_connection


def get_categories(mode=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if mode is not None:
        cursor.execute("SELECT id, name, mode FROM categories WHERE mode = ? ORDER BY name", (mode,))
    else:
        cursor.execute("SELECT id, name, mode FROM categories ORDER BY name")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def add_category(name, mode=1):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO categories (name, mode) VALUES (?, ?)", (name, mode))
    conn.commit()
    conn.close()
    return cursor.lastrowid


def delete_category(category_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    conn.commit()
    conn.close()


def delete_all_categories(mode):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM categories WHERE mode = ?", (mode,))
    conn.commit()
    conn.close()


def get_video_categories(video_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT c.id, c.name
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
        cursor.execute("INSERT INTO video_categories (video_id, category_id) VALUES (?, ?)", (video_id, cat_id))
    conn.commit()
    conn.close()