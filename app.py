"""Aru: continuation of the Meta test-number pilot with human handoff."""
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from flask import Flask, Response, jsonify, request
from jinja2 import DictLoader
from ui import TEMPLATES, ASSETS
import booking
import guided
import panel
import store

app = Flask(__name__)
app.jinja_loader = DictLoader(TEMPLATES)
app.config['MAX_CONTENT_LENGTH'] = 256 * 1024
logging.basicConfig(level=logging.INFO)
TEST_PHONE_ID = '1320569784479924'
REQUIRED = ('WHATSAPP_ACCESS_TOKEN', 'META_APP_SECRET', 'TEST_RECIPIENTS', 'VERIFY_TOKEN')
locks = {}
lock_guard = threading.Lock()
worker = None
worker_guard = threading.Lock()
wake = threading.Event()
store.init()


def sender_lock(sender):
    with lock_guard:
        return locks.setdefault(sender, threading.RLock())


def missing_config():
    required = REQUIRED + (('OPENAI_API_KEY',) if engine() == 'openai' else ())
    return [name for name in required if not os.getenv(name, '').strip()]


def engine():
    return 'openai' if os.getenv('ARU_ENGINE', '').strip().lower() == 'openai' else 'guided'


def next_question(facts, first=False):
    return (booking.next_question if engine() == 'openai' else guided.question)(facts, first)


def allowed_numbers():
    return {n.strip() for n in os.getenv('TEST_RECIPIENTS', '').split(',') if n.strip().isdigit()}


def post_json(url, token, payload):
    provider = 'openai' if url.startswith('https://api.openai.com/') else 'whatsapp'
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Only safe diagnostic codes, never raw provider messages or credentials.
        try:
            code = str(json.loads(exc.read(8192)).get('error', {}).get('code', ''))
            if not re.fullmatch(r'[a-zA-Z0-9_]{1,60}', code):
                code = 'unknown'
        except (ValueError, AttributeError):
            code = 'unknown'
        raise RuntimeError(f'{provider}_http_{exc.code}_{code}') from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError(provider + '_network_uncertain') from None


def safe_error(exc):
    text = str(exc)
    return text if isinstance(exc, RuntimeError) and re.fullmatch(r'[a-zA-Z0-9_]{1,100}', text) else type(exc).__name__


def send(sender, text, role='assistant', nonce=None):
    """Caller holds sender lock. Never automatically retry an uncertain send."""
    convo = store.conversation(sender)
    if sender not in allowed_numbers() or not convo or time.time() - convo['last_inbound'] >= 86400:
        raise RuntimeError('whatsapp_window_closed_or_recipient_blocked')
    mid = 'out:' + (nonce or uuid.uuid4().hex)
    with store.db() as con:
        if con.execute('SELECT 1 FROM messages WHERE id=?', (mid,)).fetchone():
            raise RuntimeError('send_already_attempted')
        con.execute('INSERT INTO messages(id,sender,role,body,state,created) VALUES (?,?,?,?,?,?)',
                    (mid, sender, role, text, 'sending', time.time()))
    try:
        result = post_json('https://graph.facebook.com/v26.0/' + TEST_PHONE_ID + '/messages',
                           os.environ['WHATSAPP_ACCESS_TOKEN'], {
                               'messaging_product': 'whatsapp', 'to': sender, 'type': 'text',
                               'text': {'body': text, 'preview_url': False}})
        pid = result.get('messages', [{}])[0].get('id')
        if not pid:
            raise RuntimeError('whatsapp_send_uncertain')
        with store.db() as con:
            con.execute("UPDATE messages SET state='accepted', provider_id=? WHERE id=?", (pid, mid))
        app.logger.info('whatsapp_send_accepted')
    except Exception as exc:
        reason = safe_error(exc)
        with store.db() as con:
            con.execute("UPDATE messages SET state='uncertain', error=? WHERE id=?", (reason, mid))
        raise RuntimeError(reason) from None


def queue_human(sender, reason):
    with store.db() as con:
        con.execute("UPDATE conversations SET mode='waiting',owner=NULL,reason=?,epoch=epoch+1,updated=? WHERE sender=?",
                    (reason, time.time(), sender))
        store.audit(con, sender, 'aru', 'handoff_' + reason)
    if panel.operators():
        return 'Deixei seu pedido na fila da equipe e pausei as respostas automáticas. Um atendente precisa assumir a conversa por aqui.'
    return 'Seu pedido precisa da equipe da pousada. Registrei a solicitação e pausei as respostas automáticas, mas o atendimento humano deste teste ainda está em configuração.'


