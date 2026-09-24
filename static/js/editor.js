/* ============================================================
   editor.js — страница /editor/<video_id>

   • Таймлайн с двумя "ручками" START / END.
   • Клик по треку — seek.
   • Drag ручек — точная подстройка границ.
   • Покадровое перемещение (◀| |▶), шаг = 1/FPS.
   • Set START / Set END — снять текущую позицию как границу.
   • Preview selection — проиграть выделенный отрезок.
   • Save — модалка с настройками (Fast/Accurate, overwrite).
   • High precision — включает точный seek.

   Про overwrite на Windows:
     Пока <video> стримит исходный файл через /video/<id>, серверный
     send_file держит файл открытым, и os.replace падает с WinError 5.
     Поэтому перед POST мы делаем video.load() с пустым src — это
     закрывает HTTP-стрим. Дополнительно сервер ретраит replace.

   Конфиг: window.EDITOR_CONFIG.
   ============================================================ */
(function () {
    'use strict';

    var CFG = window.EDITOR_CONFIG || {};
    var VIDEO_ID = CFG.videoId;
    var FPS = CFG.fps || 30;
    var FRAME_STEP = 1 / FPS;

    var video = document.getElementById('editorVideo');
    if (!video) return;

    var tlTrack = document.getElementById('tlTrack');
    var tlSelection = document.getElementById('tlSelection');
    var tlCursor = document.getElementById('tlCursor');
    var tlHandleStart = document.getElementById('tlHandleStart');
    var tlHandleEnd = document.getElementById('tlHandleEnd');
    var tlDuration = document.getElementById('tlDuration');

    var infoStart = document.getElementById('infoStart');
    var infoEnd = document.getElementById('infoEnd');
    var infoDuration = document.getElementById('infoDuration');
    var infoCurrent = document.getElementById('infoCurrent');
    var statusEl = document.getElementById('editorStatus');

    var chkHighPrecision = document.getElementById('chkHighPrecision');

    var btnPlayPause = document.getElementById('btnPlayPause');
    var btnStepBack = document.getElementById('btnStepBack');
    var btnStepForward = document.getElementById('btnStepForward');
    var btnJumpStart = document.getElementById('btnJumpStart');
    var btnJumpEnd = document.getElementById('btnJumpEnd');
    var btnSetStart = document.getElementById('btnSetStart');
    var btnSetEnd = document.getElementById('btnSetEnd');
    var btnPreview = document.getElementById('btnPreview');
    var btnReset = document.getElementById('btnReset');
    var btnSave = document.getElementById('btnSave');

    /* ---- Модалка сохранения ---- */
    var saveModalEl = document.getElementById('saveModal');
    var saveModal = saveModalEl ? new bootstrap.Modal(saveModalEl) : null;
    var saveInfoStart = document.getElementById('saveInfoStart');
    var saveInfoEnd = document.getElementById('saveInfoEnd');
    var saveInfoDuration = document.getElementById('saveInfoDuration');
    var saveStatusEl = document.getElementById('saveStatus');
    var confirmSaveBtn = document.getElementById('confirmSaveBtn');
    var chkOverwrite = document.getElementById('chkOverwrite');

    var state = {
        start: 0,
        end: 0,
        duration: 0,
        previewing: false,
        previewStopAt: 0,
    };

    /* ---------------- Форматирование времени ---------------- */
    function formatTime(t) {
        if (!isFinite(t) || t < 0) t = 0;
        var m = Math.floor(t / 60);
        var s = Math.floor(t % 60);
        var ms = Math.floor((t % 1) * 1000);
        return m + ':' + (s < 10 ? '0' : '') + s + '.' + ('00' + ms).slice(-3);
    }

    /* ---------------- Обновление UI ---------------- */
    function updateInfo() {
        infoStart.textContent = formatTime(state.start);
        infoEnd.textContent = formatTime(state.end);
        infoDuration.textContent = formatTime(Math.max(0, state.end - state.start));
        infoCurrent.textContent = formatTime(video.currentTime || 0);
    }

    function updateTimeline() {
        if (!state.duration) return;
        var pctS = (state.start / state.duration) * 100;
        var pctE = (state.end / state.duration) * 100;
        var width = Math.max(0, pctE - pctS);
        tlSelection.style.left = pctS + '%';
        tlSelection.style.width = width + '%';
        tlHandleStart.style.left = pctS + '%';
        tlHandleEnd.style.left = pctE + '%';
    }

    function updateCursor() {
        if (!state.duration) return;
        var pct = (video.currentTime / state.duration) * 100;
        tlCursor.style.left = pct + '%';
        updateInfo();
    }

    /* ---------------- Seek ---------------- */
    function seekTo(t) {
        t = Math.max(0, Math.min(state.duration, t));
        if (chkHighPrecision.checked) {
            video.pause();
        }
        try {
            video.currentTime = t;
        } catch (e) {}
        updateCursor();
    }

    function stepFrame(dir) {
        if (!state.duration) return;
        video.pause();
        var step = chkHighPrecision.checked ? FRAME_STEP : Math.max(FRAME_STEP, 1 / 30);
        var nt = video.currentTime + dir * step;
        nt = Math.max(0, Math.min(state.duration, nt));
        try {
            video.currentTime = nt;
        } catch (e) {}
        updateCursor();
    }

    /* ---------------- START / END ---------------- */
    function setStart() {
        var t = video.currentTime;
        if (t >= state.end) t = Math.max(0, state.end - FRAME_STEP);
        state.start = Math.max(0, t);
        updateTimeline();
        updateInfo();
    }

    function setEnd() {
        var t = video.currentTime;
        if (t <= state.start) t = Math.min(state.duration, state.start + FRAME_STEP);
        state.end = Math.min(state.duration, t);
        updateTimeline();
        updateInfo();
    }

    function resetSelection() {
        state.start = 0;
        state.end = state.duration;
        updateTimeline();
        updateInfo();
    }

    /* ---------------- Preview ---------------- */
    function previewSelection() {
        if (state.previewing) return;
        if (state.end <= state.start) return;
        state.previewing = true;
        state.previewStopAt = state.end;

        try { video.currentTime = state.start; } catch (e) {}
        video.play().catch(function () {});

        function onTime() {
            if (video.currentTime >= state.previewStopAt) {
                video.pause();
                video.removeEventListener('timeupdate', onTime);
                state.previewing = false;
            }
        }
        video.addEventListener('timeupdate', onTime);
    }

    /* ---------------- Timeline click ---------------- */
    tlTrack.addEventListener('click', function (e) {
        if (e.target.classList.contains('tl-handle')) return;
        if (!state.duration) return;
        var rect = tlTrack.getBoundingClientRect();
        var pct = (e.clientX - rect.left) / rect.width;
        pct = Math.max(0, Math.min(1, pct));
        seekTo(pct * state.duration);
    });

    /* ---------------- Draggable handles ---------------- */
    function makeDraggable(handle, isStart) {
        var dragging = false;

        function pctFromClient(clientX) {
            var rect = tlTrack.getBoundingClientRect();
            var pct = (clientX - rect.left) / rect.width;
            return Math.max(0, Math.min(1, pct));
        }

        function onMove(clientX) {
            if (!dragging || !state.duration) return;
            var t = pctFromClient(clientX) * state.duration;
            if (isStart) {
                if (t > state.end - FRAME_STEP) t = state.end - FRAME_STEP;
                if (t < 0) t = 0;
                state.start = t;
            } else {
                if (t < state.start + FRAME_STEP) t = state.start + FRAME_STEP;
                if (t > state.duration) t = state.duration;
                state.end = t;
            }
            updateTimeline();
            updateInfo();
        }

        function onDown(e) {
            e.preventDefault();
            e.stopPropagation();
            dragging = true;
            document.body.style.userSelect = 'none';
        }

        function onUp() {
            if (!dragging) return;
            dragging = false;
            document.body.style.userSelect = '';
        }

        handle.addEventListener('mousedown', onDown);
        document.addEventListener('mousemove', function (e) {
            if (dragging) onMove(e.clientX);
        });
        document.addEventListener('mouseup', onUp);

        handle.addEventListener('touchstart', onDown, { passive: false });
        document.addEventListener('touchmove', function (e) {
            if (dragging && e.touches.length) onMove(e.touches[0].clientX);
        }, { passive: false });
        document.addEventListener('touchend', onUp);
        document.addEventListener('touchcancel', onUp);
    }

    makeDraggable(tlHandleStart, true);
    makeDraggable(tlHandleEnd, false);

    /* ---------------- Кнопки ---------------- */
    btnPlayPause.addEventListener('click', function () {
        if (video.paused) video.play().catch(function () {});
        else video.pause();
    });
    btnStepBack.addEventListener('click', function () { stepFrame(-1); });
    btnStepForward.addEventListener('click', function () { stepFrame(1); });
    btnJumpStart.addEventListener('click', function () { seekTo(state.start); });
    btnJumpEnd.addEventListener('click', function () { seekTo(state.end); });
    btnSetStart.addEventListener('click', setStart);
    btnSetEnd.addEventListener('click', setEnd);
    btnPreview.addEventListener('click', previewSelection);
    btnReset.addEventListener('click', resetSelection);

    /* ---------------- Модалка сохранения ---------------- */
    btnSave.addEventListener('click', function () {
        if (state.end - state.start < FRAME_STEP) {
            statusEl.textContent = '❌ Selection is too small';
            statusEl.className = 'editor-status error';
            return;
        }

        statusEl.textContent = '';
        statusEl.className = 'editor-status';

        // Заполняем сводку в модалке
        if (saveInfoStart) saveInfoStart.textContent = formatTime(state.start);
        if (saveInfoEnd) saveInfoEnd.textContent = formatTime(state.end);
        if (saveInfoDuration) {
            saveInfoDuration.textContent = formatTime(Math.max(0, state.end - state.start));
        }

        // Сбрасываем статус модалки
        if (saveStatusEl) {
            saveStatusEl.textContent = '';
            saveStatusEl.className = 'save-status';
        }

        // Возвращаем кнопку в исходное состояние
        if (confirmSaveBtn) {
            confirmSaveBtn.disabled = false;
            confirmSaveBtn.textContent = 'Save';
        }

        if (saveModal) saveModal.show();
    });

    if (confirmSaveBtn) {
        confirmSaveBtn.addEventListener('click', function () {
            var modeEl = document.querySelector('input[name="save-mode"]:checked');
            var mode = modeEl ? modeEl.value : 'fast';
            var overwrite = chkOverwrite ? chkOverwrite.checked : true;

            if (overwrite) {
                if (!confirm('Overwrite the original file? This cannot be undone.')) {
                    return;
                }
            }
            if (mode === 'fast' && !overwrite) {
                var proceed = confirm(
                    'Fast mode uses stream copy — trim points will snap to the ' +
                    'nearest keyframes and might be a few seconds off.\n\n' +
                    'Continue?'
                );
                if (!proceed) return;
            }

            // ★ КРИТИЧНО: выгружаем видео из плеера, чтобы браузер закрыл
            //   HTTP-стрим на исходный файл. Иначе на Windows os.replace
            //   упадёт с WinError 5 (Access denied) — файл залочен.
            try {
                video.pause();
                video.removeAttribute('src');
                video.load();
            } catch (e) {}

            confirmSaveBtn.disabled = true;
            confirmSaveBtn.textContent = 'Saving…';
            if (saveStatusEl) {
                saveStatusEl.textContent = '⏳ Processing video with ffmpeg…';
                saveStatusEl.className = 'save-status';
            }

            // Небольшая задержка, чтобы браузер успел разорвать соединение.
            setTimeout(function () {
                fetch('/editor/' + VIDEO_ID + '/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        start: state.start,
                        end: state.end,
                        mode: mode,
                        overwrite: overwrite,
                    }),
                })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data.success) {
                            if (data.fallback) {
                                if (saveStatusEl) {
                                    saveStatusEl.textContent =
                                        '⚠️ ' + (data.message || 'Saved as new file');
                                    saveStatusEl.className = 'save-status success';
                                }
                                setTimeout(function () {
                                    if (saveModal) saveModal.hide();
                                    window.location.href =
                                        '/watch/' + VIDEO_ID +
                                        '?profile=' + encodeURIComponent(CFG.currentProfile || 'female');
                                }, 1800);
                                return;
                            }

                            if (saveStatusEl) {
                                saveStatusEl.textContent = '✅ Saved: ' + data.filename;
                                saveStatusEl.className = 'save-status success';
                            }

                            if (overwrite) {
                                // Перезагружаем видео с cache-buster,
                                // чтобы браузер взял новый файл.
                                setTimeout(function () {
                                    if (saveModal) saveModal.hide();
                                    var bust = '/video/' + VIDEO_ID + '?v=' + Date.now();
                                    video.setAttribute('src', bust);
                                    video.load();
                                    video.addEventListener('loadedmetadata', function onMeta() {
                                        video.removeEventListener('loadedmetadata', onMeta);
                                        state.duration = video.duration || 0;
                                        state.start = 0;
                                        state.end = state.duration;
                                        tlDuration.textContent = formatTime(state.duration);
                                        updateTimeline();
                                        updateInfo();
                                    });
                                    statusEl.textContent = '✅ Saved: ' + data.filename;
                                    statusEl.className = 'editor-status success';
                                }, 500);
                            } else {
                                setTimeout(function () {
                                    if (saveModal) saveModal.hide();
                                    window.location.href =
                                        '/watch/' + VIDEO_ID +
                                        '?profile=' + encodeURIComponent(CFG.currentProfile || 'female');
                                }, 1200);
                            }
                        } else {
                            if (saveStatusEl) {
                                saveStatusEl.textContent = '❌ ' + (data.error || 'Save failed');
                                saveStatusEl.className = 'save-status error';
                            }
                            confirmSaveBtn.disabled = false;
                            confirmSaveBtn.textContent = 'Save';

                            // Возвращаем видео в плеер, раз сохранение провалилось
                            try {
                                video.setAttribute('src', '/video/' + VIDEO_ID);
                                video.load();
                            } catch (e) {}
                        }
                    })
                    .catch(function (e) {
                        if (saveStatusEl) {
                            saveStatusEl.textContent = '❌ Network error: ' + e;
                            saveStatusEl.className = 'save-status error';
                        }
                        confirmSaveBtn.disabled = false;
                        confirmSaveBtn.textContent = 'Save';
                        try {
                            video.setAttribute('src', '/video/' + VIDEO_ID);
                            video.load();
                        } catch (err) {}
                    });
            }, 350); // ждём разрыва соединения
        });
    }

    /* ---------------- Video events ---------------- */
    video.addEventListener('loadedmetadata', function () {
        state.duration = video.duration || CFG.duration || 0;
        state.start = 0;
        state.end = state.duration;
        tlDuration.textContent = formatTime(state.duration);
        updateTimeline();
        updateInfo();
    });
    video.addEventListener('timeupdate', updateCursor);
    video.addEventListener('play', function () {
        btnPlayPause.textContent = '⏸ Pause';
    });
    video.addEventListener('pause', function () {
        btnPlayPause.textContent = '▶ Play';
    });
    video.addEventListener('contextmenu', function (e) { e.preventDefault(); });

    /* ---------------- Keyboard ---------------- */
    document.addEventListener('keydown', function (e) {
        // Не перехватываем, если фокус в поле ввода
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        // Не перехватываем, если открыта модалка сохранения
        if (saveModalEl && saveModalEl.classList.contains('show')) {
            if (e.key === 'Escape') {
                e.preventDefault();
                if (saveModal) saveModal.hide();
            }
            return;
        }

        if (e.key === 'ArrowLeft') {
            e.preventDefault();
            stepFrame(-1);
        } else if (e.key === 'ArrowRight') {
            e.preventDefault();
            stepFrame(1);
        } else if (e.key === ' ') {
            e.preventDefault();
            btnPlayPause.click();
        }
    });

})();