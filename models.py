"""
Обратная совместимость. Все импорты вида `from models import X` продолжают работать.
Реальная реализация — в пакете db/.
"""

from db.connection import (
    get_app_data_dir,
    APP_DATA_DIR,
    DB_PATH,
    CONFIG_PATH,
    get_db_connection,
)

from db.migrations import init_db

from db.libraries import (
    get_libraries,
    add_library,
    delete_library,
)

from db.categories import (
    get_categories,
    add_category,
    delete_category,
    delete_all_categories,
    get_video_categories,
    update_video_categories,
)

from db.videos import (
    add_video,
    delete_video,
    get_all_videos,
    get_video_by_id,
    update_rating,
    rename_video,
    query_videos_paged,
    count_videos,
)

from db.stats import (
    get_stats,
    get_folders,
    get_videos_by_folder,
)

from db.playlists import (
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
]