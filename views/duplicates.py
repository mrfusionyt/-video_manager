"""Роуты дубликатов: сравнение, перемещение, blacklist."""
from flask import request, jsonify, render_template, abort, redirect, url_for

from duplicate_finder import (
    move_group, move_all_groups, move_selected, get_duplicate_groups,
    list_ignored_pairs, mark_group_ignored, mark_pair_by_ids,
    unmark_pair_by_paths, clear_ignored_pairs,
)
from helpers.profiles import get_current_profile


def register(app):

    @app.route('/compare/<int:group_index>')
    def compare_group(group_index):
        groups = get_duplicate_groups()
        if group_index < 0 or group_index >= len(groups):
            abort(404)
        group = groups[group_index]
        files = group.get('files', [])
        file_names = {v['id']: v['filename'] for v in files}
        return render_template(
            'compare.html',
            group=group,
            group_index=group_index,
            file_names=file_names,
            current_profile=get_current_profile(),
        )

    @app.route('/duplicates/blacklist')
    def duplicates_blacklist_page():
        pairs = list_ignored_pairs()
        return render_template(
            'blacklist.html',
            pairs=pairs,
            current_profile=get_current_profile(),
        )

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

    # ---------------- Blacklist ----------------
    @app.route('/ignore_group', methods=['POST'])
    def ignore_group():
        data = request.get_json() or {}
        video_ids = data.get('video_ids') or []
        note = data.get('note', '') or ''
        if not video_ids or len(video_ids) < 2:
            return jsonify({'success': False,
                            'message': 'Need >= 2 video_ids'}), 400
        ok, msg, count = mark_group_ignored(video_ids, note)
        if ok:
            return jsonify({'success': True, 'message': msg, 'count': count})
        return jsonify({'success': False, 'message': msg}), 400

    @app.route('/ignore_pair', methods=['POST'])
    def ignore_pair():
        data = request.get_json() or {}
        a_id = data.get('a_id')
        b_id = data.get('b_id')
        note = data.get('note', '') or ''
        if a_id is None or b_id is None:
            return jsonify({'success': False,
                            'message': 'a_id and b_id required'}), 400
        ok, msg = mark_pair_by_ids(a_id, b_id, note)
        if ok:
            return jsonify({'success': True, 'message': msg})
        return jsonify({'success': False, 'message': msg}), 400

    @app.route('/unignore_pair', methods=['POST'])
    def unignore_pair():
        data = request.get_json() or {}
        a = data.get('path_a')
        b = data.get('path_b')
        if not a or not b:
            return jsonify({'success': False,
                            'message': 'path_a and path_b required'}), 400
        unmark_pair_by_paths(a, b)
        return jsonify({'success': True})

    @app.route('/clear_ignored', methods=['POST'])
    def clear_ignored():
        clear_ignored_pairs()
        return jsonify({'success': True})