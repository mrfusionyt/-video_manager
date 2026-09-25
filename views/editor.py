"""
Роуты видеоредактора /editor/<video_id>.

Crop теперь применяется ПО КЛИПУ, а не глобально:
каждый клип может иметь собственный crop = sub-rect исходника.
Канвас (target_w/target_h) не меняется.

Модель клипа (payload):
  { video_id, in, out, tl_start, scale, offset_x, offset_y,
    video_on, audio_on,
    crop: {x, y, w, h} | null   # sub-rect исходника в пикселях
  }

Backend: ffmpeg filter_complex:
  • Фон: color=black:s=WxH:d=total
  • Каждый клип:
      trim → setpts=0
      crop (если задан) → scale=iw*S:ih*S → setpts+=tl_start
  • Цепочка overlay'ев на фон
  • Аудио: atrim → adelay → amix
  • Опциональный global crop на финале (fallback для старых запросов)
"""
import os
import json
import time
import shutil
import traceback
import subprocess
from datetime import datetime

from flask import render_template, request, jsonify, abort

from models import get_video_by_id, get_all_videos, get_db_connection
from helpers.profiles import get_current_profile


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(BASE_DIR, '_dop', 'ffmpeg.exe')
FFPROBE = os.path.join(BASE_DIR, '_dop', 'ffprobe.exe')
if not os.path.exists(FFMPEG):
    FFMPEG = shutil.which('ffmpeg') or 'ffmpeg'
if not os.path.exists(FFPROBE):
    FFPROBE = shutil.which('ffprobe') or 'ffprobe'

print(f"[editor] FFMPEG  = {FFMPEG}")
print(f"[editor] FFPROBE = {FFPROBE}")


def _sp_kwargs():
    return dict(capture_output=True, encoding='utf-8', errors='replace')


def probe_video(path):
    info = {
        'duration': 0.0, 'width': 0, 'height': 0,
        'fps': 30.0, 'has_audio': False, 'codec': '',
    }
    if not path or not os.path.exists(path):
        return info
    try:
        r = subprocess.run(
            [FFPROBE, '-v', 'error',
             '-show_entries',
             'stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate',
             '-show_entries', 'format=duration',
             '-of', 'json', path],
            timeout=20, **_sp_kwargs()
        )
        if r.returncode != 0 or not r.stdout:
            return info
        data = json.loads(r.stdout)
        if not isinstance(data, dict):
            return info
        fmt = data.get('format') or {}
        try:
            info['duration'] = float(fmt.get('duration', 0) or 0)
        except (TypeError, ValueError):
            info['duration'] = 0.0
        for s in data.get('streams', []):
            if not isinstance(s, dict):
                continue
            ct = s.get('codec_type')
            if ct == 'video' and not info['width']:
                try:
                    info['width'] = int(s.get('width', 0) or 0)
                    info['height'] = int(s.get('height', 0) or 0)
                except (TypeError, ValueError):
                    pass
                info['codec'] = s.get('codec_name', '') or ''
                fps_str = s.get('r_frame_rate') or s.get('avg_frame_rate') or '30/1'
                try:
                    if '/' in fps_str:
                        n, d = fps_str.split('/')
                        n, d = float(n), float(d)
                        info['fps'] = n / d if d else 30.0
                    else:
                        info['fps'] = float(fps_str)
                except Exception:
                    info['fps'] = 30.0
            elif ct == 'audio':
                info['has_audio'] = True
    except Exception as e:
        print(f"[editor] probe_video error for {path}: {e}")
    return info


def _num(x):
    return f'{float(x):.6f}'


def _even(n):
    n = int(n)
    return n - (n % 2)


