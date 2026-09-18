"""
Все роуты плейлистов: CRUD, добавление/удаление видео, обложка.
"""
import math
from flask import (
    render_template, request, url_for, jsonify, abort,
)

from models import (
    get_playlists,
    count_playlists,
    create_playlist,
    delete_playlist,
    add_video_to_playlist,
    remove_video_from_playlist,
    is_video_in_playlist,
    get_playlist_by_id,
    rename_playlist,
    get_playlist_videos,
    set_playlist_cover,
)
from helpers.profiles import get_current_profile, profile_to_mode


def register(app):

    @app.route('/playlists')
    def get_playlists_json():
        current_profile = get_current_profile()
        current_mode = profile_to_mode(current_profile)
        page = request.args.get('page', 1, type=int)
        limit = 10
        offset = (page - 1) * limit

        playlists = get_playlists(mode=current_mode, limit=limit, offset=offset)
        total = count_playlists(mode=current_mode)

        video_id = request.args.get('video_id', type=int)
        for pl in playlists:
            pl['video_in_playlist'] = is_video_in_playlist(pl['id'], video_id) if video_id else False

        return jsonify({
            'playlists': playlists,
            'total': total,
            'page': page,
            'limit': limit,
            'has_more': total > page * limit
        })

    @app.route('/playlist/create', methods=['POST'])
    def create_playlist_route():
        current_profile = get_current_profile()
        current_mode = profile_to_mode(current_profile)
        data = request.get_json()
        name = data.get('name', '').strip()
        if not name:
            return jsonify({'success': False, 'error': 'Name is required'}), 400

        playlist_id = create_playlist(name, mode=current_mode)
        if playlist_id:
            return jsonify({'success': True, 'playlist_id': playlist_id, 'name': name})
        else:
            return jsonify({'success': False, 'error': 'Playlist with this name already exists'}), 400

    @app.route('/playlist/<int:playlist_id>/rename', methods=['POST'])
    def rename_playlist_route(playlist_id):
        data = request.get_json()
        new_name = data.get('name', '').strip()
        if not new_name:
            return jsonify({'success': False, 'error': 'Name is required'}), 400
        success = rename_playlist(playlist_id, new_name)
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Name already exists or other error'}), 400

    @app.route('/playlist/<int:playlist_id>/delete', methods=['POST'])
    def delete_playlist_route(playlist_id):
        delete_playlist(playlist_id)
        return jsonify({'success': True})

    @app.route('/playlist/<int:playlist_id>/add', methods=['POST'])
    def add_video_to_playlist_route(playlist_id):
        data = request.get_json()
        video_id = data.get('video_id')
        if not video_id:
            return jsonify({'success': False, 'error': 'Video ID required'}), 400

        success = add_video_to_playlist(playlist_id, video_id)
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Video already in playlist'}), 400

    @app.route('/playlist/<int:playlist_id>/remove', methods=['POST'])
    def remove_video_from_playlist_route(playlist_id):
        data = request.get_json()
        video_id = data.get('video_id')
        if not video_id:
            return jsonify({'success': False, 'error': 'Video ID required'}), 400

        remove_video_from_playlist(playlist_id, video_id)
        return jsonify({'success': True})

    @app.route('/playlist/<int:playlist_id>/videos')
    def get_playlist_videos_route(playlist_id):
        videos = get_playlist_videos(playlist_id)
        return jsonify({'videos': videos})

    @app.route('/playlist/<int:playlist_id>/set_cover', methods=['POST'])
    def set_playlist_cover_route(playlist_id):
        data = request.get_json()
        video_id = data.get('video_id')
        if not video_id:
            return jsonify({'success': False, 'error': 'Video ID required'}), 400
        if not is_video_in_playlist(playlist_id, video_id):
            return jsonify({'success': False, 'error': 'Video not in playlist'}), 400
        set_playlist_cover(playlist_id, video_id)
        return jsonify({'success': True})

    @app.route('/playlist/<int:playlist_id>')
    def view_playlist(playlist_id):
        current_profile = get_current_profile()
        current_mode = profile_to_mode(current_profile)
        playlist = get_playlist_by_id(playlist_id)
        if not playlist:
            abort(404)
        all_videos = get_playlist_videos(playlist_id)
        page = request.args.get('page', 1, type=int)
        per_page = 15
        total = len(all_videos)
        total_pages = math.ceil(total / per_page) if total > 0 else 1
        if page < 1:
            page = 1
        if page > total_pages:
            page = total_pages
        start = (page - 1) * per_page
        end = start + per_page
        page_videos = all_videos[start:end]
        return render_template('playlist.html',
                               playlist=playlist,
                               videos=page_videos,
                               page=page,
                               total_pages=total_pages,
                               total_videos=total,
                               current_profile=current_profile)