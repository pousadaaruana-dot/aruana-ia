import os
from flask import Flask, request

app = Flask(__name__)

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "aruana_teste_2026")

@app.route("/", methods=["GET"])
def home():
    return "Aruana IA funcionando", 200

@app.route("/webhook", methods=["GET"])
def verificar_webhook():
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    mode = request.args.get("hub.mode")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200

    return "Token invalido", 403

@app.route("/webhook", methods=["POST"])
def receber_mensagem():
    dados = request.get_json(silent=True)
    print(dados)
    return "EVENT_RECEIVED", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
  
