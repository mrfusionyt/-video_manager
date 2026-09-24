"""
Роут страницы видеоредактора /editor/<video_id>.

Позволяет:
  • Смотреть видео.
  • Выбирать START и END (обрезка спереди и сзади).
  • Точный покадровый выбор через знание FPS.
  • Высокая точность (high precision) — покадровое перемещение.
  • Два режима сохранения: fast (stream copy) и accurate (re-encode).

Fast     — `-c copy`, мгновенно, но обрезка прилипает к ближайшим
           keyframe (может быть неточной на ±несколько секунд).
Accurate — `libx264 + aac`, покадровая точность, но долго и теряет качество
           (crf=18 — минимально заметно).
"""
import os
import json
import shutil
import subprocess
from datetime import datetime

from flask import render_template, request, jsonify, abort

from models import get_video_by_id, get_db_connection
from helpers.profiles import get_current_profile


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG_PATH = os.path.join(BASE_DIR, '_dop', 'ffmpeg.exe')
FFPROBE_PATH = os.path.join(BASE_DIR, '_dop', 'ffprobe.exe')
if not os.path.exists(FFMPEG_PATH):
    FFMPEG_PATH = shutil.which('ffmpeg') or 'ffmpeg'
if not os.path.exists(FFPROBE_PATH):
    FFPROBE_PATH = shutil.which('ffprobe') or 'ffprobe'


def _subprocess_kwargs():
    return dict(capture_output=True, encoding='utf-8', errors='replace')


def get_video_fps(filepath):
    """Возвращает FPS видео (float). Fallback — 30.0."""
    try:
        r = subprocess.run(
            [FFPROBE_PATH, '-v', 'error',
             '-select_streams', 'v:0',
             '-show_entries', 'stream=r_frame_rate,avg_frame_rate',
             '-of', 'json', filepath],
            timeout=15, **_subprocess_kwargs()
        )
        if r.returncode != 0 or not r.stdout:
            return 30.0
        data = json.loads(r.stdout)
        streams = data.get('streams', [])
        if not streams:
            return 30.0
        s = streams[0]
        fps_str = s.get('r_frame_rate') or s.get('avg_frame_rate') or '30/1'
        if '/' in fps_str:
            num, den = fps_str.split('/')
            n, d = float(num), float(den)
            if d:
                return n / d
        return float(fps_str)
    except Exception:
        return 30.0


def get_keyframes(filepath):
    """Возвращает список timestamp-ов ключевых кадров (секунды)."""
    keyframes = []
    try:
        r = subprocess.run(
            [FFPROBE_PATH, '-v', 'error',
             '-select_streams', 'v:0',
             '-skip_frame', 'nokey',
             '-show_entries', 'frame=pkt_pts_time,pts_time',
             '-of', 'json', filepath],
            timeout=120, **_subprocess_kwargs()
        )
        if r.returncode == 0 and r.stdout:
            data = json.loads(r.stdout)
            for f in data.get('frames', []):
                t = f.get('pkt_pts_time') or f.get('pts_time')
                if t is not None:
                    try:
                        keyframes.append(round(float(t), 3))
                    except (ValueError, TypeError):
                        pass
    except Exception:
        pass
    return keyframes