def build_filter_complex(clips_meta, target_w, target_h, target_fps,
                         total_duration, global_crop=None):
    """
    filter_complex:
      input 0 = lavfi color (фон)
      input 1..N = файлы клипов

    Для каждого клипа:
      trim → setpts=0
      [crop=cw:ch:cx:cy] (если clip_crop задан)
      scale → setpts+=tl_start
    """
    parts = []
    parts.append(
        f"color=c=black:s={target_w}x{target_h}:r={target_fps}"
        f":d={_num(total_duration)}[base0]"
    )

    for i, c in enumerate(clips_meta):
        if not c['video_on']:
            continue
        idx = c['input_index'] + 1
        s = float(c.get('scale', 1.0)) or 1.0
        tl_start = float(c.get('tl_start', 0.0))

        chain = (
            f"trim=start={_num(c['in'])}:end={_num(c['out'])},"
            f"setpts=PTS-STARTPTS"
        )

        # Per-clip crop (до scale)
        cc = c.get('clip_crop')
        if cc:
            cw = _even(cc['w'])
            ch = _even(cc['h'])
            cx = max(0, int(cc['x']))
            cy = max(0, int(cc['y']))
            # Иногда исходник нечётный — подстрахуемся через выражение
            chain += f",crop={cw}:{ch}:{cx}:{cy}"

        chain += (
            f",scale=trunc(iw*{_num(s)}/2)*2:trunc(ih*{_num(s)}/2)*2,"
            f"setsar=1,"
            f"format=rgba,"
            f"setpts=PTS+{_num(tl_start)}/TB"
        )
        parts.append(f"[{idx}:v]{chain}[v{i}]")

    # Overlay chain
    prev = "base0"
    overlay_count = 0
    for i, c in enumerate(clips_meta):
        if not c['video_on']:
            continue
        ox = int(c.get('offset_x', 0) or 0)
        oy = int(c.get('offset_y', 0) or 0)
        out = f"base{overlay_count + 1}"
        parts.append(
            f"[{prev}][v{i}]"
            f"overlay=x={ox}:y={oy}:eof_action=pass:format=auto"
            f"[{out}]"
        )
        prev = out
        overlay_count += 1

    # Финальный crop (fallback для старых запросов)
    if global_crop and int(global_crop.get('w', 0)) >= 16 and int(global_crop.get('h', 0)) >= 16:
        cw = _even(global_crop['w'])
        ch = _even(global_crop['h'])
        cx = max(0, int(global_crop.get('x', 0)))
        cy = max(0, int(global_crop.get('y', 0)))
        parts.append(f"[{prev}]crop={cw}:{ch}:{cx}:{cy},format=yuv420p[vout]")
    else:
        parts.append(f"[{prev}]format=yuv420p[vout]")

    # Аудио
    audio_labels = []
    for i, c in enumerate(clips_meta):
        if not c['audio_on'] or not c['has_audio']:
            continue
        idx = c['input_index'] + 1
        tl_start = float(c.get('tl_start', 0.0))
        delay_ms = int(tl_start * 1000)
        if delay_ms < 0:
            delay_ms = 0
        parts.append(
            f"[{idx}:a]"
            f"atrim=start={_num(c['in'])}:end={_num(c['out'])},"
            f"asetpts=PTS-STARTPTS,"
            f"adelay=delays={delay_ms}:all=1,"
            f"aresample=44100,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo"
            f"[a{i}]"
        )
        audio_labels.append(f"a{i}")

    if audio_labels:
        inputs = ''.join(f'[{l}]' for l in audio_labels)
        if len(audio_labels) == 1:
            parts.append(f"{inputs}anull[aout]")
        else:
            parts.append(
                f"{inputs}amix=inputs={len(audio_labels)}"
                f":duration=longest:dropout_transition=0,"
                f"atrim=duration={_num(total_duration)}[aout]"
            )
    else:
        parts.append(
            f"anullsrc=channel_layout=stereo:sample_rate=44100,"
            f"atrim=duration={_num(total_duration)}[aout]"
        )

    return ';'.join(parts)


