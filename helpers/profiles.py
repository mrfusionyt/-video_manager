"""Определение текущего профиля (female / transgender / all) из request."""
from flask import request


def get_current_profile():
    """
    Возвращает 'female' | 'transgender' | 'all'.
    - Новое значение: 'female'
    - Алиас 'classic' — превращается в 'female'
    """
    profile = request.args.get('profile', 'female')
    if profile == 'classic':
        profile = 'female'
    if profile not in ('female', 'transgender', 'all'):
        profile = 'female'
    return profile


def profile_to_mode(profile):
    if profile == 'all':
        return None
    return 1 if profile == 'female' else 2