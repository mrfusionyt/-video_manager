/* ============================================================
   playlist.js — JS страницы /playlist/<id>
   • Set Cover — открытие модалки, выбор, отправка
   • hover-превью карточек — уже делает preview_hover.js
   Конфиг: window.PLAYLIST_CONFIG
   ============================================================ */
(function () {
    'use strict';

    var CFG = window.PLAYLIST_CONFIG || {};
    var playlistId     = CFG.playlistId;
    var currentProfile = CFG.currentProfile || 'female';

    var setCoverBtn = document.getElementById('setCoverBtn');
    if (!setCoverBtn) return;

    var playlistCoverModal = null;

    function openCoverModal() {
        var modalEl = document.getElementById('playlistCoverModal');
        var grid    = document.getElementById('playlistCoverGrid');
        var selectBtn = document.getElementById('selectPlaylistCoverBtn');

        grid.innerHTML = '<div class="text-muted" style="grid-column:1/-1; padding:20px;">Loading videos...</div>';
        selectBtn.disabled = true;

        if (!playlistCoverModal) {
            playlistCoverModal = new bootstrap.Modal(modalEl);
        }
        playlistCoverModal.show();

        var selectedVideoId = null;

        fetch('/playlist/' + playlistId + '/videos')
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (!data.videos || data.videos.length === 0) {
                    grid.innerHTML = '<div class="text-muted" style="grid-column:1/-1; padding:20px;">No videos to choose from.</div>';
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
                        document.querySelectorAll('.cover-item').forEach(function (el) {
                            el.classList.remove('active');
                        });
                        this.classList.add('active');
                        selectedVideoId = v.id;
                        selectBtn.disabled = false;
                    });

                    grid.appendChild(item);
                });
            })
            .catch(function (err) {
                grid.innerHTML = '<div class="text-danger" style="grid-column:1/-1; padding:20px;">Error loading videos.</div>';
                console.error(err);
            });

        selectBtn.onclick = function () {
            if (!selectedVideoId) return;
            fetch('/playlist/' + playlistId + '/set_cover', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ video_id: selectedVideoId })
            })
                .then(function (res) { return res.json(); })
                .then(function (data) {
                    if (data.success) {
                        if (playlistCoverModal) playlistCoverModal.hide();
                        location.reload();
                    } else {
                        alert('Error: ' + (data.error || 'Unknown error'));
                    }
                })
                .catch(function (err) { alert('Error: ' + err); });
        };
    }

    setCoverBtn.addEventListener('click', openCoverModal);
})();