def process_message(job):
    sender = job['sender']
    with sender_lock(sender):
        convo = store.conversation(sender)
        if convo['mode'] != 'aru' or convo['epoch'] != job['epoch']:
            return
        if time.time() - convo['last_inbound'] >= 86400:
            queue_human(sender, 'window_closed')
            return
        if job['kind'] != 'text':
            send(sender, 'Neste teste consigo ler texto. Pode escrever sua mensagem? Se precisar, peça um atendente.')
            return
        text = job['body']
        if text.strip().lower() in ('/reiniciar', '/reset'):
            with store.db() as con:
                con.execute("UPDATE conversations SET facts='{}',reason=NULL,epoch=epoch+1 WHERE sender=?", (sender,))
                store.audit(con, sender, 'guest', 'reset')
            send(sender, next_question({}, first=True))
            return
        urgent = booking.urgent_reason(text)
        if urgent:
            send(sender, queue_human(sender, urgent))
            return
        epoch = convo['epoch']
        with store.db() as con:
            reset = con.execute("SELECT max(created) FROM audit WHERE sender=? AND action='reset'", (sender,)).fetchone()[0] or 0
            rows = con.execute("""SELECT role,body FROM messages WHERE sender=? AND created>=? AND created<=?
                AND (role='user' OR (role IN ('assistant','human') AND provider_id IS NOT NULL))
                ORDER BY created DESC LIMIT 20""", (sender, reset, job['created'])).fetchall()
        context = [{'role': 'user' if r['role'] == 'user' else 'assistant', 'content': r['body']}
                   for r in reversed(rows)]
    # ASSUMIR remains available while the model is running.
    try:
        if engine() == 'guided':
            facts = guided.extract(text, convo['facts'])
        else:
            facts = booking.extract(post_json, os.environ['OPENAI_API_KEY'],
                                    os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'), context, convo['facts'])
    except Exception as exc:
        reason = safe_error(exc)
        app.logger.error('aru_extraction_failed: %s', reason)
        with sender_lock(sender):
            current = store.conversation(sender)
            if current['mode'] == 'aru' and current['epoch'] == epoch:
                send(sender, 'Não consegui concluir o atendimento automático agora. ' + queue_human(sender, 'ai_unavailable'))
        return
    with sender_lock(sender):
        current = store.conversation(sender)
        if current['mode'] != 'aru' or current['epoch'] != epoch:
            app.logger.info('aru_reply_suppressed_human_control')
            return
        reason = facts.pop('reason')
        with store.db() as con:
            con.execute('UPDATE conversations SET facts=?,updated=? WHERE sender=?',
                        (json.dumps(facts, ensure_ascii=False), time.time(), sender))
        if reason != 'none':
            reply = queue_human(sender, reason)
        else:
            reply = next_question(facts, first=len(context) == 1)
            if reply is None:
                booking.availability(facts)
                reply = ('Já reuni os dados da estadia. A disponibilidade, os valores e as condições '
                         'ainda precisam ser conferidos pela equipe; nenhuma reserva está confirmada. '
                         + queue_human(sender, 'availability'))
        send(sender, reply)


def run_worker():
    while True:
        try:
            with store.db() as con:
                con.execute('BEGIN IMMEDIATE')
                row = con.execute("SELECT * FROM messages WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
                if row:
                    con.execute("UPDATE messages SET state='processing' WHERE id=?", (row['id'],))
            if row:
                error = None
                try:
                    process_message(dict(row))
                except Exception as exc:
                    error = safe_error(exc)
                    app.logger.error('aru_processing_failed: %s', error)
                    with sender_lock(row['sender']):
                        if store.conversation(row['sender'])['mode'] == 'aru':
                            queue_human(row['sender'], 'send_failed')
                with store.db() as con:
                    con.execute('UPDATE messages SET state=?,error=? WHERE id=?',
                                ('failed' if error else 'processed', error, row['id']))
                continue
        except Exception as exc:
            app.logger.error('worker_failed: %s', type(exc).__name__)
        wake.wait(2)
        wake.clear()


def ensure_worker():
    global worker
    with worker_guard:
        if worker is None or not worker.is_alive():
            worker = threading.Thread(target=run_worker, daemon=True)
            worker.start()
    wake.set()


def control(sender, actor, action):
    with sender_lock(sender):
        item = store.conversation(sender)
        if not item:
            return jsonify(error='Conversa não encontrada.'), 404
        if item['owner'] and item['owner'] != actor:
            return jsonify(error='Outro atendente já assumiu esta conversa.'), 409
        if action == 'devolver' and (item['mode'] != 'human' or item['owner'] != actor):
            return jsonify(error='Assuma a conversa antes de devolver.'), 409
        with store.db() as con:
            con.execute('UPDATE conversations SET mode=?,owner=?,epoch=epoch+1,updated=? WHERE sender=?',
                        ('human' if action == 'assumir' else 'aru', actor if action == 'assumir' else None,
                         time.time(), sender))
            con.execute("UPDATE messages SET state='human' WHERE sender=? AND state='queued'", (sender,))
            store.audit(con, sender, actor, action)
        if action == 'devolver':
            try:
                send(sender, 'A equipe devolveu seu atendimento para mim. Sou a Aru e posso continuar. Como posso ajudar?')
            except Exception as exc:
                return jsonify(ok=True, warning='Aru reativada; aviso não confirmado: ' + safe_error(exc))
        return jsonify(ok=True)


def send_human(sender, actor, text, nonce):
    with sender_lock(sender):
        item = store.conversation(sender)
        if not item or item['mode'] != 'human' or item['owner'] != actor:
            return jsonify(error='Assuma a conversa para enviar.'), 409
        try:
            send(sender, text, role='human', nonce=actor + ':' + nonce)
        except Exception as exc:
            return jsonify(error='Envio não confirmado: ' + safe_error(exc) + '. Confira o histórico antes de reenviar.'), 502
        return jsonify(ok=True)


panel.register(app, control, send_human)


@app.get('/assets/<name>')
def assets(name):
    if name not in ASSETS:
        return 'Não encontrado', 404
    return Response(ASSETS[name], mimetype='text/css' if name.endswith('.css') else 'text/javascript')


@app.after_request
def security_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; form-action 'self'"
    return response


@app.get('/')
def home():
    return 'Aru — piloto WhatsApp. Atendimento da equipe em /painel.', 200


@app.get('/health')
def health():
    missing = missing_config()
    return jsonify(mode='test_only', configured=not missing and bool(allowed_numbers()),
                   missing=missing, version='aru-guided-3', engine=engine(), operators_configured=len(panel.operators()),
                   availability='human_confirmation_omnibees_18272',
                   storage='sqlite_requires_persistent_volume'), 200


@app.get('/webhook')
def verify_webhook():
    token = os.getenv('VERIFY_TOKEN', '')
    if (token and request.args.get('hub.mode') == 'subscribe'
            and hmac.compare_digest(request.args.get('hub.verify_token', ''), token)
            and request.args.get('hub.challenge')):
        return request.args['hub.challenge'], 200
    return 'Token inválido', 403


@app.post('/webhook')
def receive_message():
    secret = os.getenv('META_APP_SECRET', '')
    if not secret:
        return 'Configuração ausente', 503
    signature = 'sha256=' + hmac.new(secret.encode(), request.get_data(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, request.headers.get('X-Hub-Signature-256', '')):
        return 'Assinatura inválida', 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get('object') != 'whatsapp_business_account':
        return 'EVENT_RECEIVED', 200
    entries = data.get('entry', [])
    if not isinstance(entries, list):
        return 'Payload inválido', 400
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('changes', []), list):
            continue
        for change in entry.get('changes', []):
            if not isinstance(change, dict):
                continue
            value = change.get('value')
            if not isinstance(value, dict) or change.get('field') != 'messages':
                continue
            metadata = value.get('metadata')
            if not isinstance(metadata, dict) or metadata.get('phone_number_id') != TEST_PHONE_ID:
                continue
            statuses = value.get('statuses', [])
            for status in statuses if isinstance(statuses, list) else []:
                if isinstance(status, dict) and status.get('status') in ('sent', 'delivered', 'read', 'failed'):
                    with store.db() as con:
                        row = con.execute('SELECT state FROM messages WHERE provider_id=?', (status.get('id'),)).fetchone()
                        rank = {'accepted': 0, 'sending': 0, 'uncertain': 0, 'sent': 1, 'failed': 1, 'delivered': 2, 'read': 3}
                        if row and rank.get(row['state'], 0) < rank[status['status']]:
                            con.execute('UPDATE messages SET state=? WHERE provider_id=?', (status['status'], status.get('id')))
                    app.logger.info('whatsapp_delivery_%s', status['status'])
            messages = value.get('messages', [])
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict):
                    continue
                sender, mid = message.get('from', ''), message.get('id', '')
                if not isinstance(sender, str) or sender not in allowed_numbers() or not isinstance(mid, str) or not mid.startswith('wamid.'):
                    continue
                if not os.getenv('WHATSAPP_ACCESS_TOKEN'):
                    return 'Configuração ausente', 503
                try:
                    timestamp = int(message.get('timestamp', 0))
                    if not -60 <= time.time() - timestamp < 86400:
                        continue
                except (TypeError, ValueError):
                    continue
                body = message.get('text', {})
                text = body.get('body', '') if isinstance(body, dict) else ''
                if not isinstance(text, str):
                    text = ''
                with sender_lock(sender):
                    outcome = store.receive(sender, mid, text or '[Mensagem sem texto]',
                                            message.get('type', 'unknown'), timestamp)
                app.logger.info('whatsapp_inbound_%s', outcome)
    ensure_worker()
    return 'EVENT_RECEIVED', 200


# One Gunicorn worker, no --preload. SQLite inbox survives process restarts.
if os.getenv('ARU_DISABLE_WORKER') != '1':
    ensure_worker()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.getenv('PORT', '10000')))
