"""
Пакет работы с БД. Реальная реализация — в подмодулях.
Экспортирует все публичные имена для удобства.
"""
from .connection import (
    get_app_data_dir,
    APP_DATA_DIR,
    DB_PATH,
    CONFIG_PATH,
    get_db_connection,
)
from .migrations import init_db
from .libraries import (
    get_libraries,
    add_library,
    delete_library,
)
from .categories import (
    get_categories,
    add_category,
    delete_category,
    delete_all_categories,
    get_video_categories,
    update_video_categories,
)
from .videos import (
    add_video,
    delete_video,
    get_all_videos,
    get_video_by_id,
    update_rating,
    rename_video,
    query_videos_paged,
    count_videos,
)
from .stats import (
    get_stats,
    get_folders,
    get_videos_by_folder,
)
from .playlists import (
    get_playlists,
    count_playlists,
    create_playlist,
    delete_playlist,
    rename_playlist,
    get_playlist_videos,
    add_video_to_playlist,
    remove_video_from_playlist,
    is_video_in_playlist,
    get_playlist_by_id,
    set_playlist_cover,
    get_playlist_cover,
)
from .thumbnails import (
    generate_thumbnail,
    has_thumbnail,
    delete_thumbnail,
    get_thumbnail_path,
    generate_all_missing,
    clear_all_thumbnails,
    THUMBNAILS_DIR,
)

__all__ = [
    'get_app_data_dir', 'APP_DATA_DIR', 'DB_PATH', 'CONFIG_PATH', 'get_db_connection',
    'init_db',
    'get_libraries', 'add_library', 'delete_library',
    'get_categories', 'add_category', 'delete_category', 'delete_all_categories',
    'get_video_categories', 'update_video_categories',
    'add_video', 'delete_video', 'get_all_videos', 'get_video_by_id',
    'update_rating', 'rename_video', 'query_videos_paged', 'count_videos',
    'get_stats', 'get_folders', 'get_videos_by_folder',
    'get_playlists', 'count_playlists', 'create_playlist', 'delete_playlist',
    'rename_playlist', 'get_playlist_videos', 'add_video_to_playlist',
    'remove_video_from_playlist', 'is_video_in_playlist', 'get_playlist_by_id',
    'set_playlist_cover', 'get_playlist_cover',
    'generate_thumbnail', 'has_thumbnail', 'delete_thumbnail',
    'get_thumbnail_path', 'generate_all_missing', 'clear_all_thumbnails',
    'THUMBNAILS_DIR',
]