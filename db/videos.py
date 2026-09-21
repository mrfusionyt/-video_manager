"""CRUD для таблицы videos."""
import os
import sqlite3
from datetime import datetime
from .connection import get_db_connection


# ===================================================================
#                     batch categories
# ===================================================================
def _batch_load_categories(cursor, video_ids, chunk_size=500):
    if not video_ids:
        return {}
    result = {}
    for i in range(0, len(video_ids), chunk_size):
        chunk = video_ids[i:i + chunk_size]
        placeholders = ','.join('?' * len(chunk))
        cursor.execute(f'''
            SELECT vc.video_id, c.id, c.name
            FROM video_categories vc
            JOIN categories c ON c.id = vc.category_id
            WHERE vc.video_id IN ({placeholders})
        ''', chunk)
        for row in cursor.fetchall():
            vid = row['video_id']
            result.setdefault(vid, []).append({
                'id': row['id'],
                'name': row['name'],
            })
    return result


# ===================================================================
#                     CRUD
# ===================================================================
def add_video(library_id, filename, filepath, duration=0, size=0, added=None,
              rating=0, orientation='horizontal', media_type='video',
              width=0, height=0, codec='', bitrate=0, fps=0.0, mode=1,
              folder=None):
    if added is None:
        added = datetime.now().isoformat()
    if folder is None:
        folder = os.path.dirname(os.path.normpath(filepath))

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO videos (
                    library_id, filename, filepath, folder, duration, size, added,
                    rating, orientation, media_type,
                    width, height, codec, bitrate, fps, mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (library_id, filename, filepath, folder, duration, size, added,
                  rating, orientation, media_type,
                  width, height, codec, bitrate, fps, mode))
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            cursor.execute('''
                UPDATE videos SET
                    library_id = ?,
                    filename = ?,
                    folder = ?,
                    duration = ?,
                    size = ?,
                    added = ?,
                    rating = ?,
                    orientation = ?,
                    media_type = ?,
                    width = ?,
                    height = ?,
                    codec = ?,
                    bitrate = ?,
                    fps = ?,
                    mode = ?
                WHERE filepath = ?
            ''', (library_id, filename, folder, duration, size, added,
                  rating, orientation, media_type,
                  width, height, codec, bitrate, fps, mode, filepath))
            conn.commit()
            return cursor.lastrowid
    finally:
        conn.close()


def delete_video(video_id):
    try:
        from .thumbnails import delete_thumbnail
        delete_thumbnail(video_id)
    except Exception:
        pass

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        conn.commit()
    finally:
        conn.close()


