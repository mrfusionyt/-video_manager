"""Статистика, папки и видео по папкам."""
import os
from .connection import get_db_connection
from .videos import _batch_load_categories


def get_stats(mode=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if mode is not None:
        cursor.execute("SELECT COUNT(*) as total_videos FROM videos WHERE mode = ?", (mode,))
        total_videos = cursor.fetchone()['total_videos']
        cursor.execute("SELECT COUNT(*) as total_categories FROM categories WHERE mode = ?", (mode,))
        total_categories = cursor.fetchone()['total_categories']
        cursor.execute("SELECT SUM(size) as total_size FROM videos WHERE mode = ?", (mode,))
        total_size = cursor.fetchone()['total_size'] or 0
        cursor.execute("SELECT AVG(rating) as avg_rating FROM videos WHERE rating > 0 AND mode = ?", (mode,))
        avg_rating = cursor.fetchone()['avg_rating'] or 0.0
    else:
        cursor.execute("SELECT COUNT(*) as total_videos FROM videos")
        total_videos = cursor.fetchone()['total_videos']
        cursor.execute("SELECT COUNT(*) as total_categories FROM categories")
        total_categories = cursor.fetchone()['total_categories']
        cursor.execute("SELECT SUM(size) as total_size FROM videos")
        total_size = cursor.fetchone()['total_size'] or 0
        cursor.execute("SELECT AVG(rating) as avg_rating FROM videos WHERE rating > 0")
        avg_rating = cursor.fetchone()['avg_rating'] or 0.0
    conn.close()
    return {
        'total_videos': total_videos,
        'total_categories': total_categories,
        'total_size': total_size,
        'avg_rating': round(avg_rating, 2)
    }


def get_folders(library_id=None, mode=None):
    """Быстрый GROUP BY folder в SQL (использует колонку folder + индекс)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    where_parts = ["folder != ''", "folder IS NOT NULL"]
    params = []
    if library_id:
        where_parts.append('library_id = ?')
        params.append(library_id)
    if mode is not None:
        where_parts.append('mode = ?')
        params.append(mode)
    where_sql = 'WHERE ' + ' AND '.join(where_parts)

    cursor.execute(f'''
        SELECT folder, COUNT(*) AS cnt
        FROM videos
        {where_sql}
        GROUP BY folder
    ''', params)
    rows = cursor.fetchall()
    conn.close()

    folders = []
    for row in rows:
        folder_path = row['folder']
        if not folder_path:
            continue
        folders.append({
            'path': folder_path,
            'name': os.path.basename(folder_path) or folder_path,
            'video_count': row['cnt'],
        })
    folders.sort(key=lambda x: x['name'].lower())
    return folders


def get_videos_by_folder(folder_path, limit=None, mode=None):
    """Быстрый SELECT WHERE folder = ? (индекс idx_videos_folder)."""
    folder_path = os.path.normpath(folder_path)
    conn = get_db_connection()
    cursor = conn.cursor()

    query = 'SELECT * FROM videos WHERE folder = ?'
    params = [folder_path]
    if mode is not None:
        query += ' AND mode = ?'
        params.append(mode)
    if limit is not None:
        query += ' LIMIT ?'
        params.append(int(limit))

    cursor.execute(query, params)
    rows = cursor.fetchall()

    video_ids = [row['id'] for row in rows]
    categories_map = _batch_load_categories(cursor, video_ids)

    videos = []
    for row in rows:
        v = dict(row)
        v['categories'] = categories_map.get(v['id'], [])
        videos.append(v)
    conn.close()
    return videos