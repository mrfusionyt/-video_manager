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

Про overwrite на Windows:
  Пока браузер стримит исходное видео через /video/<id> (Flask send_file
  держит файл открытым), os.replace падает с WinError 5. Поэтому:
    1) JS перед save() выгружает <video> из плеера;
    2) _safe_replace() ретраит с задержкой;
    3) если совсем не выходит — rename-swap (переименовать оригинал в .bak,
       поставить новый файл, удалить .bak);
    4) если и это не сработало — сохраняем как <name>_trim_<ts>, чтобы
       пользователь не потерял результат.
"""
import os
import json
import time
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


def _safe_replace(src, dst, retries=30, delay=0.5):
    """
    Атомарно заменяет dst на src.

    На Windows dst может быть залочен (браузер ещё стримит исходник через
    Flask send_file, либо сторонний процесс держит файл). Ретраим, потом
    делаем rename-swap: переименовываем оригинал в .bak, ставим новый
    файл, удаляем .bak.

    Возвращает (True, None) при успехе, (False, error_str) при неудаче.
    """
    last_err = None

    # --- Фаза 1: обычный os.replace с ретраями ---
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return True, None
        except (PermissionError, OSError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(delay)

    print(f"[editor] os.replace failed after {retries} tries: {last_err}")

    # --- Фаза 2: rename-swap ---
    backup = dst + f'.__bak_{int(time.time() * 1000)}'
    try:
        os.rename(dst, backup)
    except Exception as e:
        return False, (
            f'locked after {retries} tries ({last_err}); '
            f'rename-swap also failed: {e}'
        )

    try:
        os.rename(src, dst)
    except Exception as e:
        # пробуем вернуть оригинал на место
        try:
            os.rename(backup, dst)
        except Exception:
            pass
        return False, f'rename-swap failed at place step: {e}'

    try:
        os.remove(backup)
    except Exception as e:
        print(f"[editor] could not remove backup {backup}: {e}")

    return True, None


def _update_db(video_id, new_name, new_path, info, new_size):
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

        ts_ms = int(time.time() * 1000)
        tmp_name = f'.__trim_{ts_ms}_{base_name}{ext}'
        tmp_path = os.path.join(folder, tmp_name)

        duration = end - start

        # --- Собираем команду ffmpeg ---
        if mode == 'fast':
            cmd = [
                FFMPEG_PATH, '-y',
                '-ss', f'{start:.6f}',
                '-i', src,
                '-t', f'{duration:.6f}',
                '-c', 'copy',
                '-avoid_negative_ts', 'make_zero',
                '-movflags', '+faststart',
                tmp_path,
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
                tmp_path,
            ]

        try:
            r = subprocess.run(cmd, timeout=7200, **_subprocess_kwargs())
        except subprocess.TimeoutExpired:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return jsonify({'error': 'FFmpeg timeout'}), 500
        except Exception as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return jsonify({'error': f'FFmpeg error: {e}'}), 500

        if r.returncode != 0:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            tail = (r.stderr or '')[-400:]
            return jsonify({'error': f'FFmpeg failed: {tail}'}), 500

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return jsonify({'error': 'Empty output'}), 500

        # --- Куда девать результат ---
        fallback_used = False
        final_path = None
        final_name = None

        if overwrite:
            # Небольшая пауза — даём браузеру закрыть HTTP-стрим на исходник
            time.sleep(0.6)
            ok, err = _safe_replace(tmp_path, src)
            if ok:
                final_path = src
                final_name = video['filename']
            else:
                # Fallback: не overwrite — сохраняем как отдельный файл
                print(f"[editor] overwrite failed, saving as new file: {err}")
                fallback_used = True
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                fallback_name = f'{base_name}_trim_{ts}{ext}'
                fallback_path = os.path.join(folder, fallback_name)
                try:
                    os.rename(tmp_path, fallback_path)
                except Exception as e2:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                    return jsonify({
                        'error': (
                            f'Cannot replace source: {err}. '
                            f'Fallback rename also failed: {e2}'
                        )
                    }), 500
                final_path = fallback_path
                final_name = fallback_name
        else:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            new_name = f'{base_name}_trim_{ts}{ext}'
            new_path = os.path.join(folder, new_name)
            try:
                os.rename(tmp_path, new_path)
            except Exception as e:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                return jsonify({'error': f'Cannot create new file: {e}'}), 500
            final_path = new_path
            final_name = new_name

        # --- Обновляем метаданные и БД ---
        try:
            from scanner import get_media_info
            info = get_media_info(final_path) or {}
        except Exception as e:
            print(f"[editor] get_media_info failed: {e}")
            info = {}

        new_size = os.path.getsize(final_path)

        _update_db(video_id, final_name, final_path, info, new_size)

        resp = {
            'success': True,
            'filename': final_name,
            'filepath': final_path,
            'duration': info.get('duration', 0),
            'size': new_size,
            'mode': mode,
        }
        if fallback_used:
            resp['fallback'] = True
            resp['message'] = (
                'Original was locked — saved as a new file. '
                'Close anything playing the video and try overwrite again.'
            )
        return jsonify(resp)