"""CRUD для плейлистов и playlist_videos."""
import sqlite3
from datetime import datetime
from .connection import get_db_connection
from .videos import get_video_by_id
from .categories import get_video_categories


def get_playlists(mode=None, limit=None, offset=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if mode is not None:
        query = "SELECT id, name, mode, created, cover_video_id FROM playlists WHERE mode = ? ORDER BY created DESC"
        params = [mode]
    else:
        query = "SELECT id, name, mode, created, cover_video_id FROM playlists ORDER BY created DESC"
        params = []
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    if offset is not None:
        query += " OFFSET ?"
        params.append(offset)
    cursor.execute(query, params)
    rows = cursor.fetchall()
    playlists = []
    for row in rows:
        pl = dict(row)
        if pl['cover_video_id']:
            cover_video = get_video_by_id(pl['cover_video_id'])
            pl['cover_video'] = cover_video
        else:
            videos = get_playlist_videos(pl['id'], limit=1)
            if videos:
                pl['cover_video'] = videos[0]
            else:
                pl['cover_video'] = None
        playlists.append(pl)
    conn.close()
    return playlists


def count_playlists(mode=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if mode is not None:
        cursor.execute("SELECT COUNT(*) as cnt FROM playlists WHERE mode = ?", (mode,))
    else:
        cursor.execute("SELECT COUNT(*) as cnt FROM playlists")
    row = cursor.fetchone()
    conn.close()
    return row['cnt'] if row else 0


def create_playlist(name, mode=1):
    conn = get_db_connection()
    cursor = conn.cursor()
    created = datetime.now().isoformat()
    try:
        cursor.execute(
            "INSERT INTO playlists (name, mode, created) VALUES (?, ?, ?)",
            (name, mode, created)
        )
        conn.commit()
        playlist_id = cursor.lastrowid
        conn.close()
        return playlist_id
    except sqlite3.IntegrityError:
        conn.close()
        return None


def delete_playlist(playlist_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    conn.commit()
    conn.close()


def rename_playlist(playlist_id, new_name):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE playlists SET name = ? WHERE id = ?", (new_name, playlist_id))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def get_playlist_videos(playlist_id, limit=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    query = '''
        SELECT v.* FROM videos v
        JOIN playlist_videos pv ON v.id = pv.video_id
        WHERE pv.playlist_id = ?
        ORDER BY pv.added DESC
    '''
    params = [playlist_id]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    cursor.execute(query, params)
    rows = cursor.fetchall()
    videos = []
    for row in rows:
        video = dict(row)
        video['categories'] = get_video_categories(video['id'])
        videos.append(video)
    conn.close()
    return videos


def add_video_to_playlist(playlist_id, video_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    added = datetime.now().isoformat()
    try:
        cursor.execute(
            "INSERT INTO playlist_videos (playlist_id, video_id, added) VALUES (?, ?, ?)",
            (playlist_id, video_id, added)
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def remove_video_from_playlist(playlist_id, video_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM playlist_videos WHERE playlist_id = ? AND video_id = ?",
        (playlist_id, video_id)
    )
    conn.commit()
    conn.close()


def is_video_in_playlist(playlist_id, video_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM playlist_videos WHERE playlist_id = ? AND video_id = ?",
        (playlist_id, video_id)
    )
    row = cursor.fetchone()
    conn.close()
    return row is not None


def get_playlist_by_id(playlist_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, mode, created, cover_video_id FROM playlists WHERE id = ?", (playlist_id,))
    row = cursor.fetchone()
    if row:
        pl = dict(row)
        if pl['cover_video_id']:
            pl['cover_video'] = get_video_by_id(pl['cover_video_id'])
        else:
            videos = get_playlist_videos(pl['id'], limit=1)
            pl['cover_video'] = videos[0] if videos else None
        conn.close()
        return pl
    conn.close()
    return None


def set_playlist_cover(playlist_id, video_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE playlists SET cover_video_id = ? WHERE id = ?", (video_id, playlist_id))
    conn.commit()
    conn.close()


def get_playlist_cover(playlist_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT cover_video_id FROM playlists WHERE id = ?", (playlist_id,))
    row = cursor.fetchone()
    conn.close()
    return row['cover_video_id'] if row else None