def get_all_videos(mode=None, sort_by='id', folder=None):
    """
    Возвращает список видео.

    :param mode: режим профиля (1 или 2), или None для всех.
    :param sort_by: 'id' (по умолчанию) или 'filename' (алфавитный порядок).
    :param folder: если указан, вернуть только видео из этой папки.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        where_parts = []
        params = []

        if mode is not None:
            where_parts.append('v.mode = ?')
            params.append(mode)

        if folder:
            where_parts.append('v.folder = ?')
            params.append(folder)

        where_sql = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

        if sort_by == 'filename':
            order_sql = 'ORDER BY v.filename COLLATE NOCASE ASC'
        else:
            order_sql = 'ORDER BY v.id'

        cursor.execute(f'''
            SELECT v.*, l.name as library_name
            FROM videos v
            LEFT JOIN libraries l ON v.library_id = l.id
            {where_sql}
            {order_sql}
        ''', params)

        rows = cursor.fetchall()
        video_ids = [row['id'] for row in rows]
        categories_map = _batch_load_categories(cursor, video_ids)

        videos = []
        for row in rows:
            v = dict(row)
            v['categories'] = categories_map.get(v['id'], [])
            videos.append(v)
        return videos
    finally:
        conn.close()


def get_video_by_id(video_id):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT v.*, l.name as library_name
            FROM videos v
            LEFT JOIN libraries l ON v.library_id = l.id
            WHERE v.id = ?
        ''', (video_id,))
        row = cursor.fetchone()
        if not row:
            return None
        video = dict(row)
        categories_map = _batch_load_categories(cursor, [video_id])
        video['categories'] = categories_map.get(video_id, [])
        return video
    finally:
        conn.close()


def update_rating(video_id, rating):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE videos SET rating = ? WHERE id = ?", (rating, video_id))
        conn.commit()
    finally:
        conn.close()


def rename_video(video_id, new_filename):
    video = get_video_by_id(video_id)
    if not video:
        return False
    old_path = video['filepath']
    dir_path = os.path.dirname(old_path)
    new_path = os.path.join(dir_path, new_filename)
    if not os.path.exists(old_path):
        return False

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        os.rename(old_path, new_path)
        cursor.execute(
            "UPDATE videos SET filename = ?, filepath = ? WHERE id = ?",
            (new_filename, new_path, video_id)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Rename error: {e}")
        return False
    finally:
        conn.close()


# ===================================================================
#                     SQL-пагинация
# ===================================================================
_ORDER_BY_MAP = {
    'date': 'v.added DESC',
    'rating': 'v.rating DESC',
    'duration_asc': 'v.duration ASC',
    'duration_desc': 'v.duration DESC',
    'orientation_vertical_first':
        "CASE WHEN v.orientation = 'vertical' THEN 0 ELSE 1 END, v.id",
    'orientation_horizontal_first':
        "CASE WHEN v.orientation = 'horizontal' THEN 0 ELSE 1 END, v.id",
    'top10': 'v.rating DESC',
    'top50': 'v.rating DESC',
    'top100': 'v.rating DESC',
}

_TOP_LIMIT = {'top10': 10, 'top50': 50, 'top100': 100}


def query_videos_paged(mode=None, sort='date', search='', folder='',
                       category_ids=None, page=1, per_page=15):
    if category_ids:
        return None, None

    order_clause = _ORDER_BY_MAP.get(sort)
    if order_clause is None:
        return None, None

    where_parts = []
    params = []

    if search:
        where_parts.append('LOWER(v.filename) LIKE ?')
        params.append(f'%{search.lower()}%')
    elif mode is not None:
        where_parts.append('v.mode = ?')
        params.append(mode)

    if folder:
        where_parts.append('v.folder = ?')
        params.append(folder)

    where_sql = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        cursor.execute(f'SELECT COUNT(*) AS cnt FROM videos v {where_sql}', params)
        total = cursor.fetchone()['cnt']

        if sort in _TOP_LIMIT:
            limit = _TOP_LIMIT[sort]
            pagination_sql = f'LIMIT {limit}'
        else:
            limit = per_page
            offset = max(0, (page - 1) * per_page)
            pagination_sql = f'LIMIT {limit} OFFSET {offset}'

        cursor.execute(f'''
            SELECT v.*, l.name as library_name
            FROM videos v
            LEFT JOIN libraries l ON v.library_id = l.id
            {where_sql}
            ORDER BY {order_clause}
            {pagination_sql}
        ''', params)
        rows = cursor.fetchall()

        video_ids = [row['id'] for row in rows]
        categories_map = _batch_load_categories(cursor, video_ids)

        videos = []
        for row in rows:
            v = dict(row)
            v['categories'] = categories_map.get(v['id'], [])
            videos.append(v)

        return videos, total
    finally:
        conn.close()


def count_videos(mode=None, search='', folder=''):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        where_parts = []
        params = []
        if search:
            where_parts.append('LOWER(filename) LIKE ?')
            params.append(f'%{search.lower()}%')
        elif mode is not None:
            where_parts.append('mode = ?')
            params.append(mode)
        if folder:
            where_parts.append('folder = ?')
            params.append(folder)
        where_sql = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''
        cursor.execute(f'SELECT COUNT(*) AS cnt FROM videos {where_sql}', params)
        return cursor.fetchone()['cnt']
    finally:
        conn.close()