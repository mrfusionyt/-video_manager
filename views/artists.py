"""
Все роуты артистов: список, CRUD, добавление/удаление видео, обложка.

ВАЖНО: этот файл называется artists.py и лежит в пакете views.
        Top-level модуль проекта тоже называется artists.py.
        Python 3 использует абсолютные импорты, поэтому `from artists import ...`
        внутри этого файла импортирует именно top-level модуль, а не сам себя.
"""
import os
from flask import (
    render_template, request, url_for, jsonify, abort, redirect,
)

from models import get_all_videos, get_video_by_id
from helpers.profiles import get_current_profile, profile_to_mode

# Top-level artists.py (не путать с views.artists)
from artists import (
    get_all_artists,
    get_artist_by_id,
    add_artist,
    delete_artist,
    rename_artist,
    set_artist_cover,
    get_artist_video_ids,
    get_all_assigned_video_ids,
    add_video_to_artist,
    remove_video_from_artist,
)


def register(app):

    @app.route('/artists')
    def artists_page():
        current_profile = get_current_profile()
        current_mode = profile_to_mode(current_profile)
        artists = get_all_artists(mode=current_mode)

        for a in artists:
            preview = None
            if a.get('cover_video_id'):
                v = get_video_by_id(a['cover_video_id'])
                if v and os.path.exists(v['filepath']):
                    preview = v
            if preview is None:
                vids = get_artist_video_ids(a['id'])
                for vid in vids:
                    v = get_video_by_id(vid)
                    if v and os.path.exists(v['filepath']):
                        preview = v
                        break
            a['preview'] = preview

        return render_template('artists.html', artists=artists)

    @app.route('/artists/add', methods=['POST'])
    def artists_add():
        current_profile = get_current_profile()
        current_mode = profile_to_mode(current_profile)
        name = request.form.get('name', '').strip()
        if name:
            add_artist(name, mode=current_mode)
        return redirect(url_for('artists_page', profile=current_profile))

    @app.route('/artists/<int:artist_id>/delete', methods=['POST'])
    def artists_delete(artist_id):
        delete_artist(artist_id)

        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True})

        current_profile = get_current_profile()
        return redirect(url_for('artists_page', profile=current_profile))

    @app.route('/artists/<int:artist_id>/edit', methods=['POST'])
    def artist_edit(artist_id):
        artist = get_artist_by_id(artist_id)
        if not artist:
            return jsonify({'success': False, 'error': 'Artist not found'}), 404

        data = request.get_json() or {}
        new_name = (data.get('name') or '').strip()
        cover_video_id = data.get('cover_video_id')

        if new_name and new_name != artist['name']:
            ok = rename_artist(artist_id, new_name)
            if not ok:
                return jsonify({
                    'success': False,
                    'error': 'Name already exists or is invalid'
                }), 400

        if cover_video_id is not None:
            try:
                cover_video_id = int(cover_video_id)
            except (TypeError, ValueError):
                return jsonify({'success': False, 'error': 'Invalid cover_video_id'}), 400

            video_ids = get_artist_video_ids(artist_id)
            if cover_video_id not in video_ids:
                return jsonify({
                    'success': False,
                    'error': 'Video is not attached to this artist'
                }), 400

            set_artist_cover(artist_id, cover_video_id)

        return jsonify({'success': True})

    @app.route('/artists/<int:artist_id>/rename', methods=['POST'])
    def artists_rename(artist_id):
        new_name = request.form.get('name', '').strip()
        if new_name:
            rename_artist(artist_id, new_name)
        current_profile = get_current_profile()
        return redirect(url_for('artist_view', artist_id=artist_id, profile=current_profile))

    @app.route('/artists/<int:artist_id>')
    def artist_view(artist_id):
        current_profile = get_current_profile()
        current_mode = profile_to_mode(current_profile)
        artist = get_artist_by_id(artist_id)
        if not artist:
            abort(404)

        # Видео текущего артиста (для основного грида)
        video_ids = get_artist_video_ids(artist_id)
        videos = []
        for vid in video_ids:
            v = get_video_by_id(vid)
            if v:
                videos.append(v)

        videos.sort(key=lambda x: x.get('added', ''), reverse=True)

        # Видео, доступные для добавления: всё, что НЕ привязано
        # ни к одному артисту этого профиля (включая текущего).
        all_videos = get_all_videos(mode=current_mode)
        assigned_ids = get_all_assigned_video_ids(mode=current_mode)
        available = [v for v in all_videos if v['id'] not in assigned_ids]

        return render_template('artist.html',
                               artist=artist,
                               videos=videos,
                               available_videos=available)

    @app.route('/artists/<int:artist_id>/videos')
    def artist_videos_json(artist_id):
        video_ids = get_artist_video_ids(artist_id)
        videos = []
        for vid in video_ids:
            v = get_video_by_id(vid)
            if v:
                videos.append({
                    'id': v['id'],
                    'filename': v['filename'],
                    'duration': v.get('duration', 0),
                })
        return jsonify({'videos': videos})

    @app.route('/artists/<int:artist_id>/add_video', methods=['POST'])
    def artist_add_video(artist_id):
        data = request.get_json() or {}
        video_id = data.get('video_id')
        if not video_id:
            return jsonify({'success': False, 'error': 'Video ID required'}), 400
        add_video_to_artist(artist_id, video_id)
        return jsonify({'success': True})

    @app.route('/artists/<int:artist_id>/remove_video', methods=['POST'])
    def artist_remove_video(artist_id):
        data = request.get_json() or {}
        video_id = data.get('video_id')
        if not video_id:
            return jsonify({'success': False, 'error': 'Video ID required'}), 400
        remove_video_from_artist(artist_id, video_id)
        return jsonify({'success': True})

    @app.route('/artists/<int:artist_id>/set_cover', methods=['POST'])
    def artist_set_cover(artist_id):
        data = request.get_json() or {}
        video_id = data.get('video_id')
        if not video_id:
            return jsonify({'success': False, 'error': 'Video ID required'}), 400
        set_artist_cover(artist_id, video_id)
        return jsonify({'success': True})