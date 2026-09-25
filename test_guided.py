import os
import unittest
from unittest.mock import patch
import guided
import store
import app as aru
from test_app import AruTests, SENDER


class ParsingTests(unittest.TestCase):
    def test_complete_intake_with_children_and_pet(self):
        facts = {}
        for answer in ('01/10/2099', '05/10/2099', '2', '2', '4, 8', 'sim', '1 cachorro pequeno'):
            facts = guided.extract(answer, facts)
        self.assertIsNone(guided.question(facts))
        self.assertEqual(facts['ages'], [4, 8])
        self.assertEqual(facts['adults'], 2)
        self.assertTrue(facts['pets'])

    def test_ambiguous_and_invalid_dates_are_not_guessed(self):
        for text in ('amanhã', '01/10', '31/02/2099', '01/10/2020', '2 adultos'):
            self.assertIsNone(guided.extract(text, {})['checkin'])
        facts = guided.extract('05/10/2099', {})
        self.assertIsNone(guided.extract('01/10/2099', facts)['checkout'])

    def test_quantities_do_not_leak_between_fields(self):
        facts = guided.extract('01/10/2099', {})
        facts = guided.extract('05/10/2099', facts)
        self.assertIsNone(guided.extract('2 crianças de 4 e 8 anos', facts)['adults'])
        facts = guided.extract('2', facts)
        facts = guided.extract('2', facts)
        self.assertIsNone(guided.extract('4', facts)['ages'])
        self.assertIsNone(guided.extract('4, 18', facts)['ages'])

    def test_sensitive_requests_route_to_human(self):
        for text, reason in [('quero pagar', 'payment'), ('atendente', 'human'),
                             ('qual o endereço?', 'information'), ('reclamação', 'complaint')]:
            self.assertEqual(guided.extract(text, {})['reason'], reason)


class GuidedFlowTests(AruTests):
    # Reuse only the fixture; inherited OpenAI tests remain covered by test_app.
    def test_guided_flow_never_calls_ai_and_preserves_handoff(self):
        with patch.dict(os.environ, {'ARU_ENGINE': 'guided', 'OPENAI_API_KEY': ''}):
            self.assertNotIn('OPENAI_API_KEY', aru.missing_config())
            with patch('booking.extract', side_effect=AssertionError('Paid API used')):
                for i, answer in enumerate(('Oi', '01/10/2099', '05/10/2099', '2', '0', 'não')):
                    aru.process_message(self.job(answer, 'wamid.guided'+str(i)))
                self.assertEqual(store.conversation(SENDER)['mode'], 'waiting')
                self.assertEqual(store.conversation(SENDER)['facts']['children'], 0)
                self.assertIn('nenhuma reserva está confirmada', self.sends[-1])
                headers = self.login()
                self.assertEqual(self.action('assumir', headers).status_code, 200)
                self.assertEqual(self.action('devolver', headers).status_code, 200)
                aru.process_message(self.job('/reiniciar', 'wamid.guided-reset'))
                self.assertIn('DD/MM/AAAA', self.sends[-1])
                self.assertEqual(store.conversation(SENDER)['facts'], {})


# Avoid executing the inherited tests twice in discovery.
for name in tuple(dir(AruTests)):
    if name.startswith('test_'):
        setattr(GuidedFlowTests, name, None)
del AruTests

