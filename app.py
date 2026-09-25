"""
Точка входа VideoManager.
"""
import os
import sys

from flask import Flask, request, send_from_directory

def resource_path(relative_path: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath(os.path.dirname(__file__)), relative_path)

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))

from models import init_db, get_categories, get_all_videos, get_libraries
from artists import init_artists_db, get_all_artists
from scanner import scan_libraries
from config import app_config, DARK_MODE, MODE_LABELS
from helpers.profiles import get_current_profile, profile_to_mode
from helpers.pagination import paginate_range
from addon.instagram_downloader import instagram_bp, set_post_download_hook
from addon.youtube_downloader import youtube_bp
from views import register_all

app = Flask(
    __name__,
    template_folder=resource_path("templates"),
    static_folder=resource_path("static"),
    static_url_path="/static",
)
app.secret_key = 'change-me-to-a-random-secret-key'
app.config["BASE_DIR"] = BASE_DIR
app.config["IMAGE_DIR"] = resource_path("image")
app.config["DOP_DIR"] = resource_path("_dop")

os.environ["PATH"] = app.config["DOP_DIR"] + os.pathsep + os.environ.get("PATH", "")

app.jinja_env.globals['paginate_range'] = paginate_range

init_db()
init_artists_db()

app.register_blueprint(instagram_bp)
app.register_blueprint(youtube_bp)
set_post_download_hook(scan_libraries)
register_all(app)

try:
    from views import tools as views_tools
    views_tools.register(app)
except Exception as _e:
    print(f"[app] WARNING: tools page not registered: {_e}")


@app.route("/image/<path:filename>")
def image_files(filename):
    return send_from_directory(app.config["IMAGE_DIR"], filename)


@app.template_filter('mode_name')
def mode_name_filter(mode):
    try:
        m = int(mode)
    except (TypeError, ValueError):
        return str(mode)
    return MODE_LABELS.get(m, str(mode))


@app.context_processor
def inject_globals():
    current_profile = get_current_profile()
    current_mode = profile_to_mode(current_profile)
    categories = get_categories(mode=current_mode)
    all_videos = get_all_videos(mode=current_mode)
    libraries = get_libraries(mode=current_mode)

    cat_counts = {}
    for video in all_videos:
        for cat in video.get('categories', []):
            cat_counts[cat['id']] = cat_counts.get(cat['id'], 0) + 1
    for cat in categories:
        cat['video_count'] = cat_counts.get(cat['id'], 0)

    total_videos = len(all_videos)

    artists_sidebar = []
    selected_artist_id = None
    if request.endpoint in ('artists_page', 'artist_view'):
        try:
            artists_sidebar = get_all_artists(mode=current_mode)
        except Exception as e:
            print(f"[sidebar] get_all_artists error: {e}")
            artists_sidebar = []
        if request.endpoint == 'artist_view':
            try:
                selected_artist_id = int((request.view_args or {}).get('artist_id', 0))
            except (TypeError, ValueError):
                selected_artist_id = None

    settings_tabs = [
        {'id': 'libraries', 'label': 'Libraries'},
        {'id': 'categories', 'label': 'Categories'},
        {'id': 'network', 'label': 'Network'},
        {'id': 'stats', 'label': 'Stats'},
    ]

    return dict(
        all_categories=categories,
        all_libraries=libraries,
        all_videos_count=total_videos,
        dark_mode=DARK_MODE,
        settings_tabs=settings_tabs,
        app_config=app_config,
        current_profile=current_profile,
        all_artists=artists_sidebar,
        selected_artist_id=selected_artist_id,
        mode_labels=MODE_LABELS,
    )


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False)