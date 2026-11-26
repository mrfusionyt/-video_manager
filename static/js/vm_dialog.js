/* ============================================================
   vm_dialog.js — единые программные диалоги приложения.
   Заменяет нативные alert()/confirm() на Bootstrap-модалку.

   API:
     VMDialog.alert(message, title?, type?, onOk?) -> Promise<void>
     VMDialog.confirm(message, title?, type?)      -> Promise<boolean>
     VMDialog.notify(title, message, type?, onOk?) -> Promise<void>
                                                      (совместимость с folders.js)
   ============================================================ */
(function () {
    'use strict';

    var ICONS = {
        success: '✓',
        error: '!',
        warning: '!',
        info: 'i'
    };

    var el = null;
    var instance = null;
    var pending = null;

    function ensure() {
        if (el) return true;
        el = document.getElementById('vmDialog');
        if (!el || typeof bootstrap === 'undefined') return false;
        instance = bootstrap.Modal.getOrCreateInstance(el);

        document.getElementById('vmDialogOkBtn').addEventListener('click', function () {
            settle(true);
            instance.hide();
        });
        document.getElementById('vmDialogCancelBtn').addEventListener('click', function () {
            settle(false);
            instance.hide();
        });
        el.addEventListener('hidden.bs.modal', function () {
            settle(false);
        });
        return true;
    }

    function settle(result) {
        if (!pending) return;
        var current = pending;
        pending = null;
        if (typeof current.onOk === 'function' && result) {
            try { current.onOk(); } catch (e) { console.error(e); }
        }
        current.resolve(result);
    }

    function open(options) {
        if (!ensure()) {
            // bootstrap ещё не загружен — последний безопасный fallback.
            var text = options.message || '';
            if (options.confirm) return Promise.resolve(window.confirm(text));
            window.alert(text);
            if (typeof options.onOk === 'function') options.onOk();
            return Promise.resolve(true);
        }

        if (pending) settle(false);

        var type = ICONS[options.type] ? options.type : 'info';
        document.getElementById('vmDialogTitle').textContent = options.title || 'Notification';
        document.getElementById('vmDialogMessage').textContent = options.message || '';
        var icon = document.getElementById('vmDialogIcon');
        icon.className = 'vm-dialog-icon ' + type;
        icon.textContent = ICONS[type];

        var cancel = document.getElementById('vmDialogCancelBtn');
        cancel.style.display = options.confirm ? '' : 'none';

        return new Promise(function (resolve) {
            pending = {resolve: resolve, onOk: options.onOk};
            instance.show();
        });
    }

    window.VMDialog = {
        alert: function (message, title, type, onOk) {
            return open({
                message: message, title: title, type: type, onOk: onOk
            });
        },
        confirm: function (message, title, type) {
            return open({
                message: message, title: title || 'Confirm',
                type: type || 'warning', confirm: true
            });
        },
        notify: function (title, message, type, onOk) {
            return open({
                message: message, title: title, type: type, onOk: onOk
            });
        }
    };
})();
