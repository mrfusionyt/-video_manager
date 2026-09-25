"""Создание схемы и миграции."""
import os
from datetime import datetime
from .connection import get_db_connection
from .thumbnails import ensure_dir as ensure_thumbnails_dir


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")

    # WAL mode — обязательно до создания таблиц.
    # Позволяет читателям и писателю работать параллельно.
    try:
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA busy_timeout = 30000")
    except Exception as e:
        print(f"[init_db] WAL pragma warning: {e}")

    # --- Таблица libraries ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS libraries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            mode INTEGER DEFAULT 1
        )
    ''')
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_libraries_path_mode ON libraries(path, mode)")

    # --- Таблица videos ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            library_id INTEGER,
            filename TEXT,
            filepath TEXT UNIQUE,
            folder TEXT DEFAULT '',
            duration INTEGER DEFAULT 0,
            size INTEGER DEFAULT 0,
            added TEXT,
            rating INTEGER DEFAULT 0,
            orientation TEXT DEFAULT 'horizontal',
            media_type TEXT DEFAULT 'video',
            width INTEGER DEFAULT 0,
            height INTEGER DEFAULT 0,
            codec TEXT DEFAULT '',
            bitrate INTEGER DEFAULT 0,
            fps REAL DEFAULT 0.0,
            mode INTEGER DEFAULT 1,
            FOREIGN KEY (library_id) REFERENCES libraries (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute("PRAGMA table_info(videos)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'mode' not in columns:
        cursor.execute("ALTER TABLE videos ADD COLUMN mode INTEGER DEFAULT 1")
    if 'added' not in columns:
        cursor.execute("ALTER TABLE videos ADD COLUMN added TEXT")
        now = datetime.now().isoformat()
        cursor.execute("UPDATE videos SET added = ? WHERE added IS NULL", (now,))
    if 'rating' not in columns:
        cursor.execute("ALTER TABLE videos ADD COLUMN rating INTEGER DEFAULT 0")
    if 'orientation' not in columns:
        cursor.execute("ALTER TABLE videos ADD COLUMN orientation TEXT DEFAULT 'horizontal'")
    if 'media_type' not in columns:
        cursor.execute("ALTER TABLE videos ADD COLUMN media_type TEXT DEFAULT 'video'")
    if 'width' not in columns:
        cursor.execute("ALTER TABLE videos ADD COLUMN width INTEGER DEFAULT 0")
    if 'height' not in columns:
        cursor.execute("ALTER TABLE videos ADD COLUMN height INTEGER DEFAULT 0")
    if 'codec' not in columns:
        cursor.execute("ALTER TABLE videos ADD COLUMN codec TEXT DEFAULT ''")
    if 'bitrate' not in columns:
        cursor.execute("ALTER TABLE videos ADD COLUMN bitrate INTEGER DEFAULT 0")
    if 'fps' not in columns:
        cursor.execute("ALTER TABLE videos ADD COLUMN fps REAL DEFAULT 0.0")

    # --- Новая колонка folder ---
    if 'folder' not in columns:
        cursor.execute("ALTER TABLE videos ADD COLUMN folder TEXT DEFAULT ''")
        print("[INFO] Adding 'folder' column to videos, backfilling...")
        cursor.execute("SELECT id, filepath FROM videos")
        backfill_rows = cursor.fetchall()
        for row in backfill_rows:
            folder_path = os.path.dirname(os.path.normpath(row['filepath']))
            cursor.execute("UPDATE videos SET folder = ? WHERE id = ?", (folder_path, row['id']))
        conn.commit()
        print(f"[INFO] Backfilled folder for {len(backfill_rows)} videos")

    # ================================================================
    # --- Таблица categories (с media_type: 'video' | 'image') ---
    # ================================================================
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='categories'")
    table_exists = cursor.fetchone() is not None

    if not table_exists:
        # Создаём с нуля — сразу с media_type
        cursor.execute('''
            CREATE TABLE categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mode INTEGER DEFAULT 1,
                media_type TEXT DEFAULT 'video'
            )
        ''')
        cursor.execute(
            "CREATE UNIQUE INDEX idx_categories_name_mode_type "
            "ON categories(name, mode, media_type)"
        )
        print("[INFO] Created categories table with media_type")
    else:
        # 1) Добавляем колонку media_type, если её нет
        cursor.execute("PRAGMA table_info(categories)")
        cat_cols = [col['name'] for col in cursor.fetchall()]

        if 'media_type' not in cat_cols:
            cursor.execute(
                "ALTER TABLE categories ADD COLUMN media_type TEXT DEFAULT 'video'"
            )
            cursor.execute(
                "UPDATE categories SET media_type = 'video' WHERE media_type IS NULL"
            )
            print("[INFO] Added media_type column to categories")

        # 2) Ищем и удаляем старые уникальные индексы (name) / (name, mode),
        #    которые мешают новому UNIQUE(name, mode, media_type)
        cursor.execute("PRAGMA index_list('categories')")
        indexes = cursor.fetchall()

        has_new_index = False
        old_indexes_to_drop = []

        for idx in indexes:
            if not idx['unique']:
                continue
            idx_name = idx['name']
            cursor.execute(f"PRAGMA index_info('{idx_name}')")
            idx_cols = [c['name'] for c in cursor.fetchall()]
            if idx_cols == ['name', 'mode', 'media_type']:
                has_new_index = True
            elif idx_cols in (['name'], ['name', 'mode']):
                old_indexes_to_drop.append(idx_name)

        for old_name in old_indexes_to_drop:
            cursor.execute(f"DROP INDEX IF EXISTS {old_name}")
            print(f"[INFO] Dropped old index {old_name}")

        if not has_new_index:
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_name_mode_type "
                "ON categories(name, mode, media_type)"
            )
            print("[INFO] Created UNIQUE(name, mode, media_type) on categories")

    # --- Таблица video_categories ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS video_categories (
            video_id INTEGER,
            category_id INTEGER,
            FOREIGN KEY (video_id) REFERENCES videos (id) ON DELETE CASCADE,
            FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE CASCADE,
            PRIMARY KEY (video_id, category_id)
        )
    ''')

    # --- Таблицы плейлистов ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            mode INTEGER DEFAULT 1,
            created TEXT,
            cover_video_id INTEGER,
            UNIQUE(name, mode),
            FOREIGN KEY (cover_video_id) REFERENCES videos (id) ON DELETE SET NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS playlist_videos (
            playlist_id INTEGER,
            video_id INTEGER,
            added TEXT,
            PRIMARY KEY (playlist_id, video_id),
            FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE,
            FOREIGN KEY (video_id) REFERENCES videos (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute("PRAGMA table_info(playlists)")
    columns = [col['name'] for col in cursor.fetchall()]
    if 'cover_video_id' not in columns:
        cursor.execute("ALTER TABLE playlists ADD COLUMN cover_video_id INTEGER REFERENCES videos(id) ON DELETE SET NULL")

    # --- Индексы ---
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_mode ON videos(mode)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_added ON videos(added DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_rating ON videos(rating DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_library ON videos(library_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_media_type ON videos(media_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_folder ON videos(folder)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_mode_folder ON videos(mode, folder)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_mode_media_type ON videos(mode, media_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_video_categories_category ON video_categories(category_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_video_categories_video ON video_categories(video_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_playlist_videos_video ON playlist_videos(video_id)")

    conn.commit()
    conn.close()

    try:
        ensure_thumbnails_dir()
    except Exception as e:
        print(f"[init_db] cannot create thumbnails dir: {e}")
    