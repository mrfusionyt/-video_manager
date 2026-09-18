import os
import sqlite3
from datetime import datetime

# Путь к БД — берём из models.py, если там есть DB_PATH,
# иначе используем videos.db рядом с проектом.
try:
    from models import DB_PATH as _MODELS_DB_PATH
    DB_PATH = _MODELS_DB_PATH
except (ImportError, AttributeError):
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'videos.db')


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_artists_db():
    """Создаёт таблицы artists и video_artists, если их нет."""
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS artists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            cover_video_id INTEGER,
            mode INTEGER DEFAULT 1,
            created_at TEXT,
            UNIQUE(name, mode)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS video_artists (
            video_id INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            PRIMARY KEY (video_id, artist_id),
            FOREIGN KEY (artist_id) REFERENCES artists(id) ON DELETE CASCADE
        )
    """)

    c.execute("CREATE INDEX IF NOT EXISTS idx_video_artists_artist ON video_artists(artist_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_video_artists_video ON video_artists(video_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_artists_mode ON artists(mode)")

    conn.commit()
    conn.close()


# ===================================================================
#                     CRUD
# ===================================================================
def get_all_artists(mode=None):
    conn = get_conn()
    c = conn.cursor()
    if mode is None:
        c.execute("SELECT * FROM artists ORDER BY name COLLATE NOCASE ASC")
    else:
        c.execute("SELECT * FROM artists WHERE mode = ? ORDER BY name COLLATE NOCASE ASC", (mode,))
    rows = c.fetchall()

    result = []
    for r in rows:
        d = dict(r)
        c.execute("SELECT COUNT(*) FROM video_artists WHERE artist_id = ?", (d['id'],))
        d['video_count'] = c.fetchone()[0]
        result.append(d)
    conn.close()
    return result


def get_artist_by_id(artist_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM artists WHERE id = ?", (artist_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def add_artist(name, mode=1):
    name = (name or '').strip()
    if not name:
        return None
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO artists (name, mode, created_at) VALUES (?, ?, ?)",
            (name, mode, datetime.now().isoformat())
        )
        artist_id = c.lastrowid
        conn.commit()
        return artist_id
    except sqlite3.IntegrityError:
        # Уже существует в этом профиле — возвращаем существующий id
        c.execute("SELECT id FROM artists WHERE name = ? AND mode = ?", (name, mode))
        row = c.fetchone()
        return row['id'] if row else None
    finally:
        conn.close()


def delete_artist(artist_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM artists WHERE id = ?", (artist_id,))
    conn.commit()
    conn.close()


def rename_artist(artist_id, new_name):
    new_name = (new_name or '').strip()
    if not new_name:
        return False
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("UPDATE artists SET name = ? WHERE id = ?", (new_name, artist_id))
        conn.commit()
        return c.rowcount > 0
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def set_artist_cover(artist_id, video_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE artists SET cover_video_id = ? WHERE id = ?", (video_id, artist_id))
    conn.commit()
    conn.close()


# ===================================================================
#                     СВЯЗЬ ВИДЕО ↔ АРТИСТ
# ===================================================================
def get_artist_video_ids(artist_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT video_id FROM video_artists WHERE artist_id = ?", (artist_id,))
    rows = c.fetchall()
    conn.close()
    return [r['video_id'] for r in rows]


def get_all_assigned_video_ids(mode=None):
    """
    Возвращает set() ID всех видео, привязанных к ЛЮБОМУ артисту.

    Если mode задан — учитываются только артисты этого профиля.
    Если mode=None — вообще все привязки.
    """
    conn = get_conn()
    c = conn.cursor()
    if mode is None:
        c.execute("SELECT DISTINCT video_id FROM video_artists")
    else:
        c.execute("""
            SELECT DISTINCT va.video_id
            FROM video_artists va
            JOIN artists a ON a.id = va.artist_id
            WHERE a.mode = ?
        """, (mode,))
    rows = c.fetchall()
    conn.close()
    return {r['video_id'] for r in rows}


def add_video_to_artist(artist_id, video_id):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            "INSERT OR IGNORE INTO video_artists (video_id, artist_id) VALUES (?, ?)",
            (video_id, artist_id)
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def remove_video_from_artist(artist_id, video_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "DELETE FROM video_artists WHERE artist_id = ? AND video_id = ?",
        (artist_id, video_id)
    )
    conn.commit()
    conn.close()


def get_artists_for_video(video_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT a.* FROM artists a
        JOIN video_artists va ON va.artist_id = a.id
        WHERE va.video_id = ?
        ORDER BY a.name COLLATE NOCASE ASC
    """, (video_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_video_artists(video_id, artist_ids):
    """Полностью перезаписывает список артистов для видео."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM video_artists WHERE video_id = ?", (video_id,))
    for aid in artist_ids:
        try:
            c.execute(
                "INSERT OR IGNORE INTO video_artists (video_id, artist_id) VALUES (?, ?)",
                (video_id, aid)
            )
        except Exception:
            pass
    conn.commit()
    conn.close()