def register(app):

    @app.route('/editor/<int:video_id>')
    def editor_page(video_id):
        video = get_video_by_id(video_id)
        if not video:
            abort(404)
        current_profile = get_current_profile()

        fps = 30.0
        try:
            fps = get_video_fps(video['filepath'])
        except Exception:
            pass

        return render_template(
            'editor.html',
            video=video,
            fps=fps,
            current_profile=current_profile,
        )

    @app.route('/editor/<int:video_id>/keyframes')
    def editor_keyframes(video_id):
        video = get_video_by_id(video_id)
        if not video:
            return jsonify({'error': 'Video not found'}), 404
        kfs = get_keyframes(video['filepath'])
        return jsonify({'keyframes': kfs})

    @app.route('/editor/<int:video_id>/save', methods=['POST'])
    def editor_save(video_id):
        video = get_video_by_id(video_id)
        if not video:
            return jsonify({'error': 'Video not found'}), 404

        data = request.get_json() or {}

        try:
            start = float(data.get('start', 0))
            end = float(data.get('end', 0))
        except (TypeError, ValueError):
            return jsonify({'error': 'Invalid start/end'}), 400

        mode = data.get('mode', 'fast')
        if mode not in ('fast', 'accurate'):
            mode = 'fast'
        overwrite = bool(data.get('overwrite', True))

        src = video['filepath']
        if not os.path.exists(src):
            return jsonify({'error': 'Source file not found'}), 404

        if start < 0:
            start = 0
        if end <= start:
            return jsonify({'error': 'End must be greater than start'}), 400

        folder = os.path.dirname(src)
        base_name = os.path.splitext(video['filename'])[0]
        ext = os.path.splitext(video['filename'])[1].lower() or '.mp4'

        if overwrite:
            tmp_name = f'.__trim_{int(datetime.now().timestamp())}_{base_name}{ext}'
            dst = os.path.join(folder, tmp_name)
        else:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            new_name = f'{base_name}_trim_{ts}{ext}'
            dst = os.path.join(folder, new_name)

        duration = end - start

        if mode == 'fast':
            cmd = [
                FFMPEG_PATH, '-y',
                '-ss', f'{start:.6f}',
                '-i', src,
                '-t', f'{duration:.6f}',
                '-c', 'copy',
                '-avoid_negative_ts', 'make_zero',
                '-movflags', '+faststart',
                dst,
            ]
        else:
            cmd = [
                FFMPEG_PATH, '-y',
                '-ss', f'{start:.6f}',
                '-i', src,
                '-t', f'{duration:.6f}',
                '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
                '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '192k',
                '-movflags', '+faststart',
                dst,
            ]

        try:
            r = subprocess.run(cmd, timeout=7200, **_subprocess_kwargs())
        except subprocess.TimeoutExpired:
            return jsonify({'error': 'FFmpeg timeout'}), 500
        except Exception as e:
            return jsonify({'error': f'FFmpeg error: {e}'}), 500

        if r.returncode != 0:
            try:
                if os.path.exists(dst):
                    os.remove(dst)
            except Exception:
                pass
            tail = (r.stderr or '')[-400:]
            return jsonify({'error': f'FFmpeg failed: {tail}'}), 500

        if not os.path.exists(dst) or os.path.getsize(dst) == 0:
            return jsonify({'error': 'Empty output'}), 500

        if overwrite:
            try:
                os.replace(dst, src)
            except Exception as e:
                try:
                    os.remove(dst)
                except Exception:
                    pass
                return jsonify({'error': f'Cannot replace source: {e}'}), 500
            new_path = src
            new_name = video['filename']
        else:
            new_path = dst
            new_name = os.path.basename(dst)

        # Обновляем БД
        try:
            from scanner import get_media_info
            info = get_media_info(new_path) or {}
        except Exception as e:
            print(f"[editor] get_media_info failed: {e}")
            info = {}

        new_size = os.path.getsize(new_path)

        conn = get_db_connection()
        try:
            c = conn.cursor()
            c.execute('''
                UPDATE videos SET
                    filename = ?, filepath = ?, size = ?,
                    duration = ?, width = ?, height = ?,
                    codec = ?, bitrate = ?, fps = ?, orientation = ?
                WHERE id = ?
            ''', (
                new_name, new_path, new_size,
                info.get('duration', 0),
                info.get('width', 0),
                info.get('height', 0),
                info.get('codec', ''),
                info.get('bitrate', 0),
                info.get('fps', 0.0),
                info.get('orientation', 'horizontal'),
                video_id,
            ))
            conn.commit()
        finally:
            conn.close()

        return jsonify({
            'success': True,
            'filename': new_name,
            'filepath': new_path,
            'duration': info.get('duration', 0),
            'size': new_size,
            'mode': mode,
        })