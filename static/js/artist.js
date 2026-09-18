/* ============================================================
   artist.js — JS страницы /artists/<id>
   • Add videos modal (выбор и отправка)
   • Remove video modal (подтверждение)
   Конфиг: window.ARTIST_CONFIG
   ============================================================ */
(function () {
    'use strict';

    var CFG = window.ARTIST_CONFIG || {};
    var artistId       = CFG.artistId;
    var currentProfile = CFG.currentProfile || 'female';

    var addModalInstance    = null;
    var removeModalInstance = null;
    var pendingRemoveVideoId = null;

    function updateSaveBtn() {
        var btn = document.getElementById('saveAddBtn');
        if (!btn) return;
        var n = document.querySelectorAll('.avail-item.active').length;
        btn.disabled = (n === 0);
        btn.textContent = n ? ('Add ' + n + ' video(s)') : 'Add selected';
    }

    window.openAddVideos = function () {
        var el = document.getElementById('addVideosModal');
        if (!el) return;
        if (!addModalInstance) addModalInstance = new bootstrap.Modal(el);
        addModalInstance.show();
    };

    window.openRemoveVideoModal = function (videoId) {
        pendingRemoveVideoId = videoId;

        var tile = document.getElementById('vtile-' + videoId);
        var name = 'this video';
        if (tile) {
            var tileInner = tile.querySelector('.video-tile');
            if (tileInner && tileInner.dataset.videoName) {
                name = '"' + tileInner.dataset.videoName + '"';
            }
        }
        var nameEl = document.getElementById('removeVideoName');
        if (nameEl) nameEl.textContent = name;

        if (!removeModalInstance) {
            removeModalInstance = new bootstrap.Modal(document.getElementById('removeVideoModal'));
        }
        removeModalInstance.show();
    };

    document.addEventListener('DOMContentLoaded', function () {
        // Скрытая проверка: доступные видео
        document.querySelectorAll('.avail-item').forEach(function (item) {
            item.addEventListener('click', function () {
                this.classList.toggle('active');
                updateSaveBtn();
            });
        });

        var saveBtn = document.getElementById('saveAddBtn');
        if (saveBtn) {
            saveBtn.addEventListener('click', async function () {
                var selected = document.querySelectorAll('.avail-item.active');
                if (!selected.length) return;

                saveBtn.disabled = true;
                saveBtn.textContent = 'Adding...';

                for (var i = 0; i < selected.length; i++) {
                    var vid = parseInt(selected[i].dataset.videoId, 10);
                    try {
                        await fetch('/artists/' + artistId + '/add_video', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ video_id: vid })
                        });
                    } catch (e) { console.error(e); }
                }
                location.reload();
            });
        }

        var confirmRemoveBtn = document.getElementById('confirmRemoveVideoBtn');
        if (confirmRemoveBtn) {
            confirmRemoveBtn.addEventListener('click', async function () {
                if (!pendingRemoveVideoId) return;
                confirmRemoveBtn.disabled = true;
                confirmRemoveBtn.textContent = 'Removing…';
                try {
                    var r = await fetch('/artists/' + artistId + '/remove_video', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ video_id: pendingRemoveVideoId })
                    });
                    var d = await r.json();
                    if (d.success) {
                        var tile = document.getElementById('vtile-' + pendingRemoveVideoId);
                        if (tile) tile.remove();
                        if (removeModalInstance) removeModalInstance.hide();
                    } else {
                        alert('Error: ' + (d.error || 'Unknown'));
                    }
                } catch (e) {
                    console.error(e);
                    alert('Network error');
                } finally {
                    confirmRemoveBtn.disabled = false;
                    confirmRemoveBtn.textContent = 'Remove';
                    pendingRemoveVideoId = null;
                }
            });
        }
    });
})();