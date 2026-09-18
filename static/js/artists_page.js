/* ============================================================
   artists_page.js — JS страницы /artists (список)
   • Edit artist modal: имя + обложка
   • Delete artist modal
   Конфиг: window.ARTISTS_CONFIG
   ============================================================ */
(function () {
    'use strict';

    var CFG = window.ARTISTS_CONFIG || {};
    var CURRENT_PROFILE = CFG.currentProfile || 'female';
    var PREVIEW_SEEK_SECONDS = 6;

    var editModal = null;
    var editArtistId = null;
    var editSelectedCoverId = null;

    var deleteModal = null;
    var deleteArtistId = null;

    window.goToArtist = function (artistId) {
        window.location.href = '/artists/' + artistId + '?profile=' + CURRENT_PROFILE;
    };

    window.openEditModal = function (artistId) {
        editArtistId = artistId;
        editSelectedCoverId = null;

        var card = document.querySelector('.artist-card[data-artist-id="' + artistId + '"]');
        var currentName    = card ? card.dataset.artistName : '';
        var currentCoverId = card ? parseInt(card.dataset.coverVideoId || '0', 10) : 0;

        document.getElementById('editArtistName').value = currentName;
        var picker = document.getElementById('coverPicker');
        picker.innerHTML = '<div class="no-covers-msg">Loading…</div>';

        if (!editModal) {
            editModal = new bootstrap.Modal(document.getElementById('editArtistModal'));
        }
        editModal.show();

        fetch('/artists/' + artistId + '/videos')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                picker.innerHTML = '';
                var videos = data.videos || [];
                if (!videos.length) {
                    picker.innerHTML = '<div class="no-covers-msg">' +
                        'No videos in this artist yet.<br>' +
                        'Add videos on the artist page first.' +
                        '</div>';
                    return;
                }

                videos.forEach(function (v) {
                    var item = document.createElement('div');
                    item.className = 'cover-item';
                    item.dataset.videoId = v.id;

                    var ext = (v.filename.split('.').pop() || '').toLowerCase();
                    var isVideo = ['mp4', 'mov', 'webm', 'mkv', 'm4v'].indexOf(ext) !== -1;
                    var url = '/video/' + v.id;

                    if (isVideo) {
                        var vid = document.createElement('video');
                        vid.src = url;
                        vid.muted = true;
                        vid.playsInline = true;
                        vid.preload = 'metadata';
                        vid.addEventListener('loadedmetadata', function () {
                            try {
                                var dur = vid.duration;
                                if (!dur || isNaN(dur)) return;
                                var target = PREVIEW_SEEK_SECONDS;
                                if (dur < PREVIEW_SEEK_SECONDS * 2) target = dur / 2;
                                vid.currentTime = target;
                            } catch (e) {}
                        }, { once: true });
                        item.appendChild(vid);
                    } else {
                        var img = document.createElement('img');
                        img.src = url;
                        img.loading = 'lazy';
                        item.appendChild(img);
                    }

                    var nm = document.createElement('div');
                    nm.className = 'cname';
                    nm.textContent = v.filename;
                    nm.title = v.filename;
                    item.appendChild(nm);

                    var chk = document.createElement('div');
                    chk.className = 'check-mark';
                    chk.textContent = '✓';
                    item.appendChild(chk);

                    if (currentCoverId && parseInt(v.id, 10) === currentCoverId) {
                        item.classList.add('active');
                        editSelectedCoverId = v.id;
                    }

                    item.addEventListener('click', function () {
                        document.querySelectorAll('.cover-item').forEach(function (el) { el.classList.remove('active'); });
                        this.classList.add('active');
                        editSelectedCoverId = parseInt(this.dataset.videoId, 10);
                    });

                    picker.appendChild(item);
                });
            })
            .catch(function (err) {
                console.error(err);
                picker.innerHTML = '<div class="no-covers-msg">Error loading videos.</div>';
            });
    };

    window.openDeleteModal = function (artistId) {
        deleteArtistId = artistId;

        var card = document.querySelector('.artist-card[data-artist-id="' + artistId + '"]');
        var artistName = card ? card.dataset.artistName : 'this artist';

        document.getElementById('deleteArtistName').textContent = '"' + artistName + '"';

        if (!deleteModal) {
            deleteModal = new bootstrap.Modal(document.getElementById('deleteArtistModal'));
        }
        deleteModal.show();
    };

    document.addEventListener('DOMContentLoaded', function () {
        var saveBtn = document.getElementById('saveEditBtn');
        if (saveBtn) {
            saveBtn.addEventListener('click', async function () {
                if (!editArtistId) return;

                var newName = document.getElementById('editArtistName').value.trim();
                if (!newName) {
                    alert('Name cannot be empty');
                    return;
                }

                saveBtn.disabled = true;
                saveBtn.textContent = 'Saving…';

                var payload = { name: newName };
                if (editSelectedCoverId) {
                    payload.cover_video_id = editSelectedCoverId;
                }

                try {
                    var r = await fetch('/artists/' + editArtistId + '/edit', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    var d = await r.json();
                    if (d.success) {
                        location.reload();
                    } else {
                        alert('Error: ' + (d.error || 'Unknown'));
                        saveBtn.disabled = false;
                        saveBtn.textContent = 'Save';
                    }
                } catch (e) {
                    console.error(e);
                    alert('Network error');
                    saveBtn.disabled = false;
                    saveBtn.textContent = 'Save';
                }
            });
        }

        var confirmDeleteBtn = document.getElementById('confirmDeleteArtistBtn');
        if (confirmDeleteBtn) {
            confirmDeleteBtn.addEventListener('click', async function () {
                if (!deleteArtistId) return;

                confirmDeleteBtn.disabled = true;
                confirmDeleteBtn.textContent = 'Deleting…';

                try {
                    var r = await fetch('/artists/' + deleteArtistId + '/delete', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' }
                    });
                    if (r.ok) {
                        location.reload();
                    } else {
                        alert('Error deleting artist');
                        confirmDeleteBtn.disabled = false;
                        confirmDeleteBtn.textContent = 'Delete';
                    }
                } catch (e) {
                    console.error(e);
                    alert('Network error');
                    confirmDeleteBtn.disabled = false;
                    confirmDeleteBtn.textContent = 'Delete';
                }
            });
        }
    });
})();