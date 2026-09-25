/* ============================================================
   editor.js — мультиклиповый редактор.

   Ключевое изменение этой версии:
     • Apply crop применяется к АКТИВНОМУ КЛИПУ (clip.crop),
       а НЕ к канвасу. Канвас остаётся как задан.
     • После apply клип показывает обрезанный фрагмент источника,
       и его можно двигать/масштабировать по канвасу.
     • Revert crop — возвращает активному клипу полный источник.

   Модель клипа:
     { video_id, filename, src_duration, src_width, src_height,
       has_audio, in, out, tl_start, scale, offset_x, offset_y,
       video_on, audio_on, lastTime,
       crop: { x, y, w, h } | null   // sub-rect исходника
     }
   ============================================================ */
(function () {
    'use strict';

    var CFG = window.EDITOR_CONFIG || {};
    var META = CFG.meta || {};

    var DEFAULT_TARGET_W = 1920;
    var DEFAULT_TARGET_H = 1080;

    var TARGET_W = DEFAULT_TARGET_W;
    var TARGET_H = DEFAULT_TARGET_H;
    var TARGET_FPS = META.fps || 30;
    function frameStep() { return 1 / (TARGET_FPS || 30); }

    var MAX_HISTORY = 3;

    var state = {
        clips: [],
        activeIndex: 0,
        crop: null,             // прямоугольник в координатах канваса
        cropEnabled: false,
        snapEnabled: true,
        thumbs: {},
        history: [],
        lastLoadedVideoId: null,
    };

    /* ============================================================
       DOM
       ============================================================ */
    var video = document.getElementById('editorVideo');
    if (!video) return;

    var editorMeta = document.getElementById('editorMeta');
    var previewStage = document.getElementById('previewStage');
    var stageAspect = document.getElementById('stageAspect');
    var stageCanvas = document.getElementById('stageCanvas');
    var ctx = stageCanvas.getContext('2d');

    var activeClipBox = document.getElementById('activeClipBox');
    var activeClipLabel = document.getElementById('activeClipLabel');

    var cropMaskTop = document.getElementById('cropMaskTop');
    var cropMaskBottom = document.getElementById('cropMaskBottom');
    var cropMaskLeft = document.getElementById('cropMaskLeft');
    var cropMaskRight = document.getElementById('cropMaskRight');
    var cropBox = document.getElementById('cropBox');

    var tlSideTracks = document.getElementById('tlSideTracks');
    var tlRuler = document.getElementById('tlRuler');
    var tlTracks = document.getElementById('tlTracks');
    var tlPlayhead = document.getElementById('tlPlayhead');
    var tlSnapline = document.getElementById('tlSnapline');
    var tlTotal = document.getElementById('tlTotal');
    var chkSnap = document.getElementById('chkSnap');
    var clipCount = document.getElementById('clipCount');

    var propsId = document.getElementById('propsId');
    var propsName = document.getElementById('propsName');
    var propIn = document.getElementById('propIn');
    var propOut = document.getElementById('propOut');
    var propTlStart = document.getElementById('propTlStart');
    var propScale = document.getElementById('propScale');
    var propScaleVal = document.getElementById('propScaleVal');
    var propOffsetX = document.getElementById('propOffsetX');
    var propOffsetY = document.getElementById('propOffsetY');
    var propVideoOn = document.getElementById('propVideoOn');
    var propAudioOn = document.getElementById('propAudioOn');
    var propMoveUp = document.getElementById('propMoveUp');
    var propMoveDown = document.getElementById('propMoveDown');
    var propDelete = document.getElementById('propDelete');

    var targetWEl = document.getElementById('targetW');
    var targetHEl = document.getElementById('targetH');
    var targetFpsEl = document.getElementById('targetFps');
    var targetPreset = document.getElementById('targetPreset');
    var panelCanvasInfo = document.getElementById('panelCanvasInfo');
    var btnTargetFromFirst = document.getElementById('btnTargetFromFirst');
    var btnTargetSquare = document.getElementById('btnTargetSquare');
    var btnTargetLandscape = document.getElementById('btnTargetLandscape');
    var btnTargetVertical = document.getElementById('btnTargetVertical');
    var btnTarget4x5 = document.getElementById('btnTarget4x5');
    var btnTarget3x4 = document.getElementById('btnTarget3x4');

    var cropXEl = document.getElementById('cropX');
    var cropYEl = document.getElementById('cropY');
    var cropWEl = document.getElementById('cropW');
    var cropHEl = document.getElementById('cropH');
    var chkCropEnabled = document.getElementById('chkCropEnabled');
    var btnCropFull = document.getElementById('btnCropFull');
    var btnApplyCrop = document.getElementById('btnApplyCrop');
    var btnRevertCrop = document.getElementById('btnRevertCrop');
    var cropAppliedBadge = document.getElementById('cropAppliedBadge');

    var btnUndo = document.getElementById('btnUndo');
    var btnPlayPause = document.getElementById('btnPlayPause');
    var btnStepBack = document.getElementById('btnStepBack');
    var btnStepForward = document.getElementById('btnStepForward');
    var btnSetIn = document.getElementById('btnSetIn');
    var btnSetOut = document.getElementById('btnSetOut');
    var btnResetTrim = document.getElementById('btnResetTrim');
    var btnAddClip = document.getElementById('btnAddClip');
    var btnSave = document.getElementById('btnSave');
    var statusEl = document.getElementById('editorStatus');
    var totalInfo = document.getElementById('totalInfo');

    var addClipModalEl = document.getElementById('addClipModal');
    var addClipModal = addClipModalEl ? new bootstrap.Modal(addClipModalEl) : null;
    var videoPicker = document.getElementById('videoPicker');
    var pickerSearch = document.getElementById('pickerSearch');
    var confirmAddClipBtn = document.getElementById('confirmAddClipBtn');

    var saveModalEl = document.getElementById('saveModal');
    var saveModal = saveModalEl ? new bootstrap.Modal(saveModalEl) : null;
    var saveInfoClips = document.getElementById('saveInfoClips');
    var saveInfoDuration = document.getElementById('saveInfoDuration');
    var saveInfoCanvas = document.getElementById('saveInfoCanvas');
    var saveInfoCrop = document.getElementById('saveInfoCrop');
    var saveInfoPath = document.getElementById('saveInfoPath');
    var saveStatusEl = document.getElementById('saveStatus');
    var confirmSaveBtn = document.getElementById('confirmSaveBtn');
    var chkOverwriteSave = document.getElementById('chkOverwriteSave');

    var overwriteConfirmModalEl = document.getElementById('overwriteConfirmModal');
    var overwriteConfirmModal = overwriteConfirmModalEl
        ? new bootstrap.Modal(overwriteConfirmModalEl)
        : null;
    var overwriteFileNameEl = document.getElementById('overwriteFileName');
    var confirmOverwriteBtn = document.getElementById('confirmOverwriteBtn');

    /* ============================================================
       Утилиты
       ============================================================ */
    function formatTime(t) {
        if (!isFinite(t) || t < 0) t = 0;
        var m = Math.floor(t / 60);
        var s = Math.floor(t % 60);
        var ms = Math.floor((t % 1) * 1000);
        return m + ':' + (s < 10 ? '0' : '') + s + '.' + ('00' + ms).slice(-3);
    }
    function clipDur(c) { return Math.max(0, (c.out || 0) - (c.in || 0)); }
    function clipEnd(c) { return (c.tl_start || 0) + clipDur(c); }
    function totalDuration() {
        var t = 0;
        state.clips.forEach(function (c) {
            var e = clipEnd(c);
            if (e > t) t = e;
        });
        return t;
    }
    function activeClip() { return state.clips[state.activeIndex] || null; }
    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

    function splitPath(fullPath) {
        if (!fullPath) return { dir: '', base: '', ext: '' };
        var m = String(fullPath).match(/^(.*[\\/])([^\\/]+?)(\.[^.\\/]+)?$/);
        if (!m) return { dir: '', base: fullPath, ext: '' };
        return { dir: m[1], base: m[2], ext: m[3] || '.mp4' };
    }

    // Эффективный размер клипа (с учётом clip.crop)
    function clipEffectiveBase(c) {
        var W = c.src_width || TARGET_W;
        var H = c.src_height || TARGET_H;
        if (c.crop) {
            return { w: c.crop.w, h: c.crop.h };
        }
        return { w: W, h: H };
    }

    function clipVisualSize(c) {
        var b = clipEffectiveBase(c);
        return { w: b.w * c.scale, h: b.h * c.scale };
    }

    /* ============================================================
       CLAMP КЛИПА В ГРАНИЦЫ КАНВАСА
       ============================================================ */
    function clampClipIntoCanvas(c) {
        if (!c) return;
        var vis = clipVisualSize(c);
        var sw = vis.w;
        var sh = vis.h;

        // X
        var minX, maxX;
        if (sw <= TARGET_W) {
            minX = 0;
            maxX = TARGET_W - sw;
        } else {
            minX = TARGET_W - sw;
            maxX = 0;
        }
        if (c.offset_x < minX) c.offset_x = Math.round(minX);
        else if (c.offset_x > maxX) c.offset_x = Math.round(maxX);

        // Y
        var minY, maxY;
        if (sh <= TARGET_H) {
            minY = 0;
            maxY = TARGET_H - sh;
        } else {
            minY = TARGET_H - sh;
            maxY = 0;
        }
        if (c.offset_y < minY) c.offset_y = Math.round(minY);
        else if (c.offset_y > maxY) c.offset_y = Math.round(maxY);
    }

    function clampAllClips() {
        state.clips.forEach(function (c) { clampClipIntoCanvas(c); });
    }

    /* ============================================================
       UNDO
       ============================================================ */
    function deepCopyClips(clips) {
        return clips.map(function (c) {
            return {
                video_id: c.video_id,
                filename: c.filename,
                src_duration: c.src_duration,
                src_width: c.src_width,
                src_height: c.src_height,
                has_audio: c.has_audio,
                in: c.in,
                out: c.out,
                tl_start: c.tl_start,
                scale: c.scale,
                offset_x: c.offset_x,
                offset_y: c.offset_y,
                video_on: c.video_on,
                audio_on: c.audio_on,
                lastTime: c.lastTime || 0,
                crop: c.crop ? { x: c.crop.x, y: c.crop.y,
                                 w: c.crop.w, h: c.crop.h } : null,
            };
        });
    }

    function updateUndoBtn() {
        if (!btnUndo) return;
        var n = state.history.length;
        btnUndo.disabled = n === 0;
        btnUndo.textContent = n > 0 ? ('↶ Undo (' + n + ')') : '↶ Undo';
    }

    function rememberCurrentTime() {
        var c = activeClip();
        if (!c || !video) return;
        if (video.readyState < 1) return;
        var t = video.currentTime;
        if (!isFinite(t)) return;
        c.lastTime = Math.round(t * 1000) / 1000;
    }

    function snapshot() {
        rememberCurrentTime();
        var snap = {
            clips: deepCopyClips(state.clips),
            activeIndex: state.activeIndex,
            crop: state.crop ? { x: state.crop.x, y: state.crop.y,
                                 w: state.crop.w, h: state.crop.h } : null,
            cropEnabled: state.cropEnabled,
            snapEnabled: state.snapEnabled,
            targetW: TARGET_W,
            targetH: TARGET_H,
            targetFps: TARGET_FPS,
        };
        state.history.push(snap);
        while (state.history.length > MAX_HISTORY) state.history.shift();
        updateUndoBtn();
    }

    function undo() {
        if (!state.history.length) return;
        var snap = state.history.pop();

        state.clips = snap.clips;
        state.activeIndex = Math.min(snap.activeIndex, state.clips.length - 1);
        state.crop = snap.crop ? Object.assign({}, snap.crop) : null;
        state.cropEnabled = snap.cropEnabled;
        state.snapEnabled = snap.snapEnabled;
        TARGET_W = snap.targetW;
        TARGET_H = snap.targetH;
        TARGET_FPS = snap.targetFps;

        chkSnap.checked = snap.snapEnabled;
        chkCropEnabled.checked = snap.cropEnabled;

        syncTargetInputs();
        layoutStage();
        renderTimeline();
        syncProps();
        renderCropOverlay();
        renderActiveClipBox();
        updateTotalInfo();
        updateCropButtons();
        updateUndoBtn();

        var c = state.clips[state.activeIndex];
        if (c) {
            var target = (typeof c.lastTime === 'number' && c.lastTime > 0) ? c.lastTime : c.in;
            if (target < c.in) target = c.in;
            if (target > c.out) target = c.out;

            if (state.lastLoadedVideoId === c.video_id) {
                try { video.currentTime = target; } catch (e) {}
                renderCanvas();
                updatePlayhead();
            } else {
                try {
                    video.pause();
                    video.src = '/video/' + c.video_id + '?v=' + Date.now();
                    video.load();
                    state.lastLoadedVideoId = c.video_id;
                    video.addEventListener('loadedmetadata', function onMeta() {
                        video.removeEventListener('loadedmetadata', onMeta);
                        try { video.currentTime = target; } catch (e) {}
                        renderCanvas();
                        updatePlayhead();
                    }, { once: true });
                } catch (e) {}
            }
        } else {
            renderCanvas();
        }
    }

    /* ============================================================
       Layout stage
       ============================================================ */
    function layoutStage() {
        var maxW = Math.max(200, previewStage.clientWidth - 16);
        var maxH = Math.max(150, window.innerHeight * 0.5);
        var ar = TARGET_W / TARGET_H;
        var w = maxW;
        var h = w / ar;
        if (h > maxH) { h = maxH; w = h * ar; }
        stageAspect.style.width = Math.round(w) + 'px';
        stageAspect.style.height = Math.round(h) + 'px';
        stageCanvas.width = TARGET_W;
        stageCanvas.height = TARGET_H;
        if (state.cropEnabled) renderCropOverlay();
        renderActiveClipBox();
    }
    function stageRect() { return stageAspect.getBoundingClientRect(); }
    function clientToTarget(cx, cy) {
        var r = stageRect();
        return {
            x: (cx - r.left) / r.width * TARGET_W,
            y: (cy - r.top) / r.height * TARGET_H,
        };
    }
    function targetScale() {
        var r = stageRect();
        return { sx: r.width / TARGET_W, sy: r.height / TARGET_H };
    }

    /* ============================================================
       Canvas render
       ============================================================ */
    function ensureThumb(clip) {
        if (state.thumbs[clip.video_id]) return state.thumbs[clip.video_id];
        var img = new Image();
        img.onload = function () { renderCanvas(); };
        img.src = '/thumbnail/' + clip.video_id;
        state.thumbs[clip.video_id] = img;
        return img;
    }

    function currentTlTime() {
        var c = activeClip();
        if (!c) return 0;
        return (c.tl_start || 0) + ((video.currentTime || 0) - c.in);
    }

    function renderCanvas() {
        ctx.fillStyle = '#000';
        ctx.fillRect(0, 0, TARGET_W, TARGET_H);

        if (!state.clips.length) return;
        var tlPos = currentTlTime();

        state.clips.forEach(function (c, i) {
            if (!c.video_on) return;
            var cStart = c.tl_start || 0;
            var cEnd = cStart + clipDur(c);
            if (tlPos < cStart - 0.001 || tlPos > cEnd + 0.001) return;

            var base_w = c.src_width || TARGET_W;
            var base_h = c.src_height || TARGET_H;
            var crop = c.crop || { x: 0, y: 0, w: base_w, h: base_h };

            var draw_w = crop.w * c.scale;
            var draw_h = crop.h * c.scale;

            var src = (i === state.activeIndex) ? video
                    : (state.thumbs[c.video_id] || ensureThumb(c));

            if (src === video) {
                if (video.readyState < 2 || !video.videoWidth) return;
            } else {
                if (!src.complete || !src.naturalWidth) return;
            }

            var isFullCrop = (!c.crop) ||
                (crop.x === 0 && crop.y === 0 &&
                 crop.w === base_w && crop.h === base_h);

            try {
                if (isFullCrop) {
                    ctx.drawImage(src, c.offset_x, c.offset_y, draw_w, draw_h);
                } else {
                    ctx.drawImage(
                        src,
                        crop.x, crop.y, crop.w, crop.h,
                        c.offset_x, c.offset_y, draw_w, draw_h
                    );
                }
            } catch (e) {}
        });
    }

    /* ============================================================
       Active clip box
       ============================================================ */
    function renderActiveClipBox() {
        var c = activeClip();
        if (!c || !c.video_on) {
            activeClipBox.style.display = 'none';
            return;
        }
        var s = targetScale();
        var vis = clipVisualSize(c);

        activeClipBox.style.display = 'block';
        activeClipBox.style.left = (c.offset_x * s.sx) + 'px';
        activeClipBox.style.top = (c.offset_y * s.sy) + 'px';
        activeClipBox.style.width = (vis.w * s.sx) + 'px';
        activeClipBox.style.height = (vis.h * s.sy) + 'px';

        var tag = c.crop ? '  ✂' : '';
        activeClipLabel.textContent = '#' + (state.activeIndex + 1) + '  ' +
            Math.round(vis.w) + '×' + Math.round(vis.h) +
            '  ×' + Number(c.scale).toFixed(2) + tag;
    }

    /* ============================================================
       Crop overlay (прямоугольник для выделения области)
       ============================================================ */
    function defaultCrop() { return { x: 0, y: 0, w: TARGET_W, h: TARGET_H }; }

    function isCropNonTrivial() {
        if (!state.crop) return false;
        return state.crop.x > 0 || state.crop.y > 0 ||
               state.crop.w < TARGET_W || state.crop.h < TARGET_H;
    }

    function renderCropOverlay() {
        if (!state.cropEnabled || !state.crop) {
            cropBox.style.display = 'none';
            cropMaskTop.style.display = 'none';
            cropMaskBottom.style.display = 'none';
            cropMaskLeft.style.display = 'none';
            cropMaskRight.style.display = 'none';
            updateCropButtons();
            return;
        }
        var s = targetScale();
        var r = stageRect();
        var x = state.crop.x * s.sx;
        var y = state.crop.y * s.sy;
        var w = state.crop.w * s.sx;
        var h = state.crop.h * s.sy;

        cropBox.style.display = 'block';
        cropBox.style.left = x + 'px';
        cropBox.style.top = y + 'px';
        cropBox.style.width = w + 'px';
        cropBox.style.height = h + 'px';

        cropMaskTop.style.display = 'block';
        cropMaskTop.style.left = '0'; cropMaskTop.style.top = '0';
        cropMaskTop.style.width = r.width + 'px';
        cropMaskTop.style.height = y + 'px';

        cropMaskBottom.style.display = 'block';
        cropMaskBottom.style.left = '0'; cropMaskBottom.style.top = (y + h) + 'px';
        cropMaskBottom.style.width = r.width + 'px';
        cropMaskBottom.style.height = Math.max(0, r.height - (y + h)) + 'px';

        cropMaskLeft.style.display = 'block';
        cropMaskLeft.style.left = '0'; cropMaskLeft.style.top = y + 'px';
        cropMaskLeft.style.width = x + 'px';
        cropMaskLeft.style.height = h + 'px';

        cropMaskRight.style.display = 'block';
        cropMaskRight.style.left = (x + w) + 'px'; cropMaskRight.style.top = y + 'px';
        cropMaskRight.style.width = Math.max(0, r.width - (x + w)) + 'px';
        cropMaskRight.style.height = h + 'px';

        syncCropInputs();
        updateCropButtons();
    }

    function updateCropButtons() {
        var c = activeClip();
        if (btnApplyCrop) {
            var canApply = state.cropEnabled && state.crop && isCropNonTrivial();
            btnApplyCrop.disabled = !canApply;
            btnApplyCrop.style.opacity = canApply ? '1' : '0.5';
        }
        if (btnRevertCrop) {
            btnRevertCrop.style.display = (c && c.crop) ? 'inline-flex' : 'none';
        }
        if (cropAppliedBadge) {
            cropAppliedBadge.style.display = (c && c.crop) ? 'inline-flex' : 'none';
        }
    }

    function syncCropInputs() {
        if (!state.crop) return;
        cropXEl.value = state.crop.x;
        cropYEl.value = state.crop.y;
        cropWEl.value = state.crop.w;
        cropHEl.value = state.crop.h;
        cropXEl.max = Math.max(0, TARGET_W - state.crop.w);
        cropYEl.max = Math.max(0, TARGET_H - state.crop.h);
        cropWEl.max = TARGET_W - state.crop.x;
        cropHEl.max = TARGET_H - state.crop.y;
    }

    function setCropFromInputs() {
        if (!state.cropEnabled) return;
        var x = parseInt(cropXEl.value, 10) || 0;
        var y = parseInt(cropYEl.value, 10) || 0;
        var w = parseInt(cropWEl.value, 10) || 16;
        var h = parseInt(cropHEl.value, 10) || 16;
        if (w < 16) w = 16; if (h < 16) h = 16;
        if (x < 0) x = 0; if (y < 0) y = 0;
        if (x + w > TARGET_W) w = TARGET_W - x;
        if (y + h > TARGET_H) h = TARGET_H - y;
        state.crop = { x: x, y: y, w: w, h: h };
        renderCropOverlay();
    }

    /* ============================================================
       Apply / Revert crop — для АКТИВНОГО КЛИПА
       ============================================================ */

    /**
     * Применяет текущий crop-rect (в координатах канваса) к активному клипу.
     * Результат: clip.crop = sub-rect ИСХОДНИКА, offset сдвигается так,
     * чтобы клип визуально остался на месте.
     * Canvas НЕ меняется.
     */
    function applyCropToActiveClip() {
        var c = activeClip();
        if (!c) return;
        if (!state.cropEnabled || !state.crop) return;
        if (!isCropNonTrivial()) return;

        var S = c.scale || 1;
        var base_w = c.src_width || TARGET_W;
        var base_h = c.src_height || TARGET_H;

        // Base sub-rect (уже применённый ранее crop или весь источник)
        var base = c.crop
            ? { x: c.crop.x, y: c.crop.y, w: c.crop.w, h: c.crop.h }
            : { x: 0, y: 0, w: base_w, h: base_h };

        // Crop-rect в координатах канваса → в координаты источника
        // (относительно base, т.е. "локальные" для текущего клипа)
        var rel_x = (state.crop.x - c.offset_x) / S;
        var rel_y = (state.crop.y - c.offset_y) / S;
        var rel_x2 = (state.crop.x + state.crop.w - c.offset_x) / S;
        var rel_y2 = (state.crop.y + state.crop.h - c.offset_y) / S;

        // Clamp в границах base
        if (rel_x < 0) rel_x = 0;
        if (rel_y < 0) rel_y = 0;
        if (rel_x2 > base.w) rel_x2 = base.w;
        if (rel_y2 > base.h) rel_y2 = base.h;

        var rel_w = rel_x2 - rel_x;
        var rel_h = rel_y2 - rel_y;

        if (rel_w < 2 || rel_h < 2) {
            // Пересечение пустое или слишком маленькое — выходим
            return;
        }

        // Абсолютные координаты в исходном видео
        var abs_x = base.x + rel_x;
        var abs_y = base.y + rel_y;

        snapshot();

        // Сдвиг на канвасе: верхний-левый угол пересечения
        c.offset_x = Math.round(c.offset_x + rel_x * S);
        c.offset_y = Math.round(c.offset_y + rel_y * S);

        // Обновляем crop клипа (в исходных пикселях)
        c.crop = {
            x: Math.round(abs_x),
            y: Math.round(abs_y),
            w: Math.round(rel_w),
            h: Math.round(rel_h),
        };

        clampClipIntoCanvas(c);

        // Сброс режима выделения
        state.crop = { x: 0, y: 0, w: TARGET_W, h: TARGET_H };
        state.cropEnabled = false;
        chkCropEnabled.checked = false;

        renderCropOverlay();
        renderTimeline();
        syncProps();
        renderActiveClipBox();
        updateTotalInfo();
        updateCropButtons();
        renderCanvas();
    }

    /**
     * Снимает crop у активного клипа (возвращает полный источник).
     */
    function revertCropForActiveClip() {
        var c = activeClip();
        if (!c || !c.crop) return;

        snapshot();

        var old_crop = c.crop;
        var S = c.scale || 1;

        // Восстанавливаем offset: crop.x в источнике теперь на offset_x
        // Раньше (до crop) source(0) был на offset_x - crop.x * S
        c.offset_x = Math.round(c.offset_x - old_crop.x * S);
        c.offset_y = Math.round(c.offset_y - old_crop.y * S);
        c.crop = null;

        clampClipIntoCanvas(c);

        renderCropOverlay();
        renderTimeline();
        syncProps();
        renderActiveClipBox();
        updateTotalInfo();
        updateCropButtons();
        renderCanvas();
    }

    /* ============================================================
       Props sync
       ============================================================ */
    function syncProps() {
        var c = activeClip();
        if (!c) {
            propsName.textContent = '—';
            propsId.textContent = '—';
            return;
        }
        propsName.textContent = c.filename;
        var cropTag = c.crop ? ' ✂' : '';
        propsId.textContent = '#' + (state.activeIndex + 1) +
            ' • ' + (c.src_width || '?') + '×' + (c.src_height || '?') + cropTag;

        propIn.value = Number(c.in).toFixed(3);
        propOut.value = Number(c.out).toFixed(3);
        propTlStart.value = Number(c.tl_start).toFixed(3);
        propScale.value = c.scale;
        propScaleVal.textContent = Number(c.scale).toFixed(2) + '×';
        propOffsetX.value = c.offset_x;
        propOffsetY.value = c.offset_y;
        propVideoOn.checked = !!c.video_on;
        propAudioOn.checked = !!c.audio_on;
        propAudioOn.disabled = !c.has_audio;
        propMoveUp.disabled = state.activeIndex === 0;
        propMoveDown.disabled = state.activeIndex === state.clips.length - 1;

        editorMeta.textContent = 'canvas ' + TARGET_W + '×' + TARGET_H +
            ' @ ' + TARGET_FPS + ' fps';
        panelCanvasInfo.textContent = TARGET_W + '×' + TARGET_H;
        updateCropButtons();
    }

    /* ============================================================
       TIMELINE
       ============================================================ */
    function renderRuler() {
        if (!tlRuler) return;
        var total = totalDuration();
        tlRuler.innerHTML = '';
        if (total <= 0) return;
        var candidates = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
        var step = candidates[0];
        for (var i = 0; i < candidates.length; i++) {
            if (total / candidates[i] <= 12) { step = candidates[i]; break; }
            step = candidates[i];
        }
        for (var t = 0; t <= total + 0.0001; t += step) {
            var tick = document.createElement('div');
            tick.className = 'tl-ruler-tick';
            tick.style.left = (t / total * 100) + '%';
            tick.textContent = formatTime(t);
            tlRuler.appendChild(tick);
        }
    }

    function renderTimeline() {
        tlSideTracks.innerHTML = '';
        Array.from(tlTracks.children).forEach(function (el) {
            if (el.id === 'tlPlayhead' || el.id === 'tlSnapline') return;
            el.remove();
        });

        var total = totalDuration();
        if (total <= 0) {
            tlTotal.textContent = '0:00.000';
            clipCount.textContent = '(0)';
            renderRuler();
            return;
        }
        clipCount.textContent = '(' + state.clips.length + ')';

        state.clips.forEach(function (c, i) {
            var sideEl = document.createElement('div');
            sideEl.className = 'tl-side-track' + (i === state.activeIndex ? ' active' : '');
            sideEl.addEventListener('click', function () { setActiveClip(i); });

            var numEl = document.createElement('span');
            numEl.className = 'tl-side-num';
            numEl.textContent = '#' + (i + 1) + (c.crop ? '✂' : '');
            sideEl.appendChild(numEl);

            var acts = document.createElement('span');
            acts.className = 'tl-side-actions';

            var upBtn = document.createElement('button');
            upBtn.textContent = '↑';
            upBtn.title = 'Move up';
            upBtn.disabled = (i === 0);
            upBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                if (i <= 0) return;
                snapshot();
                var t = state.clips[i - 1];
                state.clips[i - 1] = state.clips[i];
                state.clips[i] = t;
                state.activeIndex = i - 1;
                renderTimeline(); syncProps(); renderCanvas();
            });
            acts.appendChild(upBtn);

            var dnBtn = document.createElement('button');
            dnBtn.textContent = '↓';
            dnBtn.title = 'Move down';
            dnBtn.disabled = (i === state.clips.length - 1);
            dnBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                if (i >= state.clips.length - 1) return;
                snapshot();
                var t = state.clips[i + 1];
                state.clips[i + 1] = state.clips[i];
                state.clips[i] = t;
                state.activeIndex = i + 1;
                renderTimeline(); syncProps(); renderCanvas();
            });
            acts.appendChild(dnBtn);

            var delBtn = document.createElement('button');
            delBtn.textContent = '✕';
            delBtn.title = 'Delete';
            delBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                if (state.clips.length <= 1) return;
                snapshot();
                state.clips.splice(i, 1);
                if (state.activeIndex >= state.clips.length) state.activeIndex = state.clips.length - 1;
                renderTimeline(); syncProps(); updateTotalInfo(); renderCanvas();
                setActiveClip(state.activeIndex);
            });
            acts.appendChild(delBtn);

            sideEl.appendChild(acts);
            tlSideTracks.appendChild(sideEl);

            var trackEl = document.createElement('div');
            trackEl.className = 'tl-track' + (i === state.activeIndex ? ' active' : '');
            trackEl.dataset.index = String(i);

            trackEl.addEventListener('mousedown', function (e) {
                if (e.target.classList.contains('tl-clip')) return;
                var r = tlTracks.getBoundingClientRect();
                var pct = clamp((e.clientX - r.left) / r.width, 0, 1);
                var tlPos = pct * total;
                var local = tlPos - state.clips[i].tl_start;
                if (local < 0) return;
                var newTime = state.clips[i].in + local;
                if (newTime > state.clips[i].out) newTime = state.clips[i].out;
                if (i !== state.activeIndex) {
                    setActiveClip(i, newTime);
                } else {
                    try { video.currentTime = newTime; } catch (err) {}
                    renderCanvas();
                    updatePlayhead();
                }
            });

            var leftPct = (c.tl_start / total) * 100;
            var widthPct = (clipDur(c) / total) * 100;

            var clipEl = document.createElement('div');
            clipEl.className = 'tl-clip' + (!c.video_on ? ' muted-video' : '');
            clipEl.style.left = leftPct + '%';
            clipEl.style.width = Math.max(0.5, widthPct) + '%';
            clipEl.dataset.index = String(i);

            var lbl = document.createElement('div');
            lbl.className = 'tl-clip-label';
            lbl.textContent = (c.crop ? '✂ ' : '') + (c.filename || '');
            lbl.title = c.filename;
            clipEl.appendChild(lbl);

            var flags = document.createElement('div');
            flags.className = 'tl-clip-flags';
            flags.innerHTML =
                '<span>' + (c.video_on ? '🎬' : '🚫') + '</span>' +
                '<span>' + (c.audio_on ? '🔊' : '🔇') + '</span>';
            clipEl.appendChild(flags);

            clipEl.addEventListener('mousedown', function (e) {
                e.stopPropagation();
                setActiveClip(i);
                startTimelineDrag(i, e.clientX);
            });
            clipEl.addEventListener('touchstart', function (e) {
                if (e.touches.length !== 1) return;
                e.stopPropagation();
                setActiveClip(i);
                startTimelineDrag(i, e.touches[0].clientX);
            }, { passive: false });

            trackEl.appendChild(clipEl);
            tlTracks.appendChild(trackEl);
        });

        tlTotal.textContent = formatTime(total);
        renderRuler();
        updatePlayhead();
    }

    function startTimelineDrag(i, clientX0) {
        var c = state.clips[i];
        if (!c) return;
        var startTlStart = c.tl_start;
        var wasDragging = false;
        var snapshotted = false;

        function onMove(clientX) {
            var dx = clientX - clientX0;
            if (!wasDragging && Math.abs(dx) < 3) return;
            if (!snapshotted) {
                snapshot();
                snapshotted = true;
            }
            wasDragging = true;

            var r = tlTracks.getBoundingClientRect();
            var total = totalDuration();
            if (total <= 0) return;
            var dt = (dx / r.width) * total;
            var newT = Math.max(0, startTlStart + dt);

            var snappedTo = null;
            if (state.snapEnabled) {
                var pixelThresholdSec = (8 / r.width) * total;
                var cands = [0];
                state.clips.forEach(function (cc, j) {
                    if (j === i) return;
                    cands.push(cc.tl_start);
                    cands.push(clipEnd(cc));
                });
                var best = null, bestD = pixelThresholdSec;
                cands.forEach(function (cc) {
                    var d = Math.abs(newT - cc);
                    if (d < bestD) { bestD = d; best = cc; }
                });
                if (best !== null) { newT = best; snappedTo = best; }
            }
            c.tl_start = Math.round(newT * 1000) / 1000;
            renderTimeline();
            if (snappedTo !== null && total > 0) {
                tlSnapline.style.display = 'block';
                tlSnapline.style.left = (snappedTo / total * 100) + '%';
            } else {
                tlSnapline.style.display = 'none';
            }
            syncProps();
        }
        function onUp() {
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onUp);
            document.removeEventListener('touchmove', onTouchMove);
            document.removeEventListener('touchend', onUp);
            tlSnapline.style.display = 'none';
            updateTotalInfo();
            renderCanvas();
        }
        function onMouseMove(e) { onMove(e.clientX); }
        function onTouchMove(e) {
            if (e.touches.length !== 1) return;
            e.preventDefault();
            onMove(e.touches[0].clientX);
        }

        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onUp);
        document.addEventListener('touchmove', onTouchMove, { passive: false });
        document.addEventListener('touchend', onUp);
    }

    function updatePlayhead() {
        var total = totalDuration();
        if (total <= 0) { tlPlayhead.style.left = '0%'; return; }
        var t = currentTlTime();
        t = clamp(t, 0, total);
        tlPlayhead.style.left = (t / total * 100) + '%';
    }

    /* ============================================================
       RULER CLICK — seek
       ============================================================ */
    function seekToTlPos(tlPos) {
        var total = totalDuration();
        if (total <= 0) return;
        if (tlPos < 0) tlPos = 0;
        if (tlPos > total) tlPos = total;

        var idx = -1;
        for (var i = 0; i < state.clips.length; i++) {
            var cc = state.clips[i];
            if (tlPos >= cc.tl_start - 0.0001 && tlPos <= clipEnd(cc) + 0.0001) {
                idx = i;
                break;
            }
        }
        if (idx === -1) {
            var best = -1, bestStart = Infinity;
            for (var j = 0; j < state.clips.length; j++) {
                if (state.clips[j].tl_start >= tlPos &&
                    state.clips[j].tl_start < bestStart) {
                    bestStart = state.clips[j].tl_start;
                    best = j;
                }
            }
            if (best === -1) return;
            idx = best;
            tlPos = state.clips[idx].tl_start;
        }

        var local = tlPos - state.clips[idx].tl_start;
        var newTime = state.clips[idx].in + local;
        if (newTime > state.clips[idx].out) newTime = state.clips[idx].out;
        if (newTime < state.clips[idx].in) newTime = state.clips[idx].in;

        if (idx !== state.activeIndex) {
            setActiveClip(idx, newTime);
        } else {
            try { video.currentTime = newTime; } catch (err) {}
            renderCanvas();
            updatePlayhead();
        }
    }

    tlRuler.addEventListener('mousedown', function (e) {
        var r = tlRuler.getBoundingClientRect();
        var pct = clamp((e.clientX - r.left) / r.width, 0, 1);
        var tlPos = pct * totalDuration();
        seekToTlPos(tlPos);
    });

    (function () {
        var dragRuler = false;
        tlRuler.addEventListener('mousedown', function () { dragRuler = true; });
        document.addEventListener('mousemove', function (e) {
            if (!dragRuler) return;
            var r = tlRuler.getBoundingClientRect();
            if (e.clientX < r.left - 5 || e.clientX > r.right + 5) return;
            var pct = clamp((e.clientX - r.left) / r.width, 0, 1);
            var tlPos = pct * totalDuration();
            seekToTlPos(tlPos);
        });
        document.addEventListener('mouseup', function () { dragRuler = false; });
    })();

    /* ============================================================
       STAGE CLICK — выбор клипа
       ============================================================ */
    function pickClipAtTarget(tx, ty) {
        for (var i = state.clips.length - 1; i >= 0; i--) {
            var c = state.clips[i];
            if (!c.video_on) continue;
            var vis = clipVisualSize(c);
            if (tx >= c.offset_x && tx <= c.offset_x + vis.w &&
                ty >= c.offset_y && ty <= c.offset_y + vis.h) {
                return i;
            }
        }
        return -1;
    }

    stageCanvas.addEventListener('mousedown', function (e) {
        if (state.cropEnabled) return;
        var p = clientToTarget(e.clientX, e.clientY);
        var idx = pickClipAtTarget(p.x, p.y);
        if (idx >= 0 && idx !== state.activeIndex) {
            setActiveClip(idx);
        }
    });

    /* ============================================================
       Props inputs
       ============================================================ */
    function updateTotalInfo() {
        var t = totalDuration();
        totalInfo.textContent = formatTime(t) + ' • ' + state.clips.length +
            ' clip(s) • ' + TARGET_W + '×' + TARGET_H;
    }

    /* ============================================================
       Player
       ============================================================ */
    function setActiveClip(i, restoreTime) {
        if (i < 0 || i >= state.clips.length) return;

        rememberCurrentTime();

        state.activeIndex = i;
        var c = state.clips[i];

        var target;
        if (typeof restoreTime === 'number' && isFinite(restoreTime)) {
            target = restoreTime;
        } else if (typeof c.lastTime === 'number' && isFinite(c.lastTime) && c.lastTime > 0) {
            target = c.lastTime;
        } else {
            target = c.in;
        }
        if (target < c.in) target = c.in;
        if (target > c.out) target = c.out;
        c.lastTime = target;

        var sameVideo = (state.lastLoadedVideoId === c.video_id);

        try {
            video.pause();
            if (!sameVideo) {
                video.src = '/video/' + c.video_id + '?v=' + Date.now();
                video.load();
                state.lastLoadedVideoId = c.video_id;
                video.addEventListener('loadedmetadata', function onMeta() {
                    video.removeEventListener('loadedmetadata', onMeta);
                    try { video.currentTime = target; } catch (e) {}
                    renderCanvas();
                    updatePlayhead();
                }, { once: true });
            } else {
                try { video.currentTime = target; } catch (e) {}
                renderCanvas();
                updatePlayhead();
            }
        } catch (e) {}

        renderTimeline();
        syncProps();
        renderActiveClipBox();
        renderCropOverlay();
        updateCropButtons();
    }

    function togglePlay() {
        var c = activeClip(); if (!c) return;
        if (video.paused) {
            if (video.currentTime < c.in || video.currentTime >= c.out) {
                try { video.currentTime = c.in; } catch (e) {}
            }
            video.play().catch(function () {});
        } else {
            video.pause();
        }
    }
    function stepFrame(dir) {
        var c = activeClip(); if (!c) return;
        video.pause();
        var nt = video.currentTime + dir * frameStep();
        if (nt < c.in) nt = c.in;
        if (nt > c.out) nt = c.out - frameStep();
        try { video.currentTime = nt; } catch (e) {}
        renderCanvas();
        updatePlayhead();
    }

    video.addEventListener('timeupdate', function () {
        var c = activeClip();
        if (c && video.readyState >= 1 && isFinite(video.currentTime)) {
            c.lastTime = video.currentTime;
        }
        renderCanvas();
        updatePlayhead();
        if (c && !video.paused && video.currentTime >= c.out - 0.03) {
            video.pause();
        }
    });
    video.addEventListener('play', function () { btnPlayPause.textContent = '⏸ Pause'; });
    video.addEventListener('pause', function () { btnPlayPause.textContent = '▶ Play'; });
    video.addEventListener('loadeddata', function () { renderCanvas(); });
    video.addEventListener('seeked', function () { renderCanvas(); });
    video.addEventListener('contextmenu', function (e) { e.preventDefault(); });

    btnPlayPause.addEventListener('click', togglePlay);
    btnStepBack.addEventListener('click', function () { stepFrame(-1); });
    btnStepForward.addEventListener('click', function () { stepFrame(1); });

    if (btnUndo) {
        btnUndo.addEventListener('click', function () { undo(); });
    }

    btnSetIn.addEventListener('click', function () {
        var c = activeClip(); if (!c) return;
        var v = video.currentTime;
        if (v < 0) v = 0;
        if (v > c.out - frameStep()) v = c.out - frameStep();
        snapshot();
        c.in = Math.round(v * 1000) / 1000;
        renderTimeline(); syncProps(); updateTotalInfo(); renderCanvas();
    });
    btnSetOut.addEventListener('click', function () {
        var c = activeClip(); if (!c) return;
        var v = video.currentTime;
        if (v < c.in + frameStep()) v = c.in + frameStep();
        if (v > c.src_duration) v = c.src_duration;
        snapshot();
        c.out = Math.round(v * 1000) / 1000;
        renderTimeline(); syncProps(); updateTotalInfo(); renderCanvas();
    });
    btnResetTrim.addEventListener('click', function () {
        var c = activeClip(); if (!c) return;
        snapshot();
        c.in = 0; c.out = c.src_duration;
        renderTimeline(); syncProps(); updateTotalInfo(); renderCanvas();
    });

    /* ============================================================
       Props inputs (bind)
       ============================================================ */
    propIn.addEventListener('change', function () {
        var c = activeClip(); if (!c) return;
        var v = parseFloat(this.value);
        if (!isFinite(v)) { syncProps(); return; }
        v = clamp(v, 0, c.out - frameStep());
        if (Math.abs(v - c.in) < 0.0001) return;
        snapshot();
        c.in = Math.round(v * 1000) / 1000;
        renderTimeline(); syncProps(); updateTotalInfo(); renderCanvas();
    });
    propOut.addEventListener('change', function () {
        var c = activeClip(); if (!c) return;
        var v = parseFloat(this.value);
        if (!isFinite(v)) { syncProps(); return; }
        v = clamp(v, c.in + frameStep(), c.src_duration);
        if (Math.abs(v - c.out) < 0.0001) return;
        snapshot();
        c.out = Math.round(v * 1000) / 1000;
        renderTimeline(); syncProps(); updateTotalInfo(); renderCanvas();
    });
    propTlStart.addEventListener('change', function () {
        var c = activeClip(); if (!c) return;
        var v = parseFloat(this.value);
        if (!isFinite(v)) { syncProps(); return; }
        v = Math.max(0, Math.round(v * 1000) / 1000);
        if (Math.abs(v - c.tl_start) < 0.0001) return;
        snapshot();
        c.tl_start = v;
        renderTimeline(); syncProps(); updateTotalInfo(); renderCanvas();
    });

    propScale.addEventListener('mousedown', function () { snapshot(); });
    propScale.addEventListener('touchstart', function () { snapshot(); }, { passive: true });
    propScale.addEventListener('input', function () {
        var c = activeClip(); if (!c) return;
        c.scale = clamp(parseFloat(this.value) || 1, 0.1, 3);
        propScaleVal.textContent = Number(c.scale).toFixed(2) + '×';
        clampClipIntoCanvas(c);
        syncProps();
        renderActiveClipBox();
        renderCanvas();
    });

    propOffsetX.addEventListener('change', function () {
        var c = activeClip(); if (!c) return;
        var v = parseInt(this.value, 10) || 0;
        if (v === c.offset_x) return;
        snapshot();
        c.offset_x = v;
        clampClipIntoCanvas(c);
        syncProps();
        renderActiveClipBox(); renderCanvas();
    });
    propOffsetY.addEventListener('change', function () {
        var c = activeClip(); if (!c) return;
        var v = parseInt(this.value, 10) || 0;
        if (v === c.offset_y) return;
        snapshot();
        c.offset_y = v;
        clampClipIntoCanvas(c);
        syncProps();
        renderActiveClipBox(); renderCanvas();
    });

    propVideoOn.addEventListener('change', function () {
        var c = activeClip(); if (!c) return;
        snapshot();
        c.video_on = this.checked;
        renderTimeline(); renderActiveClipBox(); renderCanvas();
    });
    propAudioOn.addEventListener('change', function () {
        var c = activeClip(); if (!c) return;
        snapshot();
        c.audio_on = this.checked;
        renderTimeline();
    });

    propMoveUp.addEventListener('click', function () {
        if (state.activeIndex <= 0) return;
        snapshot();
        var i = state.activeIndex;
        var t = state.clips[i - 1];
        state.clips[i - 1] = state.clips[i];
        state.clips[i] = t;
        state.activeIndex = i - 1;
        renderTimeline(); syncProps(); renderCanvas();
    });
    propMoveDown.addEventListener('click', function () {
        if (state.activeIndex >= state.clips.length - 1) return;
        snapshot();
        var i = state.activeIndex;
        var t = state.clips[i + 1];
        state.clips[i + 1] = state.clips[i];
        state.clips[i] = t;
        state.activeIndex = i + 1;
        renderTimeline(); syncProps(); renderCanvas();
    });
    propDelete.addEventListener('click', function () {
        if (state.clips.length <= 1) return;
        snapshot();
        state.clips.splice(state.activeIndex, 1);
        if (state.activeIndex >= state.clips.length) state.activeIndex = state.clips.length - 1;
        renderTimeline(); syncProps(); updateTotalInfo(); renderCanvas();
        setActiveClip(state.activeIndex);
    });

    document.querySelectorAll('[data-pos]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var c = activeClip(); if (!c) return;
            snapshot();
            var vis = clipVisualSize(c);
            var pos = this.dataset.pos;
            if (pos === 'center') {
                c.offset_x = Math.round((TARGET_W - vis.w) / 2);
                c.offset_y = Math.round((TARGET_H - vis.h) / 2);
            } else if (pos === 'fit') {
                var base = clipEffectiveBase(c);
                if (base.w && base.h) {
                    var sx = TARGET_W / base.w;
                    var sy = TARGET_H / base.h;
                    c.scale = clamp(Math.max(sx, sy), 0.1, 3);
                    vis = clipVisualSize(c);
                    c.offset_x = Math.round((TARGET_W - vis.w) / 2);
                    c.offset_y = Math.round((TARGET_H - vis.h) / 2);
                }
            } else if (pos === 'reset') {
                c.scale = 1.0;
                vis = clipVisualSize(c);
                c.offset_x = Math.round((TARGET_W - vis.w) / 2);
                c.offset_y = Math.round((TARGET_H - vis.h) / 2);
            }
            clampClipIntoCanvas(c);
            syncProps(); renderActiveClipBox(); renderCanvas();
        });
    });

    /* ============================================================
       Target (canvas)
       ============================================================ */
    function presetKey(w, h) { return String(w) + 'x' + String(h); }

    function syncPresetSelect() {
        if (!targetPreset) return;
        var key = presetKey(TARGET_W, TARGET_H);
        var found = false;
        for (var i = 0; i < targetPreset.options.length; i++) {
            if (targetPreset.options[i].value === key) {
                targetPreset.value = key;
                found = true;
                break;
            }
        }
        if (!found) targetPreset.value = '';
    }

    function syncTargetInputs() {
        targetWEl.value = TARGET_W;
        targetHEl.value = TARGET_H;
        targetFpsEl.value = TARGET_FPS;
        syncPresetSelect();
        editorMeta.textContent = 'canvas ' + TARGET_W + '×' + TARGET_H +
            ' @ ' + TARGET_FPS + ' fps';
        panelCanvasInfo.textContent = TARGET_W + '×' + TARGET_H;
    }

    function applyTargetFromInputs() {
        var nw = Math.max(16, parseInt(targetWEl.value, 10) || TARGET_W);
        var nh = Math.max(16, parseInt(targetHEl.value, 10) || TARGET_H);
        var nf = Math.max(1, parseFloat(targetFpsEl.value) || TARGET_FPS);
        if (nw === TARGET_W && nh === TARGET_H && nf === TARGET_FPS) {
            syncTargetInputs();
            return;
        }
        snapshot();
        TARGET_W = nw; TARGET_H = nh; TARGET_FPS = nf;
        clampAllClips();
        syncTargetInputs();
        layoutStage();
        renderCanvas();
        renderActiveClipBox();
        renderCropOverlay();
        renderTimeline();
        syncProps();
        updateTotalInfo();
    }
    targetWEl.addEventListener('change', applyTargetFromInputs);
    targetHEl.addEventListener('change', applyTargetFromInputs);
    targetFpsEl.addEventListener('change', applyTargetFromInputs);

    if (targetPreset) {
        targetPreset.addEventListener('change', function () {
            var v = this.value;
            if (!v) return;
            var parts = v.split('x');
            var w = parseInt(parts[0], 10);
            var h = parseInt(parts[1], 10);
            if (!w || !h) return;
            if (w === TARGET_W && h === TARGET_H) return;
            snapshot();
            TARGET_W = w;
            TARGET_H = h;
            clampAllClips();
            syncTargetInputs();
            layoutStage();
            renderCanvas();
            renderActiveClipBox();
            renderCropOverlay();
            renderTimeline();
            syncProps();
            updateTotalInfo();
        });
    }

    function setTargetRatio(ar_w, ar_h) {
        var w = TARGET_W;
        var h = Math.round(w * ar_h / ar_w);
        if (h % 2) h += 1;
        if (h === TARGET_H) return;
        snapshot();
        TARGET_H = h;
        clampAllClips();
        syncTargetInputs();
        layoutStage();
        renderCanvas();
        renderActiveClipBox();
        renderCropOverlay();
    }
    btnTargetFromFirst.addEventListener('click', function () {
        var c = state.clips[0];
        if (!c) return;
        if (c.src_width === TARGET_W && c.src_height === TARGET_H) return;
        snapshot();
        TARGET_W = c.src_width || TARGET_W;
        TARGET_H = c.src_height || TARGET_H;
        clampAllClips();
        syncTargetInputs();
        layoutStage();
        renderCanvas();
        renderActiveClipBox();
        renderCropOverlay();
    });
    btnTargetSquare.addEventListener('click', function () { setTargetRatio(1, 1); });
    btnTargetLandscape.addEventListener('click', function () { setTargetRatio(16, 9); });
    btnTargetVertical.addEventListener('click', function () { setTargetRatio(9, 16); });
    btnTarget4x5.addEventListener('click', function () { setTargetRatio(4, 5); });
    btnTarget3x4.addEventListener('click', function () { setTargetRatio(3, 4); });

    chkSnap.addEventListener('change', function () {
        state.snapEnabled = this.checked;
    });

    /* ============================================================
       Crop enable/presets
       ============================================================ */
    chkCropEnabled.addEventListener('change', function () {
        var wasEnabled = state.cropEnabled;
        state.cropEnabled = this.checked;
        if (state.cropEnabled && !state.crop) state.crop = defaultCrop();
        if (wasEnabled !== state.cropEnabled) snapshot();
        renderCropOverlay();
    });
    btnCropFull.addEventListener('click', function () {
        snapshot();
        state.crop = defaultCrop();
        if (!state.cropEnabled) {
            chkCropEnabled.checked = true;
            state.cropEnabled = true;
        }
        renderCropOverlay();
    });
    document.querySelectorAll('[data-croppreset]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            snapshot();
            if (!state.cropEnabled) {
                chkCropEnabled.checked = true;
                state.cropEnabled = true;
            }
            var parts = this.dataset.croppreset.split('x');
            var ar_w = parseInt(parts[0], 10);
            var ar_h = parseInt(parts[1], 10);
            var w = TARGET_W, h = Math.round(w * ar_h / ar_w);
            if (h > TARGET_H) { h = TARGET_H; w = Math.round(h * ar_w / ar_h); }
            if (w > TARGET_W) { w = TARGET_W; h = Math.round(w * ar_h / ar_w); }
            state.crop = {
                x: Math.floor((TARGET_W - w) / 2),
                y: Math.floor((TARGET_H - h) / 2),
                w: w, h: h,
            };
            renderCropOverlay();
        });
    });
    [cropXEl, cropYEl, cropWEl, cropHEl].forEach(function (inp) {
        inp.addEventListener('change', function () {
            snapshot();
            setCropFromInputs();
        });
        inp.addEventListener('blur', setCropFromInputs);
        inp.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); setCropFromInputs(); }
        });
    });

    if (btnApplyCrop) {
        btnApplyCrop.addEventListener('click', applyCropToActiveClip);
    }
    if (btnRevertCrop) {
        btnRevertCrop.addEventListener('click', revertCropForActiveClip);
    }

    /* ============================================================
       Drag активного клипа на canvas
       ============================================================ */
    function clipBoxDragStart(e) {
        if (!activeClip()) return;
        e.preventDefault(); e.stopPropagation();
        var c = activeClip();
        var startClientX = e.clientX;
        var startClientY = e.clientY;
        var snap = { ox: c.offset_x, oy: c.offset_y };
        var snapshotted = false;

        function onMove(ev) {
            if (!snapshotted) {
                snapshot();
                snapshotted = true;
            }
            var r = stageRect();
            var dx = (ev.clientX - startClientX) / r.width * TARGET_W;
            var dy = (ev.clientY - startClientY) / r.height * TARGET_H;
            c.offset_x = Math.round(snap.ox + dx);
            c.offset_y = Math.round(snap.oy + dy);
            clampClipIntoCanvas(c);
            syncProps();
            renderActiveClipBox();
            renderCanvas();
        }
        function onUp() {
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            document.removeEventListener('touchmove', onTouchMove);
            document.removeEventListener('touchend', onUp);
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        }
        function onTouchMove(ev) {
            if (ev.touches.length !== 1) return;
            ev.preventDefault();
            onMove({ clientX: ev.touches[0].clientX, clientY: ev.touches[0].clientY });
        }
        document.body.style.cursor = 'move';
        document.body.style.userSelect = 'none';
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        document.addEventListener('touchmove', onTouchMove, { passive: false });
        document.addEventListener('touchend', onUp);
    }

    activeClipBox.addEventListener('mousedown', function (e) {
        if (e.target.classList.contains('clip-resize')) return;
        clipBoxDragStart(e);
    });
    activeClipBox.addEventListener('touchstart', function (e) {
        if (e.touches.length !== 1) return;
        if (e.target.classList.contains('clip-resize')) return;
        clipBoxDragStart({
            preventDefault: function () { e.preventDefault(); },
            stopPropagation: function () { e.stopPropagation(); },
            clientX: e.touches[0].clientX,
            clientY: e.touches[0].clientY,
        });
    }, { passive: false });

    function startClipResize(handle, clientX0, clientY0, ev) {
        var c = activeClip();
        if (!c) return;
        if (ev && ev.preventDefault) ev.preventDefault();
        if (ev && ev.stopPropagation) ev.stopPropagation();

        var base = clipEffectiveBase(c);
        var snap = {
            ox: c.offset_x, oy: c.offset_y,
            sw: base.w * c.scale,
            sh: base.h * c.scale,
            src_w: base.w,
            src_h: base.h,
        };
        var snapshotted = false;

        function onMove(ev2) {
            if (!snapshotted) {
                snapshot();
                snapshotted = true;
            }
            var r = stageRect();
            var dx = (ev2.clientX - clientX0) / r.width * TARGET_W;
            var dy = (ev2.clientY - clientY0) / r.height * TARGET_H;

            var x0 = snap.ox, y0 = snap.oy;
            var x1 = x0 + snap.sw, y1 = y0 + snap.sh;

            if (handle.indexOf('w') !== -1) x0 = x0 + dx;
            if (handle.indexOf('e') !== -1) x1 = x1 + dx;
            if (handle.indexOf('n') !== -1) y0 = y0 + dy;
            if (handle.indexOf('s') !== -1) y1 = y1 + dy;

            var newW = Math.max(20, x1 - x0);
            var newScale = clamp(newW / snap.src_w, 0.1, 3);
            var realW = snap.src_w * newScale;
            var realH = snap.src_h * newScale;

            var nOx = snap.ox, nOy = snap.oy;
            if (handle.indexOf('w') !== -1) nOx = x1 - realW;
            if (handle.indexOf('n') !== -1) nOy = y1 - realH;

            c.scale = newScale;
            c.offset_x = Math.round(nOx);
            c.offset_y = Math.round(nOy);
            clampClipIntoCanvas(c);
            syncProps();
            renderActiveClipBox();
            renderCanvas();
        }
        function onUp() {
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            document.removeEventListener('touchmove', onTouchMove);
            document.removeEventListener('touchend', onUp);
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        }
        function onTouchMove(ev2) {
            if (ev2.touches.length !== 1) return;
            ev2.preventDefault();
            onMove({ clientX: ev2.touches[0].clientX, clientY: ev2.touches[0].clientY });
        }
        document.body.style.userSelect = 'none';
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        document.addEventListener('touchmove', onTouchMove, { passive: false });
        document.addEventListener('touchend', onUp);
    }

    activeClipBox.querySelectorAll('.clip-resize').forEach(function (h) {
        h.addEventListener('mousedown', function (e) {
            startClipResize(h.dataset.h, e.clientX, e.clientY, e);
        });
        h.addEventListener('touchstart', function (e) {
            if (e.touches.length !== 1) return;
            startClipResize(h.dataset.h, e.touches[0].clientX, e.touches[0].clientY, e);
        }, { passive: false });
    });

    /* ============================================================
       Crop drag/resize (перетаскивание crop-прямоугольника)
       ============================================================ */
    cropBox.addEventListener('mousedown', function (e) {
        if (e.target.classList.contains('crop-handle')) return;
        startCropMove(e.clientX, e.clientY, e);
    });
    cropBox.addEventListener('touchstart', function (e) {
        if (e.touches.length !== 1) return;
        if (e.target.classList.contains('crop-handle')) return;
        startCropMove(e.touches[0].clientX, e.touches[0].clientY, e);
    }, { passive: false });

    function startCropMove(cx0, cy0, ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        if (ev && ev.stopPropagation) ev.stopPropagation();
        var snap = { x: state.crop.x, y: state.crop.y };
        var snapshotted = false;

        function onMove(ev2) {
            if (!snapshotted) {
                snapshot();
                snapshotted = true;
            }
            var r = stageRect();
            var dx = (ev2.clientX - cx0) / r.width * TARGET_W;
            var dy = (ev2.clientY - cy0) / r.height * TARGET_H;
            var nx = clamp(snap.x + dx, 0, TARGET_W - state.crop.w);
            var ny = clamp(snap.y + dy, 0, TARGET_H - state.crop.h);
            state.crop.x = Math.round(nx);
            state.crop.y = Math.round(ny);
            renderCropOverlay();
        }
        function onUp() {
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            document.removeEventListener('touchmove', onTouchMove);
            document.removeEventListener('touchend', onUp);
            document.body.style.cursor = '';
        }
        function onTouchMove(ev2) {
            if (ev2.touches.length !== 1) return;
            ev2.preventDefault();
            onMove({ clientX: ev2.touches[0].clientX, clientY: ev2.touches[0].clientY });
        }
        document.body.style.cursor = 'move';
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        document.addEventListener('touchmove', onTouchMove, { passive: false });
        document.addEventListener('touchend', onUp);
    }

    cropBox.querySelectorAll('.crop-handle').forEach(function (h) {
        h.addEventListener('mousedown', function (e) {
            startCropResize(h.dataset.h, e.clientX, e.clientY, e);
        });
        h.addEventListener('touchstart', function (e) {
            if (e.touches.length !== 1) return;
            startCropResize(h.dataset.h, e.touches[0].clientX, e.touches[0].clientY, e);
        }, { passive: false });
    });

    function startCropResize(handle, cx0, cy0, ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        if (ev && ev.stopPropagation) ev.stopPropagation();
        var snap = {
            x: state.crop.x, y: state.crop.y,
            w: state.crop.w, h: state.crop.h,
        };
        var snapshotted = false;

        function onMove(ev2) {
            if (!snapshotted) {
                snapshot();
                snapshotted = true;
            }
            var r = stageRect();
            var dx = (ev2.clientX - cx0) / r.width * TARGET_W;
            var dy = (ev2.clientY - cy0) / r.height * TARGET_H;
            var l = snap.x, t = snap.y, rt = snap.x + snap.w, b = snap.y + snap.h;
            if (handle.indexOf('w') !== -1) l += dx;
            if (handle.indexOf('e') !== -1) rt += dx;
            if (handle.indexOf('n') !== -1) t += dy;
            if (handle.indexOf('s') !== -1) b += dy;
            if (l < 0) l = 0;
            if (t < 0) t = 0;
            if (rt > TARGET_W) rt = TARGET_W;
            if (b > TARGET_H) b = TARGET_H;
            if (rt - l < 16) { if (handle.indexOf('w') !== -1) l = rt - 16; else rt = l + 16; }
            if (b - t < 16) { if (handle.indexOf('n') !== -1) t = b - 16; else b = t + 16; }
            state.crop.x = Math.round(l);
            state.crop.y = Math.round(t);
            state.crop.w = Math.round(rt - l);
            state.crop.h = Math.round(b - t);
            renderCropOverlay();
        }
        function onUp() {
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            document.removeEventListener('touchmove', onTouchMove);
            document.removeEventListener('touchend', onUp);
            document.body.style.cursor = '';
        }
        function onTouchMove(ev2) {
            if (ev2.touches.length !== 1) return;
            ev2.preventDefault();
            onMove({ clientX: ev2.touches[0].clientX, clientY: ev2.touches[0].clientY });
        }
        document.body.style.userSelect = 'none';
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        document.addEventListener('touchmove', onTouchMove, { passive: false });
        document.addEventListener('touchend', onUp);
    }

    /* ============================================================
       Add clip modal
       ============================================================ */
    var pickerAllVideos = [];
    var pickerSelectedId = null;

    function loadPickerList() {
        videoPicker.innerHTML = '<div class="text-muted" style="grid-column:1/-1; padding:20px;">Loading…</div>';
        fetch('/editor/list_videos')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                pickerAllVideos = data.videos || [];
                renderPickerList();
            })
            .catch(function (err) {
                console.error(err);
                videoPicker.innerHTML = '<div class="text-danger" style="grid-column:1/-1; padding:20px;">Error</div>';
            });
    }
    function renderPickerList() {
        var q = (pickerSearch.value || '').trim().toLowerCase();
        var list = pickerAllVideos;
        if (q) list = list.filter(function (v) {
            return (v.filename || '').toLowerCase().indexOf(q) !== -1;
        });
        videoPicker.innerHTML = '';
        if (!list.length) {
            videoPicker.innerHTML = '<div class="text-muted" style="grid-column:1/-1; padding:20px;">No videos</div>';
            return;
        }
        list.forEach(function (v) {
            var item = document.createElement('div');
            item.className = 'picker-item' + (pickerSelectedId === v.id ? ' active' : '');
            item.dataset.videoId = v.id;
            var img = document.createElement('img');
            img.src = '/thumbnail/' + v.id;
            img.loading = 'lazy'; img.alt = '';
            item.appendChild(img);
            var nm = document.createElement('div');
            nm.className = 'pname'; nm.textContent = v.filename; nm.title = v.filename;
            item.appendChild(nm);
            var chk = document.createElement('div');
            chk.className = 'pcheck'; chk.textContent = '✓';
            item.appendChild(chk);
            item.addEventListener('click', function () {
                pickerSelectedId = v.id;
                renderPickerList();
                confirmAddClipBtn.disabled = false;
            });
            videoPicker.appendChild(item);
        });
    }
    pickerSearch.addEventListener('input', renderPickerList);
    btnAddClip.addEventListener('click', function () {
        pickerSelectedId = null;
        pickerSearch.value = '';
        confirmAddClipBtn.disabled = true;
        loadPickerList();
        addClipModal.show();
    });
    confirmAddClipBtn.addEventListener('click', function () {
        if (!pickerSelectedId) return;
        fetch('/editor/' + pickerSelectedId + '/info')
            .then(function (r) { return r.json(); })
            .then(function (meta) {
                if (meta.error) { alert('Error: ' + meta.error); return; }

                snapshot();

                var newTlStart = 0;
                if (state.snapEnabled && state.clips.length) {
                    var maxEnd = 0;
                    state.clips.forEach(function (c) {
                        var e = clipEnd(c);
                        if (e > maxEnd) maxEnd = e;
                    });
                    newTlStart = maxEnd;
                }

                var scale = 1.0;
                if (meta.width > TARGET_W || meta.height > TARGET_H) {
                    var sx = TARGET_W / meta.width;
                    var sy = TARGET_H / meta.height;
                    scale = Math.min(sx, sy) * 0.9;
                }
                var sw = meta.width * scale;
                var sh = meta.height * scale;
                var ox = Math.round((TARGET_W - sw) / 2);
                var oy = Math.round((TARGET_H - sh) / 2);

                var newClip = {
                    video_id: meta.id,
                    filename: meta.filename,
                    src_duration: meta.duration || 0,
                    src_width: meta.width || 0,
                    src_height: meta.height || 0,
                    has_audio: !!meta.has_audio,
                    in: 0,
                    out: meta.duration || 0,
                    tl_start: newTlStart,
                    scale: scale,
                    offset_x: ox,
                    offset_y: oy,
                    video_on: true,
                    audio_on: !!meta.has_audio,
                    lastTime: 0,
                    crop: null,
                };
                clampClipIntoCanvas(newClip);
                state.clips.push(newClip);
                addClipModal.hide();
                renderTimeline();
                setActiveClip(state.clips.length - 1);
                updateTotalInfo();
            })
            .catch(function (err) { alert('Network error: ' + err); });
    });

    /* ============================================================
       Save flow
       ============================================================ */
    function updateSavePathPreview() {
        if (!saveInfoPath) return;
        var c0 = state.clips[0];
        if (!c0) { saveInfoPath.textContent = '—'; return; }

        var fullPath = CFG.videoFilepath || c0.filename;
        var overwrite = chkOverwriteSave && chkOverwriteSave.checked;

        if (overwrite) {
            saveInfoPath.textContent = fullPath;
        } else {
            var parts = splitPath(fullPath);
            saveInfoPath.textContent = parts.dir + parts.base + '_edit_<timestamp>' + parts.ext;
        }
    }

    function openSaveModal() {
        if (!state.clips.length) return;
        saveInfoClips.textContent = state.clips.length;
        saveInfoDuration.textContent = formatTime(totalDuration());
        saveInfoCanvas.textContent = TARGET_W + '×' + TARGET_H + ' @ ' + TARGET_FPS;

        var croppedCount = state.clips.filter(function (c) { return !!c.crop; }).length;
        if (croppedCount > 0) {
            saveInfoCrop.textContent = croppedCount + ' clip(s) cropped';
        } else {
            saveInfoCrop.textContent = '—';
        }

        updateSavePathPreview();
        if (saveStatusEl) { saveStatusEl.textContent = ''; saveStatusEl.className = 'editor-status'; }
        confirmSaveBtn.disabled = false;
        confirmSaveBtn.textContent = 'Save';
        saveModal.show();
    }
    btnSave.addEventListener('click', openSaveModal);

    if (chkOverwriteSave) {
        chkOverwriteSave.addEventListener('change', updateSavePathPreview);
    }

    function getSelectedQuality() {
        var el = document.querySelector('input[name="save-quality"]:checked');
        return el ? el.value : 'accurate';
    }

    function performSave(quality, overwrite) {
        try {
            video.pause();
            video.removeAttribute('src');
            video.load();
            state.lastLoadedVideoId = null;
        } catch (e) {}

        confirmSaveBtn.disabled = true;
        confirmSaveBtn.textContent = 'Saving…';
        if (saveStatusEl) {
            saveStatusEl.textContent = '⏳ Processing with ffmpeg…';
            saveStatusEl.className = 'editor-status';
        }

        var payload = {
            clips: state.clips.map(function (c) {
                return {
                    video_id: c.video_id,
                    in: c.in, out: c.out,
                    tl_start: c.tl_start,
                    scale: c.scale,
                    offset_x: c.offset_x, offset_y: c.offset_y,
                    video_on: c.video_on, audio_on: c.audio_on,
                    crop: c.crop ? { x: c.crop.x, y: c.crop.y,
                                     w: c.crop.w, h: c.crop.h } : null,
                };
            }),
            crop: null,   // глобальный crop больше не отправляем
            overwrite: overwrite,
            quality: quality,
            target_w: TARGET_W,
            target_h: TARGET_H,
            target_fps: TARGET_FPS,
        };

        setTimeout(function () {
            fetch('/editor/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        if (saveStatusEl) {
                            saveStatusEl.textContent =
                                (data.fallback ? '⚠️ ' : '✅ ') + 'Saved: ' + data.filename;
                            saveStatusEl.className = 'editor-status ' +
                                (data.fallback ? 'error' : 'success');
                        }
                        if (saveInfoPath && data.filepath) {
                            saveInfoPath.textContent = data.filepath;
                        }
                        setTimeout(function () {
                            if (saveModal) saveModal.hide();
                            window.location.href =
                                '/watch/' + data.video_id +
                                '?profile=' + encodeURIComponent(CFG.currentProfile || 'female');
                        }, 1400);
                    } else {
                        if (saveStatusEl) {
                            saveStatusEl.textContent = '❌ ' + (data.error || 'Save failed');
                            saveStatusEl.className = 'editor-status error';
                        }
                        confirmSaveBtn.disabled = false;
                        confirmSaveBtn.textContent = 'Save';
                    }
                })
                .catch(function (e) {
                    if (saveStatusEl) {
                        saveStatusEl.textContent = '❌ Network error: ' + e;
                        saveStatusEl.className = 'editor-status error';
                    }
                    confirmSaveBtn.disabled = false;
                    confirmSaveBtn.textContent = 'Save';
                });
        }, 350);
    }

    confirmSaveBtn.addEventListener('click', function () {
        var quality = getSelectedQuality();
        var overwrite = chkOverwriteSave ? chkOverwriteSave.checked : true;

        if (overwrite && overwriteConfirmModal) {
            var firstClip = state.clips[0];
            if (overwriteFileNameEl) {
                overwriteFileNameEl.textContent = firstClip
                    ? '"' + firstClip.filename + '"'
                    : 'this file';
            }
            overwriteConfirmModal.show();
            return;
        }

        if (overwrite) {
            if (!confirm('Overwrite the FIRST clip\'s original file? This cannot be undone.')) return;
        }
        performSave(quality, overwrite);
    });

    if (confirmOverwriteBtn) {
        confirmOverwriteBtn.addEventListener('click', function () {
            if (overwriteConfirmModal) overwriteConfirmModal.hide();
            performSave(getSelectedQuality(), true);
        });
    }

    /* ============================================================
       INIT
       ============================================================ */
    function initInitialClip() {
        TARGET_W = DEFAULT_TARGET_W;
        TARGET_H = DEFAULT_TARGET_H;
        TARGET_FPS = META.fps || 30;

        state.clips = [{
            video_id: CFG.videoId,
            filename: CFG.videoFilename,
            src_duration: META.duration || 0,
            src_width: META.width || 0,
            src_height: META.height || 0,
            has_audio: !!META.hasAudio,
            in: 0,
            out: META.duration || 0,
            tl_start: 0,
            scale: 1.0,
            offset_x: 0,
            offset_y: 0,
            video_on: true,
            audio_on: !!META.hasAudio,
            lastTime: 0,
            crop: null,
        }];

        var c0 = state.clips[0];
        if (c0.src_width && c0.src_height) {
            var sx = TARGET_W / c0.src_width;
            var sy = TARGET_H / c0.src_height;
            var s = Math.min(sx, sy);
            c0.scale = s;
            c0.offset_x = Math.round((TARGET_W - c0.src_width * s) / 2);
            c0.offset_y = Math.round((TARGET_H - c0.src_height * s) / 2);
        }
        clampClipIntoCanvas(c0);

        state.activeIndex = 0;
        state.crop = defaultCrop();
        state.cropEnabled = false;
        state.history = [];
        chkCropEnabled.checked = false;

        syncTargetInputs();
        layoutStage();
        renderTimeline();
        syncProps();
        renderCropOverlay();
        renderActiveClipBox();
        updateTotalInfo();
        updateUndoBtn();
        updateCropButtons();

        setActiveClip(0);
    }

    video.addEventListener('loadeddata', function () { renderCanvas(); });

    window.addEventListener('resize', function () { layoutStage(); });
    window.addEventListener('beforeunload', function () { rememberCurrentTime(); });

    document.addEventListener('keydown', function (e) {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        if (addClipModalEl && addClipModalEl.classList.contains('show')) return;
        if (saveModalEl && saveModalEl.classList.contains('show')) return;
        if (overwriteConfirmModalEl && overwriteConfirmModalEl.classList.contains('show')) return;

        if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z')) {
            e.preventDefault();
            undo();
            return;
        }

        if (e.key === 'ArrowLeft') { e.preventDefault(); stepFrame(-1); }
        else if (e.key === 'ArrowRight') { e.preventDefault(); stepFrame(1); }
        else if (e.key === ' ') { e.preventDefault(); togglePlay(); }
    });

    initInitialClip();
    renderCanvas();

    console.log('[editor] ready. canvas=' + TARGET_W + 'x' + TARGET_H + '@' + TARGET_FPS);
})();