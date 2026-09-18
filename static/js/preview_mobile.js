/* ============================================================
   preview_mobile.js
   Автопревью карточек видео на устройствах без hover (телефон,
   планшет, iPad). На десктопе модуль молча выключается — там
   работает наведение мыши из шаблонов.

   Логика:
     • Карточка в зоне видимости (>= 60% площади) — превью играет
       три отрезка по 2 секунды: 0%, 33%, 66% от длительности.
     • Карточка ушла из кадра — превью на паузу и в 0.
     • Одновременно не больше 3 превью (MAX_ACTIVE).
     • Новые карточки после бесконечной прокрутки ловятся
       автоматически через MutationObserver.
   ============================================================ */

(function () {
    'use strict';

    /* --- 1. Гейт: работаем только на touch-устройствах без hover --- */
    var noHover    = window.matchMedia('(hover: none)').matches;
    var coarsePtr  = window.matchMedia('(pointer: coarse)').matches;
    if (!noHover && !coarsePtr) return;

    if (!('IntersectionObserver' in window)) return;

    /* --- 2. Константы --- */
    var SEGMENTS            = 3;
    var SHOW_DURATION       = 2;      // секунд на отрезок
    var MAX_ACTIVE          = 3;      // одновременно играющих превью
    var VISIBILITY_THRESHOLD = 0.6;   // доля карточки, которая должна быть видна

    /* --- 3. Состояние --- */
    var cardMap     = new Map();       // card  -> video
    var intervals   = new Map();       // video -> intervalId (или 'loop')
    var activeQueue = [];              // card[] — в порядке активации
    var registered  = new WeakSet();   // уже подписанные карточки

    /* --- 4. Запуск / остановка анимации одного видео --- */
    function startPreview(v) {
        if (!v || intervals.has(v)) return;
        v.muted = true;

        if (!v.duration || isNaN(v.duration) || v.duration === Infinity) {
            v.addEventListener('loadedmetadata', function onMeta() {
                v.removeEventListener('loadedmetadata', onMeta);
                startPreview(v);
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

    function stopPreview(v) {
        if (!v) return;
        var id = intervals.get(v);
        if (id) {
            if (id !== 'loop') clearInterval(id);
            intervals.delete(v);
        }
        try { v.currentTime = 0; } catch (e) {}
        try { v.pause(); } catch (e) {}
    }

    /* --- 5. Очередь активных карточек --- */
    function activate(card) {
        if (activeQueue.indexOf(card) !== -1) return;
        activeQueue.push(card);

        // Если уже играет больше MAX_ACTIVE — глушим самое старое
        while (activeQueue.length > MAX_ACTIVE) {
            var oldest = activeQueue.shift();
            var oldV = cardMap.get(oldest);
            if (oldV) stopPreview(oldV);
        }

        var v = cardMap.get(card);
        if (v) startPreview(v);
    }

    function deactivate(card) {
        var idx = activeQueue.indexOf(card);
        if (idx !== -1) activeQueue.splice(idx, 1);
        var v = cardMap.get(card);
        if (v) stopPreview(v);
    }

    /* --- 6. IntersectionObserver --- */
    var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            var card = entry.target;
            if (entry.isIntersecting && entry.intersectionRatio >= VISIBILITY_THRESHOLD) {
                activate(card);
            } else {
                deactivate(card);
            }
        });
    }, { threshold: [0, VISIBILITY_THRESHOLD, 1.0] });

    /* --- 7. Подписка карточки --- */
    function registerCard(card) {
        if (registered.has(card)) return;
        var v = card.querySelector('video');
        if (!v) return;
        registered.add(card);
        cardMap.set(card, v);
        io.observe(card);
    }

    function scan() {
        document.querySelectorAll(
            '.video-card, .video-tile, .recommendation-card-wrapper, .folder-card'
        ).forEach(registerCard);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', scan);
    } else {
        scan();
    }

    /* --- 8. Новые карточки (бесконечная прокрутка) --- */
    var scanTimer = null;
    function scheduleScan() {
        if (scanTimer) return;
        scanTimer = setTimeout(function () {
            scanTimer = null;
            scan();
        }, 200);
    }

    try {
        var mo = new MutationObserver(scheduleScan);
        mo.observe(document.body, { childList: true, subtree: true });
    } catch (e) { /* старые браузеры — не критично */ }

    /* --- 9. Свернуть всё, когда вкладка в фоне --- */
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) return;
        activeQueue.slice().forEach(function (card) {
            var v = cardMap.get(card);
            if (v) stopPreview(v);
        });
        activeQueue.length = 0;
    });
})();