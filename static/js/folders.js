/* ============================================================
   folders.js — JS страницы /folders
   • Клик по карточке — переход в папку
   • Модалка установки обложки папки
   • Модалка назначения артиста для всей папки
   • Универсальная Bootstrap-модалка уведомлений (вместо alert)
   Конфиг: window.FOLDERS_CONFIG
   ============================================================ */
(function () {
    'use strict';

    var CFG = window.FOLDERS_CONFIG || {};
    var CURRENT_PROFILE      = CFG.currentProfile || 'female';
    var PREVIEW_SEEK_SECONDS = 6;

    var coverModalInstance   = null;
    var currentFolderPath    = null;
    var currentFolderName    = null;
    var selectedVideoId      = null;

    var assignModalInstance  = null;
    var assignFolderPath     = null;
    var assignFolderName     = null;

    var notifyModalInstance  = null;
    var notifyOkCallback     = null;

    /* ============================================================
       УВЕДОМЛЕНИЯ (замена alert)
       ============================================================ */
    function showNotify(title, message, type, onOk) {
        // type: 'success' | 'error' | 'info'
        type = type || 'info';

        document.getElementById('notifyTitle').textContent = title;

        var msgEl = document.getElementById('notifyMessage');
        if (msgEl) msgEl.textContent = message || '';

        var iconEl = document.getElementById('notifyIcon');
        if (iconEl) {
            iconEl.className = 'notify-icon ' + type;
            if (type === 'success')      iconEl.textContent = '✓';
            else if (type === 'error')   iconEl.textContent = '!';
            else                          iconEl.textContent = 'i';
        }

        notifyOkCallback = (typeof onOk === 'function') ? onOk : null;

        var el = document.getElementById('notifyModal');
        if (!el) {
            // fallback, если модалки нет в шаблоне
            window.alert(title + '\n\n' + (message || ''));
            if (notifyOkCallback) notifyOkCallback();
            return;
        }

        if (!notifyModalInstance) {
            notifyModalInstance = new bootstrap.Modal(el);
        }
        notifyModalInstance.show();
    }

    /* ============================================================
       МОДАЛКА ОБЛОЖКИ
       ============================================================ */
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

    function setCover(folderPath, videoId) {
        if (!folderPath || !videoId) {
            showNotify('Обложка', 'Сначала выберите видео.', 'info');
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
                    showNotify('Ошибка', 'Не удалось установить обложку: ' + (data.error || 'Unknown'), 'error');
                    selectBtn.disabled = false;
                    selectBtn.textContent = 'Select';
                }
            })
            .catch(function (err) {
                console.error('Error setting cover:', err);
                showNotify('Ошибка сети', 'Проверьте соединение и попробуйте снова.', 'error');
                selectBtn.disabled = false;
                selectBtn.textContent = 'Select';
            });
    }

    /* ============================================================
       МОДАЛКА НАЗНАЧЕНИЯ АРТИСТА
       ============================================================ */
    function renderArtistCheckboxList(preselectIds) {
        var container = document.getElementById('artistCheckboxList');
        var artists = CFG.artists || [];
        container.innerHTML = '';

        if (!artists.length) {
            container.innerHTML =
                '<div class="text-muted">Артистов пока нет. ' +
                'Создайте первого на вкладке «Создать нового».</div>';
            updateAssignBtn();
            return;
        }

        var preselected = (preselectIds || []).map(function (x) { return parseInt(x, 10); });

        artists.forEach(function (a) {
            var div = document.createElement('div');
            div.className = 'form-check';

            var input = document.createElement('input');
            input.type = 'checkbox';
            input.className = 'form-check-input';
            input.id = 'artistChk_' + a.id;
            input.value = a.id;
            input.dataset.artistId = a.id;

            if (preselected.indexOf(parseInt(a.id, 10)) !== -1) {
                input.checked = true;
            }

            input.addEventListener('change', updateAssignBtn);

            var label = document.createElement('label');
            label.className = 'form-check-label';
            label.htmlFor = input.id;
            label.textContent = a.name;

            div.appendChild(input);
            div.appendChild(label);
            container.appendChild(div);
        });

        updateAssignBtn();
    }

    function updateAssignBtn() {
        var btn = document.getElementById('assignArtistBtn');
        if (!btn) return;
        var n = document.querySelectorAll('#artistCheckboxList input[type="checkbox"]:checked').length;
        btn.disabled = (n === 0);
        btn.textContent = n ? ('Назначить (' + n + ')') : 'Назначить';
    }

    function openAssignArtistModal(folderPath, folderName) {
        assignFolderPath = folderPath;
        assignFolderName = folderName;

        document.getElementById('assignFolderName').textContent = '"' + folderName + '"';
        document.getElementById('newArtistName').value = '';
        document.getElementById('onlyUnassignedChk').checked = true;

        renderArtistCheckboxList();

        if (!assignModalInstance) {
            assignModalInstance = new bootstrap.Modal(document.getElementById('assignArtistModal'));
        }
        assignModalInstance.show();
    }

    function assignArtist() {
        var checked = document.querySelectorAll('#artistCheckboxList input[type="checkbox"]:checked');
        if (!checked.length) return;

        var artistIds = Array.prototype.map.call(checked, function (c) {
            return parseInt(c.value, 10);
        });
        var onlyUnassigned = document.getElementById('onlyUnassignedChk').checked;

        var btn = document.getElementById('assignArtistBtn');
        btn.disabled = true;
        btn.textContent = 'Назначение…';

        fetch('/folders/assign_artist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                folder_path: assignFolderPath,
                artist_ids: artistIds,
                only_unassigned: onlyUnassigned
            })
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (assignModalInstance) assignModalInstance.hide();

                if (d.success) {
                    var n = d.assigned || 0;
                    var msg = (n > 0)
                        ? ('Назначено: ' + n + ' видео.')
                        : 'Нет видео для назначения (все уже привязаны к артистам).';
                    showNotify('Готово', msg, 'success', function () {
                        location.reload();
                    });
                } else {
                    showNotify('Ошибка', d.error || 'Не удалось назначить артиста.', 'error');
                    btn.disabled = false;
                    updateAssignBtn();
                }
            })
            .catch(function (err) {
                console.error('assign_artist error:', err);
                showNotify('Ошибка сети', 'Проверьте соединение и попробуйте снова.', 'error');
                btn.disabled = false;
                updateAssignBtn();
            });
    }

    function createArtistAndSelect() {
        var name = document.getElementById('newArtistName').value.trim();
        if (!name) {
            showNotify('Внимание', 'Введите имя артиста.', 'info');
            return;
        }

        var btn = document.getElementById('createArtistBtn');
        btn.disabled = true;
        btn.textContent = 'Создание…';

        fetch('/folders/create_artist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.success) {
                    CFG.artists = CFG.artists || [];
                    var exists = CFG.artists.some(function (a) {
                        return parseInt(a.id, 10) === parseInt(d.artist_id, 10);
                    });
                    if (!exists) {
                        CFG.artists.push({ id: d.artist_id, name: d.name });
                    }
                    // Пересобираем список и предвыбираем только что созданного
                    renderArtistCheckboxList([d.artist_id]);

                    // Переключаемся на вкладку "Существующие артисты"
                    var tabBtn = document.getElementById('tabExistingBtn');
                    if (tabBtn && window.bootstrap && bootstrap.Tab) {
                        bootstrap.Tab.getOrCreateInstance(tabBtn).show();
                    }

                    document.getElementById('newArtistName').value = '';
                } else {
                    showNotify('Ошибка', d.error || 'Не удалось создать артиста.', 'error');
                }
            })
            .catch(function (err) {
                console.error('create_artist error:', err);
                showNotify('Ошибка сети', 'Проверьте соединение и попробуйте снова.', 'error');
            })
            .finally(function () {
                btn.disabled = false;
                btn.textContent = 'Создать и выбрать';
            });
    }

    /* ============================================================
       DOM READY
       ============================================================ */
    document.addEventListener('DOMContentLoaded', function () {
        // Клик по карточке — переход в папку (исключая кнопки)
        document.querySelectorAll('.folder-card').forEach(function (card) {
            card.addEventListener('click', function (e) {
                if (e.target.closest('.cover-btn'))  return;
                if (e.target.closest('.assign-btn')) return;
                var folderPath = this.dataset.folderPath;
                if (folderPath) {
                    var url = new URL('/', window.location.origin);
                    url.searchParams.set('folder', folderPath);
                    url.searchParams.set('profile', CURRENT_PROFILE);
                    window.location.href = url.toString();
                }
            });
        });

        // Кнопка Select в модалке обложки
        var selectBtn = document.getElementById('selectCoverBtn');
        if (selectBtn) {
            selectBtn.addEventListener('click', function () {
                if (!selectedVideoId) return;
                setCover(currentFolderPath, selectedVideoId);
            });
        }

        // Кнопка Назначить в модалке артиста
        var assignBtn = document.getElementById('assignArtistBtn');
        if (assignBtn) {
            assignBtn.addEventListener('click', assignArtist);
        }

        // Кнопка Создать нового артиста
        var createBtn = document.getElementById('createArtistBtn');
        if (createBtn) {
            createBtn.addEventListener('click', createArtistAndSelect);
        }

        // Enter в поле нового артиста
        var newName = document.getElementById('newArtistName');
        if (newName) {
            newName.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    createArtistAndSelect();
                }
            });
        }

        // Возможность передать callback при закрытии notifyModal
        var notifyEl = document.getElementById('notifyModal');
        if (notifyEl) {
            notifyEl.addEventListener('hidden.bs.modal', function () {
                if (typeof notifyOkCallback === 'function') {
                    var cb = notifyOkCallback;
                    notifyOkCallback = null;
                    cb();
                }
            });
        }
    });

    // Экспорт для inline onclick в шаблоне
    window.openCoverModal        = openCoverModal;
    window.openAssignArtistModal = openAssignArtistModal;
})();