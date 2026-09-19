# Aruana IA — piloto de testes

Exclusivo no numero Meta +1 (555) 142-1547, ID 1320569784479924. Outros numeros sao ignorados. Nao migrar o WhatsApp oficial nesta etapa.

## Configuracao no Render

Manter gunicorn app:app com um worker, sem --preload. Configurar em Environment, nunca no repositorio:

- OPENAI_API_KEY: chave da OpenAI com acesso a API e saldo/limite disponivel.
- WHATSAPP_ACCESS_TOKEN: token autorizado para a conta WhatsApp de teste.
- META_APP_SECRET: segredo do app Aruana na Meta, para validar assinaturas.
- TEST_RECIPIENTS: telefones pessoais autorizados no painel da Meta, com pais e DDD, somente digitos, separados por virgulas.
- OPENAI_MODEL: opcional; padrao gpt-4.1-mini.
- VERIFY_TOKEN: mesmo token de verificacao do callback da Meta.

Callback /webhook, campo messages assinado. /health indica configuracoes ausentes; configured=true nao valida tokens ou saldo. Credenciais ausentes desativam o processamento.

## Testar

Enviar Oi do telefone pessoal autorizado para o numero de teste. Perguntar sobre hospedagem, informar datas e quantidade de hospedes. /reiniciar limpa o contexto. Conferir a resposta no telefone: test_reply_accepted_by_whatsapp nos logs significa aceite pela Meta, nao entrega final. Eventos sinteticos do painel apenas testam o webhook; IDs ficticios e status nao geram respostas.

## Limites

Apenas texto. Sem consulta a precos, disponibilidade, reservas, Hospedin, Omnibees ou transferencia humana. A assistente deve explicar as limitacoes sem inventar dados.

Fila, deduplicacao e historico ficam em memoria de um processo e se perdem com reinicios. Historico expira em uma hora e usa ate 12 mensagens anteriores. Render gratuito pode suspender o servico. Limite de dez mensagens por minuto por testador. Eventos com mais de cinco minutos sao ignorados. Falhas de API ficam nos logs sem conteudo da conversa; nao ha repeticao automatica de envios incertos. Corrigir e enviar uma nova mensagem.

Antes de uso oficial: armazenamento duravel, dados confirmados da pousada, integracoes e avaliacao humana.

## Validacao

11 testes locais com APIs simuladas passaram: assinatura e verificacao do webhook, bloqueio de numero oficial e remetentes nao autorizados, duplicatas, eventos antigos e status, credenciais ausentes, fluxo IA/WhatsApp, falhas de envio, reinicio, midia e limite de frequencia. Conversa real com IA pendente das credenciais.
