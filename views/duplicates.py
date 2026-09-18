"""Роуты дубликатов: перемещение одной группы / всех групп."""
from flask import request, jsonify

from duplicate_finder import move_group, move_all_groups


def register(app):

    @app.route('/move_duplicates', methods=['POST'])
    def move_duplicates():
        data = request.get_json()
        group_index = data.get('group_index')
        if group_index is None:
            return jsonify({'success': False, 'message': 'Group index required'}), 400
        try:
            group_index = int(group_index)
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid group index'}), 400

        success, msg = move_group(group_index, keep_best=True)
        if success:
            return jsonify({'success': True, 'message': msg})
        else:
            return jsonify({'success': False, 'message': msg}), 500

    @app.route('/move_all_duplicates', methods=['POST'])
    def move_all_duplicates():
        success, msg = move_all_groups(keep_best=True)
        if success:
            return jsonify({'success': True, 'message': msg})
        else:
            return jsonify({'success': False, 'message': msg}), 500