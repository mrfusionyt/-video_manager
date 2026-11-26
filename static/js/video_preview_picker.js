/* ============================================================
   video_preview_picker.js
   Per-video main preview selection, styled like the cover pickers.

   The modal receives a small set of JPEG frames extracted from the video
   (starting at 1:00) and saves the chosen timestamp for that video only.
   ============================================================ */
(function () {
    'use strict';

    var modalEl = null;
    var modal = null;
    var grid = null;
    var nameEl = null;
    var hintEl = null;
    var statusEl = null;
    var saveBtn = null;
    var state = {videoId: null, selected: null, isAuto: true};

    function labelFor(seconds) {
        var t = Math.max(0, Math.round(Number(seconds) || 0));
        return Math.floor(t / 60) + ':' + ('0' + (t % 60)).slice(-2);
    }

    function setStatus(text, isError) {
        if (!statusEl) return;
        statusEl.textContent = text || '';
        statusEl.style.color = isError ? '#ff8f9a' : '';
    }

    function markActive(tile) {
        if (!grid) return;
        Array.prototype.forEach.call(
            grid.querySelectorAll('.video-preview-item'),
            function (el) { el.classList.remove('active'); }
        );
        if (tile) tile.classList.add('active');
    }

    function choose(tile, seconds, isAuto) {
        state.selected = seconds;
        state.isAuto = !!isAuto;
        markActive(tile);
        if (saveBtn) saveBtn.disabled = false;
        setStatus(isAuto ? 'Automatic frame selected.' : 'Frame at ' + labelFor(seconds) + ' selected.');
    }

    function addTile(url, label, seconds, isAuto, active) {
        var tile = document.createElement('div');
        tile.className = 'video-preview-item' + (active ? ' active' : '');
        tile.setAttribute('role', 'button');
        tile.setAttribute('tabindex', '0');
        tile.title = isAuto ? 'Automatic frame' : 'Use frame at ' + label;

        var img = document.createElement('img');
        img.src = url;
        img.alt = label;
        img.loading = 'lazy';
        tile.appendChild(img);

        var caption = document.createElement('div');
        caption.className = 'video-preview-label';
        caption.textContent = isAuto ? 'Auto · ' + label : label;
        tile.appendChild(caption);

        var check = document.createElement('div');
        check.className = 'video-preview-check';
        check.textContent = '✓';
        tile.appendChild(check);

        tile.addEventListener('click', function () {
            choose(tile, seconds, isAuto);
        });
        tile.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                choose(tile, seconds, isAuto);
            }
        });

        grid.appendChild(tile);
        return tile;
    }

    function render(data) {
        grid.innerHTML = '';
        state.selected = null;
        state.isAuto = true;
        if (saveBtn) saveBtn.disabled = true;

        var previewAt = Number(data.preview_at || 0);
        var autoActive = previewAt <= 0;
        addTile(data.auto_url, labelFor(data.auto_time), 0, true, autoActive);

        (data.candidates || []).forEach(function (c) {
            var t = Number(c.time || 0);
            var active = !autoActive && Math.abs(t - previewAt) < 1.5;
            addTile(c.url, c.label || labelFor(t), t, false, active);
        });

        if (autoActive) {
            state.selected = 0;
            state.isAuto = true;
        } else if (previewAt > 0) {
            state.selected = previewAt;
            state.isAuto = false;
            if (saveBtn) saveBtn.disabled = false;
        }

        if (hintEl) {
            var extra = data.duration > 0 ? ' Duration: ' + labelFor(data.duration) + '.' : '';
            hintEl.textContent = 'Frames start at 1:00 to skip the intro.' + extra +
                ' Choose one, or keep the automatic frame.';
        }
        setStatus('');
    }

    function open(videoId, name) {
        if (!modalEl) return;
        state.videoId = videoId;
        state.selected = null;
        state.isAuto = true;
        if (nameEl) nameEl.textContent = name || ('#' + videoId);
        if (grid) grid.innerHTML = '<div class="no-videos-msg">Loading…</div>';
        if (saveBtn) saveBtn.disabled = true;
        setStatus('');

        if (!modal && window.bootstrap && window.bootstrap.Modal) {
            modal = new window.bootstrap.Modal(modalEl);
        }
        if (modal) modal.show();

        fetch('/api/video/' + encodeURIComponent(videoId) + '/preview_candidates', {
            credentials: 'same-origin'
        })
        .then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (data) {
                if (!r.ok || !data.success) throw new Error(data.error || ('HTTP ' + r.status));
                return data;
            });
        })
        .then(render)
        .catch(function (e) {
            if (grid) grid.innerHTML = '';
            setStatus(e.message || String(e), true);
        });
    }

    function save() {
        if (!state.videoId || state.selected === null || !saveBtn) return;
        saveBtn.disabled = true;
        setStatus('Saving…');

        fetch('/api/video/' + encodeURIComponent(state.videoId) + '/set_preview', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                preview_at: state.isAuto ? 0 : state.selected
            })
        })
        .then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (data) {
                if (!r.ok || !data.success) throw new Error(data.error || ('HTTP ' + r.status));
                return data;
            });
        })
        .then(function () {
            setStatus('Preview saved.');
            if (modal) modal.hide();
            window.location.reload();
        })
        .catch(function (e) {
            setStatus(e.message || String(e), true);
            saveBtn.disabled = false;
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        modalEl = document.getElementById('videoPreviewModal');
        grid = document.getElementById('videoPreviewGrid');
        nameEl = document.getElementById('videoPreviewName');
        hintEl = document.getElementById('videoPreviewHint');
        statusEl = document.getElementById('videoPreviewStatus');
        saveBtn = document.getElementById('selectVideoPreviewBtn');
        if (saveBtn) saveBtn.addEventListener('click', save);

        // Capture phase: stop the parent card's click handler before it navigates.
        document.addEventListener('click', function (e) {
            var btn = e.target.closest
                ? e.target.closest('.preview-pick-btn, .preview-pick-btn-static')
                : null;
            if (!btn) return;
            e.preventDefault();
            e.stopPropagation();
            open(btn.dataset.videoId, btn.dataset.videoName);
        }, true);
    });

    window.VideoPreviewPicker = {open: open};
})();
