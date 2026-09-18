import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tasks.db')


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            profile_url TEXT,
            profile_name TEXT,
            save_dir TEXT,
            username TEXT,
            media_type TEXT,
            status TEXT DEFAULT 'starting',
            progress INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            message TEXT,
            created_at TEXT,
            completed_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS task_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            idx INTEGER,
            filename TEXT,
            file_type TEXT,
            kind TEXT,
            is_video INTEGER DEFAULT 0,
            pk TEXT,
            ok INTEGER DEFAULT 1,
            FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
        )
    """)

    c.execute("CREATE INDEX IF NOT EXISTS idx_task_files_task ON task_files(task_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC)")
    conn.commit()
    conn.close()

    cleanup_stale_tasks()


def cleanup_stale_tasks():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        UPDATE tasks
        SET status = 'cancelled',
            message = '⚠️ Прервано из-за перезапуска сервера',
            completed_at = ?
        WHERE status IN ('starting', 'downloading')
    """, (datetime.now().isoformat(),))
    affected = c.rowcount
    conn.commit()
    conn.close()
    if affected:
        print(f"[db] Помечено зависших задач: {affected}")


def create_task(task_id, profile_url, profile_name, save_dir, username, media_type):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO tasks
        (task_id, profile_url, profile_name, save_dir, username, media_type,
         status, progress, total, message, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'starting', 0, 0, 'Initializing...', ?)
    """, (task_id, profile_url, profile_name, save_dir, username, media_type,
          datetime.now().isoformat()))
    conn.commit()
    conn.close()


def update_task(task_id, **kwargs):
    if not kwargs:
        return
    fields = []
    values = []
    for k, v in kwargs.items():
        fields.append(f"{k} = ?")
        values.append(v)
    values.append(task_id)
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE task_id = ?", values)
    conn.commit()
    conn.close()


def complete_task(task_id, status, message):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        UPDATE tasks
        SET status = ?, message = ?, progress = 100, completed_at = ?
        WHERE task_id = ?
    """, (status, message, datetime.now().isoformat(), task_id))
    conn.commit()
    conn.close()


def add_task_file(task_id, idx, filename, file_type, kind, is_video, pk, ok=True):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM task_files WHERE task_id = ? AND idx = ?", (task_id, idx))
    c.execute("""
        INSERT INTO task_files (task_id, idx, filename, file_type, kind, is_video, pk, ok)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (task_id, idx, filename, file_type, kind, 1 if is_video else 0, pk, 1 if ok else 0))
    conn.commit()
    conn.close()


def get_task(task_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_tasks(limit=50):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_task_files(task_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM task_files WHERE task_id = ? ORDER BY idx ASC", (task_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_task(task_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
    conn.commit()
    conn.close()


def clear_all_tasks():
    """Удаляет все задачи и связанные с ними файлы."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM task_files")
    c.execute("DELETE FROM tasks")
    conn.commit()
    conn.close()