import os
import sys

from flask import Blueprint


def _templates_dir() -> str:
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(
            sys._MEIPASS, 'addon', 'photo_classifier', 'templates'
        )
    return os.path.join(os.path.abspath(os.path.dirname(__file__)), 'templates')


photo_classifier_bp = Blueprint(
    'photo_classifier',
    __name__,
    url_prefix='/pv',
    template_folder=_templates_dir(),
)


from . import routes  # noqa: E402,F401