import hashlib
import hmac
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch

os.environ['ARU_DISABLE_WORKER'] = '1'
os.environ['ARU_LOCAL_HTTP'] = '1'
_initial = tempfile.TemporaryDirectory()
os.environ['ARU_DB_PATH'] = _initial.name + '/initial.sqlite3'
import app as aru
import booking
import store
from werkzeug.security import generate_password_hash

SENDER = '5585999999999'
HASH = generate_password_hash('test-password-only')


class AruTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            'ARU_DB_PATH': self.temp.name + '/test.sqlite3',
            'META_APP_SECRET': 'test-secret', 'WHATSAPP_ACCESS_TOKEN': 'fake-token',
            'OPENAI_API_KEY': 'fake-key', 'VERIFY_TOKEN': 'test-verify',
            'ARU_ENGINE': 'openai',
            'TEST_RECIPIENTS': SENDER,
            'ARU_OPERATORS': json.dumps({'one': HASH, 'two': HASH, 'three': HASH})})
        self.env.start()
        store.init()
        self.client = aru.app.test_client()
        self.worker = patch.object(aru, 'ensure_worker')
        self.worker.start()
        self.sends = []
        self.provider = patch.object(aru, 'post_json', side_effect=self.post)
        self.provider.start()
        self.facts = dict.fromkeys(booking.FIELDS)
        self.facts['reason'] = 'none'
        self.extract = patch.object(booking, 'extract', side_effect=lambda *args: dict(self.facts))
        self.extract.start()

    def tearDown(self):
        self.extract.stop()
        self.provider.stop()
        self.worker.stop()
        self.env.stop()
        self.temp.cleanup()

    def post(self, url, token, payload):
        self.sends.append(payload['text']['body'])
        return {'messages': [{'id': 'wamid.out' + str(len(self.sends))}]}

    def payload(self, text='Olá', mid='wamid.test1', sender=SENDER, phone=aru.TEST_PHONE_ID, age=0):
        return {'object': 'whatsapp_business_account', 'entry': [{'changes': [{
            'field': 'messages', 'value': {'metadata': {'phone_number_id': phone},
                'messages': [{'from': sender, 'id': mid, 'timestamp': str(int(time.time()-age)),
                              'type': 'text', 'text': {'body': text}}]}}]}]}

    def webhook(self, data, signed=True):
        body = json.dumps(data).encode()
        digest = hmac.new(b'test-secret', body, hashlib.sha256).hexdigest()
        return self.client.post('/webhook', data=body, content_type='application/json',
                                headers={'X-Hub-Signature-256': 'sha256='+digest if signed else 'bad'})

    def job(self, text='Olá', mid='wamid.test1'):
        self.assertEqual(self.webhook(self.payload(text, mid)).status_code, 200)
        with store.db() as con:
            return dict(con.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone())

    def login(self, name='one', client=None):
        client = client or self.client
        client.get('/login')
        with client.session_transaction() as s:
            csrf = s['login_csrf']
        self.assertEqual(client.post('/login', data={'operator':name,'password':'test-password-only','csrf':csrf}).status_code,302)
        with client.session_transaction() as s:
            return {'X-CSRF-Token':s['csrf']}

    def action(self, name, headers, client=None, data=None):
        return (client or self.client).post('/api/conversations/'+SENDER+'/'+name,
                                           json=data or {}, headers=headers)

    def test_realistic_flow_received_reply_assume_return_resume(self):
        aru.process_message(self.job())
        self.assertIn('Sou a Aru', self.sends[-1])
        headers = self.login()
        self.assertEqual(self.action('assumir', headers).status_code, 200)
        count = len(self.sends)
        held = self.job('Datas de outubro', 'wamid.test2')
        aru.process_message(held)
        self.assertEqual(len(self.sends), count)
        self.assertEqual(self.action('enviar', headers, data={'text':'Aqui é a equipe.','nonce':'unique-nonce-123456'}).status_code,200)
        self.assertEqual(self.action('devolver', headers).status_code,200)
        aru.process_message(self.job('Vamos continuar', 'wamid.test3'))
        self.assertEqual(store.conversation(SENDER)['mode'],'aru')
        self.assertGreater(len(self.sends), count+2)

    def test_three_operators_auth_and_conflict(self):
        self.job()
        one=self.login()
        self.action('assumir',one)
        for name in ('two','three'):
            client=aru.app.test_client();headers=self.login(name,client)
            self.assertEqual(self.action('assumir',headers,client).status_code,409)
            self.assertEqual(self.action('devolver',headers,client).status_code,409)

    def test_unauthenticated_and_csrf_blocked(self):
        self.job()
        self.assertEqual(self.client.get('/api/conversations').status_code,401)
        self.login()
        self.assertEqual(self.action('assumir',{}).status_code,403)

    def test_four_operators_fail_closed(self):
        with patch.dict(os.environ,{'ARU_OPERATORS':json.dumps({str(n):HASH for n in range(4)})}):
            self.assertEqual(aru.panel.operators(),{})

    def test_password_change_revokes_session(self):
        self.login()
        with patch.dict(os.environ,{'ARU_OPERATORS':'{}'}):
            self.assertEqual(self.client.get('/api/conversations').status_code,401)

    def test_handoff_during_ai_suppresses_late_answer(self):
        job=self.job()
        started=threading.Event(); finish=threading.Event()
        def slow(*args):
            started.set();finish.wait(3);return dict(self.facts)
        with patch.object(booking,'extract',side_effect=slow):
            thread=threading.Thread(target=aru.process_message,args=(job,));thread.start()
            self.assertTrue(started.wait(2))
            self.action('assumir',self.login())
            finish.set();thread.join(3)
        self.assertEqual(self.sends,[])

    def test_old_queued_messages_do_not_resume_after_handoff(self):
        job=self.job();headers=self.login()
        self.action('assumir',headers);self.action('devolver',headers)
        count=len(self.sends);aru.process_message(job)
        self.assertEqual(len(self.sends),count)

    def test_payment_complaint_exception_and_human(self):
        for i,text in enumerate(('Quero pagar por PIX','Tenho uma reclamação','Quero cancelar','Preciso de um atendente')):
            with store.db() as con:
                con.execute("UPDATE conversations SET mode='aru',owner=NULL")
            aru.process_message(self.job(text,'wamid.urgent'+str(i)))
            self.assertEqual(store.conversation(SENDER)['mode'],'waiting')
        self.assertEqual(booking.extract.call_count,0)

    def test_reset_cannot_override_human(self):
        self.job();self.action('assumir',self.login())
        aru.process_message(self.job('/reiniciar','wamid.reset'))
        self.assertEqual(store.conversation(SENDER)['mode'],'human')
        self.assertEqual(self.sends,[])

    def test_provider_failure_queues_human_and_sends_notice(self):
        with patch.object(booking,'extract',side_effect=RuntimeError('openai_http_429_insufficient_quota')):
            aru.process_message(self.job())
        self.assertEqual(store.conversation(SENDER)['reason'],'ai_unavailable')
        self.assertIn('Não consegui', self.sends[-1])

    def test_booking_collection_children_pets_no_fake_inventory(self):
        self.facts.update(checkin='2027-01-10',checkout='2027-01-12',adults=2,children=1,ages=[5],pets=None)
        aru.process_message(self.job('Somos dois adultos e uma criança de cinco anos'))
        self.assertIn('pet',self.sends[-1])
        self.facts.update(pets=True,pet_details='Um cachorro pequeno')
        aru.process_message(self.job('Um cachorro pequeno','wamid.pet'))
        self.assertEqual(store.conversation(SENDER)['reason'],'availability')
        self.assertIn('nenhuma reserva está confirmada', self.sends[-1])
        self.assertEqual(booking.availability(self.facts)['offers'],[])
        self.assertEqual(booking.availability(self.facts)['property_id'],'18272')

    def test_invalid_dates_and_ages_are_not_accepted(self):
        facts=booking.validate({'checkin':'2027-02-10','checkout':'2027-02-09','adults':0,'children':2,'ages':[5],'pets':'yes'})
        self.assertIsNone(facts['checkout']);self.assertIsNone(facts['adults'])
        self.assertIsNone(facts['ages']);self.assertIsNone(facts['pets'])

    def test_webhook_verification_and_no_default_token(self):
        self.assertEqual(self.client.get('/webhook?hub.mode=subscribe&hub.verify_token=test-verify&hub.challenge=123').data,b'123')
        with patch.dict(os.environ,{'VERIFY_TOKEN':''}):
            self.assertEqual(self.client.get('/webhook?hub.mode=subscribe&hub.verify_token=&hub.challenge=123').status_code,403)

    def test_bad_signature_missing_secret_and_wrong_phone(self):
        self.assertEqual(self.webhook(self.payload(),False).status_code,403)
        with patch.dict(os.environ,{'META_APP_SECRET':''}):
            self.assertEqual(self.webhook(self.payload()).status_code,503)
        self.webhook(self.payload(phone='official-phone'))
        self.assertIsNone(store.conversation(SENDER))

    def test_unauthorized_synthetic_and_old_messages_ignored(self):
        self.webhook(self.payload(sender='5511000000000'))
        self.webhook(self.payload(mid='synthetic'))
        self.webhook(self.payload(age=86401))
        self.assertIsNone(store.conversation(SENDER))

    def test_duplicate_and_restart_persistence(self):
        self.job();self.webhook(self.payload());store.init()
        with store.db() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM messages').fetchone()[0],1)
        self.assertIsNotNone(store.conversation(SENDER))

    def test_rate_limit(self):
        for i in range(11):self.webhook(self.payload(mid='wamid.'+str(i)))
        with store.db() as con:self.assertEqual(con.execute('SELECT count(*) FROM messages').fetchone()[0],10)

    def test_closed_window_no_send(self):
        self.job()
        with store.db() as con:con.execute('UPDATE conversations SET last_inbound=?',(time.time()-86401,))
        with self.assertRaisesRegex(RuntimeError,'window_closed'):aru.send(SENDER,'Oi')
        self.assertEqual(self.sends,[])

    def test_human_idempotency(self):
        self.job();headers=self.login();self.action('assumir',headers)
        data={'text':'Teste','nonce':'same-request-123456'}
        self.assertEqual(self.action('enviar',headers,data=data).status_code,200)
        self.assertEqual(self.action('enviar',headers,data=data).status_code,502)
        self.assertEqual(len(self.sends),1)

    def test_delivery_receipt_and_no_response_loop(self):
        aru.process_message(self.job())
        data=self.payload();value=data['entry'][0]['changes'][0]['value'];value.pop('messages')
        value['statuses']=[{'id':'wamid.out1','status':'read'}]
        self.webhook(data)
        value['statuses'][0]['status']='sent';self.webhook(data)
        with store.db() as con:self.assertEqual(con.execute("SELECT state FROM messages WHERE provider_id='wamid.out1'").fetchone()[0],'read')
        self.assertEqual(len(self.sends),1)

    def test_templates_escape_guest_content(self):
        self.job('<script>alert(1)</script>');self.login()
        self.assertEqual(self.client.get('/painel').status_code,200)
        self.assertIn("frame-ancestors 'none'",self.client.get('/painel').headers['Content-Security-Policy'])

    def test_structured_extractor_payload_and_validation(self):
        self.extract.stop()
        facts=dict(self.facts,adults=2)
        def mock(url,token,payload):
            self.assertTrue(payload['text']['format']['strict'])
            self.assertFalse(payload['store'])
            return {'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(facts)}]}]}
        result=booking.extract(mock,'fake','gpt-4.1-mini',[{'role':'user','content':'2 adultos'}],{})
        self.assertEqual(result['adults'],2)
        self.extract.start()

    def test_safe_provider_error_code_only(self):
        self.provider.stop()
        raw=json.dumps({'error':{'code':'insufficient_quota','message':'SECRET must never appear'}}).encode()
        error=urllib.error.HTTPError('https://api.openai.com/v1/responses',429,'error',{},io.BytesIO(raw))
        with patch('urllib.request.urlopen',side_effect=error):
            with self.assertRaisesRegex(RuntimeError,'^openai_http_429_insufficient_quota$'):
                aru.post_json('https://api.openai.com/v1/responses','fake',{})
        self.provider.start()


if __name__=='__main__':unittest.main(verbosity=2)
