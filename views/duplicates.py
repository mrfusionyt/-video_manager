"""Роуты дубликатов: перемещение выбранных / одной группы / всех групп."""
from flask import request, jsonify

from duplicate_finder import move_group, move_all_groups, move_selected


def register(app):

    @app.route('/move_selected_duplicates', methods=['POST'])
    def move_selected_duplicates():
        data = request.get_json() or {}
        video_ids = data.get('video_ids') or []
        if not video_ids:
            return jsonify({'success': False,
                            'message': 'No video_ids provided'}), 400
        ok, msg = move_selected(video_ids)
        return jsonify({'success': ok, 'message': msg}), (200 if ok else 500)

    @app.route('/move_duplicates', methods=['POST'])
    def move_duplicates():
        data = request.get_json() or {}
        group_index = data.get('group_index')
        if group_index is None:
            return jsonify({'success': False,
                            'message': 'Group index required'}), 400
        try:
            group_index = int(group_index)
        except (TypeError, ValueError):
            return jsonify({'success': False,
                            'message': 'Invalid group index'}), 400
        ok, msg = move_group(group_index, keep_best=True)
        return jsonify({'success': ok, 'message': msg}), (200 if ok else 500)

    @app.route('/move_all_duplicates', methods=['POST'])
    def move_all_duplicates():
        ok, msg = move_all_groups(keep_best=True)
        return jsonify({'success': ok, 'message': msg}), (200 if ok else 500)