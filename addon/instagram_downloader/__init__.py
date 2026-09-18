import os
from flask import Blueprint

instagram_bp = Blueprint(
    'instagram',
    __name__,
    url_prefix='/instagram',
    template_folder='templates',
    static_folder='static',
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


# Импортируем маршруты после создания blueprint, чтобы не было циклического импорта
from . import routes  # noqa: E402,F401