from flask import Blueprint

youtube_bp = Blueprint(
    'youtube',
    __name__,
    url_prefix='/youtube',
    template_folder='templates',
)

from . import routes  # noqa: E402,F401