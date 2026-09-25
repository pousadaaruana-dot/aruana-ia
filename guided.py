"""Conservative, offline booking intake. No external model or paid API."""
import re
import unicodedata
import booking


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.lower())
                   if not unicodedata.combining(c)).strip().rstrip('.!')


def pending(facts):
    for field in ('checkin', 'checkout', 'adults', 'children', 'ages', 'pets'):
        if facts.get(field) is None:
            return field
    if facts.get('pets') and not facts.get('pet_details'):
        return 'pet_details'
    return None


def extract(text, current):
    facts = booking.validate(dict(current, reason='none'))
    field = pending(facts)
    value = normalized(text)
    reason = booking.urgent_reason(text)
    if reason:
        facts['reason'] = reason
        return facts
    if re.search(r'\b(endereco|localizacao|cafe|piscina|estacionamento|horario|politica|aceita pet)\b', value):
        facts['reason'] = 'information'
        return facts
    # Parse only the answer requested, never guess quantities from unrelated prose.
    if field in ('checkin', 'checkout'):
        match = re.fullmatch(r'(\d{2})/(\d{2})/(\d{4})', value)
        if match:
            day, month, year = match.groups()
            facts[field] = f'{year}-{month}-{day}'
    elif field in ('adults', 'children'):
        if re.fullmatch(r'\d{1,2}', value):
            facts[field] = int(value)
            if field == 'children':
                facts['ages'] = [] if facts[field] == 0 else None
        elif field == 'children' and value in ('nao', 'nenhuma', 'sem criancas'):
            facts['children'], facts['ages'] = 0, []
    elif field == 'ages':
        if re.fullmatch(r'\d{1,2}(\s*[,;]\s*\d{1,2})*', value):
            facts['ages'] = [int(a.strip()) for a in re.split('[,;]', value)]
    elif field == 'pets':
        if value in ('sim', 'nao', 'sem pet', 'sem pets'):
            facts['pets'] = value == 'sim'
    elif field == 'pet_details':
        if 3 <= len(text.strip()) <= 300 and '?' not in text:
            facts['pet_details'] = text.strip()
    return booking.validate(facts)


def question(facts, first=False):
    prefix = ('Olá! Sou a Aru, assistente virtual da Pousada Aruanã. '
              'Vou anotar os dados, uma etapa por vez. Para falar com a equipe, '
              'escreva ATENDENTE; para corrigir os dados, /reiniciar. ') if first else ''
    questions = {
        'checkin': 'Qual é a data de entrada? Envie apenas DD/MM/AAAA, incluindo o ano.',
        'checkout': 'Qual é a data de saída? Envie apenas DD/MM/AAAA, depois da entrada.',
        'adults': 'Quantos adultos vão se hospedar? Envie apenas o número, por exemplo: 2.',
        'children': 'Quantas crianças vão se hospedar? Envie apenas o número; se não houver, envie 0.',
        'ages': 'Quais são as idades das crianças? Separe por vírgula, por exemplo: 4, 8.',
        'pets': 'Vocês vão levar pets? Responda SIM ou NÃO. A equipe confirmará as condições.',
        'pet_details': 'Informe tipo, quantidade e porte dos pets, por exemplo: 1 cachorro pequeno. A equipe precisa confirmar a aceitação.',
    }
    step = pending(facts)
    return prefix + questions[step] if step else None
