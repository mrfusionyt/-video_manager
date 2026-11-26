"""Необязательная парольная защита веб-интерфейса."""
from urllib.parse import urlsplit

from flask import (
    abort, has_request_context, jsonify, redirect, render_template, request,
    session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from config import app_config, save_config


PUBLIC_PREFIXES = ('/static/', '/image/', '/favicon.ico')


def auth_enabled():
    return bool(app_config.get('auth_hash'))


def auth_marker():
    """Changing the password invalidates all existing session cookies."""
    # Full hash, not a prefix: PBKDF/scrypt prefixes are identical for every
    # password and would not invalidate old sessions on a password change.
    return str(app_config.get('auth_hash') or '')


def set_password(password):
    if not password:
        return False
    app_config['auth_hash'] = generate_password_hash(password)
    save_config(app_config)
    if has_request_context():
        session.pop('auth_ok', None)
        session.pop('auth_marker', None)
    return True


def disable_password():
    app_config['auth_hash'] = ''
    save_config(app_config)
    if has_request_context():
        session.pop('auth_ok', None)
        session.pop('auth_marker', None)


def _is_loopback():
    return request.remote_addr in ('127.0.0.1', '::1', None)


def _safe_next(target):
    if not target:
        return '/'
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not target.startswith('/'):
        return '/'
    return target


def register(app):
    @app.before_request
    def _require_auth():
        if request.method == 'OPTIONS' or _is_loopback():
            return None

        path = request.path
        if (path == '/login' or path == '/api/auth/status'
                or path.startswith(PUBLIC_PREFIXES)):
            return None

        if not auth_enabled():
            return None

        if (session.get('auth_ok') is True
                and session.get('auth_marker') == auth_marker()):
            return None

        # Lightweight CSRF guard for cookie-authenticated state changes.
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            origin = request.headers.get('Origin')
            if origin:
                host = urlsplit(origin).netloc.lower()
                if host and host != request.host.lower():
                    abort(403)

        if path.startswith('/api/') or path.startswith('/transfer/api/'):
            return jsonify({'error': 'Authentication required'}), 401

        target = request.full_path
        if target.endswith('?'):
            target = target[:-1]
        return redirect(url_for('login', next=target))

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error = None
        next_url = _safe_next(request.values.get('next'))
        if request.method == 'POST':
            password = request.form.get('password', '')
            if auth_enabled() and check_password_hash(
                    app_config.get('auth_hash') or '', password):
                session.clear()
                session['auth_ok'] = True
                session['auth_marker'] = auth_marker()
                return redirect(next_url)
            error = 'Invalid password.'
        return render_template('login.html', error=error, next_url=next_url,
                               enabled=auth_enabled())

    @app.route('/api/auth/status')
    def auth_status():
        return jsonify({'enabled': auth_enabled()})

    @app.route('/api/auth/password', methods=['POST'])
    def api_set_password():
        """Used by the desktop launcher; loopback is always allowed."""
        data = request.get_json(silent=True) or request.form
        password = str(data.get('password') or '')
        confirm = str(data.get('password_confirm') or data.get('confirm') or '')
        if not password:
            return jsonify({'success': False, 'error': 'Enter a password first.'}), 400
        if password != confirm:
            return jsonify({'success': False, 'error': 'Passwords do not match.'}), 400
        set_password(password)
        return jsonify({'success': True, 'enabled': True})

    @app.route('/api/auth/disable', methods=['POST'])
    def api_disable_password():
        disable_password()
        return jsonify({'success': True, 'enabled': False})

    @app.route('/logout', methods=['GET', 'POST'])
    def logout():
        session.clear()
        return redirect(url_for('login'))
