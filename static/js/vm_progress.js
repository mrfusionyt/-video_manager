/* Shared progress controller for library scan, duplicate search and tools. */
(function () {
    'use strict';

    function clampPct(value) {
        var n = Number(value);
        if (!isFinite(n) || n < 0) return 0;
        if (n > 100) return 100;
        return Math.round(n);
    }

    function statusKind(status) {
        var s = String(status || '').toLowerCase();
        if (s === 'complete' || s === 'done' || s === 'finished') return 'done';
        if (s === 'error' || s === 'failed') return 'error';
        if (s === 'not_found' || s === 'notfound') return 'notfound';
        return 'active';
    }

    function reset(container) {
        if (!container) return;
        container.style.display = 'block';
        container.classList.remove('is-done', 'is-error', 'is-active');
        container.classList.add('is-active');
        container.dataset.vmMax = '0';
        var fill = container.querySelector('[data-vm-fill]');
        var pct = container.querySelector('[data-vm-percent]');
        var detail = container.querySelector('[data-vm-detail]');
        var extra = container.querySelector('[data-vm-extra]');
        var track = container.querySelector('[data-vm-track]');
        if (fill) fill.style.width = '0%';
        if (pct) pct.textContent = '0%';
        if (track) track.setAttribute('aria-valuenow', '0');
        if (detail) detail.textContent = 'Initializing...';
        if (extra) extra.textContent = '';
    }

    function apply(container, state) {
        if (!container || !state) return;
        state = state || {};
        var kind = statusKind(state.status);
        var pct = clampPct(state.progress);
        var previous = Number(container.dataset.vmMax || 0);

        if (kind === 'done') pct = 100;
        if (kind === 'active') pct = Math.max(previous, pct);
        container.dataset.vmMax = String(pct);

        container.classList.remove('is-done', 'is-error', 'is-active');
        container.classList.add(kind === 'done' ? 'is-done' :
                                 kind === 'error' ? 'is-error' : 'is-active');

        var fill = container.querySelector('[data-vm-fill]');
        var pctEl = container.querySelector('[data-vm-percent]');
        var detail = container.querySelector('[data-vm-detail]');
        var extra = container.querySelector('[data-vm-extra]');
        var track = container.querySelector('[data-vm-track]');
        if (fill) fill.style.width = pct + '%';
        if (pctEl) pctEl.textContent = pct + '%';
        if (track) track.setAttribute('aria-valuenow', String(pct));

        var parts = [];
        if (state.mode && kind !== 'done') parts.push(String(state.mode).toUpperCase());
        var processed = Number(state.processed || 0);
        var total = Number(state.total || 0);
        if (total > 0) parts.push(processed + '/' + total);
        if (state.message) parts.push(String(state.message));
        if (detail) {
            detail.textContent = parts.length ? parts.join('  ·  ') : 'Working...';
        }
        if (extra) extra.textContent = state.current ? String(state.current) : '';
        return kind;
    }

    function monitor(container, options) {
        options = options || {};
        if (!container || !options.url) return null;
        reset(container);
        var es = new EventSource(options.url);
        es.onmessage = function (event) {
            var data;
            try { data = JSON.parse(event.data); } catch (e) { return; }
            var kind = apply(container, data);
            if (options.onMessage) options.onMessage(data, kind);
            if (kind === 'done' || kind === 'error' || kind === 'notfound') {
                es.close();
                if (kind === 'done' && options.onComplete) options.onComplete(data);
                if (kind === 'error' && options.onError) options.onError(data);
                if (kind === 'notfound' && options.onNotFound) options.onNotFound(data);
            }
        };
        es.onerror = function () {
            es.close();
            if (options.onConnectionError) options.onConnectionError();
        };
        return es;
    }

    window.VMProgress = {
        reset: reset,
        apply: apply,
        monitor: monitor
    };
})();
