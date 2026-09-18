import os
import sys

from flask import Blueprint


def _templates_dir() -> str:
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(
            sys._MEIPASS, 'addon', 'instagram_downloader', 'templates'
        )
    return os.path.join(os.path.abspath(os.path.dirname(__file__)), 'templates')


def _static_dir() -> str | None:
    if hasattr(sys, '_MEIPASS'):
        p = os.path.join(sys._MEIPASS, 'addon', 'instagram_downloader', 'static')
    else:
        p = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static')
    return p if os.path.isdir(p) else None


instagram_bp = Blueprint(
    'instagram',
    __name__,
    url_prefix='/instagram',
    template_folder=_templates_dir(),
    static_folder=_static_dir(),
)

_post_download_hook = None


def set_post_download_hook(fn):
    """Регистрирует функцию, вызываемую после успешной загрузки."""
    global _post_download_hook
    _post_download_hook = fn


def run_post_download_hook():
    if _post_download_hook:
        try:
            _post_download_hook()
        except Exception as e:
            print(f"[instagram] post-download hook error: {e}")


from . import routes  # noqa: E402,F401