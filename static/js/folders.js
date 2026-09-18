/* ============================================================
   folders.js — JS страницы /folders
   • Клик по карточке — переход в папку
   • Модалка установки обложки папки
   Конфиг: window.FOLDERS_CONFIG
   ============================================================ */
(function () {
    'use strict';

    var CFG = window.FOLDERS_CONFIG || {};
    var CURRENT_PROFILE     = CFG.currentProfile || 'female';
    var PREVIEW_SEEK_SECONDS = 6;

    var coverModalInstance = null;
    var currentFolderPath  = null;
    var currentFolderName  = null;
    var selectedVideoId    = null;

    function openCoverModal(folderPath, folderName) {
        currentFolderPath = folderPath;
        currentFolderName = folderName;
        selectedVideoId   = null;

        document.getElementById('coverFolderName').textContent = folderName;
        var selectBtn = document.getElementById('selectCoverBtn');
        selectBtn.disabled = true;

        var grid = document.getElementById('coverVideosGrid');
        grid.innerHTML = '<div class="no-videos-msg">Loading…</div>';

        if (!coverModalInstance) {
            coverModalInstance = new bootstrap.Modal(document.getElementById('coverModal'));
        }
        coverModalInstance.show();

        fetch('/folder_videos?path=' + encodeURIComponent(folderPath))
            .then(function (response) { return response.json(); })
            .then(function (data) {
                var videos = data.videos || [];
                grid.innerHTML = '';

                if (videos.length === 0) {
                    grid.innerHTML = '<div class="no-videos-msg">No videos in this folder.</div>';
                    return;
                }

                videos.forEach(function (video) {
                    var item = document.createElement('div');
                    item.className = 'cover-item';
                    item.dataset.videoId = video.id;

                    var ext = (video.filename.split('.').pop() || '').toLowerCase();
                    var isVideo = ['mp4', 'mov', 'webm', 'mkv', 'm4v'].indexOf(ext) !== -1;
                    var url = '/video/' + video.id;

                    if (isVideo) {
                        var videoEl = document.createElement('video');
                        videoEl.src = url;
                        videoEl.muted = true;
                        videoEl.playsInline = true;
                        videoEl.preload = 'metadata';
                        videoEl.addEventListener('loadedmetadata', function () {
                            try {
                                var dur = videoEl.duration;
                                if (!dur || isNaN(dur)) return;
                                var target = PREVIEW_SEEK_SECONDS;
                                if (dur < PREVIEW_SEEK_SECONDS * 2) target = dur / 2;
                                videoEl.currentTime = target;
                            } catch (e) {}
                        }, { once: true });
                        item.appendChild(videoEl);
                    } else {
                        var img = document.createElement('img');
                        img.src = url;
                        img.alt = video.filename;
                        img.loading = 'lazy';
                        item.appendChild(img);
                    }

                    var nameEl = document.createElement('div');
                    nameEl.className = 'cname';
                    nameEl.textContent = video.filename;
                    nameEl.title = video.filename;
                    item.appendChild(nameEl);

                    var chk = document.createElement('div');
                    chk.className = 'check-mark';
                    chk.textContent = '✓';
                    item.appendChild(chk);

                    if (data.current_cover && parseInt(video.id, 10) === parseInt(data.current_cover, 10)) {
                        item.classList.add('active');
                        selectedVideoId = video.id;
                        selectBtn.disabled = false;
                    }

                    item.addEventListener('click', function (e) {
                        e.stopPropagation();
                        document.querySelectorAll('.cover-item').forEach(function (el) {
                            el.classList.remove('active');
                        });
                        this.classList.add('active');
                        selectedVideoId = video.id;
                        selectBtn.disabled = false;
                    });

                    grid.appendChild(item);
                });
            })
            .catch(function (err) {
                console.error('Error loading videos:', err);
                grid.innerHTML = '<div class="no-videos-msg">Error loading videos.</div>';
            });
    }

    // Экспорт для inline onclick в шаблоне (не обязательно, но удобно)
    window.openCoverModal = openCoverModal;

    function setCover(folderPath, videoId) {
        if (!folderPath || !videoId) {
            alert('Please select a video first.');
            return;
        }

        var selectBtn = document.getElementById('selectCoverBtn');
        selectBtn.disabled = true;
        selectBtn.textContent = 'Saving…';

        fetch('/set_folder_cover', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                folder_path: folderPath,
                video_id: videoId
            })
        })
            .then(function (response) { return response.json(); })
            .then(function (data) {
                if (data.success) {
                    if (coverModalInstance) coverModalInstance.hide();
                    location.reload();
                } else {
                    alert('Failed to set cover: ' + (data.error || 'Unknown error'));
                    selectBtn.disabled = false;
                    selectBtn.textContent = 'Select';
                }
            })
            .catch(function (err) {
                console.error('Error setting cover:', err);
                alert('Network error');
                selectBtn.disabled = false;
                selectBtn.textContent = 'Select';
            });
    }

    document.addEventListener('DOMContentLoaded', function () {
        // Клик по карточке — переход в папку
        document.querySelectorAll('.folder-card').forEach(function (card) {
            card.addEventListener('click', function (e) {
                if (e.target.closest('.cover-btn')) return;
                var folderPath = this.dataset.folderPath;
                if (folderPath) {
                    var url = new URL('/', window.location.origin);
                    url.searchParams.set('folder', folderPath);
                    url.searchParams.set('profile', CURRENT_PROFILE);
                    window.location.href = url.toString();
                }
            });
        });

        // Кнопка Select в модалке
        var selectBtn = document.getElementById('selectCoverBtn');
        if (selectBtn) {
            selectBtn.addEventListener('click', function () {
                if (!selectedVideoId) return;
                setCover(currentFolderPath, selectedVideoId);
            });
        }
    });
})();