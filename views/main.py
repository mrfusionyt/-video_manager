"""
Главная страница, пагинация, folders, thumbnails.
"""
import os
import math
from flask import (
    render_template, request, redirect, url_for, jsonify,
    send_from_directory, Response, send_file,
)

from models import (
    get_all_videos,
    get_video_by_id,
    add_library,
    get_folders,
    get_videos_by_folder,
    query_videos_paged,
)
from scanner import scan_libraries
from config import (
    app_config,
    save_config,
    ITEMS_PER_PAGE,
    load_folder_covers,
    save_folder_covers,
)
from helpers.profiles import get_current_profile, profile_to_mode
from helpers.video_filter import process_videos
from db.thumbnails import (
    has_thumbnail,
    get_thumbnail_path,
    generate_thumbnail,
)

# Top-level artists.py — для назначения артистов из папок
from artists import (
    get_all_artists,
    get_all_assigned_video_ids,
    add_video_to_artist,
    add_artist,
)


def register(app):

    @app.route('/')
    def index():
        current_profile = get_current_profile()
        current_mode = profile_to_mode(current_profile)
        last_folder = app_config.get('last_folder', '')
        category_ids = request.args.getlist('category', type=int)
        page = request.args.get('page', 1, type=int)
        sort = request.args.get('sort', 'date')
        search = request.args.get('search', '').strip()
        folder = request.args.get('folder', '')
        if page < 1:
            page = 1

        if folder:
            folder = os.path.normpath(folder)

        selected_category = category_ids[0] if len(category_ids) == 1 else None

        # ---------- SQL path ----------
        sql_result = None
        if not category_ids:
            sql_result = query_videos_paged(
                mode=current_mode,
                sort=sort,
                search=search,
                folder=folder,
                category_ids=None,
                page=page,
                per_page=ITEMS_PER_PAGE,
            )

        if sql_result is not None:
            page_videos, total_videos = sql_result
            if sort in ('top10', 'top50', 'top100'):
                total_pages = 1
                page = 1
            else:
                total_pages = math.ceil(total_videos / ITEMS_PER_PAGE) if total_videos > 0 else 1
            return render_template('index.html',
                                   videos=page_videos,
                                   selected_category=selected_category,
                                   selected_categories=category_ids,
                                   last_folder=last_folder,
                                   page=page,
                                   total_pages=total_pages,
                                   total_videos=total_videos,
                                   sort=sort,
                                   search=search,
                                   folder=folder)

        # ---------- Python fallback ----------
        search_lc = search.lower()
        if search_lc:
            videos = get_all_videos(mode=None)
        else:
            videos = get_all_videos(mode=current_mode)

        if folder:
            videos = [v for v in videos if v.get('folder') == folder]

        if search_lc:
            videos = [v for v in videos if search_lc in v['filename'].lower()]

        videos = process_videos(videos, category_ids, sort)

        total_videos = len(videos)
        if sort in ('top10', 'top50', 'top100'):
            total_pages = 1
            page = 1
            page_videos = videos
        else:
            total_pages = math.ceil(total_videos / ITEMS_PER_PAGE) if total_videos > 0 else 1
            start = (page - 1) * ITEMS_PER_PAGE
            end = start + ITEMS_PER_PAGE
            page_videos = videos[start:end]

        return render_template('index.html',
                               videos=page_videos,
                               selected_category=selected_category,
                               selected_categories=category_ids,
                               last_folder=last_folder,
                               page=page,
                               total_pages=total_pages,
                               total_videos=total_videos,
                               sort=sort,
                               search=search,
                               folder=folder)

    @app.route('/load_more')
    def load_more():
        current_profile = get_current_profile()
        current_mode = profile_to_mode(current_profile)
        category_ids = request.args.getlist('category', type=int)
        page = request.args.get('page', 1, type=int)
        sort = request.args.get('sort', 'date')
        search = request.args.get('search', '').strip()
        folder = request.args.get('folder', '')
        if page < 1:
            page = 1
        if folder:
            folder = os.path.normpath(folder)

        if not category_ids:
            sql_result = query_videos_paged(
                mode=current_mode,
                sort=sort,
                search=search,
                folder=folder,
                category_ids=None,
                page=page,
                per_page=ITEMS_PER_PAGE,
            )
            if sql_result is not None:
                page_videos, total_videos = sql_result
                if sort in ('top10', 'top50', 'top100'):
                    return jsonify({'html': '', 'page': 1, 'total_pages': 1, 'has_more': False})
                total_pages = math.ceil(total_videos / ITEMS_PER_PAGE) if total_videos > 0 else 1
                html = render_template('_video_cards.html', videos=page_videos)
                return jsonify({
                    'html': html,
                    'page': page,
                    'total_pages': total_pages,
                    'has_more': page < total_pages
                })

        search_lc = search.lower()
        videos = get_all_videos(mode=None) if search_lc else get_all_videos(mode=current_mode)
        if folder:
            videos = [v for v in videos if v.get('folder') == folder]
        if search_lc:
            videos = [v for v in videos if search_lc in v['filename'].lower()]
        videos = process_videos(videos, category_ids, sort)
        total_videos = len(videos)
        if sort in ('top10', 'top50', 'top100'):
            return jsonify({'html': '', 'page': 1, 'total_pages': 1, 'has_more': False})
        total_pages = math.ceil(total_videos / ITEMS_PER_PAGE) if total_videos > 0 else 1
        start = (page - 1) * ITEMS_PER_PAGE
        end = start + ITEMS_PER_PAGE
        page_videos = videos[start:end]
        html = render_template('_video_cards.html', videos=page_videos)
        return jsonify({
            'html': html,
            'page': page,
            'total_pages': total_pages,
            'has_more': page < total_pages
        })

    @app.route('/image/<filename>')
    def serve_image(filename):
        return send_from_directory('image', filename)

    # ---------- Превью ----------
    def _thumbnail_placeholder():
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 270">'
            '<rect width="480" height="270" fill="#2a2a2a"/>'
            '<circle cx="240" cy="135" r="34" fill="#1e1e1e" stroke="#444" stroke-width="2"/>'
            '<polygon points="228,118 228,152 258,135" fill="#666"/>'
            '</svg>'
        )
        resp = Response(svg, mimetype='image/svg+xml')
        resp.headers['Cache-Control'] = 'public, max-age=604800, immutable'
        return resp

    @app.route('/thumbnail/<int:video_id>')
    def thumbnail(video_id):
        video = get_video_by_id(video_id)
        if not video:
            return _thumbnail_placeholder()
        if has_thumbnail(video_id):
            path = get_thumbnail_path(video_id)
        else:
            ok = generate_thumbnail(video_id, video['filepath'], video.get('duration', 0))
            if not ok:
                return _thumbnail_placeholder()
            path = get_thumbnail_path(video_id)
        try:
            resp = send_file(path, mimetype='image/jpeg', conditional=True)
            resp.headers['Cache-Control'] = 'public, max-age=604800, immutable'
            return resp
        except Exception:
            return _thumbnail_placeholder()

    # ---------- Folders ----------
    @app.route('/folders')
    def folders():
        current_profile = get_current_profile()
        current_mode = profile_to_mode(current_profile)
        library_id = request.args.get('library', type=int)
        folders_list = get_folders(library_id=library_id, mode=current_mode)
        covers = load_folder_covers()

        for folder in folders_list:
            cover_video_id = covers.get(folder['path'])
            if cover_video_id:
                video = get_video_by_id(cover_video_id)
                if video and os.path.exists(video['filepath']):
                    folder['preview'] = video
                    continue
            videos = get_videos_by_folder(folder['path'], limit=1, mode=current_mode)
            folder['preview'] = videos[0] if videos else None

        # Список артистов текущего профиля — для модалки «Назначить артиста»
        artists = get_all_artists(mode=current_mode)

        return render_template('folders.html',
                               folders=folders_list,
                               selected_library=library_id,
                               artists=artists)

    @app.route('/folder_videos')
    def folder_videos():
        folder_path = request.args.get('path', '')
        if not folder_path:
            return jsonify({'error': 'Folder path required'}), 400

        folder_path = os.path.normpath(folder_path)
        videos = get_videos_by_folder(folder_path, limit=None, mode=None)
        covers = load_folder_covers()
        current_cover_id = covers.get(folder_path)

        videos_list = [{'id': v['id'], 'filename': v['filename']} for v in videos]
        return jsonify({
            'videos': videos_list,
            'current_cover': current_cover_id
        })

    @app.route('/set_folder_cover', methods=['POST'])
    def set_folder_cover():
        data = request.get_json()
        folder_path = data.get('folder_path')
        video_id = data.get('video_id')
        if not folder_path or not video_id:
            return jsonify({'success': False, 'error': 'Missing parameters'}), 400
        covers = load_folder_covers()
        covers[folder_path] = video_id
        save_folder_covers(covers)
        return jsonify({'success': True})

    @app.route('/add_library_from_folder', methods=['POST'])
    def add_library_from_folder():
        current_profile = get_current_profile()
        current_mode = profile_to_mode(current_profile)
        data = request.get_json()
        folder_path = data.get('folder_path')
        if not folder_path:
            return jsonify({'success': False, 'error': 'Folder path required'}), 400
        try:
            add_library(folder_path, mode=current_mode)
            scan_libraries()
            return jsonify({'success': True, 'message': 'Library added and scanned'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/set_folder', methods=['POST'])
    def set_folder():
        folder = request.form.get('folder')
        if folder and os.path.isdir(folder):
            app_config['last_folder'] = folder
            save_config(app_config)
        return redirect(url_for('index'))

    # =============================================================
    #        НАЗНАЧЕНИЕ АРТИСТА ИЗ ПАПКИ
    # =============================================================
    @app.route('/folders/assign_artist', methods=['POST'])
    def folders_assign_artist():
        """
        Привязывает все видео папки к указанным артистам.
        Ожидает JSON:
            {
              folder_path: "...",
              artist_ids: [1, 2, 3],
              only_unassigned: true|false
            }
        """
        data = request.get_json() or {}
        folder_path = data.get('folder_path')
        artist_ids = data.get('artist_ids') or []
        only_unassigned = bool(data.get('only_unassigned', True))

        if not folder_path:
            return jsonify({'success': False, 'error': 'folder_path required'}), 400
        if not artist_ids:
            return jsonify({'success': False, 'error': 'artist_ids required'}), 400

        try:
            artist_ids = [int(a) for a in artist_ids]
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'invalid artist_ids'}), 400

        current_profile = get_current_profile()
        current_mode = profile_to_mode(current_profile)

        videos = get_videos_by_folder(folder_path, limit=None, mode=current_mode)
        if not videos:
            return jsonify({'success': True, 'assigned': 0, 'ops': 0})

        if only_unassigned:
            assigned_ids = get_all_assigned_video_ids(mode=current_mode)
            videos = [v for v in videos if v['id'] not in assigned_ids]

        ops = 0
        for v in videos:
            for aid in artist_ids:
                try:
                    if add_video_to_artist(aid, v['id']):
                        ops += 1
                except Exception as e:
                    print(f"[folders_assign_artist] error: {e}")

        return jsonify({'success': True, 'assigned': len(videos), 'ops': ops})

    @app.route('/folders/create_artist', methods=['POST'])
    def folders_create_artist():
        """
        Создаёт артиста в текущем профиле.
        Ожидает JSON: { name: "..." }
        """
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'success': False, 'error': 'name required'}), 400

        current_profile = get_current_profile()
        current_mode = profile_to_mode(current_profile)

        artist_id = add_artist(name, mode=current_mode)
        if not artist_id:
            return jsonify({'success': False, 'error': 'Cannot create artist'}), 400

        return jsonify({'success': True, 'artist_id': artist_id, 'name': name})