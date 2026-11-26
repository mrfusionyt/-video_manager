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
    let runToken = 0;
    let lastSample = { bytes: 0, time: 0, speed: 0 };

    const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

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

    function selectionUpdate() {
        const seen = new Map();
        [...$('fileInput').files, ...$('folderInput').files].forEach(file => {
            seen.set(`${file.webkitRelativePath || file.name}|${file.size}`, file);
        });
        chosenFiles = [...seen.values()];
        const total = chosenFiles.reduce((sum, file) => sum + file.size, 0);

        $('selectedTitle').textContent = chosenFiles.length
            ? `${chosenFiles.length} file(s) selected`
            : 'Nothing selected';
        $('selectedDetail').textContent = chosenFiles.length
            ? `Total size · ${fmtBytes(total)}`
            : 'Add files or a folder above';
        $('uploadBtn').disabled = !chosenFiles.length || Boolean(activeJob);
    }

    function clearSelection() {
        $('fileInput').value = '';
        $('folderInput').value = '';
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
        const response = await fetch(jobUrl(FT_API.jobChunk, jobId), {
            method: 'POST',
            headers: {
                'X-File-Index': String(fileIndex),
                'X-Chunk-Index': String(chunkIndex),
            },
            body: blob,
        });
        let data = {};
        try {
            data = await response.json();
        } catch (_) {
            /* ignore */
        }
        if (!response.ok) {
            throw new Error(data.error || `${response.status} ${response.statusText}`);
        }
        return data;
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

    async function runJob(job) {
        const token = ++runToken;
        while (token === runToken && activeJob && activeJob.id === job.id) {
            let status;
            try {
                status = await api(jobUrl(FT_API.jobStatus, job.id));
            } catch (e) {
                if (e.status === 404) {
                    activeJob = null;
                    localStorage.removeItem(LS_JOB);
                    hideProgress();
                    setStatus($('uploadStatus'), 'Upload session expired on the server.', 'error');
                    return;
                }
                setStatus($('uploadStatus'), 'Connection lost: ' + e.message, 'error');
                await delay(1000);
                continue;
            }

            updateProgress(status);
            if (status.status !== 'uploading') {
                await finishJob(status);
                return;
            }

            const next = status.files.find(f =>
                (f.status === 'pending' || f.status === 'uploading') && f.received < f.size);
            if (!next) {
                await delay(250);
                continue;
            }

            let blob = null;
            try {
                blob = await dbGet(`${job.id}:${next.index}`);
            } catch (_) {
                /* fall back to files chosen in this tab */
            }
            if (!blob && chosenFiles[next.index]) blob = chosenFiles[next.index];
            if (!blob) {
                setStatus($('uploadStatus'),
                    'Selected files are no longer available in this browser. Please select them again.',
                    'error');
                await cancelUpload();
                return;
            }

            const from = next.chunks_received * job.chunk_size;
            if (from >= blob.size) {
                await delay(200);
                continue;
            }
            const to = Math.min(from + job.chunk_size, blob.size);
            try {
                await sendChunk(job.id, next.index, next.chunks_received, blob.slice(from, to));
            } catch (e) {
                setStatus($('uploadStatus'), 'Retrying: ' + e.message, 'error');
                await delay(900);
            }
        }
    }

    async function startUpload() {
        if (!chosenFiles.length || activeJob) return;
        $('uploadBtn').disabled = true;
        setStatus($('uploadStatus'), 'Starting upload…');

        const files = chosenFiles.map(file => ({
            name: file.name,
            path: file.webkitRelativePath || file.name,
            size: file.size,
        }));

        try {
            const start = await api(FT_API.uploadStart, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ files: files }),
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
                runJob(job);
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
            runJob(job);
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
    $('folderInput').addEventListener('change', selectionUpdate);

    loadConfig();
    refreshHistory();
    restoreActiveJob();
})();
