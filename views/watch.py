r"""
Страница просмотра.
  • /video/<id>   — потоковая отдача видео (конвертация на месте при необходимости)
  • /watch/<id>   — HTML-плеер для видео
  • /photo/<id>   — HTML-просмотрщик для фото (новая вкладка)
Несовместимые видео конвертируются на месте. Битые файлы отсеивает сканер.
"""
import os
import mimetypes
import math
import threading

from flask import (
    render_template, request, redirect, url_for, jsonify, abort, send_file,
)

from models import (
    get_all_videos,
    get_video_by_id,
    update_rating,
    rename_video,
    update_video_categories,
)
from config import ITEMS_PER_PAGE
from helpers.profiles import get_current_profile, profile_to_mode
from helpers.video_filter import process_videos
from converter import (
    needs_conversion,
    convert_video_in_place,
    update_db_after_conversion,
)


_CONVERT_LOCK = threading.Lock()


def register(app):

    # ================================================================
    #                    СЫРОЙ ПОТОК ФАЙЛА
    # ================================================================
    @app.route('/video/<int:video_id>')
    def video_file(video_id):
        video = get_video_by_id(video_id)
        if not video:
            abort(404)

        filepath = video['filepath']
        if not os.path.exists(filepath):
            abort(404)

        # Конвертация только для видео. Для фото — отдаём как есть.
        if video.get('media_type') != 'image':
            need, reason = needs_conversion(filepath, video.get('codec'))
            if need:
                with _CONVERT_LOCK:
                    video = get_video_by_id(video_id)
                    filepath = video['filepath']
                    need, reason = needs_conversion(filepath, video.get('codec'))
                    if need:
                        print(f"[watch] on-demand convert ({reason}): "
                              f"{video['filename']}")
                        ok, new_video, msg = convert_video_in_place(video)
                        if ok:
                            update_db_after_conversion(video_id, new_video)
                            filepath = new_video['filepath']
                        else:
                            print(f"[watch] convert FAILED, serving original: {msg}")
                            filepath = video['filepath']

        mimetype, _ = mimetypes.guess_type(filepath)
        if not mimetype:
            mimetype = 'video/mp4'

        response = send_file(
            filepath, mimetype=mimetype,
            as_attachment=False, conditional=True,
        )
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, HEAD, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Range'
        response.headers['Accept-Ranges'] = 'bytes'
        response.headers['Cache-Control'] = 'public, max-age=86400'
        return response

    # ================================================================
    #                       ИНФО О ФАЙЛЕ
    # ================================================================
    @app.route('/video/<int:video_id>/info')
    def video_info(video_id):
        video = get_video_by_id(video_id)
        if not video:
            return jsonify({'error': 'Video not found'}), 404
        data = {
            'id': video['id'], 'filename': video['filename'],
            'duration': video['duration'], 'size': video['size'],
            'added': video['added'], 'rating': video['rating'],
            'width': video['width'], 'height': video['height'],
            'fps': video['fps'], 'codec': video['codec'],
            'bitrate': video['bitrate'], 'orientation': video['orientation'],
            'library_name': video.get('library_name'),
            'mode': video['mode'],
            'media_type': video.get('media_type', 'video'),
            'categories': video.get('categories', []),
        }
        return jsonify(data)

    # ================================================================
    #                       СКАЧИВАНИЕ
    # ================================================================
    @app.route('/download/<int:video_id>')
    def download_video(video_id):
        video = get_video_by_id(video_id)
        if not video:
            abort(404)
        filepath = video['filepath']
        if not os.path.exists(filepath):
            abort(404)
        return send_file(filepath, as_attachment=True,
                         download_name=video['filename'])

    # ================================================================
    #                       VR-ПЛЕЕР
    # ================================================================
    @app.route('/vr/<int:video_id>')
    def vr_player(video_id):
        video = get_video_by_id(video_id)
        if not video:
            abort(404, "Video not found")
        return render_template('vr.html', video=video)

    # ================================================================
    #                       РЕЙТИНГ / ПЕРЕИМЕНОВАНИЕ
    # ================================================================
    @app.route('/rate/<int:video_id>', methods=['POST'])
    def rate_video(video_id):
        data = request.get_json()
        rating = data.get('rating', 0)
        if rating < 0 or rating > 5:
            return jsonify({'error': 'Rating must be between 0 and 5'}), 400
        update_rating(video_id, rating)
        return jsonify({'success': True, 'rating': rating})

    @app.route('/rename/<int:video_id>', methods=['POST'])
    def rename_video_route(video_id):
        data = request.get_json()
        new_name = data.get('new_name', '').strip()
        if not new_name:
            return jsonify({'error': 'New name is required'}), 400
        video = get_video_by_id(video_id)
        if not video:
            return jsonify({'error': 'Video not found'}), 404
        old_ext = os.path.splitext(video['filename'])[1]
        if not new_name.endswith(old_ext):
            new_name += old_ext
        success = rename_video(video_id, new_name)
        if success:
            return jsonify({'success': True, 'new_name': new_name})
        else:
            return jsonify({'error': 'Rename failed'}), 500

    # ================================================================
    #                       КАТЕГОРИИ ВИДЕО/ФОТО
    # ================================================================
    @app.route('/edit_video/<int:video_id>', methods=['GET', 'POST'])
    def edit_video(video_id):
        video = get_video_by_id(video_id)
        if not video:
            abort(404)
        if request.method == 'POST':
            category_ids = request.form.getlist('categories')
            category_ids = [int(x) for x in category_ids if x]
            update_video_categories(video_id, category_ids)
            # Возвращаемся туда, откуда пришли: фото → /photo/<id>, видео → /watch/<id>
            if video.get('media_type') == 'image':
                return redirect(url_for(
                    'photo_view', photo_id=video_id,
                    profile=request.args.get('profile', 'female')
                ))
            return redirect(url_for(
                'watch', video_id=video_id,
                profile=request.args.get('profile', 'female')
            ))
        return render_template('edit_video.html', video=video)

    @app.route('/video/<int:video_id>/set_categories', methods=['POST'])
    def set_video_categories(video_id):
        video = get_video_by_id(video_id)
        if not video:
            return jsonify({'success': False, 'error': 'Video not found'}), 404
        data = request.get_json() or {}
        category_ids = data.get('category_ids', [])
        if not isinstance(category_ids, list):
            return jsonify({'success': False,
                            'error': 'category_ids must be a list'}), 400
        try:
            category_ids = [int(x) for x in category_ids]
        except (ValueError, TypeError):
            return jsonify({'success': False,
                            'error': 'Invalid category ids'}), 400
        update_video_categories(video_id, category_ids)
        return jsonify({'success': True})

    # ================================================================
    #                       WATCH (видео)
    # ================================================================
    @app.route('/watch/<int:video_id>')
    def watch(video_id):
        current_profile = get_current_profile()
        current_mode = profile_to_mode(current_profile)
        video = get_video_by_id(video_id)
        if not video:
            abort(404)

        # Если случайно открыли фото через /watch — редирект на /photo
        if video.get('media_type') == 'image':
            return redirect(url_for('photo_view', photo_id=video_id,
                                    profile=current_profile))

        sort = request.args.get('sort', 'date')
        search = request.args.get('search', '').strip()
        folder = request.args.get('folder', '')
        category_ids = request.args.getlist('category', type=int)

        if folder:
            folder = os.path.normpath(folder)

        search_lc = search.lower()
        if search_lc:
            all_videos = get_all_videos(mode=None, media_type='video')
        else:
            all_videos = get_all_videos(mode=current_mode, media_type='video')

        if folder:
            all_videos = [v for v in all_videos if v.get('folder') == folder]
        if search_lc:
            all_videos = [v for v in all_videos
                          if search_lc in (v.get('filename') or '').lower()]

        all_videos = process_videos(all_videos, category_ids, sort)

        feed_ids = [v['id'] for v in all_videos]
        if video_id not in feed_ids:
            all_videos = [video] + all_videos
            feed_ids = [v['id'] for v in all_videos]

        video_categories = video.get('categories', [])
        video_cat_ids = [cat['id'] for cat in video_categories]

        recommendations = []
        for v in all_videos:
            if v['id'] == video_id:
                continue
            v_cat_ids = [c['id'] for c in v.get('categories', [])]
            common = set(video_cat_ids) & set(v_cat_ids)
            if common:
                recommendations.append(v)

        total_recs = len(recommendations)
        total_rec_pages = (math.ceil(total_recs / ITEMS_PER_PAGE)
                           if total_recs > 0 else 1)
        rec_page = request.args.get('rec_page', 1, type=int)
        if rec_page < 1:
            rec_page = 1
        if rec_page > total_rec_pages:
            rec_page = total_rec_pages
        start = (rec_page - 1) * ITEMS_PER_PAGE
        end = start + ITEMS_PER_PAGE
        page_recs = recommendations[start:end]

        try:
            feed_index = feed_ids.index(video_id)
        except ValueError:
            feed_ids = [video_id] + feed_ids
            feed_index = 0

        return render_template(
            'watch.html',
            video=video,
            recommendations=page_recs,
            rec_page=rec_page,
            total_rec_pages=total_rec_pages,
            total_recs=total_recs,
            feed_ids=feed_ids,
            feed_index=feed_index,
            sort=sort,
            search=search,
            folder=folder,
            category_ids=category_ids,
        )

    # ================================================================
    #                       PHOTO VIEW (просмотрщик фото)
    # ================================================================
    @app.route('/photo/<int:photo_id>')
    def photo_view(photo_id):
        current_profile = get_current_profile()
        current_mode = profile_to_mode(current_profile)
        photo = get_video_by_id(photo_id)
        if not photo:
            abort(404)

        # Если открыли видео через /photo — редирект на /watch
        if photo.get('media_type') != 'image':
            return redirect(url_for('watch', video_id=photo_id,
                                    profile=current_profile))

        sort = request.args.get('sort', 'date')
        search = request.args.get('search', '').strip()
        folder = request.args.get('folder', '')
        category_ids = request.args.getlist('category', type=int)

        if folder:
            folder = os.path.normpath(folder)

        search_lc = search.lower()
        if search_lc:
            all_photos = get_all_videos(mode=None, media_type='image')
        else:
            all_photos = get_all_videos(mode=current_mode, media_type='image')

        if folder:
            all_photos = [v for v in all_photos if v.get('folder') == folder]
        if search_lc:
            all_photos = [v for v in all_photos
                          if search_lc in (v.get('filename') or '').lower()]

        all_photos = process_videos(all_photos, category_ids, sort)

        feed_ids = [p['id'] for p in all_photos]
        if photo_id not in feed_ids:
            all_photos = [photo] + all_photos
            feed_ids = [p['id'] for p in all_photos]

        try:
            feed_index = feed_ids.index(photo_id)
        except ValueError:
            feed_ids = [photo_id] + feed_ids
            feed_index = 0

        prev_id = feed_ids[feed_index - 1] if feed_index > 0 else None
        next_id = (feed_ids[feed_index + 1]
                   if feed_index < len(feed_ids) - 1 else None)

        return render_template(
            'photo.html',
            photo=photo,
            prev_id=prev_id,
            next_id=next_id,
            feed_ids=feed_ids,
            feed_index=feed_index,
            total_photos=len(feed_ids),
            sort=sort,
            search=search,
            folder=folder,
            category_ids=category_ids,
        )