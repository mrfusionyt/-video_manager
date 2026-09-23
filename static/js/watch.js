/* ============================================================
   watch.js — JS страницы /watch/<id>
   ---------------------------------------------
   Всё, что раньше было инлайн в watch.html:
     • вертикальная карусель (.vp-strip) с 3 слотами
     • fullscreen state
     • зеркалирование (localStorage)
     • звёзды с волной при наведении
     • rating panel (мини-звёзды) + rating stars (большие)
     • bottom sheets: playlists, categories
     • playlist carousel + dropdown
     • scrub-tooltip над таймлайном
     • persist позиции просмотра (localStorage)
     • swipe up/down в fullscreen (mobile)
     • бегущая строка для длинных названий плейлистов

   Конфиг приходит из окна как window.WATCH_CONFIG.

   Лента (feed) строится в том же порядке, что и список на
   странице index с учётом sort / search / folder / category.
   При переходе на следующее видео все эти параметры
   передаются в URL — см. buildWatchQuery().
   ============================================================ */
(function () {
    'use strict';

    /* ====== КОНФИГ ИЗ ШАБЛОНА ====== */
    var CFG = window.WATCH_CONFIG || {};
    var FEED_IDS = CFG.feedIds || [];
    var CURRENT_PROFILE = CFG.currentProfile || 'female';
    var ALL_CATEGORIES = CFG.allCategories || [];

    var FEED_SORT = CFG.sort || 'date';
    var FEED_SEARCH = CFG.search || '';
    var FEED_FOLDER = CFG.folder || '';
    var FEED_CATEGORIES = CFG.selectedCategories || [];

    var currentVideoId = CFG.videoId;
    var currentFeedIndex = CFG.feedIndex || 0;
    var currentRating = CFG.initialRating || 0;
    var videoCategoryIds = CFG.videoCategoryIds || [];

    var ICONS = CFG.icons || {};
    var ICON_PLAY     = ICONS.play;
    var ICON_PAUSE    = ICONS.pause;
    var ICON_UNMUTE   = ICONS.unmute;
    var ICON_MUTE     = ICONS.mute;
    var ICON_LOOP_OFF = ICONS.loopOff;
    var ICON_LOOP_ON  = ICONS.loopOn;
    var ICON_PIC      = ICONS.pic;
    var ICON_PENCIL   = ICONS.pencil;
    var ICON_BIN      = ICONS.bin;
    var ICON_PLN      = ICONS.pln;
    var ICON_PLD      = ICONS.pld;

    /* ====== МЕДИА-ЗАПРОСЫ ====== */
    var MOBILE_MQ = window.matchMedia('(max-width: 768px), (max-height: 500px)');
    var IS_MOBILE = MOBILE_MQ.matches;
    MOBILE_MQ.addEventListener('change', function (e) { IS_MOBILE = e.matches; });

    var HAS_HOVER = window.matchMedia('(hover: hover)').matches;

    /* ====== СОСТОЯНИЕ ====== */
    var isNavigating = false;
    var isPlaying = true;
    var loopActive = false;
    var controlsTimeout = null;
    var isFullscreen = false;
    var isPlaylistSheetOpen = false;
    var isCategorySheetOpen = false;
    var isRatingPanelOpen = false;

    var video = null;

    var playerState = {
        volume: 1.0,
        muted: false,
        rate: 1.0,
        loop: false
    };

    /* ====== СБОРКА QUERY ДЛЯ URL /watch/<id> ====== */
    function buildWatchQuery() {
        var parts = ['profile=' + encodeURIComponent(CURRENT_PROFILE)];
        if (FEED_SORT && FEED_SORT !== 'date') {
            parts.push('sort=' + encodeURIComponent(FEED_SORT));
        }
        if (FEED_SEARCH) {
            parts.push('search=' + encodeURIComponent(FEED_SEARCH));
        }
        if (FEED_FOLDER) {
            parts.push('folder=' + encodeURIComponent(FEED_FOLDER));
        }
        for (var i = 0; i < FEED_CATEGORIES.length; i++) {
            parts.push('category=' + encodeURIComponent(FEED_CATEGORIES[i]));
        }
        return parts.join('&');
    }

    /* ====== DOM ====== */
    var wrapper = document.getElementById('videoWrapper');
    var vpStrip = document.getElementById('vpStrip');

    var playBtn = document.getElementById('playBtn');
    var playIcon = document.getElementById('playIcon');
    var progressFill = document.getElementById('progressFill');
    var progressTrack = document.getElementById('progressTrack');
    var muteBtn = document.getElementById('muteBtn');
    var muteIcon = document.getElementById('muteIcon');
    var fullscreenBtn = document.getElementById('fullscreenBtn');
    var loopBtn = document.getElementById('loopBtn');
    var loopIcon = document.getElementById('loopIcon');
    var nextBtn = document.getElementById('nextBtn');

    var scrubTooltip = document.getElementById('scrubTooltip');
    var scrubVideo = document.getElementById('scrubVideo');
    var scrubTime = document.getElementById('scrubTime');

    var starBtn = document.getElementById('starBtn');
    var starIcon = document.getElementById('starIcon');
    var ratingPanel = document.getElementById('ratingPanel');
    var starsMini = ratingPanel ? ratingPanel.querySelectorAll('.star-mini') : [];

    var mobilePlaylistBtn = document.getElementById('mobilePlaylistBtn');
    var mobilePlaylistIcon = document.getElementById('mobilePlaylistIcon');
    var mobileCategoryBtn = document.getElementById('mobileCategoryBtn');

    var playlistSheet = document.getElementById('playlistSheet');
    var playlistSheetOverlay = document.getElementById('playlistSheetOverlay');
    var playlistSheetClose = document.getElementById('playlistSheetClose');

    var categorySheet = document.getElementById('categorySheet');
    var categorySheetOverlay = document.getElementById('categorySheetOverlay');
    var categorySheetClose = document.getElementById('categorySheetClose');
    var categorySaveBtn = document.getElementById('categorySaveBtn');

    /* =========================================================
       БЕГУЩАЯ СТРОКА ДЛЯ ДЛИННЫХ НАЗВАНИЙ ПЛЕЙЛИСТОВ
       ========================================================= */
    function setupPlaylistNameMarquee(nameSpan) {
        if (!nameSpan) return;
        var text = (nameSpan.dataset.pnText || nameSpan.textContent || '').trim();
        if (!text) return;
        nameSpan.dataset.pnText = text;

        nameSpan.classList.remove('marquee');
        nameSpan.style.removeProperty('--pn-duration');
        nameSpan.style.removeProperty('--pn-distance');
        nameSpan.textContent = text;

        requestAnimationFrame(function () {
            var containerW = nameSpan.clientWidth;
            var textW = nameSpan.scrollWidth;
            if (!containerW) return;
            if (textW <= containerW + 1) return;

            var gap = 40;

            var t1 = document.createElement('span');
            t1.className = 'pn-text';
            t1.textContent = text;

            var t2 = document.createElement('span');
            t2.className = 'pn-text pn-dup';
            t2.setAttribute('aria-hidden', 'true');
            t2.textContent = text;

            var track = document.createElement('span');
            track.className = 'pn-track';
            track.appendChild(t1);
            track.appendChild(t2);

            nameSpan.textContent = '';
            nameSpan.appendChild(track);
            nameSpan.classList.add('marquee');

            var distance = textW + gap;
            var duration = Math.max(8, distance / 30);
            nameSpan.style.setProperty('--pn-distance', distance + 'px');
            nameSpan.style.setProperty('--pn-duration', duration.toFixed(2) + 's');
        });
    }

    /* =========================================================
       PERSISTENCE — сохранение позиции просмотра
       ========================================================= */
    var POS_KEY = 'vp_pos_';
    var MIN_SAVE_SECONDS = 3;
    var END_THRESHOLD = 5;

    function saveVideoPosition(id, time) {
        if (!id) return;
        var t = parseFloat(time);
        if (!isFinite(t) || t < MIN_SAVE_SECONDS) return;
        try { localStorage.setItem(POS_KEY + id, String(Math.floor(t))); } catch (e) {}
    }

    function loadVideoPosition(id) {
        if (!id) return 0;
        try {
            var v = localStorage.getItem(POS_KEY + id);
            var n = v ? parseFloat(v) : 0;
            return isFinite(n) && n > 0 ? n : 0;
        } catch (e) { return 0; }
    }

    function clearVideoPosition(id) {
        if (!id) return;
        try { localStorage.removeItem(POS_KEY + id); } catch (e) {}
    }

    function attachPersistence(videoEl, videoId) {
        if (!videoEl || !videoId) return;

        videoEl.addEventListener('loadedmetadata', function onMeta() {
            videoEl.removeEventListener('loadedmetadata', onMeta);
            var saved = loadVideoPosition(videoId);
            if (!saved) return;
            var dur = videoEl.duration;
            if (!dur || !isFinite(dur)) return;
            if (saved >= dur - END_THRESHOLD) return;
            try { videoEl.currentTime = saved; } catch (e) {}
        });

        var lastSaved = -1;
        videoEl.addEventListener('timeupdate', function () {
            if (videoEl.seeking) return;
            var t = videoEl.currentTime;
            if (Math.abs(t - lastSaved) >= 1) {
                lastSaved = t;
                saveVideoPosition(videoId, t);
            }
        });

        videoEl.addEventListener('pause', function () {
            if (videoEl.seeking) return;
            saveVideoPosition(videoId, videoEl.currentTime);
        });

        videoEl.addEventListener('ended', function () {
            clearVideoPosition(videoId);
        });
    }

    /* =========================================================
       КАРУСЕЛЬ: 3 СЛОТА, ОДНА ПОЛОСА
       ========================================================= */
    var slideMap = new Map();
    var FEED_DURATION = 300;
    var FEED_EASING = 'cubic-bezier(0.22, 0.61, 0.36, 1)';

    function createSlide(feedIndex) {
        var videoId = FEED_IDS[feedIndex];
        var slideEl = document.createElement('div');
        slideEl.className = 'vp-slide';
        slideEl.dataset.feedIndex = String(feedIndex);

        var videoEl = document.createElement('video');
        videoEl.className = 'vp-video';
        videoEl.playsInline = true;
        videoEl.setAttribute('preload', 'auto');
        videoEl.setAttribute('src', '/video/' + videoId);
        videoEl.muted = true;
        videoEl.loop = false;

        slideEl.appendChild(videoEl);

        attachPersistence(videoEl, videoId);

        var entry = { slide: slideEl, video: videoEl, feedIndex: feedIndex, videoId: videoId };
        slideMap.set(feedIndex, entry);
        return entry;
    }

    function destroySlide(feedIndex) {
        var entry = slideMap.get(feedIndex);
        if (!entry) return;

        try {
            if (!entry.video.paused && !entry.video.seeking) {
                saveVideoPosition(entry.videoId, entry.video.currentTime);
            }
        } catch (e) {}

        try { entry.video.pause(); } catch (e) {}
        try { entry.video.removeAttribute('src'); entry.video.load(); } catch (e) {}
        if (entry.slide.parentNode) entry.slide.parentNode.removeChild(entry.slide);
        slideMap.delete(feedIndex);
    }

    function windowIndices() {
        var want = [];
        for (var d = -1; d <= 1; d++) {
            var i = currentFeedIndex + d;
            if (i >= 0 && i < FEED_IDS.length) want.push(i);
        }
        return want;
    }

    function rebuildWindow(resetPosition) {
        var want = windowIndices();
        var wantSet = new Set(want);

        var existingKeys = Array.from(slideMap.keys());
        for (var k = 0; k < existingKeys.length; k++) {
            var idx = existingKeys[k];
            if (!wantSet.has(idx)) destroySlide(idx);
        }
        for (var j = 0; j < want.length; j++) {
            if (!slideMap.has(want[j])) createSlide(want[j]);
        }

        vpStrip.innerHTML = '';
        for (var m = 0; m < want.length; m++) {
            vpStrip.appendChild(slideMap.get(want[m]).slide);
        }

        if (resetPosition) {
            var currentPos = want.indexOf(currentFeedIndex);
            vpStrip.style.transition = 'none';
            vpStrip.style.transform = 'translate3d(0, ' + (-currentPos * 100) + '%, 0)';
            void vpStrip.offsetHeight;
        }
    }

    function applyPlayerStateToVideo(el) {
        if (!el) return;
        el.volume = playerState.volume;
        el.muted = playerState.muted;
        el.playbackRate = playerState.rate;
        el.loop = playerState.loop;
        if (isMirrored) el.style.transform = 'scaleX(-1)';
        else el.style.transform = '';
    }

    function navigateToVideo(direction) {
        if (!IS_MOBILE) return;
        if (isNavigating) return;
        if (isPlaylistSheetOpen) closePlaylistSheet();
        if (isCategorySheetOpen) closeCategorySheet();
        closeRatingPanel();

        var nextIdx = currentFeedIndex + direction;
        if (nextIdx < 0) { showFeedToast('Это первое видео'); return; }
        if (nextIdx >= FEED_IDS.length) { showFeedToast('Это последнее видео'); return; }
        if (!slideMap.has(nextIdx)) {
            rebuildWindow(true);
            if (!slideMap.has(nextIdx)) return;
        }

        isNavigating = true;

        var want = windowIndices();
        var targetPos = want.indexOf(nextIdx);

        var currentEntry = slideMap.get(currentFeedIndex);
        var targetEntry = slideMap.get(nextIdx);

        try {
            if (currentEntry && !currentEntry.video.paused && !currentEntry.video.seeking) {
                saveVideoPosition(currentEntry.videoId, currentEntry.video.currentTime);
            }
        } catch (e) {}

        playerState.volume = currentEntry.video.volume;
        playerState.muted = currentEntry.video.muted;
        playerState.rate = currentEntry.video.playbackRate;
        playerState.loop = currentEntry.video.loop;

        targetEntry.video.muted = true;
        targetEntry.video.volume = playerState.volume;
        targetEntry.video.playbackRate = playerState.rate;
        try { targetEntry.video.currentTime = 0; } catch (e) {}
        currentEntry.video.muted = true;

        var ready = new Promise(function (resolve) {
            if (targetEntry.video.readyState >= 3) { resolve(); return; }
            var done = false;
            var finish = function () {
                if (done) return; done = true;
                targetEntry.video.removeEventListener('canplay', finish);
                targetEntry.video.removeEventListener('loadeddata', finish);
                targetEntry.video.removeEventListener('error', finish);
                resolve();
            };
            targetEntry.video.addEventListener('canplay', finish, { once: true });
            targetEntry.video.addEventListener('loadeddata', finish, { once: true });
            targetEntry.video.addEventListener('error', finish, { once: true });
            setTimeout(finish, 800);
        });

        ready.then(function () {
            targetEntry.video.play().catch(function () {});

            requestAnimationFrame(function () {
                vpStrip.style.transition = 'transform ' + FEED_DURATION + 'ms ' + FEED_EASING;
                vpStrip.style.transform = 'translate3d(0, ' + (-targetPos * 100) + '%, 0)';

                var finalized = false;
                var finalize = function () {
                    if (finalized) return;
                    finalized = true;
                    vpStrip.removeEventListener('transitionend', onEnd);
                    finalizeNavigation(nextIdx);
                };
                var onEnd = function (e) {
                    if (e.target !== vpStrip || e.propertyName !== 'transform') return;
                    finalize();
                };
                vpStrip.addEventListener('transitionend', onEnd);
                setTimeout(finalize, FEED_DURATION + 80);
            });
        });
    }

    function finalizeNavigation(nextIdx) {
        var oldEntry = slideMap.get(currentFeedIndex);
        if (oldEntry) {
            try { oldEntry.video.pause(); } catch (e) {}
            try { oldEntry.video.currentTime = 0; } catch (e) {}
        }

        currentFeedIndex = nextIdx;
        currentVideoId = FEED_IDS[nextIdx];

        rebuildWindow(true);

        var newEntry = slideMap.get(currentFeedIndex);
        if (newEntry) {
            video = newEntry.video;
            applyPlayerStateToVideo(video);
            video.muted = playerState.muted;
            video.play().catch(function () {});
        }

        window.history.pushState(
            { videoId: currentVideoId, feedIndex: currentFeedIndex },
            '',
            '/watch/' + currentVideoId + '?' + buildWatchQuery()
        );

        updateScrubSrc(currentVideoId);

        fetch('/watch/' + currentVideoId + '?' + buildWatchQuery(), {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
            .then(function (r) { return r.text(); })
            .then(function (html) {
                var doc = new DOMParser().parseFromString(html, 'text/html');

                var newTitleEl = doc.querySelector('#videoTitle');
                var oldTitleEl = document.getElementById('videoTitle');
                if (newTitleEl && oldTitleEl) oldTitleEl.innerHTML = newTitleEl.innerHTML;

                var newAddedEl = doc.querySelector('#videoAdded');
                var oldAddedEl = document.getElementById('videoAdded');
                if (newAddedEl && oldAddedEl) oldAddedEl.textContent = newAddedEl.textContent;

                var newRating = doc.querySelectorAll('#ratingStars .star.active').length;
                currentRating = newRating;
                updateStarIcon();
                updateMiniStars();
                updateBigStars();

                var newTags = doc.querySelector('.category-tags');
                var oldTags = document.querySelector('.category-tags');
                if (newTags && oldTags) oldTags.innerHTML = newTags.innerHTML;

                var newInfo = doc.querySelector('#videoInfoModal .modal-body');
                var oldInfo = document.querySelector('#videoInfoModal .modal-body');
                if (newInfo && oldInfo) oldInfo.innerHTML = newInfo.innerHTML;
            })
            .catch(function (err) { console.error('[feed] meta fetch error:', err); });

        refreshVideoInfo(currentVideoId);
        loadPlaylists(1);
        updateMobilePlaylistButtonState();

        playIcon.src = ICON_PAUSE;
        playIcon.alt = 'Pause';
        isPlaying = true;

        isNavigating = false;
    }

    /* =========================================================
       FULLSCREEN STATE
       ========================================================= */
    function getFullscreenElement() {
        return document.fullscreenElement
            || document.webkitFullscreenElement
            || document.mozFullScreenElement
            || document.msFullscreenElement
            || null;
    }

    function updateFullscreenState() {
        var fsElement = getFullscreenElement();
        var inFs = !!fsElement;
        if (inFs !== isFullscreen) {
            isFullscreen = inFs;
            wrapper.classList.toggle('is-fullscreen', isFullscreen);
        }
    }

    /* =========================================================
       ЗЕРКАЛИРОВАНИЕ
       ========================================================= */
    var MIRROR_STORAGE_KEY = 'watch_mirror_enabled';
    var isMirrored = false;
    try { isMirrored = localStorage.getItem(MIRROR_STORAGE_KEY) === '1'; } catch (e) {}

    function applyMirrorState() {
        if (video) video.style.transform = isMirrored ? 'scaleX(-1)' : '';
        var btn = document.getElementById('mirrorBtn');
        if (btn) btn.classList.toggle('active', isMirrored);
        slideMap.forEach(function (entry) {
            entry.video.style.transform = isMirrored ? 'scaleX(-1)' : '';
        });
    }

    function toggleMirror() {
        isMirrored = !isMirrored;
        try { localStorage.setItem(MIRROR_STORAGE_KEY, isMirrored ? '1' : '0'); } catch (e) {}
        applyMirrorState();
    }

    document.addEventListener('click', function (e) {
        var btn = e.target.closest('#mirrorBtn');
        if (!btn) return;
        e.preventDefault();
        e.stopPropagation();
        toggleMirror();
    });

    /* =========================================================
       ОЦЕНКИ + ВОЛНА
       ========================================================= */
    function updateStarIcon() {
        if (!starIcon) return;
        starIcon.classList.toggle('inactive', !(currentRating > 0));
    }

    function updateMiniStars() {
        starsMini.forEach(function (s) {
            var val = parseInt(s.dataset.value, 10);
            s.classList.toggle('active', val <= currentRating);
        });
    }

    function updateBigStars() {
        document.querySelectorAll('#ratingStars .star').forEach(function (s) {
            var val = parseInt(s.dataset.value, 10);
            s.classList.toggle('active', val <= currentRating);
        });
    }

    function openRatingPanel() { if (!ratingPanel) return; isRatingPanelOpen = true; ratingPanel.classList.add('open'); }
    function closeRatingPanel() { if (!ratingPanel) return; isRatingPanelOpen = false; ratingPanel.classList.remove('open'); clearStarWave(starsMini); }
    function toggleRatingPanel() { if (isRatingPanelOpen) closeRatingPanel(); else openRatingPanel(); }

    function setRating(val) {
        fetch('/rate/' + currentVideoId, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rating: val })
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.success) {
                    currentRating = val;
                    updateStarIcon();
                    updateMiniStars();
                    updateBigStars();
                    setTimeout(closeRatingPanel, 300);
                }
            })
            .catch(function (err) { console.error('setRating', err); });
    }

    var STAR_WAVE_DELAY = 30;

    function clearStarWave(stars) {
        stars.forEach(function (s) {
            s.style.transitionDelay = '0ms';
            s.classList.remove('hover-active');
        });
    }

    function attachStarWave(stars) {
        if (!stars || !stars.length) return;
        stars.forEach(function (star, idx) {
            star.addEventListener('mouseenter', function () {
                stars.forEach(function (s, i) {
                    if (i <= idx) {
                        s.style.transitionDelay = (i * STAR_WAVE_DELAY) + 'ms';
                        s.classList.add('hover-active');
                    } else {
                        s.style.transitionDelay = '0ms';
                        s.classList.remove('hover-active');
                    }
                });
            });
        });
    }

    var bigStars = document.querySelectorAll('#ratingStars .star');
    attachStarWave(bigStars);
    var ratingStarsContainer = document.getElementById('ratingStars');
    if (ratingStarsContainer) {
        ratingStarsContainer.addEventListener('mouseleave', function () {
            clearStarWave(bigStars);
        });
    }

    attachStarWave(starsMini);
    if (ratingPanel) {
        ratingPanel.addEventListener('mouseleave', function () {
            clearStarWave(starsMini);
        });
    }

    if (starBtn) {
        starBtn.addEventListener('click', function (e) {
            e.preventDefault(); e.stopPropagation();
            toggleRatingPanel();
        });
    }

    starsMini.forEach(function (s) {
        s.addEventListener('click', function (e) {
            e.preventDefault(); e.stopPropagation();
            setRating(parseInt(this.dataset.value, 10));
        });
    });

    document.addEventListener('click', function (e) {
        if (!isRatingPanelOpen) return;
        if (e.target.closest('#ratingPanel')) return;
        if (e.target.closest('#starBtn')) return;
        closeRatingPanel();
    });

    bigStars.forEach(function (star) {
        star.addEventListener('click', function () {
            setRating(parseInt(this.dataset.value, 10));
        });
    });

    updateStarIcon();
    updateMiniStars();

    /* =========================================================
       КОНТРОЛЫ
       ========================================================= */
    function showControls() {
        wrapper.classList.add('show-controls');
        clearTimeout(controlsTimeout);
    }
    function hideControls() {
        clearTimeout(controlsTimeout);
        controlsTimeout = setTimeout(function () {
            wrapper.classList.remove('show-controls');
        }, 3000);
    }
    function toggleControlsVisibility() {
        if (isPlaylistSheetOpen || isCategorySheetOpen) return;
        if (wrapper.classList.contains('show-controls')) {
            wrapper.classList.remove('show-controls');
            clearTimeout(controlsTimeout);
        } else {
            showControls();
            hideControls();
        }
    }

    function openPlaylistSheet() {
        if (isPlaylistSheetOpen || isCategorySheetOpen) return;
        isPlaylistSheetOpen = true;
        playlistSheet.classList.add('open');
        playlistSheetOverlay.classList.add('open');
        wrapper.classList.remove('show-controls');
        clearTimeout(controlsTimeout);
        closeRatingPanel();
        loadMobilePlaylistsForModal();
    }
    function closePlaylistSheet() {
        if (!isPlaylistSheetOpen) return;
        isPlaylistSheetOpen = false;
        playlistSheet.classList.remove('open');
        playlistSheetOverlay.classList.remove('open');
    }

    if (playlistSheetClose) {
        playlistSheetClose.addEventListener('click', function (e) { e.stopPropagation(); closePlaylistSheet(); });
    }
    if (playlistSheetOverlay) {
        playlistSheetOverlay.addEventListener('click', function () { closePlaylistSheet(); });
    }

    function openCategorySheet() {
        if (isPlaylistSheetOpen || isCategorySheetOpen) return;
        isCategorySheetOpen = true;
        categorySheet.classList.add('open');
        categorySheetOverlay.classList.add('open');
        wrapper.classList.remove('show-controls');
        clearTimeout(controlsTimeout);
        closeRatingPanel();
        loadCategoriesForModal();
    }
    function closeCategorySheet() {
        if (!isCategorySheetOpen) return;
        isCategorySheetOpen = false;
        categorySheet.classList.remove('open');
        categorySheetOverlay.classList.remove('open');
    }

    if (categorySheetClose) {
        categorySheetClose.addEventListener('click', function (e) { e.stopPropagation(); closeCategorySheet(); });
    }
    if (categorySheetOverlay) {
        categorySheetOverlay.addEventListener('click', function () { closeCategorySheet(); });
    }
    if (categorySaveBtn) {
        categorySaveBtn.addEventListener('click', saveCategories);
    }

    function loadCategoriesForModal() {
        var listEl = document.getElementById('watchCatSheetList');
        if (!listEl) return;

        if (!ALL_CATEGORIES || ALL_CATEGORIES.length === 0) {
            listEl.innerHTML = '<div class="mp-empty">No categories yet.<br>Add them in Settings.</div>';
            return;
        }

        listEl.innerHTML = '';
        ALL_CATEGORIES.forEach(function (cat) {
            var item = document.createElement('label');
            item.className = 'cat-item';
            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.value = cat.id;
            cb.checked = videoCategoryIds.indexOf(cat.id) !== -1;
            var txt = document.createElement('span');
            txt.textContent = cat.name;
            item.appendChild(cb);
            item.appendChild(txt);
            listEl.appendChild(item);
        });
    }

    function saveCategories() {
        var checked = [];
        document.querySelectorAll('#watchCatSheetList input[type="checkbox"]:checked').forEach(function (cb) {
            checked.push(parseInt(cb.value, 10));
        });

        categorySaveBtn.disabled = true;
        var origText = categorySaveBtn.textContent;
        categorySaveBtn.textContent = 'Saving...';

        fetch('/video/' + currentVideoId + '/set_categories', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ category_ids: checked })
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.success) {
                    videoCategoryIds = checked.slice();
                    updateCategoryTags(checked);
                    closeCategorySheet();
                } else {
                    alert('Error: ' + (data.error || 'Unknown'));
                }
            })
            .catch(function (err) { console.error('saveCategories', err); alert('Network error'); })
            .finally(function () {
                categorySaveBtn.disabled = false;
                categorySaveBtn.textContent = origText;
            });
    }

    function updateCategoryTags(ids) {
        var container = document.getElementById('categoryTags');
        if (!container) return;
        if (!ids || ids.length === 0) {
            container.innerHTML = '<span class="badge bg-secondary">none</span>';
            return;
        }
        container.innerHTML = '';
        ids.forEach(function (id) {
            var cat = ALL_CATEGORIES.find(function (c) { return c.id === id; });
            if (cat) {
                var span = document.createElement('span');
                span.className = 'badge bg-primary';
                span.dataset.categoryId = id;
                span.textContent = cat.name;
                container.appendChild(span);
            }
        });
    }

    function refreshVideoInfo(videoId) {
        fetch('/video/' + videoId + '/info')
            .then(function (r) { return r.json(); })
            .then(function (info) {
                if (!info) return;
                if (Array.isArray(info.categories)) {
                    videoCategoryIds = info.categories.map(function (c) { return c.id; });
                }
                if (typeof info.rating === 'number') {
                    currentRating = info.rating;
                    updateStarIcon();
                    updateMiniStars();
                    updateBigStars();
                }
            })
            .catch(function (err) { console.error('refreshVideoInfo', err); });
    }

    function togglePlay() {
        if (!video) return;
        if (video.paused) {
            video.play().catch(function () {});
            playIcon.src = ICON_PAUSE;
            playIcon.alt = 'Pause';
        } else {
            video.pause();
            playIcon.src = ICON_PLAY;
            playIcon.alt = 'Play';
        }
    }

    function updateProgress() {
        if (!video) return;
        if (video.duration && isFinite(video.duration)) {
            var pct = (video.currentTime / video.duration) * 100;
            progressFill.style.width = pct + '%';
        } else {
            progressFill.style.width = '0%';
        }
    }

    function toggleMute() {
        if (!video) return;
        video.muted = !video.muted;
        playerState.muted = video.muted;
        muteIcon.src = video.muted ? ICON_MUTE : ICON_UNMUTE;
        muteIcon.alt = video.muted ? 'Mute' : 'Unmute';
    }

    function toggleFullscreen() {
        var fsElement = getFullscreenElement();
        if (!fsElement) {
            var req = wrapper.requestFullscreen
                || wrapper.webkitRequestFullscreen
                || wrapper.mozRequestFullScreen
                || wrapper.msRequestFullscreen;
            if (req) {
                try {
                    var result = req.call(wrapper);
                    if (result && result.catch) result.catch(function (err) {
                        console.warn('[watch] requestFullscreen rejected:', err);
                    });
                } catch (err) {
                    console.warn('[watch] requestFullscreen threw:', err);
                }
            } else if (video && video.webkitEnterFullscreen) {
                video.webkitEnterFullscreen();
            }
        } else {
            var exit = document.exitFullscreen
                || document.webkitExitFullscreen
                || document.mozCancelFullScreen
                || document.msExitFullscreen;
            if (exit) exit.call(document);
        }
    }

    function toggleLoop() {
        loopActive = !loopActive;
        playerState.loop = loopActive;
        if (video) video.loop = loopActive;
        loopIcon.src = loopActive ? ICON_LOOP_ON : ICON_LOOP_OFF;
        loopIcon.alt = loopActive ? 'Loop On' : 'Loop Off';
    }

    playBtn.addEventListener('click', togglePlay);

    function isCurrentVideoTarget(t) {
        return t && t.classList && t.classList.contains('vp-video')
            && slideMap.has(currentFeedIndex)
            && slideMap.get(currentFeedIndex).video === t;
    }

    vpStrip.addEventListener('play', function (e) {
        if (!isCurrentVideoTarget(e.target)) return;
        isPlaying = true;
        playIcon.src = ICON_PAUSE;
        playIcon.alt = 'Pause';
    }, true);

    vpStrip.addEventListener('pause', function (e) {
        if (!isCurrentVideoTarget(e.target)) return;
        isPlaying = false;
        playIcon.src = ICON_PLAY;
        playIcon.alt = 'Play';
    }, true);

    vpStrip.addEventListener('timeupdate', function (e) {
        if (!isCurrentVideoTarget(e.target)) return;
        updateProgress();
    }, true);

    vpStrip.addEventListener('loadedmetadata', function (e) {
        if (!isCurrentVideoTarget(e.target)) return;
        updateProgress();
    }, true);

    vpStrip.addEventListener('volumechange', function (e) {
        if (!isCurrentVideoTarget(e.target)) return;
        playerState.volume = e.target.volume;
        playerState.muted = e.target.muted;
        muteIcon.src = e.target.muted ? ICON_MUTE : ICON_UNMUTE;
        muteIcon.alt = e.target.muted ? 'Mute' : 'Unmute';
    }, true);

    vpStrip.addEventListener('ratechange', function (e) {
        if (!isCurrentVideoTarget(e.target)) return;
        playerState.rate = e.target.playbackRate;
    }, true);

    var hadTouchMove = false;
    wrapper.addEventListener('touchstart', function () { hadTouchMove = false; }, { passive: true });
    wrapper.addEventListener('touchmove', function () { hadTouchMove = true; }, { passive: true });

    wrapper.addEventListener('click', function (e) {
        var targetVideo = e.target.closest('.vp-video');
        if (!targetVideo) return;
        if (IS_MOBILE) {
            if (hadTouchMove) { hadTouchMove = false; return; }
            toggleControlsVisibility();
        } else {
            togglePlay();
        }
    });

    wrapper.addEventListener('dblclick', function (e) {
        var targetVideo = e.target.closest('.vp-video');
        if (!targetVideo) return;
        toggleFullscreen();
    });

    muteBtn.addEventListener('click', toggleMute);
    fullscreenBtn.addEventListener('click', toggleFullscreen);
    if (nextBtn) {
        nextBtn.addEventListener('click', function () {
            if (IS_MOBILE) navigateToVideo(1);
            else {
                var nextRec = document.querySelector('.recommendation-card-wrapper');
                if (nextRec) nextRec.click();
            }
        });
    }
    loopBtn.addEventListener('click', toggleLoop);

    document.addEventListener('keydown', function (e) {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        switch (e.key) {
            case ' ':
            case 'Space': e.preventDefault(); togglePlay(); break;
            case 'ArrowRight':
                e.preventDefault();
                if (video && video.duration) video.currentTime = Math.min(video.currentTime + 10, video.duration);
                break;
            case 'ArrowLeft':
                e.preventDefault();
                if (video) video.currentTime = Math.max(video.currentTime - 10, 0);
                break;
            case 'f': case 'F': e.preventDefault(); toggleFullscreen(); break;
            case 'm': case 'M': e.preventDefault(); toggleMute(); break;
            case 'l': case 'L': e.preventDefault(); toggleLoop(); break;
            case 'h': case 'H': e.preventDefault(); toggleMirror(); break;
            case 'ArrowDown':
                if (nextBtn && !IS_MOBILE) { e.preventDefault(); nextBtn.click(); }
                break;
            case 'Escape':
                if (isPlaylistSheetOpen) closePlaylistSheet();
                if (isCategorySheetOpen) closeCategorySheet();
                if (isRatingPanelOpen) closeRatingPanel();
                break;
        }
    });

    document.addEventListener('keydown', function (e) {
        if (!IS_MOBILE) return;
        if (!isFullscreen) return;
        if (isPlaylistSheetOpen || isCategorySheetOpen) return;
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        if (e.key === 'ArrowUp') { e.preventDefault(); navigateToVideo(-1); }
        else if (e.key === 'ArrowDown') { e.preventDefault(); navigateToVideo(1); }
    });

    if (!IS_MOBILE) {
        wrapper.addEventListener('mousemove', function () {
            showControls();
            hideControls();
        });
        wrapper.addEventListener('mouseleave', function () {
            wrapper.classList.remove('show-controls');
            clearTimeout(controlsTimeout);
        });
    }

    /* =========================================================
       ТАЙМЛАЙН + PREVIEW-КАДР (SCRUB)
       ========================================================= */
    function formatTime(t) {
        if (!isFinite(t) || t < 0) t = 0;
        var m = Math.floor(t / 60);
        var s = Math.floor(t % 60);
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    function updateScrubSrc(videoId) {
        if (!scrubVideo) return;
        var url = '/video/' + videoId;
        if (scrubVideo.src && scrubVideo.src.indexOf(url) !== -1) return;
        scrubVideo.src = url;
        try { scrubVideo.load(); } catch (e) {}
    }

    var scrubSeeking = false;
    var scrubPending = null;

    function requestScrubFrame(time) {
        if (scrubSeeking) { scrubPending = time; return; }
        scrubSeeking = true;
        try { scrubVideo.currentTime = time; }
        catch (e) { scrubSeeking = false; }
    }

    if (scrubVideo) {
        scrubVideo.addEventListener('seeked', function () {
            scrubSeeking = false;
            if (scrubPending !== null) {
                var t = scrubPending;
                scrubPending = null;
                requestScrubFrame(t);
            }
        });
    }

    function positionScrubTooltip(clientX) {
        var rect = progressTrack.getBoundingClientRect();
        var pct = (clientX - rect.left) / rect.width;
        pct = Math.max(0, Math.min(1, pct));

        var trackWidth = rect.width;
        var tipWidth = scrubTooltip.offsetWidth || 168;

        var leftPx = pct * trackWidth - tipWidth / 2;
        leftPx = Math.max(-8, Math.min(trackWidth - tipWidth + 8, leftPx));

        scrubTooltip.style.left = (leftPx + tipWidth / 2) + 'px';

        return pct;
    }

    function seekToPct(pct) {
        if (!video || !video.duration) return;
        var target = pct * video.duration;
        try { video.currentTime = target; } catch (e) {}
        saveVideoPosition(currentVideoId, target);
        updateProgress();
    }

    if (HAS_HOVER && !IS_MOBILE) {
        progressTrack.addEventListener('mouseenter', function () {
            if (!video || !video.duration) return;
            updateScrubSrc(currentVideoId);
            scrubTooltip.classList.add('visible');
        });

        progressTrack.addEventListener('mouseleave', function () {
            scrubTooltip.classList.remove('visible');
        });

        progressTrack.addEventListener('mousemove', function (e) {
            if (!video || !video.duration) return;
            var pct = positionScrubTooltip(e.clientX);
            var targetTime = pct * video.duration;
            scrubTime.textContent = formatTime(targetTime);
            requestScrubFrame(targetTime);
        });
    }

    var isScrubbing = false;

    progressTrack.addEventListener('mousedown', function (e) {
        if (!video || !video.duration) return;
        e.preventDefault();
        isScrubbing = true;
        var rect = progressTrack.getBoundingClientRect();
        var pct = (e.clientX - rect.left) / rect.width;
        pct = Math.max(0, Math.min(1, pct));
        seekToPct(pct);
    });

    document.addEventListener('mousemove', function (e) {
        if (!isScrubbing) return;
        if (!video || !video.duration) return;
        var rect = progressTrack.getBoundingClientRect();
        var pct = (e.clientX - rect.left) / rect.width;
        pct = Math.max(0, Math.min(1, pct));
        seekToPct(pct);
    });

    document.addEventListener('mouseup', function () {
        isScrubbing = false;
    });

    progressTrack.addEventListener('click', function (e) {
        if (isScrubbing) return;
        if (!video || !video.duration) return;
        var rect = progressTrack.getBoundingClientRect();
        var pct = (e.clientX - rect.left) / rect.width;
        pct = Math.max(0, Math.min(1, pct));
        seekToPct(pct);
    });

    /* =========================================================
       СВАЙПЫ
       ========================================================= */
    var FEED_THRESHOLD = 70;
    var FEED_MAX_X = 70;
    var feedSwipeState = null;

    function feedTouchStart(e) {
        if (isPlaylistSheetOpen || isCategorySheetOpen) { feedSwipeState = null; return; }
        if (!IS_MOBILE) { feedSwipeState = null; return; }
        if (!isFullscreen) { feedSwipeState = null; return; }
        if (e.touches.length !== 1) { feedSwipeState = null; return; }
        feedSwipeState = {
            startY: e.touches[0].clientY,
            startX: e.touches[0].clientX,
            candidate: true
        };
    }

    function feedTouchMove(e) {
        if (!feedSwipeState) return;
        if (e.touches.length !== 1) { feedSwipeState.candidate = false; return; }
        var dy = e.touches[0].clientY - feedSwipeState.startY;
        var dx = e.touches[0].clientX - feedSwipeState.startX;
        if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 20) feedSwipeState.candidate = false;
    }

    function feedTouchEnd(e) {
        if (isPlaylistSheetOpen || isCategorySheetOpen) { feedSwipeState = null; return; }
        if (!IS_MOBILE) { feedSwipeState = null; return; }
        if (!feedSwipeState || !feedSwipeState.candidate) { feedSwipeState = null; return; }
        if (!e.changedTouches || e.changedTouches.length === 0) { feedSwipeState = null; return; }
        var touch = e.changedTouches[0];
        var dy = touch.clientY - feedSwipeState.startY;
        var dx = touch.clientX - feedSwipeState.startX;
        feedSwipeState = null;
        if (Math.abs(dx) > FEED_MAX_X) return;
        if (dy < -FEED_THRESHOLD) navigateToVideo(1);
        else if (dy > FEED_THRESHOLD) navigateToVideo(-1);
    }

    wrapper.addEventListener('touchstart', feedTouchStart, { passive: true });
    wrapper.addEventListener('touchmove', feedTouchMove, { passive: true });
    wrapper.addEventListener('touchend', feedTouchEnd, { passive: true });

    var wheelLocked = false;
    wrapper.addEventListener('wheel', function (e) {
        if (isPlaylistSheetOpen || isCategorySheetOpen) return;
        if (!IS_MOBILE) return;
        if (!isFullscreen) return;
        if (wheelLocked) return;
        if (Math.abs(e.deltaY) < 30) return;
        wheelLocked = true;
        setTimeout(function () { wheelLocked = false; }, 700);
        if (e.deltaY > 0) navigateToVideo(1);
        else navigateToVideo(-1);
    }, { passive: true });

    /* =========================================================
       TOAST / HINT
       ========================================================= */
    var toastEl = null;
    var toastTimer = null;
    function showFeedToast(text) {
        if (!IS_MOBILE) return;
        if (!toastEl) {
            toastEl = document.createElement('div');
            toastEl.className = 'feed-toast';
            wrapper.appendChild(toastEl);
        }
        toastEl.textContent = text;
        toastEl.classList.add('show');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { toastEl.classList.remove('show'); }, 1500);
    }

    function showFeedHint() {
        if (!IS_MOBILE) return;
        if (FEED_IDS.length <= 1) return;
        var hint = document.createElement('div');
        hint.className = 'feed-hint';
        hint.innerHTML = '<div class="arrow">▲</div><div>Swipe up for next</div>';
        wrapper.appendChild(hint);
        setTimeout(function () { hint.classList.add('show'); }, 80);
        setTimeout(function () {
            hint.classList.remove('show');
            setTimeout(function () { hint.remove(); }, 500);
        }, 2800);
    }

    /* =========================================================
       FULLSCREEN CHANGE
       ========================================================= */
    function onFullscreenChange() {
        var wasFullscreen = isFullscreen;
        updateFullscreenState();

        if (isFullscreen && !wasFullscreen) {
            wrapper.classList.remove('show-controls');
            clearTimeout(controlsTimeout);
            setTimeout(function () {
                if (isFullscreen) { showControls(); hideControls(); }
            }, 200);
            if (IS_MOBILE && FEED_IDS.length > 1 && !isPlaylistSheetOpen && !isCategorySheetOpen) {
                setTimeout(showFeedHint, 600);
            }
        } else if (!isFullscreen && wasFullscreen) {
            if (isPlaylistSheetOpen) closePlaylistSheet();
            if (isCategorySheetOpen) closeCategorySheet();
            closeRatingPanel();
            wrapper.classList.remove('show-controls');
            clearTimeout(controlsTimeout);
        }
        applyMirrorState();
    }

    document.addEventListener('fullscreenchange', onFullscreenChange);
    document.addEventListener('webkitfullscreenchange', onFullscreenChange);
    document.addEventListener('mozfullscreenchange', onFullscreenChange);
    document.addEventListener('MSFullscreenChange', onFullscreenChange);

    updateFullscreenState();

    /* =========================================================
       POPSTATE
       ========================================================= */
    window.addEventListener('popstate', function (e) {
        var state = e.state;
        if (state && state.videoId !== undefined) {
            var newId = state.videoId;
            if (newId === currentVideoId) return;
            var newIdx = state.feedIndex;
            if (typeof newIdx === 'number' && newIdx >= 0 && newIdx < FEED_IDS.length) {
                if (isPlaylistSheetOpen) closePlaylistSheet();
                if (isCategorySheetOpen) closeCategorySheet();
                closeRatingPanel();

                var old = slideMap.get(currentFeedIndex);
                if (old) {
                    try {
                        if (!old.video.paused && !old.video.seeking) {
                            saveVideoPosition(old.videoId, old.video.currentTime);
                        }
                        old.video.pause();
                    } catch (err) {}
                }

                currentFeedIndex = newIdx;
                currentVideoId = newId;
                rebuildWindow(true);
                var entry = slideMap.get(currentFeedIndex);
                if (entry) {
                    video = entry.video;
                    applyPlayerStateToVideo(video);
                    video.play().catch(function () {});
                }
                updateScrubSrc(currentVideoId);
                updateMobilePlaylistButtonState();
                refreshVideoInfo(currentVideoId);
            } else {
                window.location.reload();
            }
        } else {
            window.location.reload();
        }
    });

    window.addEventListener('beforeunload', function () {
        if (video && currentVideoId) {
            try {
                if (!video.paused && !video.seeking) {
                    saveVideoPosition(currentVideoId, video.currentTime);
                }
            } catch (e) {}
        }
    });

    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) return;
        if (video && currentVideoId) {
            try {
                if (!video.paused && !video.seeking) {
                    saveVideoPosition(currentVideoId, video.currentTime);
                }
            } catch (e) {}
        }
    });

    /* =========================================================
       PLAYLISTS
       ========================================================= */
    var mobilePlaylistData = {};

    function updateMobilePlaylistButtonState() {
        if (!mobilePlaylistBtn) return;
        fetch('/playlists?page=1&limit=1000&video_id=' + currentVideoId + '&profile=' + CURRENT_PROFILE)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var anyIn = (data.playlists || []).some(function (p) { return p.video_in_playlist; });
                if (mobilePlaylistIcon) mobilePlaylistIcon.src = anyIn ? ICON_PLD : ICON_PLN;
            })
            .catch(function (err) { console.error('updateMobilePlaylistButtonState', err); });
    }

    function loadMobilePlaylistsForModal() {
        var listEl = document.getElementById('mobilePlaylistList');
        listEl.innerHTML = '<div class="mp-empty">Loading...</div>';
        fetch('/playlists?page=1&limit=1000&video_id=' + currentVideoId + '&profile=' + CURRENT_PROFILE)
            .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(function (data) {
                mobilePlaylistData = {};
                listEl.innerHTML = '';
                if (!data.playlists || data.playlists.length === 0) {
                    listEl.innerHTML = '<div class="mp-empty">No playlists yet.<br>Create one above.</div>';
                    return;
                }
                data.playlists.forEach(function (pl) {
                    mobilePlaylistData[pl.id] = pl.video_in_playlist;

                    var item = document.createElement('div');
                    item.className = 'mp-item' + (pl.video_in_playlist ? ' in-playlist' : '');
                    item.dataset.playlistId = pl.id;

                    var info = document.createElement('div');
                    info.className = 'mp-info';
                    var name = document.createElement('div');
                    name.className = 'mp-name';
                    name.textContent = pl.name;
                    var count = document.createElement('div');
                    count.className = 'mp-count';
                    count.textContent = (pl.video_count || 0) + ' videos';
                    info.appendChild(name);
                    info.appendChild(count);

                    var toggle = document.createElement('button');
                    toggle.type = 'button';
                    toggle.className = 'mp-toggle';
                    toggle.textContent = pl.video_in_playlist ? '✓' : '+';
                    toggle.addEventListener('click', function (e) {
                        e.stopPropagation();
                        toggleVideoInPlaylistMobile(pl.id, !mobilePlaylistData[pl.id]);
                    });

                    var del = document.createElement('button');
                    del.type = 'button';
                    del.className = 'mp-delete';
                    del.title = 'Delete playlist';
                    var delImg = document.createElement('img');
                    delImg.src = ICON_BIN;
                    delImg.alt = 'Delete';
                    del.appendChild(delImg);
                    del.addEventListener('click', function (e) {
                        e.stopPropagation();
                        deletePlaylistMobile(pl.id, pl.name);
                    });

                    item.appendChild(info);
                    item.appendChild(toggle);
                    item.appendChild(del);
                    listEl.appendChild(item);
                });
            })
            .catch(function (err) {
                console.error('loadMobilePlaylistsForModal', err);
                listEl.innerHTML = '<div class="mp-empty">Error loading playlists</div>';
            });
    }

    function toggleVideoInPlaylistMobile(playlistId, add) {
        var url = '/playlist/' + playlistId + (add ? '/add' : '/remove');
        fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ video_id: currentVideoId })
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.success) {
                    var item = document.querySelector('.mp-item[data-playlist-id="' + playlistId + '"]');
                    if (item) {
                        item.classList.toggle('in-playlist', add);
                        var toggle = item.querySelector('.mp-toggle');
                        if (toggle) toggle.textContent = add ? '✓' : '+';
                    }
                    mobilePlaylistData[playlistId] = add;
                    updateMobilePlaylistButtonState();
                    loadPlaylists(1);
                    loadPlaylistDropdown();
                }
            })
            .catch(function (err) { console.error('toggleVideoInPlaylistMobile', err); });
    }

    function createPlaylistMobile() {
        var input = document.getElementById('mobilePlaylistNameInput');
        var name = input.value.trim();
        if (!name) return;
        fetch('/playlist/create?profile=' + CURRENT_PROFILE, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.success) {
                    input.value = '';
                    return fetch('/playlist/' + data.playlist_id + '/add', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ video_id: currentVideoId })
                    })
                        .then(function () {
                            loadMobilePlaylistsForModal();
                            updateMobilePlaylistButtonState();
                            loadPlaylists(1);
                            loadPlaylistDropdown();
                        });
                } else {
                    alert('Error: ' + (data.error || 'Unknown'));
                }
            })
            .catch(function (err) { console.error('createPlaylistMobile', err); });
    }

    function deletePlaylistMobile(playlistId, name) {
        if (!confirm('Delete playlist "' + name + '"?')) return;
        fetch('/playlist/' + playlistId + '/delete', { method: 'POST' })
            .then(function () {
                loadMobilePlaylistsForModal();
                updateMobilePlaylistButtonState();
                loadPlaylists(1);
                loadPlaylistDropdown();
            })
            .catch(function (err) { console.error('deletePlaylistMobile', err); });
    }

    if (mobilePlaylistBtn) {
        mobilePlaylistBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            showControls();
            hideControls();
            openPlaylistSheet();
        });
        var createBtn = document.getElementById('mobilePlaylistCreateBtn');
        if (createBtn) createBtn.addEventListener('click', createPlaylistMobile);
        var nameInput = document.getElementById('mobilePlaylistNameInput');
        if (nameInput) {
            nameInput.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') createPlaylistMobile();
            });
        }
        updateMobilePlaylistButtonState();
    }

    if (mobileCategoryBtn) {
        mobileCategoryBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            showControls();
            hideControls();
            openCategorySheet();
        });
    }

    var carouselContainer = document.getElementById('playlistCarousel');
    var carouselPrevBtn = document.getElementById('carouselPrev');
    var carouselNextBtn = document.getElementById('carouselNext');
    var debugInfo = document.getElementById('debugInfo');
    var playlistPage = 1;
    var isLoading = false;

    function showLoading() {
        carouselContainer.innerHTML = '<div class="text-muted text-center" style="width:100%; padding: 20px 0;">Loading...</div>';
        debugInfo.classList.remove('show');
    }
    function showError(message) {
        carouselContainer.innerHTML = '<div class="text-danger text-center" style="width:100%; padding: 20px 0;">⚠️ ' + message + '</div>';
        debugInfo.textContent = '❌ ' + message;
        debugInfo.classList.add('show');
    }
    function showEmpty() {
        carouselContainer.innerHTML = '<div class="text-muted text-center" style="width:100%; padding: 20px 0;">No playlists yet</div>';
        debugInfo.classList.remove('show');
    }

    function loadPlaylists(page) {
        if (isLoading) return;
        isLoading = true;
        if (page === 1) showLoading();
        var url = '/playlists?page=' + page + '&video_id=' + currentVideoId + '&profile=' + CURRENT_PROFILE;
        fetch(url)
            .then(function (response) {
                if (!response.ok) throw new Error('HTTP ' + response.status);
                return response.json();
            })
            .then(function (data) {
                isLoading = false;
                if (page === 1) carouselContainer.innerHTML = '';
                if (!data.playlists || data.playlists.length === 0) {
                    if (page === 1) showEmpty();
                    return;
                }
                data.playlists.forEach(function (pl) {
                    var card = document.createElement('div');
                    card.className = 'playlist-card';
                    card.dataset.playlistId = pl.id;
                    card.addEventListener('click', function (e) {
                        if (e.target.closest('button')) return;
                        window.location.href = '/playlist/' + pl.id + '?profile=' + CURRENT_PROFILE;
                    });

                    var coverDiv = document.createElement('div');
                    coverDiv.className = 'playlist-cover';
                    if (pl.cover_video) {
                        var coverVideo = pl.cover_video;
                        var ext = coverVideo.filename.split('.').pop().toLowerCase();
                        if (['mp4', 'avi', 'mkv', 'mov', 'wmv', 'flv', 'webm', 'm4v'].indexOf(ext) !== -1) {
                            var vid = document.createElement('video');
                            vid.muted = true;
                            vid.playsInline = true;
                            vid.preload = 'metadata';
                            var src = document.createElement('source');
                            src.src = '/video/' + coverVideo.id;
                            src.type = 'video/mp4';
                            vid.appendChild(src);
                            coverDiv.appendChild(vid);
                        } else {
                            var img = document.createElement('img');
                            img.src = '/video/' + coverVideo.id;
                            img.alt = coverVideo.filename;
                            coverDiv.appendChild(img);
                        }
                    } else {
                        var placeholder = document.createElement('div');
                        placeholder.className = 'placeholder';
                        placeholder.textContent = '📁';
                        coverDiv.appendChild(placeholder);
                    }
                    card.appendChild(coverDiv);

                    var infoDiv = document.createElement('div');
                    infoDiv.className = 'playlist-info';
                    var nameSpan = document.createElement('span');
                    nameSpan.className = 'playlist-name';
                    nameSpan.textContent = pl.name;
                    nameSpan.title = pl.name;
                    infoDiv.appendChild(nameSpan);

                    var actionsDiv = document.createElement('div');
                    actionsDiv.className = 'playlist-actions';

                    var coverBtn = document.createElement('button');
                    coverBtn.className = 'btn-cover';
                    coverBtn.title = 'Set cover';
                    var coverImg = document.createElement('img');
                    coverImg.src = ICON_PIC;
                    coverImg.alt = 'Set cover';
                    coverBtn.appendChild(coverImg);
                    coverBtn.addEventListener('click', function (e) { e.stopPropagation(); openCoverModal(pl.id); });
                    actionsDiv.appendChild(coverBtn);

                    var renameBtn = document.createElement('button');
                    renameBtn.className = 'btn-rename';
                    renameBtn.title = 'Rename playlist';
                    var renameImg = document.createElement('img');
                    renameImg.src = ICON_PENCIL;
                    renameImg.alt = 'Rename';
                    renameBtn.appendChild(renameImg);
                    renameBtn.addEventListener('click', function (e) { e.stopPropagation(); openRenameModal(pl.id, pl.name); });
                    actionsDiv.appendChild(renameBtn);

                    var delBtn = document.createElement('button');
                    delBtn.className = 'btn-delete';
                    delBtn.title = 'Delete playlist';
                    var delImg = document.createElement('img');
                    delImg.src = ICON_BIN;
                    delImg.alt = 'Delete';
                    delBtn.appendChild(delImg);
                    delBtn.addEventListener('click', function (e) {
                        e.stopPropagation();
                        document.getElementById('deletePlaylistId').value = pl.id;
                        document.getElementById('deletePlaylistMessage').textContent = 'Are you sure you want to delete the playlist "' + pl.name + '"? This action cannot be undone.';
                        new bootstrap.Modal(document.getElementById('confirmDeleteModal')).show();
                    });
                    actionsDiv.appendChild(delBtn);

                    infoDiv.appendChild(actionsDiv);
                    card.appendChild(infoDiv);
                    carouselContainer.appendChild(card);

                    setupPlaylistNameMarquee(nameSpan);
                });
                loadPlaylistDropdown();
                setTimeout(updateNavButtons, 100);
            })
            .catch(function (err) {
                isLoading = false;
                console.error('Ошибка загрузки плейлистов:', err);
                if (page === 1) showError('Error: ' + err.message);
            });
    }

    function getCardWidth() {
        var card = carouselContainer.querySelector('.playlist-card');
        if (!card) return 200;
        return card.offsetWidth + 16;
    }
    function getVisibleCount() {
        var card = carouselContainer.querySelector('.playlist-card');
        if (!card) return 10;
        var containerWidth = carouselContainer.clientWidth;
        var cardWidth = card.offsetWidth + 16;
        return Math.floor(containerWidth / cardWidth);
    }
    function scrollCarousel(direction) {
        var container = carouselContainer;
        var cardWidth = getCardWidth();
        var visible = getVisibleCount();
        var scrollAmount = cardWidth * Math.max(1, visible);
        var currentScroll = container.scrollLeft;
        var targetScroll;
        if (direction === 'next') {
            targetScroll = Math.min(currentScroll + scrollAmount, container.scrollWidth - container.clientWidth);
        } else {
            targetScroll = Math.max(currentScroll - scrollAmount, 0);
        }
        container.scrollTo({ left: targetScroll, behavior: 'smooth' });
    }

    carouselPrevBtn.addEventListener('click', function () { scrollCarousel('prev'); });
    carouselNextBtn.addEventListener('click', function () { scrollCarousel('next'); });

    function updateNavButtons() {
        var container = carouselContainer;
        if (!container) return;
        var maxScroll = container.scrollWidth - container.clientWidth;
        if (maxScroll <= 0) {
            carouselPrevBtn.disabled = true;
            carouselNextBtn.disabled = true;
            carouselPrevBtn.style.opacity = '0.35';
            carouselNextBtn.style.opacity = '0.35';
            return;
        }
        var atStart = container.scrollLeft <= 1;
        var atEnd = container.scrollLeft >= maxScroll - 1;
        carouselPrevBtn.disabled = atStart;
        carouselNextBtn.disabled = atEnd;
        carouselPrevBtn.style.opacity = atStart ? '0.35' : '1';
        carouselNextBtn.style.opacity = atEnd ? '0.35' : '1';
    }
    carouselContainer.addEventListener('scroll', updateNavButtons);
    window.addEventListener('resize', updateNavButtons);

    var currentPlaylistIdForCover = null;
    var selectedCoverVideoId = null;

    function openCoverModal(playlistId) {
        currentPlaylistIdForCover = playlistId;
        selectedCoverVideoId = null;
        document.getElementById('selectPlaylistCoverBtn').disabled = true;
        var modal = new bootstrap.Modal(document.getElementById('playlistCoverModal'));
        var grid = document.getElementById('playlistCoverGrid');
        grid.innerHTML = '<div class="text-muted" style="grid-column:1/-1; padding:20px;">Loading videos...</div>';
        modal.show();

        fetch('/playlist/' + playlistId + '/videos')
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data.videos.length === 0) {
                    grid.innerHTML = '<div class="text-muted" style="grid-column:1/-1; padding:20px;">No videos in this playlist.</div>';
                    return;
                }
                grid.innerHTML = '';
                data.videos.forEach(function (v) {
                    var item = document.createElement('div');
                    item.className = 'cover-item';
                    item.dataset.videoId = v.id;
                    var ext = v.filename.split('.').pop().toLowerCase();
                    if (['mp4', 'avi', 'mkv', 'mov', 'wmv', 'flv', 'webm', 'm4v'].indexOf(ext) !== -1) {
                        var vid = document.createElement('video');
                        vid.muted = true;
                        vid.playsInline = true;
                        vid.preload = 'metadata';
                        var src = document.createElement('source');
                        src.src = '/video/' + v.id;
                        src.type = 'video/mp4';
                        vid.appendChild(src);
                        item.appendChild(vid);
                    } else {
                        var img = document.createElement('img');
                        img.src = '/video/' + v.id;
                        img.alt = v.filename;
                        item.appendChild(img);
                    }
                    var check = document.createElement('div');
                    check.className = 'check-mark';
                    check.textContent = '✓';
                    item.appendChild(check);
                    item.addEventListener('click', function () {
                        document.querySelectorAll('.cover-item').forEach(function (el) { el.classList.remove('active'); });
                        this.classList.add('active');
                        selectedCoverVideoId = v.id;
                        document.getElementById('selectPlaylistCoverBtn').disabled = false;
                    });
                    grid.appendChild(item);
                });
            })
            .catch(function (err) {
                grid.innerHTML = '<div class="text-danger" style="grid-column:1/-1; padding:20px;">Error loading videos.</div>';
                console.error(err);
            });
    }

    document.getElementById('selectPlaylistCoverBtn').addEventListener('click', function () {
        if (!selectedCoverVideoId || !currentPlaylistIdForCover) return;
        fetch('/playlist/' + currentPlaylistIdForCover + '/set_cover', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ video_id: selectedCoverVideoId })
        })
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data.success) {
                    var modal = bootstrap.Modal.getInstance(document.getElementById('playlistCoverModal'));
                    modal.hide();
                    playlistPage = 1;
                    loadPlaylists(1);
                } else {
                    alert('Error: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(function (err) { alert('Error: ' + err); });
    });

    function confirmDeletePlaylist() {
        var playlistId = parseInt(document.getElementById('deletePlaylistId').value, 10);
        if (!playlistId) return;
        fetch('/playlist/' + playlistId + '/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        })
            .then(function (response) { return response.json(); })
            .then(function (data) {
                if (data.success) {
                    var modal = bootstrap.Modal.getInstance(document.getElementById('confirmDeleteModal'));
                    modal.hide();
                    playlistPage = 1;
                    loadPlaylists(1);
                    updateMobilePlaylistButtonState();
                } else {
                    alert('Error deleting playlist');
                }
            })
            .catch(function (err) { console.error('Error:', err); });
    }

    document.getElementById('confirmDeleteBtn').addEventListener('click', confirmDeletePlaylist);

    var dropdownMenu = document.getElementById('playlistDropdownMenu');

    function loadPlaylistDropdown() {
        fetch('/playlists?page=1&limit=100&video_id=' + currentVideoId + '&profile=' + CURRENT_PROFILE)
            .then(function (response) { return response.json(); })
            .then(function (data) {
                dropdownMenu.innerHTML = '';
                var addNew = document.createElement('li');
                var addNewLink = document.createElement('a');
                addNewLink.className = 'dropdown-item';
                addNewLink.href = '#';
                addNewLink.dataset.action = 'add-new';
                addNewLink.innerHTML = 'Add new <span class="add-btn">+</span>';
                addNewLink.addEventListener('click', function (e) {
                    e.preventDefault();
                    new bootstrap.Modal(document.getElementById('createPlaylistModal')).show();
                });
                addNew.appendChild(addNewLink);
                dropdownMenu.appendChild(addNew);

                if (data.playlists && data.playlists.length > 0) {
                    var divider = document.createElement('li');
                    divider.className = 'dropdown-divider';
                    dropdownMenu.appendChild(divider);
                    data.playlists.forEach(function (pl) {
                        var li = document.createElement('li');
                        var a = document.createElement('a');
                        a.className = 'dropdown-item';
                        a.href = '#';
                        a.dataset.playlistId = pl.id;
                        a.innerHTML = pl.name + ' <span class="add-btn">+</span>';
                        a.addEventListener('click', function (e) {
                            e.preventDefault();
                            addVideoToPlaylist(parseInt(this.dataset.playlistId, 10), currentVideoId);
                        });
                        li.appendChild(a);
                        dropdownMenu.appendChild(li);
                    });
                }
            })
            .catch(function (err) { console.error('Error loading dropdown:', err); });
    }

    function addVideoToPlaylist(playlistId, videoId) {
        fetch('/playlist/' + playlistId + '/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ video_id: videoId })
        })
            .then(function (response) { return response.json(); })
            .then(function (data) {
                if (data.success) {
                    playlistPage = 1;
                    loadPlaylists(1);
                    updateMobilePlaylistButtonState();
                } else {
                    alert('Error: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(function (err) { console.error('Error:', err); });
    }

    document.getElementById('savePlaylistBtn').addEventListener('click', function () {
        var nameInput = document.getElementById('playlistNameInput');
        var name = nameInput.value.trim();
        if (!name) { alert('Please enter a name'); return; }
        fetch('/playlist/create?profile=' + CURRENT_PROFILE, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        })
            .then(function (response) { return response.json(); })
            .then(function (data) {
                if (data.success) {
                    var modal = bootstrap.Modal.getInstance(document.getElementById('createPlaylistModal'));
                    modal.hide();
                    nameInput.value = '';
                    addVideoToPlaylist(data.playlist_id, currentVideoId);
                } else {
                    alert('Error: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(function (err) { console.error('Error:', err); });
    });

    document.getElementById('createPlaylistModal').addEventListener('shown.bs.modal', function () {
        document.getElementById('playlistNameInput').focus();
    });

    function openRenameModal(playlistId, currentName) {
        document.getElementById('renamePlaylistId').value = playlistId;
        document.getElementById('renamePlaylistInput').value = currentName;
        new bootstrap.Modal(document.getElementById('renamePlaylistModal')).show();
    }

    document.getElementById('renamePlaylistBtn').addEventListener('click', function () {
        var playlistId = parseInt(document.getElementById('renamePlaylistId').value, 10);
        var newName = document.getElementById('renamePlaylistInput').value.trim();
        if (!newName) { alert('Please enter a name'); return; }
        fetch('/playlist/' + playlistId + '/rename', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: newName })
        })
            .then(function (response) { return response.json(); })
            .then(function (data) {
                if (data.success) {
                    var modal = bootstrap.Modal.getInstance(document.getElementById('renamePlaylistModal'));
                    modal.hide();
                    playlistPage = 1;
                    loadPlaylists(1);
                } else {
                    alert('Error: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(function (err) { console.error('Error:', err); });
    });

    /* =========================================================
       INIT
       ========================================================= */
    window.history.replaceState(
        { videoId: currentVideoId, feedIndex: currentFeedIndex },
        '',
        window.location.href
    );

    rebuildWindow(true);
    var initialEntry = slideMap.get(currentFeedIndex);
    if (initialEntry) {
        video = initialEntry.video;
        playerState.volume = 1.0;
        playerState.muted = false;
        playerState.rate = 1.0;
        playerState.loop = false;
        video.volume = 1.0;
        video.muted = false;
    }

    updateScrubSrc(currentVideoId);

    applyMirrorState();
    loadPlaylists(1);

    /* =========================================================
       HOVER-ПРЕВЬЮ ДЛЯ РЕКОМЕНДАЦИЙ
       ========================================================= */
    (function () {
        var SEGMENTS = 3;
        var SHOW_DURATION = 2;
        var previewIntervals = new Map();

        function resetPreviewVideo(v) {
            if (previewIntervals.has(v)) {
                clearInterval(previewIntervals.get(v));
                previewIntervals.delete(v);
            }
            v.currentTime = 0;
            v.pause();
        }

        function startPreviewOnHover(v) {
            if (!v.duration || isNaN(v.duration) || v.duration === Infinity) {
                v.addEventListener('loadedmetadata', function onMeta() {
                    v.removeEventListener('loadedmetadata', onMeta);
                    startPreviewOnHover(v);
                }, { once: true });
                return;
            }
            var duration = v.duration;
            if (duration < 0.5) { v.loop = true; v.play().catch(function () {}); return; }
            var segmentDuration = duration / SEGMENTS;
            var startPoints = [];
            for (var i = 0; i < SEGMENTS; i++) startPoints.push(i * segmentDuration);
            var currentSegment = 0;
            function switchToNextSegment() {
                currentSegment = (currentSegment + 1) % SEGMENTS;
                var newTime = startPoints[currentSegment];
                if (newTime >= duration) newTime = duration - 0.1;
                v.currentTime = newTime;
                if (v.paused) v.play().catch(function () {});
            }
            v.currentTime = startPoints[0];
            v.play().catch(function () {});
            var intervalId = setInterval(switchToNextSegment, SHOW_DURATION * 1000);
            previewIntervals.set(v, intervalId);
        }

        document.querySelectorAll('.recommendation-card-wrapper').forEach(function (wrapperEl) {
            var v = wrapperEl.querySelector('video');
            if (!v) return;
            wrapperEl.addEventListener('mouseenter', function () {
                if (previewIntervals.has(v)) {
                    clearInterval(previewIntervals.get(v));
                    previewIntervals.delete(v);
                }
                startPreviewOnHover(v);
            });
            wrapperEl.addEventListener('mouseleave', function () {
                resetPreviewVideo(v);
            });
        });
        document.querySelectorAll('.recommendation-card-wrapper video').forEach(function (v) {
            if (v.readyState >= 1) { v.currentTime = 0; v.pause(); }
            else v.addEventListener('loadedmetadata', function () { v.currentTime = 0; v.pause(); });
        });
    })();

    console.log('[watch] ready. IS_MOBILE=' + IS_MOBILE + ', HAS_HOVER=' + HAS_HOVER + ', feed size=' + FEED_IDS.length);
})();