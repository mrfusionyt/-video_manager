/* ============================================================
   preview_hover.js
   Hover-превью карточек с видео на устройствах с мышью (desktop).

   Логика:
     • На каждой карточке статичная <img class="video-thumbnail">
       с лёгким JPEG-превью (генерируется бэкендом через ffmpeg).
     • При наведении мыши создаётся <video> с полным файлом,
       накладывается поверх <img> и начинает играть.
     • При уходе мыши — video удаляется из DOM, остаётся img.
     • Одновременно не больше MAX_ACTIVE.
     • На touch-устройствах ничего не делает — там работает
       preview_mobile.js через IntersectionObserver.
   ============================================================ */
(function () {
    'use strict';

    var HAS_HOVER = window.matchMedia('(hover: hover)').matches;
    if (!HAS_HOVER) return;

    var SEGMENTS = 3;
    var SHOW_DURATION = 2; // секунд на отрезок
    var MAX_ACTIVE = 4;

    var SELECTOR = '.video-card, .video-tile, .folder-card, .artist-card';

    // video -> intervalId | 'loop'
    var intervals = new Map();
    // card -> video
    var cardVideos = new Map();
    // массив card в порядке активации
    var activeQueue = [];

    /* ---------- Управление отрезками воспроизведения ---------- */
    function stopSegments(v) {
        var id = intervals.get(v);
        if (id && id !== 'loop') clearInterval(id);
        intervals.delete(v);
    }

    function startSegments(v) {
        stopSegments(v);
        v.muted = true;
        v.loop = false;

        if (!v.duration || isNaN(v.duration) || v.duration === Infinity) {
            v.addEventListener('loadedmetadata', function onMeta() {
                v.removeEventListener('loadedmetadata', onMeta);
                startSegments(v);
            }, { once: true });
            return;
        }

        var duration = v.duration;
        if (duration < 0.5) {
            v.loop = true;
            v.play().catch(function () {});
            intervals.set(v, 'loop');
            return;
        }

        var seg = duration / SEGMENTS;
        var points = [];
        for (var i = 0; i < SEGMENTS; i++) points.push(i * seg);

        var cur = 0;
        function next() {
            cur = (cur + 1) % SEGMENTS;
            var t = points[cur];
            if (t >= duration) t = duration - 0.1;
            try { v.currentTime = t; } catch (e) {}
            if (v.paused) v.play().catch(function () {});
        }

        try { v.currentTime = points[0]; } catch (e) {}
        v.play().catch(function () {});

        var id = setInterval(next, SHOW_DURATION * 1000);
        intervals.set(v, id);
    }

    /* ---------- Создание / удаление video-элемента ---------- */
    function ensureVideo(card) {
        var img = card.querySelector('img.video-thumbnail[data-video-src]');
        if (!img) return null;

        var v = cardVideos.get(card);
        if (v && v.parentNode) return v;

        var src = img.getAttribute('data-video-src');
        if (!src) return null;

        v = document.createElement('video');
        v.className = 'hover-preview';
        v.muted = true;
        v.playsInline = true;
        v.setAttribute('playsinline', '');
        v.setAttribute('muted', '');
        v.preload = 'auto';
        v.src = src;

        img.parentNode.appendChild(v);
        cardVideos.set(card, v);
        return v;
    }

    function destroyVideo(card) {
        var v = cardVideos.get(card);
        if (!v) return;
        stopSegments(v);
        try { v.pause(); } catch (e) {}
        try {
            v.removeAttribute('src');
            v.load();
            if (v.parentNode) v.parentNode.removeChild(v);
        } catch (e) {}
        cardVideos.delete(card);
    }

    /* ---------- Логика активации / деактивации ---------- */
    function activate(card) {
        if (activeQueue.indexOf(card) !== -1) return;
        activeQueue.push(card);

        // Ограничиваем число одновременных превью
        while (activeQueue.length > MAX_ACTIVE) {
            var oldest = activeQueue.shift();
            deactivate(oldest, true);
        }

        var v = ensureVideo(card);
        if (!v) {
            activeQueue.pop();
            return;
        }

        function play() {
            if (activeQueue.indexOf(card) === -1) return;
            v.classList.add('playing');
            startSegments(v);
        }

        if (v.readyState >= 3) {
            play();
        } else {
            v.addEventListener('canplay', function onCanPlay() {
                v.removeEventListener('canplay', onCanPlay);
                play();
            }, { once: true });
            v.addEventListener('error', function onErr() {
                v.removeEventListener('error', onErr);
                var idx = activeQueue.indexOf(card);
                if (idx !== -1) activeQueue.splice(idx, 1);
                destroyVideo(card);
            }, { once: true });
        }
    }

    function deactivate(card, keepElement) {
        var idx = activeQueue.indexOf(card);
        if (idx !== -1) activeQueue.splice(idx, 1);
        var v = cardVideos.get(card);
        if (!v) return;
        stopSegments(v);
        v.classList.remove('playing');
        try { v.pause(); } catch (e) {}
        if (!keepElement) {
            // полностью убираем video, чтобы не занимал память
            destroyVideo(card);
        }
    }

    /* ---------- Привязка обработчиков к карточке ---------- */
    function attachToCard(card) {
        card.addEventListener('mouseenter', function () { activate(card); });
        card.addEventListener('mouseleave', function () { deactivate(card); });
    }

    /* ---------- Поиск и регистрация карточек ---------- */
    function scan(root) {
        var scope = root || document;
        var cards = scope.querySelectorAll(SELECTOR);
        cards.forEach(function (card) {
            if (card.dataset.previewInit === '1') return;
            if (!card.querySelector('img.video-thumbnail[data-video-src]')) return;
            card.dataset.previewInit = '1';
            attachToCard(card);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { scan(); });
    } else {
        scan();
    }

    // Автопоиск новых карточек (после "More" / AJAX)
    var scanTimer = null;
    try {
        var mo = new MutationObserver(function () {
            if (scanTimer) return;
            scanTimer = setTimeout(function () {
                scanTimer = null;
                scan();
            }, 200);
        });
        mo.observe(document.body, { childList: true, subtree: true });
    } catch (e) {}

    // Останавливаем всё при уходе со вкладки
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) return;
        activeQueue.slice().forEach(function (card) {
            deactivate(card);
        });
    });
})();