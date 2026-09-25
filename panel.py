"""Three independent operator logins; server-side authorization for every mutation."""
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import timedelta
from functools import wraps
from flask import abort, jsonify, redirect, render_template, request, session
from werkzeug.security import check_password_hash
import store


def operators():
    try:
        values = json.loads(os.getenv('ARU_OPERATORS', '{}'))
    except ValueError:
        return {}
    if (not isinstance(values, dict) or not 1 <= len(values) <= 3
            or any(not isinstance(k, str) or not isinstance(v, str)
                   or not v.startswith(('scrypt:', 'pbkdf2:')) for k, v in values.items())):
        return {}
    return values


def register(app, control, send_human):
    app.secret_key = os.getenv('ARU_SESSION_SECRET') or secrets.token_hex(32)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_SECURE=os.getenv('ARU_LOCAL_HTTP') != '1',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    attempts = {}

    def authenticated(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            name = session.get('operator')
            digest = hashlib.sha256(operators().get(name, '').encode()).hexdigest()
            if not name or not hmac.compare_digest(digest, session.get('credential', '')):
                abort(401)
            if request.method != 'GET' and not hmac.compare_digest(
                    session.get('csrf', ''), request.headers.get('X-CSRF-Token', '')):
                abort(403)
            return fn(*args, **kwargs)
        return wrapped

    @app.get('/painel')
    def panel():
        if not session.get('operator'):
            return redirect('/login')
        return render_template('panel.html', operator=session['operator'], csrf=session['csrf'])

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error = None
        session.setdefault('login_csrf', secrets.token_urlsafe(32))
        if request.method == 'POST':
            if not hmac.compare_digest(session['login_csrf'], request.form.get('csrf', '')):
                abort(403)
            now = time.time()
            # Deliberately global: cannot be bypassed by spoofing proxy headers.
            attempts['global'] = [t for t in attempts.get('global', []) if t > now - 300]
            if len(attempts['global']) >= 15:
                return 'Muitas tentativas. Aguarde cinco minutos.', 429
            attempts['global'].append(now)
            name = request.form.get('operator', '')
            password = request.form.get('password', '')
            hashed = operators().get(name)
            if hashed and check_password_hash(hashed, password):
                session.clear()
                session.permanent = True
                session.update(operator=name, csrf=secrets.token_urlsafe(32),
                               credential=hashlib.sha256(hashed.encode()).hexdigest())
                return redirect('/painel')
            error = 'Acesso não configurado ou dados inválidos.'
        return render_template('login.html', error=error, csrf=session['login_csrf'])

    @app.post('/api/logout')
    @authenticated
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get('/api/conversations')
    @authenticated
    def conversations():
        with store.db() as con:
            rows = con.execute('SELECT sender FROM conversations ORDER BY updated DESC LIMIT 100').fetchall()
        return jsonify([store.conversation(r['sender']) for r in rows])

    @app.get('/api/conversations/<sender>')
    @authenticated
    def detail(sender):
        item = store.conversation(sender)
        if not item:
            abort(404)
        with store.db() as con:
            rows = con.execute('SELECT role,body,state,created,error FROM messages WHERE sender=? ORDER BY created DESC LIMIT 100',
                               (sender,)).fetchall()
        return jsonify(conversation=item, messages=[dict(r) for r in reversed(rows)])

    @app.post('/api/conversations/<sender>/<action>')
    @authenticated
    def action(sender, action):
        if action in ('assumir', 'devolver'):
            return control(sender, session['operator'], action)
        if action == 'enviar':
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                abort(400)
            text = data.get('text')
            nonce = data.get('nonce')
            if not isinstance(text, str) or not text.strip() or len(text) > 3500:
                abort(400)
            if not isinstance(nonce, str) or not 16 <= len(nonce) <= 80:
                abort(400)
            return send_human(sender, session['operator'], text.strip(), nonce)
        abort(404)
