/* ============================================================
   photo_view.js — JS просмотрщика /photo/<id>
   • Зум колесом и кнопками, pan мышью/пальцем
   • Поворот 90° (по кругу)
   • Fullscreen на stage
   • Клавиатура: ← → (nav), + −, R, F, Esc, 0 (reset)
   • Свайпы ←/→ на мобильных (только при zoom == 1)
   • Клик по звезде — сохраняет рейтинг, обновляет UI
   Конфиг: window.PHOTO_VIEW_CONFIG
   ============================================================ */
(function () {
    'use strict';

    var CFG = window.PHOTO_VIEW_CONFIG || {};
    var prevId = CFG.prevId;
    var nextId = CFG.nextId;
    var currentProfile = CFG.currentProfile || 'female';
    var photoId = CFG.photoId;

    var stage      = document.getElementById('pvStage');
    var imageWrap  = document.getElementById('pvImageWrap');
    var image      = document.getElementById('pvImage');
    if (!stage || !image) return;

    var btnZoomIn    = document.getElementById('pvZoomIn');
    var btnZoomOut   = document.getElementById('pvZoomOut');
    var btnRotate    = document.getElementById('pvRotate');
    var btnFullscreen= document.getElementById('pvFullscreen');
    var btnShowInfo  = document.getElementById('pvShowInfo');

    // --- Состояние ---
    var zoom = 1.0;
    var rotation = 0;         // 0, 90, 180, 270
    var offsetX = 0;
    var offsetY = 0;
    var isDragging = false;
    var dragStartX = 0, dragStartY = 0;
    var dragStartOffsetX = 0, dragStartOffsetY = 0;

    var MIN_ZOOM = 1.0;
    var MAX_ZOOM = 6.0;
    var ZOOM_STEP = 0.25;

    function updateTransform() {
        image.style.transform =
            'translate(' + offsetX + 'px, ' + offsetY + 'px) ' +
            'rotate(' + rotation + 'deg) ' +
            'scale(' + zoom + ')';
        if (zoom > 1.0) {
            stage.classList.add('zoomed');
        } else {
            stage.classList.remove('zoomed');
        }
    }

    function resetView() {
        zoom = 1.0;
        rotation = 0;
        offsetX = 0;
        offsetY = 0;
        updateTransform();
    }

    function clampOffsets() {
        if (zoom <= 1.0) {
            offsetX = 0;
            offsetY = 0;
            return;
        }
        var maxX = (image.clientWidth  * zoom - stage.clientWidth)  / 2;
        var maxY = (image.clientHeight * zoom - stage.clientHeight) / 2;
        if (maxX < 0) maxX = 0;
        if (maxY < 0) maxY = 0;
        if (offsetX >  maxX) offsetX =  maxX;
        if (offsetX < -maxX) offsetX = -maxX;
        if (offsetY >  maxY) offsetY =  maxY;
        if (offsetY < -maxY) offsetY = -maxY;
    }

    function setZoom(newZoom) {
        if (newZoom < MIN_ZOOM) newZoom = MIN_ZOOM;
        if (newZoom > MAX_ZOOM) newZoom = MAX_ZOOM;
        zoom = newZoom;
        if (zoom === 1.0) {
            offsetX = 0;
            offsetY = 0;
        } else {
            clampOffsets();
        }
        updateTransform();
    }

    function zoomIn()  { setZoom(zoom + ZOOM_STEP); }
    function zoomOut() { setZoom(zoom - ZOOM_STEP); }
    function rotate()  { rotation = (rotation + 90) % 360; updateTransform(); }

    function goPrev() {
        if (!prevId) return;
        var params = new URLSearchParams(window.location.search);
        params.set('profile', currentProfile);
        window.location.href = '/photo/' + prevId + '?' + params.toString();
    }
    function goNext() {
        if (!nextId) return;
        var params = new URLSearchParams(window.location.search);
        params.set('profile', currentProfile);
        window.location.href = '/photo/' + nextId + '?' + params.toString();
    }

    /* ---------------- Кнопки ---------------- */
    if (btnZoomIn)     btnZoomIn.addEventListener('click', function(e){ e.stopPropagation(); zoomIn(); });
    if (btnZoomOut)    btnZoomOut.addEventListener('click', function(e){ e.stopPropagation(); zoomOut(); });
    if (btnRotate)     btnRotate.addEventListener('click', function(e){ e.stopPropagation(); rotate(); });
    if (btnFullscreen) btnFullscreen.addEventListener('click', function(e){ e.stopPropagation(); toggleFullscreen(); });
    if (btnShowInfo) {
        btnShowInfo.addEventListener('click', function (e) {
            e.preventDefault();
            var el = document.getElementById('pvInfoModal');
            if (el) new bootstrap.Modal(el).show();
        });
    }

    /* ---------------- Клик по stage: toggle zoom ---------------- */
    imageWrap.addEventListener('click', function (e) {
        if (e.target.closest('.pv-nav') || e.target.closest('.pv-icon-btn')) return;
        if (zoom > 1.0) {
            setZoom(1.0);
        } else {
            setZoom(2.0);
        }
    });

    /* ---------------- Колесо мыши: zoom ---------------- */
    stage.addEventListener('wheel', function (e) {
        e.preventDefault();
        if (e.deltaY < 0) zoomIn();
        else              zoomOut();
    }, { passive: false });

    /* ---------------- Drag (pan) при zoom > 1 ---------------- */
    function onPointerDown(e) {
        if (zoom <= 1.0) return;
        if (e.target.closest('.pv-nav') || e.target.closest('.pv-icon-btn')) return;
        isDragging = true;
        var pt = _getPoint(e);
        dragStartX = pt.x;
        dragStartY = pt.y;
        dragStartOffsetX = offsetX;
        dragStartOffsetY = offsetY;
        stage.style.cursor = 'grabbing';
    }
    function onPointerMove(e) {
        if (!isDragging) return;
        var pt = _getPoint(e);
        offsetX = dragStartOffsetX + (pt.x - dragStartX);
        offsetY = dragStartOffsetY + (pt.y - dragStartY);
        clampOffsets();
        updateTransform();
    }
    function onPointerUp() {
        if (!isDragging) return;
        isDragging = false;
        stage.style.cursor = '';
    }
    function _getPoint(e) {
        if (e.touches && e.touches[0]) {
            return { x: e.touches[0].clientX, y: e.touches[0].clientY };
        }
        return { x: e.clientX, y: e.clientY };
    }

    stage.addEventListener('mousedown', onPointerDown);
    document.addEventListener('mousemove', onPointerMove);
    document.addEventListener('mouseup',   onPointerUp);

    stage.addEventListener('touchstart', function(e) {
        if (e.touches.length !== 1) return;
        onPointerDown(e);
    }, { passive: true });
    document.addEventListener('touchmove', function(e) {
        if (!isDragging) return;
        if (e.touches.length !== 1) return;
        e.preventDefault();
        onPointerMove(e);
    }, { passive: false });
    document.addEventListener('touchend',    onPointerUp);
    document.addEventListener('touchcancel', onPointerUp);

    /* ---------------- Fullscreen ---------------- */
    function getFullscreenElement() {
        return document.fullscreenElement
            || document.webkitFullscreenElement
            || document.mozFullScreenElement
            || document.msFullscreenElement
            || null;
    }
    function toggleFullscreen() {
        if (getFullscreenElement()) {
            var exit = document.exitFullscreen || document.webkitExitFullscreen
                     || document.mozCancelFullScreen || document.msExitFullscreen;
            if (exit) exit.call(document);
        } else {
            var req = stage.requestFullscreen || stage.webkitRequestFullscreen
                    || stage.mozRequestFullScreen || stage.msRequestFullscreen;
            if (req) req.call(stage);
        }
    }

    /* ---------------- Клавиатура ---------------- */
    document.addEventListener('keydown', function (e) {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

        switch (e.key) {
            case 'ArrowLeft':
                e.preventDefault();
                goPrev();
                break;
            case 'ArrowRight':
                e.preventDefault();
                goNext();
                break;
            case '+':
            case '=':
                e.preventDefault();
                zoomIn();
                break;
            case '-':
            case '_':
                e.preventDefault();
                zoomOut();
                break;
            case '0':
                e.preventDefault();
                resetView();
                break;
            case 'r':
            case 'R':
                e.preventDefault();
                rotate();
                break;
            case 'f':
            case 'F':
                e.preventDefault();
                toggleFullscreen();
                break;
            case 'Escape':
                if (zoom > 1.0) resetView();
                break;
        }
    });

    /* ============================================================
       СВАЙПЫ ←/→ (только при zoom == 1.0, чтобы не мешать pan'у)
       ============================================================ */
    var SWIPE_MIN_X = 60;       // минимальная дистанция по X, px
    var SWIPE_MAX_Y = 80;       // максимальное отклонение по Y, px
    var SWIPE_MAX_TIME = 700;   // макс. время жеста, мс

    var swipeStartX = null;
    var swipeStartY = null;
    var swipeStartTime = 0;

    stage.addEventListener('touchstart', function (e) {
        // Мультитач (pinch) — не свайп
        if (e.touches.length !== 1) { swipeStartX = null; return; }
        // При зуме работает pan, а не свайп
        if (zoom > 1.0) { swipeStartX = null; return; }
        // Игнор, если палец на кнопке
        if (e.target.closest('.pv-nav') || e.target.closest('.pv-icon-btn')) {
            swipeStartX = null;
            return;
        }
        swipeStartX = e.touches[0].clientX;
        swipeStartY = e.touches[0].clientY;
        swipeStartTime = Date.now();
    }, { passive: true });

    stage.addEventListener('touchend', function (e) {
        if (swipeStartX === null) return;
        if (!e.changedTouches || !e.changedTouches.length) {
            swipeStartX = null;
            return;
        }
        var t = e.changedTouches[0];
        var dx = t.clientX - swipeStartX;
        var dy = t.clientY - swipeStartY;
        var dt = Date.now() - swipeStartTime;
        swipeStartX = null;

        if (dt > SWIPE_MAX_TIME) return;                       // слишком долго — это не свайп
        if (Math.abs(dx) < SWIPE_MIN_X) return;                // слишком короткий
        if (Math.abs(dy) > SWIPE_MAX_Y) return;                // вертикальный жест — не наш
        if (Math.abs(dy) > Math.abs(dx) * 0.7) return;         // наклон — не наш

        if (dx > 0) goPrev();
        else        goNext();
    }, { passive: true });

    stage.addEventListener('touchcancel', function () {
        swipeStartX = null;
    }, { passive: true });

    /* ---------------- Рейтинг ---------------- */
    var ratingEl = document.getElementById('pvRating');
    if (ratingEl) {
        var stars = ratingEl.querySelectorAll('.star');
        var currentRating = CFG.initialRating || 0;

        function paint(newVal) {
            stars.forEach(function (s) {
                var v = parseInt(s.dataset.value, 10);
                s.classList.toggle('active', v <= newVal);
            });
        }

        stars.forEach(function (star) {
            star.addEventListener('mouseenter', function () {
                var v = parseInt(this.dataset.value, 10);
                stars.forEach(function (s) {
                    var sv = parseInt(s.dataset.value, 10);
                    s.classList.toggle('hover-active', sv <= v);
                });
            });
            star.addEventListener('click', function () {
                var v = parseInt(this.dataset.value, 10);
                fetch('/rate/' + photoId, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ rating: v })
                })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data.success) {
                            currentRating = v;
                            paint(currentRating);
                        }
                    })
                    .catch(function (err) { console.error('rate error:', err); });
            });
        });
        ratingEl.addEventListener('mouseleave', function () {
            stars.forEach(function (s) { s.classList.remove('hover-active'); });
        });
    }

    /* ---------------- Init ---------------- */
    image.addEventListener('load', function () {
        resetView();
    });
    resetView();

    console.log('[photo_view] ready. id=' + photoId + ', prev=' + prevId + ', next=' + nextId);
})();