"""Run privately to produce Render values. Does not write plaintext passwords."""
import getpass
import json
import secrets
from werkzeug.security import generate_password_hash

if __name__ == '__main__':
    accounts = {}
    for i in range(1, 4):
        name = input(f'Nome do atendente {i} (Enter para encerrar): ').strip()
        if not name:
            break
        if name in accounts:
            raise SystemExit('Nome duplicado.')
        password = getpass.getpass('Senha (mínimo 14 caracteres): ')
        if len(password) < 14 or password != getpass.getpass('Repita a senha: '):
            raise SystemExit('Senha curta ou confirmação diferente.')
        accounts[name] = generate_password_hash(password)
    if not accounts:
        raise SystemExit('Nenhum atendente configurado.')
    print('ARU_OPERATORS=' + json.dumps(accounts))
    print('ARU_SESSION_SECRET=' + secrets.token_urlsafe(48))
    print('VERIFY_TOKEN=' + secrets.token_urlsafe(40))