def _cleanup(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _safe_replace(src, dst, retries=30, delay=0.5):
    last_err = None
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return True, None
        except (PermissionError, OSError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(delay)

    backup = dst + f'.__bak_{int(time.time() * 1000)}'
    try:
        os.rename(dst, backup)
    except Exception as e:
        return False, f'locked ({last_err}); rename-swap failed: {e}'
    try:
        os.rename(src, dst)
    except Exception as e:
        try:
            os.rename(backup, dst)
        except Exception:
            pass
        return False, f'rename-swap place step failed: {e}'
    try:
        os.remove(backup)
    except Exception:
        pass
    return True, None


def _update_db(video_id, name, path, meta, size):
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute('''
            UPDATE videos SET
                filename = ?, filepath = ?, size = ?,
                duration = ?, width = ?, height = ?,
                codec = ?, orientation = ?
            WHERE id = ?
        ''', (
            name, path, size,
            int(meta.get('duration', 0) or 0),
            int(meta.get('width', 0) or 0),
            int(meta.get('height', 0) or 0),
            meta.get('codec', '') or '',
            'vertical' if (meta.get('height', 0) or 0) > (meta.get('width', 0) or 0) else 'horizontal',
            video_id,
        ))
        conn.commit()
    finally:
        conn.close()


def register(app):

    @app.route('/editor/<int:video_id>')
    def editor_page(video_id):
        try:
            video = get_video_by_id(video_id)
            if not video:
                abort(404)
            current_profile = get_current_profile()
            meta = probe_video(video['filepath'])
            return render_template(
                'editor.html',
                video=video,
                meta=meta,
                video_filepath=video['filepath'],
                current_profile=current_profile,
            )
        except Exception:
            tb = traceback.format_exc()
            print(f"[editor] FATAL in editor_page:\n{tb}")
            return (f"<pre style='color:#f88;background:#1a1a1a;padding:20px;'>"
                    f"Editor page error:\n\n{tb}</pre>"), 500

    @app.route('/editor/debug/<int:video_id>')
    def editor_debug(video_id):
        try:
            v = get_video_by_id(video_id)
            if not v:
                return jsonify({'error': 'video not found'}), 404
            meta = probe_video(v['filepath'])
            return jsonify({
                'ok': True,
                'video_id': v['id'],
                'filename': v['filename'],
                'filepath': v['filepath'],
                'file_exists': os.path.exists(v['filepath']),
                'meta': meta,
                'ffmpeg': FFMPEG, 'ffprobe': FFPROBE,
            })
        except Exception:
            return f"<pre>{traceback.format_exc()}</pre>", 500

    @app.route('/editor/list_videos')
    def editor_list_videos():
        try:
            videos = get_all_videos(mode=None)
            out = []
            for v in videos:
                out.append({
                    'id': v['id'],
                    'filename': v['filename'],
                    'duration': int(v.get('duration', 0) or 0),
                    'width': int(v.get('width', 0) or 0),
                    'height': int(v.get('height', 0) or 0),
                })
            return jsonify({'videos': out})
        except Exception as e:
            traceback.print_exc()
            return jsonify({'error': str(e), 'videos': []}), 500

    @app.route('/editor/<int:video_id>/info')
    def editor_video_info(video_id):
        try:
            v = get_video_by_id(video_id)
            if not v:
                return jsonify({'error': 'not found'}), 404
            meta = probe_video(v['filepath'])
            meta['id'] = v['id']
            meta['filename'] = v['filename']
            meta['filepath'] = v['filepath']
            return jsonify(meta)
        except Exception as e:
            traceback.print_exc()
            return jsonify({'error': str(e)}), 500

    @app.route('/editor/save', methods=['POST'])
    def editor_save():
        try:
            data = request.get_json() or {}
            clips_in = data.get('clips') or []
            crop = data.get('crop') or None  # fallback global crop
            overwrite = bool(data.get('overwrite', True))
            quality = data.get('quality', 'accurate')
            if quality not in ('fast', 'accurate'):
                quality = 'accurate'

            target_w = int(data.get('target_w') or 0)
            target_h = int(data.get('target_h') or 0)
            target_fps = float(data.get('target_fps') or 0)

            if not clips_in:
                return jsonify({'error': 'No clips provided'}), 400

            clips_meta = []
            source_paths = []
            for idx, c in enumerate(clips_in):
                if not isinstance(c, dict):
                    return jsonify({'error': f'Clip #{idx + 1} invalid'}), 400
                try:
                    vid = int(c.get('video_id'))
                    tin = float(c.get('in', 0))
                    tout = float(c.get('out', 0))
                    tl_start = float(c.get('tl_start', 0))
                    scale = float(c.get('scale', 1.0))
                    ox = int(c.get('offset_x', 0))
                    oy = int(c.get('offset_y', 0))
                except (TypeError, ValueError):
                    return jsonify({'error': f'Invalid clip #{idx + 1}'}), 400

                if tout <= tin:
                    return jsonify({'error': f'Clip #{idx + 1}: end <= start'}), 400
                if tl_start < 0:
                    tl_start = 0.0
                if scale <= 0.01:
                    scale = 0.01

                v = get_video_by_id(vid)
                if not v or not os.path.exists(v['filepath']):
                    return jsonify({'error': f'Clip #{idx + 1}: file not found'}), 400
                m = probe_video(v['filepath'])

                # Per-clip crop
                clip_crop = None
                cc = c.get('crop')
                if isinstance(cc, dict):
                    try:
                        ccx = max(0, int(cc.get('x', 0)))
                        ccy = max(0, int(cc.get('y', 0)))
                        ccw = int(cc.get('w', 0))
                        cch = int(cc.get('h', 0))
                    except (TypeError, ValueError):
                        return jsonify({'error': f'Clip #{idx + 1}: invalid crop'}), 400
                    src_w = m['width'] or 0
                    src_h = m['height'] or 0
                    if ccw >= 2 and cch >= 2 and src_w > 0 and src_h > 0:
                        # clamp к границам исходника
                        if ccx + ccw > src_w:
                            ccw = src_w - ccx
                        if ccy + cch > src_h:
                            cch = src_h - ccy
                        if ccw >= 2 and cch >= 2:
                            clip_crop = {'x': ccx, 'y': ccy, 'w': ccw, 'h': cch}

                clips_meta.append({
                    'input_index': idx,
                    'in': tin,
                    'out': tout,
                    'tl_start': tl_start,
                    'scale': scale,
                    'offset_x': ox,
                    'offset_y': oy,
                    'video_on': bool(c.get('video_on', True)),
                    'audio_on': bool(c.get('audio_on', True)),
                    'has_audio': m['has_audio'],
                    'filepath': v['filepath'],
                    'video_id': v['id'],
                    'clip_crop': clip_crop,
                })
                source_paths.append(v['filepath'])

            first_meta = probe_video(source_paths[0])
            if not target_w or not target_h:
                target_w = first_meta['width'] or 1280
                target_h = first_meta['height'] or 720
            if not target_fps:
                target_fps = first_meta['fps'] or 30.0
            target_fps = round(target_fps, 3)

            total_duration = 0.0
            for c in clips_meta:
                end = c['tl_start'] + (c['out'] - c['in'])
                if end > total_duration:
                    total_duration = end
            if total_duration <= 0:
                return jsonify({'error': 'Invalid total duration'}), 400

            # Global fallback crop (если фронт всё ещё его шлёт)
            global_crop = None
            if crop:
                try:
                    cw = int(crop.get('w', 0))
                    ch = int(crop.get('h', 0))
                    cx = int(crop.get('x', 0))
                    cy = int(crop.get('y', 0))
                except (TypeError, ValueError):
                    return jsonify({'error': 'Invalid crop'}), 400
                if cw < 16 or ch < 16:
                    return jsonify({'error': 'Crop too small'}), 400
                if cx < 0 or cy < 0 or cx + cw > target_w or cy + ch > target_h:
                    return jsonify({'error': 'Crop out of bounds'}), 400
                global_crop = {'x': cx, 'y': cy, 'w': cw, 'h': ch}

            try:
                filter_str = build_filter_complex(
                    clips_meta, target_w, target_h, target_fps,
                    total_duration, global_crop,
                )
            except Exception as e:
                traceback.print_exc()
                return jsonify({'error': f'Filter build failed: {e}'}), 500

            first_video = get_video_by_id(clips_meta[0]['video_id'])
            folder = os.path.dirname(first_video['filepath'])
            base_name = os.path.splitext(first_video['filename'])[0]
            ext = os.path.splitext(first_video['filename'])[1].lower() or '.mp4'
            ts_ms = int(time.time() * 1000)
            tmp_path = os.path.join(folder, f'.__edit_{ts_ms}_{base_name}{ext}')

            cmd = [FFMPEG, '-y']
            cmd += ['-f', 'lavfi', '-i',
                    f'color=c=black:s={target_w}x{target_h}:r={target_fps}:'
                    f'd={_num(total_duration)}']
            for c in clips_meta:
                cmd += ['-i', c['filepath']]

            cmd += ['-filter_complex', filter_str]
            cmd += ['-map', '[vout]', '-map', '[aout]']
            if quality == 'fast':
                cmd += ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23']
            else:
                cmd += ['-c:v', 'libx264', '-preset', 'medium', '-crf', '18']
            cmd += ['-c:a', 'aac', '-b:a', '192k']
            cmd += ['-movflags', '+faststart']
            cmd += ['-t', _num(total_duration)]
            cmd += [tmp_path]

            print(f"[editor] save: {len(clips_meta)} clips, "
                  f"target={target_w}x{target_h}@{target_fps}, "
                  f"total={total_duration:.3f}s, global_crop={global_crop}")
            for i, cm in enumerate(clips_meta):
                print(f"[editor]   clip #{i+1}: id={cm['video_id']} "
                      f"scale={cm['scale']} off=({cm['offset_x']},{cm['offset_y']}) "
                      f"clip_crop={cm['clip_crop']}")

            try:
                r = subprocess.run(cmd, timeout=7200, **_sp_kwargs())
            except subprocess.TimeoutExpired:
                _cleanup(tmp_path)
                return jsonify({'error': 'FFmpeg timeout'}), 500
            except Exception as e:
                _cleanup(tmp_path)
                return jsonify({'error': f'FFmpeg error: {e}'}), 500

            if r.returncode != 0:
                tail = (r.stderr or '')[-900:]
                _cleanup(tmp_path)
                print(f"[editor] ffmpeg FAILED:\n{tail}")
                return jsonify({'error': f'FFmpeg failed: {tail}'}), 500

            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                _cleanup(tmp_path)
                return jsonify({'error': 'Empty output'}), 500

            fallback_used = False
            if overwrite:
                time.sleep(0.6)
                dst = first_video['filepath']
                ok, err = _safe_replace(tmp_path, dst)
                if not ok:
                    fallback_used = True
                    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                    new_name = f'{base_name}_edit_{ts}{ext}'
                    new_path = os.path.join(folder, new_name)
                    try:
                        os.rename(tmp_path, new_path)
                    except Exception as e2:
                        _cleanup(tmp_path)
                        return jsonify({
                            'error': f'Cannot replace source: {err}. Fallback: {e2}'
                        }), 500
                    final_path = new_path
                    final_name = new_name
                else:
                    final_path = dst
                    final_name = first_video['filename']
            else:
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                new_name = f'{base_name}_edit_{ts}{ext}'
                new_path = os.path.join(folder, new_name)
                try:
                    os.rename(tmp_path, new_path)
                except Exception as e:
                    _cleanup(tmp_path)
                    return jsonify({'error': f'Cannot create new file: {e}'}), 500
                final_path = new_path
                final_name = new_name

            try:
                m = probe_video(final_path)
                _update_db(
                    first_video['id'], final_name, final_path, m,
                    os.path.getsize(final_path)
                )
            except Exception as e:
                print(f"[editor] DB update failed: {e}")

            resp = {
                'success': True,
                'filename': final_name,
                'filepath': final_path,
                'folder': folder,
                'video_id': first_video['id'],
                'fallback': fallback_used,
            }
            if fallback_used:
                resp['message'] = 'Original was locked — saved as a new file.'
            return jsonify(resp)
        except Exception:
            tb = traceback.format_exc()
            print(f"[editor] FATAL in editor_save:\n{tb}")
            return jsonify({'error': 'Server error', 'traceback': tb}), 500