import os
import sys

from flask import Blueprint


def _addon_dir() -> str:
    """Folder of this addon in source and in a PyInstaller one-dir build."""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, 'addon', 'FileTransferServer')
    return os.path.abspath(os.path.dirname(__file__))


file_transfer_bp = Blueprint(
    'file_transfer',
    __name__,
    url_prefix='/transfer',
    template_folder=os.path.join(_addon_dir(), 'templates'),
    static_folder=os.path.join(_addon_dir(), 'static'),
    static_url_path='/static',
)


from . import routes  # noqa: E402,F401
