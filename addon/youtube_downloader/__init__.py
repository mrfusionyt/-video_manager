import os
import sys

from flask import Blueprint


def _templates_dir() -> str:
    """
    В EXE шаблоны лежат в _MEIPASS/addon/youtube_downloader/templates.
    В dev — рядом с этим файлом, в addon/youtube_downloader/templates.
    """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(
            sys._MEIPASS, 'addon', 'youtube_downloader', 'templates'
        )
    return os.path.join(os.path.abspath(os.path.dirname(__file__)), 'templates')


youtube_bp = Blueprint(
    'youtube',
    __name__,
    url_prefix='/youtube',
    template_folder=_templates_dir(),
)

from . import routes  # noqa: E402,F401