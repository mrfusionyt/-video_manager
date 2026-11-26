/* File Transfer addon — chunked, resumable background uploads. */
(function () {
    'use strict';

    const $ = id => document.getElementById(id);
    if (!$('uploadBtn')) return;

    const LS_JOB = 'vm_ft_active_job';
    const DB_NAME = 'vm_file_transfer';
    const DB_STORE = 'blobs';

    let chosenFiles = [];
    let activeJob = null;
    let lastDetectedFolder = '';
    let folderEdited = false;
    let runToken = 0;
    let lastSample = { bytes: 0, time: 0, speed: 0 };
    let lastDomUpdate = 0;
    let lastRenderedStatus = '';
    const DOM_UPDATE_MS = 150;

    const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

    function timingsEnabled() {
        try {
            return localStorage.getItem('vm_ft_timings') === '1';
        } catch (_) {
            return false;
        }
    }

    function fmtBytes(n) {
        if (!n) return '0 B';
        if (n < 1024) return `${n} B`;
        if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
        if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
        return `${(n / 1024 ** 3).toFixed(2)} GB`;
    }

    function fmtSpeed(bytesPerSecond) {
        return bytesPerSecond > 0 ? `${fmtBytes(Math.round(bytesPerSecond))}/s` : '0 B/s';
    }

    function setStatus(el, message, type) {
        el.textContent = message;
        el.className = 'ft-status' + (type ? ' ' + type : '');
    }

    async function api(url, options) {
        const response = await fetch(url, { cache: 'no-store', ...options });
        let data = {};
        try {
            data = await response.json();
        } catch (_) {
            /* empty / non-JSON response */
        }
        if (!response.ok) {
            const error = new Error(data.error || data.detail || `${response.status} ${response.statusText}`);
            error.status = response.status;
            error.data = data;
            throw error;
        }
        return data;
    }

    function jobUrl(template, jobId) {
        return template.replace('__job__', encodeURIComponent(jobId));
    }

    /* --- IndexedDB: keeps File objects available after a page refresh --- */

    function requestDone(request) {
        return new Promise((resolve, reject) => {
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error || new Error('IndexedDB request failed'));
        });
    }

    function transactionDone(transaction) {
        return new Promise((resolve, reject) => {
            transaction.oncomplete = () => resolve();
            transaction.onerror = () => reject(transaction.error || new Error('IndexedDB error'));
            transaction.onabort = () => reject(transaction.error || new Error('IndexedDB aborted'));
        });
    }

    function openDb() {
        return new Promise((resolve, reject) => {
            if (!window.indexedDB) {
                reject(new Error('IndexedDB is unavailable'));
                return;
            }
            const request = indexedDB.open(DB_NAME, 1);
            request.onupgradeneeded = () => {
                const db = request.result;
                if (!db.objectStoreNames.contains(DB_STORE)) db.createObjectStore(DB_STORE);
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error || new Error('Cannot open IndexedDB'));
        });
    }

    async function dbPut(key, value) {
        const db = await openDb();
        try {
            const transaction = db.transaction(DB_STORE, 'readwrite');
            transaction.objectStore(DB_STORE).put(value, key);
            await transactionDone(transaction);
        } finally {
            db.close();
        }
    }

    async function dbGet(key) {
        const db = await openDb();
        try {
            const transaction = db.transaction(DB_STORE, 'readonly');
            const done = transactionDone(transaction);
            const result = await requestDone(transaction.objectStore(DB_STORE).get(key));
            await done;
            return result || null;
        } finally {
            db.close();
        }
    }

    async function dbDeletePrefix(prefix) {
        const db = await openDb();
        try {
            const transaction = db.transaction(DB_STORE, 'readwrite');
            const store = transaction.objectStore(DB_STORE);
            const done = transactionDone(transaction);
            const keys = await requestDone(store.getAllKeys());
            keys.filter(key => String(key).startsWith(prefix)).forEach(key => store.delete(key));
            await done;
        } finally {
            db.close();
        }
    }

    /* --- Settings --- */

    function applyConfig(config) {
        $('destination').value = config.destination || '';
        $('collision').value = config.collision || 'rename';
        $('preserveStructure').checked = Boolean(config.preserve_structure);
        $('maxFileSize').value = String(Math.round((config.max_file_size || 0) / 1048576));
    }

    async function loadConfig() {
        try {
            applyConfig(await api(FT_API.config));
            setStatus($('configStatus'), 'Server settings loaded', 'ok');
        } catch (e) {
            setStatus($('configStatus'), 'Could not load settings: ' + e.message, 'error');
        }
    }

    async function saveConfig() {
        const destination = $('destination').value.trim();
        if (!destination) {
            setStatus($('configStatus'), 'Enter a destination path', 'error');
            return;
        }
        const mb = Number($('maxFileSize').value);
        if (!Number.isFinite(mb) || mb < 0) {
            setStatus($('configStatus'), 'Invalid file size limit', 'error');
            return;
        }

        $('saveConfig').disabled = true;
        try {
            const result = await api(FT_API.saveConfig, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    destination: destination,
                    max_file_size: Math.round(mb * 1048576),
                    preserve_structure: $('preserveStructure').checked,
                    collision: $('collision').value,
                }),
            });
            applyConfig(result.config);
            setStatus($('configStatus'), 'Destination saved: ' + result.config.destination, 'ok');
            await refreshHistory();
        } catch (e) {
            setStatus($('configStatus'), 'Error: ' + e.message, 'error');
        } finally {
            $('saveConfig').disabled = false;
        }
    }

    /* --- File selection --- */

    function detectFolderName() {
        const input = $('folderInput');
        if (!input.files || !input.files.length) return '';
        const relative = String(input.files[0].webkitRelativePath || '').replace(/\\/g, '/');
        const parts = relative.split('/').filter(Boolean);
        if (parts.length > 1) return parts[0];
        try {
            const entries = input.webkitEntries || [];
            if (entries.length && entries[0].name) return entries[0].name;
        } catch (_) {
            /* some mobile browsers expose neither path nor entries */
        }
        return '';
    }

    function selectionUpdate() {
        const seen = new Map();
        [...$('fileInput').files, ...$('folderInput').files].forEach(file => {
            seen.set(`${file.webkitRelativePath || file.name}|${file.size}`, file);
        });
        chosenFiles = [...seen.values()];
        const total = chosenFiles.reduce((sum, file) => sum + file.size, 0);

        if ($('folderInput').files.length) {
            const detected = detectFolderName();
            if (!folderEdited && detected && detected !== lastDetectedFolder) {
                $('folderName').value = detected;
            }
            lastDetectedFolder = detected;
        } else {
            lastDetectedFolder = '';
        }
        const folder = $('folderName').value.trim();

        $('selectedTitle').textContent = chosenFiles.length
            ? `${chosenFiles.length} file(s) selected`
            : 'Nothing selected';
        const details = [`Total size · ${fmtBytes(total)}`];
        if (folder) details.unshift(`Folder on server · ${folder}`);
        $('selectedDetail').textContent = chosenFiles.length
            ? details.join(' · ')
            : 'Add files or a folder above';
        $('uploadBtn').disabled = !chosenFiles.length || Boolean(activeJob);
    }

    function clearSelection() {
        $('fileInput').value = '';
        $('folderInput').value = '';
        $('folderName').value = '';
        lastDetectedFolder = '';
        folderEdited = false;
        chosenFiles = [];
        selectionUpdate();
        setStatus($('uploadStatus'), '');
    }

    /* --- Progress UI --- */

    function resetSpeed(baselineBytes) {
        lastSample = { bytes: Number(baselineBytes) || 0, time: performance.now(), speed: 0 };
    }

    function showProgress() {
        $('progressPanel').hidden = false;
    }

    function hideProgress() {
        $('progressPanel').hidden = true;
        $('progressBar').style.width = '0%';
        $('progressPercent').textContent = '0%';
        $('progressSpeed').textContent = '0 B/s';
        $('progressBytes').textContent = '0 B / 0 B';
    }

    function updateProgress(status) {
        const percent = Number(status.percent) || 0;
        const bytes = Number(status.received_bytes) || 0;
        const now = performance.now();

        if (bytes > lastSample.bytes && now > lastSample.time) {
            const instant = (bytes - lastSample.bytes) * 1000 / (now - lastSample.time);
            lastSample.speed = lastSample.speed
                ? lastSample.speed * 0.65 + instant * 0.35
                : instant;
        } else if (status.status !== 'uploading') {
            lastSample.speed = 0;
        }
        lastSample.bytes = bytes;
        lastSample.time = now;

        const statusChanged = status.status !== lastRenderedStatus;
        lastRenderedStatus = status.status;
        if (!statusChanged && now - lastDomUpdate < DOM_UPDATE_MS) return;
        lastDomUpdate = now;

        $('progressBar').style.width = percent + '%';
        $('progressPercent').textContent = percent + '%';
        $('progressSpeed').textContent = status.status === 'uploading'
            ? fmtSpeed(lastSample.speed)
            : '—';
        $('progressBytes').textContent = `${fmtBytes(bytes)} / ${fmtBytes(status.total_bytes)}`;

        const current = status.files.find(f => f.status === 'pending' || f.status === 'uploading');
        $('progressFile').textContent = current
            ? `${current.name} · ${current.index + 1}/${status.files.length}`
            : 'All files handled';
        $('progressTitle').textContent = status.status === 'uploading'
            ? 'Uploading…'
            : status.status === 'cancelled'
                ? 'Upload cancelled'
                : status.status === 'completed'
                    ? 'Upload complete'
                    : 'Finalizing…';
    }

    /* --- Chunked upload --- */

    async function sendChunk(jobId, fileIndex, chunkIndex, blob) {
        const started = performance.now();
        const response = await fetch(jobUrl(FT_API.jobChunk, jobId), {
            method: 'POST',
            headers: {
                'X-File-Index': String(fileIndex),
                'X-Chunk-Index': String(chunkIndex),
            },
            body: blob,
        });
        const networkMs = performance.now() - started;
        let data = {};
        try {
            data = await response.json();
        } catch (_) {
            /* ignore */
        }
        if (timingsEnabled()) {
            console.log(`[FT] chunk ${fileIndex}:${chunkIndex} network=${Math.round(networkMs)}ms ` +
                `parse=${Math.round(performance.now() - started - networkMs)}ms`);
        }
        if (!response.ok) {
            const error = new Error(data.error || `${response.status} ${response.statusText}`);
            error.status = response.status;
            throw error;
        }
        return data;
    }

    function matchingLocalFile(fileState) {
        const file = chosenFiles[fileState.index];
        return file && file.name === fileState.name && file.size === fileState.size ? file : null;
    }

    function applyChunkResult(state, fileState, result, sentBytes) {
        const received = Number(result.received);
        const chunks = Number(result.chunks_received);
        fileState.received = Number.isFinite(received) ? received : fileState.received + sentBytes;
        fileState.chunks_received = Number.isFinite(chunks) ? chunks : fileState.chunks_received + 1;
        if (result.file_status) fileState.status = result.file_status;
        if (result.status) state.status = result.status;

        const total = Number(result.received_bytes);
        state.received_bytes = Number.isFinite(total) ? total : Number(state.received_bytes || 0) + sentBytes;
        if (Number.isFinite(Number(result.percent))) state.percent = Number(result.percent);
    }

    async function finishJob(status, silentRestore) {
        runToken++;
        const jobId = activeJob ? activeJob.id : (status && status.job_id);
        activeJob = null;
        if (jobId) {
            try {
                await dbDeletePrefix(jobId + ':');
            } catch (_) {
                /* storage cleanup is best effort */
            }
            localStorage.removeItem(LS_JOB);
        }

        hideProgress();
        $('fileInput').value = '';
        $('folderInput').value = '';
        $('folderName').value = '';
        lastDetectedFolder = '';
        folderEdited = false;
        chosenFiles = [];
        selectionUpdate();

        const parts = [];
        if (status.uploaded && status.uploaded.length) {
            parts.push(`${status.uploaded.length} file(s) uploaded`);
        }
        if (status.skipped && status.skipped.length) {
            parts.push(`${status.skipped.length} skipped`);
        }
        if (status.errors && status.errors.length) {
            parts.push(`${status.errors.length} error(s): ` +
                status.errors.map(item => item.error).join('; '));
        }
        if (!parts.length) {
            parts.push(status.status === 'cancelled' ? 'Upload cancelled.' : 'Upload finished.');
        }

        if (!silentRestore || status.status === 'uploading') {
            setStatus($('uploadStatus'), parts.join(' · '),
                status.errors && status.errors.length ? 'error' : 'ok');
        }
        refreshHistory();
    }

    async function cancelUpload() {
        if (!activeJob) return;
        const jobId = activeJob.id;
        runToken++;
        activeJob = null;
        try {
            await api(jobUrl(FT_API.jobCancel, jobId), { method: 'POST' });
        } catch (_) {
            /* the job may already have finished */
        }
        try {
            await dbDeletePrefix(jobId + ':');
        } catch (_) {
            /* ignore */
        }
        localStorage.removeItem(LS_JOB);
        hideProgress();
        $('uploadBtn').disabled = true;
        setStatus($('uploadStatus'), 'Upload cancelled.', 'error');
    }

    async function sessionExpired() {
        activeJob = null;
        localStorage.removeItem(LS_JOB);
        hideProgress();
        setStatus($('uploadStatus'), 'Upload session expired on the server.', 'error');
    }

    async function runJob(job, initialState) {
        const token = ++runToken;
        let state = initialState;
        if (!state) {
            try {
                state = await api(jobUrl(FT_API.jobStatus, job.id));
            } catch (e) {
                if (e.status === 404) {
                    await sessionExpired();
                    return;
                }
                setStatus($('uploadStatus'), 'Connection lost: ' + e.message, 'error');
                await delay(1000);
            }
            if (!state) return;
        }
        resetSpeed(state.received_bytes);

        while (token === runToken && activeJob && activeJob.id === job.id) {
            updateProgress(state);
            if (state.status !== 'uploading') {
                await finishJob(state);
                return;
            }

            // The chunk response already carries the new counters: no GET /status
            // round trip is needed before every chunk any more.
            const next = state.files.find(f =>
                (f.status === 'pending' || f.status === 'uploading') && f.received < f.size);
            if (!next) {
                await delay(250);
                try {
                    state = await api(jobUrl(FT_API.jobStatus, job.id));
                } catch (e) {
                    if (e.status === 404) {
                        await sessionExpired();
                        return;
                    }
                }
                continue;
            }

            const blobStarted = performance.now();
            let blob = matchingLocalFile(next);
            let source = blob ? 'memory' : 'indexeddb';
            if (!blob) {
                try {
                    blob = await dbGet(`${job.id}:${next.index}`);
                } catch (_) {
                    /* no stored copy — the file is gone */
                }
            }
            if (timingsEnabled()) {
                console.log(`[FT] blob ${next.index}: ${source}, ${Math.round(performance.now() - blobStarted)}ms`);
            }
            if (!blob) {
                setStatus($('uploadStatus'),
                    'Selected files are no longer available in this browser. Please select them again.',
                    'error');
                await cancelUpload();
                return;
            }

            const chunkSize = Number(state.chunk_size) || job.chunk_size;
            const from = next.chunks_received * chunkSize;
            if (from >= blob.size) {
                await delay(200);
                continue;
            }
            const to = Math.min(from + chunkSize, blob.size);
            try {
                const result = await sendChunk(job.id, next.index, next.chunks_received, blob.slice(from, to));
                applyChunkResult(state, next, result, to - from);
                if (state.status !== 'uploading') {
                    try {
                        state = await api(jobUrl(FT_API.jobStatus, job.id));
                    } catch (_) {
                        /* the light response still holds the final counters */
                    }
                }
            } catch (e) {
                if (e.status === 404) {
                    await sessionExpired();
                    return;
                }
                setStatus($('uploadStatus'), 'Retrying: ' + e.message, 'error');
                if (e.status === 409) {
                    try {
                        state = await api(jobUrl(FT_API.jobStatus, job.id));
                    } catch (_) {
                        /* keep the local counters and retry */
                    }
                }
                await delay(900);
            }
        }
    }

    async function startUpload() {
        if (!chosenFiles.length || activeJob) return;
        $('uploadBtn').disabled = true;
        setStatus($('uploadStatus'), 'Starting upload…');

        const fromFolder = Boolean($('folderInput').files && $('folderInput').files.length);
        const folder = $('folderName').value.trim() || (fromFolder ? detectFolderName() : '');
        const files = chosenFiles.map(file => {
            let parts = String(file.webkitRelativePath || file.name)
                .replace(/\\/g, '/').split('/').filter(Boolean);
            if (folder && parts.length > 1 && parts[0].toLowerCase() === folder.toLowerCase()) {
                parts = parts.slice(1);
            }
            return {
                name: file.name,
                path: parts.join('/') || file.name,
                size: file.size,
            };
        });

        try {
            const start = await api(FT_API.uploadStart, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ files: files, folder: folder }),
            });
            const job = { id: start.job_id, chunk_size: start.chunk_size, files: files };
            activeJob = job;
            localStorage.setItem(LS_JOB, JSON.stringify(job));

            for (let i = 0; i < chosenFiles.length; i++) {
                try {
                    await dbPut(`${job.id}:${i}`, chosenFiles[i]);
                } catch (_) {
                    /* uploading still works while this tab stays open */
                }
            }

            resetSpeed(0);
            showProgress();
            setStatus($('uploadStatus'), 'Uploading in the background. You can refresh this page.');
            const status = await api(jobUrl(FT_API.jobStatus, job.id));
            updateProgress(status);
            if (status.status !== 'uploading') {
                await finishJob(status);
            } else {
                runJob(job, status);
            }
        } catch (e) {
            setStatus($('uploadStatus'), 'Could not start upload: ' + e.message, 'error');
            $('uploadBtn').disabled = !chosenFiles.length;
        }
    }

    async function restoreActiveJob() {
        const raw = localStorage.getItem(LS_JOB);
        if (!raw) return;

        let job = null;
        try {
            job = JSON.parse(raw);
        } catch (_) {
            localStorage.removeItem(LS_JOB);
            return;
        }
        if (!job || !job.id || !job.chunk_size || !Array.isArray(job.files)) {
            localStorage.removeItem(LS_JOB);
            return;
        }

        try {
            const status = await api(jobUrl(FT_API.jobStatus, job.id));
            if (status.status !== 'uploading') {
                await finishJob(status, true);
                setStatus($('uploadStatus'),
                    status.status === 'cancelled' ? 'Upload was cancelled.'
                        : 'Upload finished while you were away.',
                    status.status === 'completed' ? 'ok' : '');
                return;
            }
            activeJob = job;
            resetSpeed(status.received_bytes);
            showProgress();
            updateProgress(status);
            setStatus($('uploadStatus'), 'Upload resumed in the background.');
            runJob(job, status);
        } catch (e) {
            if (e.status === 404) {
                localStorage.removeItem(LS_JOB);
                setStatus($('uploadStatus'), 'Previous upload session is no longer available.', 'error');
            }
        }
    }

    /* --- History --- */

    async function refreshHistory() {
        const button = $('refreshBtn');
        button.disabled = true;
        $('historyStatus').textContent = 'Loading…';
        try {
            const items = await api(`${FT_API.history}?nocache=${Date.now()}`);
            const list = $('historyBody');
            list.replaceChildren();

            if (!items.length) {
                const empty = document.createElement('div');
                empty.className = 'ft-empty';
                empty.textContent = 'No files yet. Send something — it will appear here.';
                list.append(empty);
            } else {
                items.forEach(item => {
                    const row = document.createElement('div');
                    row.className = 'ft-history-item';

                    const badge = document.createElement('div');
                    badge.className = 'ft-file-badge';
                    badge.textContent = '▤';

                    const meta = document.createElement('div');
                    meta.className = 'ft-file-meta';
                    const name = document.createElement('div');
                    name.className = 'ft-file-name';
                    name.textContent = item.name;
                    const path = document.createElement('div');
                    path.className = 'ft-file-path';
                    path.textContent = item.path;
                    meta.append(name, path);

                    const size = document.createElement('div');
                    size.className = 'ft-file-size';
                    size.textContent = fmtBytes(item.size);

                    row.append(badge, meta, size);
                    list.append(row);
                });
            }
            $('historyStatus').textContent = `${items.length} file(s) · showing up to 100`;
        } catch (e) {
            $('historyStatus').textContent = 'Could not load history: ' + e.message;
            $('historyBody').replaceChildren();
        } finally {
            button.disabled = false;
        }
    }

    $('saveConfig').addEventListener('click', saveConfig);
    $('refreshBtn').addEventListener('click', refreshHistory);
    $('uploadBtn').addEventListener('click', startUpload);
    $('cancelBtn').addEventListener('click', cancelUpload);
    $('clearBtn').addEventListener('click', clearSelection);
    $('fileInput').addEventListener('change', selectionUpdate);
    $('folderInput').addEventListener('change', () => {
        folderEdited = false;
        selectionUpdate();
    });
    $('folderName').addEventListener('input', () => {
        folderEdited = true;
        selectionUpdate();
    });

    loadConfig();
    refreshHistory();
    restoreActiveJob();
})();
