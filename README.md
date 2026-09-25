# Aru — piloto de atendimento da Pousada Aruanã

Continuação do projeto existente. Usa a API oficial WhatsApp Cloud da Meta, exclusivamente
no número de teste +1 (555) 142-1547, phone ID 1320569784479924.

## O que esta versão faz
- Recebe webhooks assinados e registra mensagens em SQLite antes de responder ao webhook.
- Extrai datas, adultos, crianças/idades e pets usando a API OpenAI Responses com esquema JSON.
- Valida os dados e usa respostas controladas: não inventa preços, disponibilidade nem políticas.
- Omnibees 18272 é a referência da consulta humana. Não existe consulta automática de inventário nesta versão.
- Encaminha pagamentos, reclamações, exceções, pedidos humanos, disponibilidade e falhas da IA para a fila.
- Painel /painel com até três contas independentes, ASSUMIR, resposta humana e DEVOLVER PARA ARU.
- O proprietário da conversa controla o atendimento. Outros atendentes não podem responder ou devolver.
- A Aru fica pausada e descarta respostas geradas durante uma tomada de controle.
- Recibos Meta distinguem aceitação, envio, entrega e leitura. Envio incerto não é repetido automaticamente.

## Configuração no Render
Manter `gunicorn app:app`. `gunicorn.conf.py` limita a um processo, com quatro threads, sem preload.
Não aumentar workers ou réplicas: os bloqueios de handoff são locais ao processo.

Configurar somente em Environment, nunca no código:
- OPENAI_API_KEY: chave com acesso e saldo/limite para a API.
- WHATSAPP_ACCESS_TOKEN: token autorizado para a conta de teste.
- META_APP_SECRET: valida a assinatura de cada webhook.
- TEST_RECIPIENTS: números autorizados, dígitos com país e DDD, separados por vírgula.
- VERIFY_TOKEN: segredo aleatório também configurado no callback da Meta. O padrão público antigo foi removido.
- ARU_OPERATORS: objeto JSON com até três nomes e hashes Werkzeug scrypt/pbkdf2 de suas senhas.
- ARU_SESSION_SECRET: segredo aleatório para sessões. Se ausente, sessões expiram após reinício.
- ARU_DB_PATH: caminho SQLite em volume persistente, por exemplo /var/data/aru.sqlite3.
- OPENAI_MODEL: opcional; mantém gpt-4.1-mini.

`python configure_operators.py` permite cadastrar senhas de forma privada e produz os valores
para Environment. Não publicar sua saída. ARU_LOCAL_HTTP=1 serve apenas para teste local;
não configurar no Render. Contas não configuradas impedem acesso ao painel.

Webhook: https://aruana-ia.onrender.com/webhook. Assinar messages e verificar a inscrição
do app Aruana 3124809067910374 na conta WhatsApp 2056312315031112.

## Segurança e armazenamento
- Cookies HttpOnly/SameSite/Secure, CSRF, limite de login e histórico sem HTML executável.
- Logs não incluem mensagens, tokens nem respostas brutas de provedores; apenas códigos de diagnóstico.
- Erro openai_http_429_insufficient_quota indica cota/saldo indisponível na API; o serviço registra fila humana.
- /health apenas verifica presença de configurações, não comprova credenciais ou entrega.
- SQLite sobrevive a reinícios de processo quando o arquivo permanece. O disco gratuito do Render é efêmero:
  deploy/recriação pode apagar conversas, fila e controle humano. Usar volume persistente antes de produção.
- Testadores autorizados somente; texto somente. Janela de resposta de 24 horas; limite de 10 entradas/minuto.
- A equipe deve manter o painel aberto para acompanhar a fila. Não há alertas externos para os atendentes.
- Histórico é limitado a 20 mensagens para a IA e 100 no painel. Não há exclusão automática de dados nesta versão.
- Se o processo cair durante um envio, a mensagem fica incerta para verificação humana.

## Validação
`python -m unittest test_app -v` usa provedores simulados e não envia mensagens reais.
Foi validado também o painel local: login, assumir, envio humano e devolução.
Teste real continua exigindo API OpenAI disponível, token Meta válido e acessos da equipe configurados.
Não confundir sucesso de teste isolado, aceite de envio e confirmação de entrega no telefone.

Arquivos ui.py contém os templates Jinja e assets servidos por rotas explícitas.
As integrações principais permanecem em app.py, booking.py, store.py e panel.py.
