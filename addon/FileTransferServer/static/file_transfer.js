/* File Transfer addon — page logic, mounted on /transfer. */
(function () {
    const $ = id => document.getElementById(id);
    if (!$('uploadBtn')) return;

    let chosenFiles = [];

    function fmtBytes(n) {
        if (!n) return '0 B';
        if (n < 1024) return `${n} B`;
        if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
        if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
        return `${(n / 1024 ** 3).toFixed(2)} GB`;
    }

    function setStatus(el, message, type) {
        el.textContent = message;
        el.className = 'ft-status' + (type ? ' ' + type : '');
    }

    async function api(url, options) {
        const response = await fetch(url, { cache: 'no-store', ...options });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.error || data.detail || `${response.status} ${response.statusText}`);
        }
        return data;
    }

    function applyConfig(config) {
        $('destination').value = config.destination || '';
        $('collision').value = config.collision || 'rename';
        $('preserveStructure').checked = Boolean(config.preserve_structure);
        $('maxFileSize').value = String(Math.round((config.max_file_size || 0) / 1048576));
    }

    async function loadConfig() {
        try {
            applyConfig(await api(FT_API.config));
            setStatus($('configStatus'), 'Параметры сервера загружены', 'ok');
        } catch (e) {
            setStatus($('configStatus'), 'Не удалось загрузить настройки: ' + e.message, 'error');
        }
    }

    async function saveConfig() {
        const destination = $('destination').value.trim();
        if (!destination) {
            setStatus($('configStatus'), 'Укажи путь к папке', 'error');
            return;
        }
        const mb = Number($('maxFileSize').value);
        if (!Number.isFinite(mb) || mb < 0) {
            setStatus($('configStatus'), 'Некорректный лимит размера', 'error');
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
            setStatus($('configStatus'), 'Папка сохранена: ' + result.config.destination, 'ok');
            await refreshHistory();
        } catch (e) {
            setStatus($('configStatus'), 'Ошибка: ' + e.message, 'error');
        } finally {
            $('saveConfig').disabled = false;
        }
    }

    function selectionUpdate() {
        const seen = new Map();
        [...$('fileInput').files, ...$('folderInput').files].forEach(file => {
            seen.set(`${file.webkitRelativePath || file.name}|${file.size}`, file);
        });
        chosenFiles = [...seen.values()];
        const total = chosenFiles.reduce((sum, file) => sum + file.size, 0);

        $('selectedTitle').textContent = chosenFiles.length
            ? `${chosenFiles.length} файл(ов) выбрано`
            : 'Ничего не выбрано';
        $('selectedDetail').textContent = chosenFiles.length
            ? `Общий размер · ${fmtBytes(total)}`
            : 'Добавь файлы или папку выше';
        $('uploadBtn').disabled = !chosenFiles.length;
    }

    function clearSelection() {
        $('fileInput').value = '';
        $('folderInput').value = '';
        chosenFiles = [];
        selectionUpdate();
        setStatus($('uploadStatus'), '');
    }

    async function uploadFiles() {
        if (!chosenFiles.length) return;
        $('uploadBtn').disabled = true;
        setStatus($('uploadStatus'), 'Отправка файлов…');

        const form = new FormData();
        chosenFiles.forEach(file => {
            form.append('files', file, file.name);
            form.append('paths', file.webkitRelativePath || file.name);
        });

        try {
            const result = await api(FT_API.upload, { method: 'POST', body: form });
            let message = `Готово! Загружено: ${result.uploaded.length}`;
            if (result.skipped && result.skipped.length) message += ` · пропущено: ${result.skipped.length}`;
            if (result.errors && result.errors.length) {
                message += ` · ошибки: ${result.errors.map(item => item.error).join('; ')}`;
            }
            setStatus($('uploadStatus'), message, result.errors && result.errors.length ? 'error' : 'ok');
            clearSelection();
            await refreshHistory();
        } catch (e) {
            setStatus($('uploadStatus'), 'Не удалось загрузить: ' + e.message, 'error');
            $('uploadBtn').disabled = false;
        }
    }

    async function refreshHistory() {
        const button = $('refreshBtn');
        button.disabled = true;
        $('historyStatus').textContent = 'Обновляем…';
        try {
            const items = await api(`${FT_API.history}?nocache=${Date.now()}`);
            const list = $('historyBody');
            list.replaceChildren();

            if (!items.length) {
                const empty = document.createElement('div');
                empty.className = 'ft-empty';
                empty.textContent = 'Здесь пока нет файлов. Отправь что-нибудь — появится в истории.';
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
            $('historyStatus').textContent = `${items.length} файл(ов) · максимум 100`;
        } catch (e) {
            $('historyStatus').textContent = 'Ошибка загрузки списка: ' + e.message;
            $('historyBody').replaceChildren();
        } finally {
            button.disabled = false;
        }
    }

    $('saveConfig').addEventListener('click', saveConfig);
    $('refreshBtn').addEventListener('click', refreshHistory);
    $('uploadBtn').addEventListener('click', uploadFiles);
    $('clearBtn').addEventListener('click', clearSelection);
    $('fileInput').addEventListener('change', selectionUpdate);
    $('folderInput').addEventListener('change', selectionUpdate);

    loadConfig();
    refreshHistory();
})();
