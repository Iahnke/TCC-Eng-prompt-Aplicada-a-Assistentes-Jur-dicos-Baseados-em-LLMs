from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import requests
import tiktoken
import os
import json
from datetime import datetime

# ==========================
# 🔧 APP CONFIG
# ==========================
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = None
socketio = SocketIO(app, cors_allowed_origins="*")

N8N_WEBHOOK_URL = "https://n8ndev.intelibox.com.br/webhook/tcc"

# ==========================
# 🧠 HISTÓRICO DA CONVERSA
# ==========================
conversation_history = []

# ==========================
# 🔢 CONTADOR DE TOKENS
# ==========================
def contar_tokens(texto: str, modelo: str = "gpt-4o-mini") -> int:
    try:
        if not texto:
            return 0
        enc = tiktoken.encoding_for_model(modelo)
        return len(enc.encode(texto))
    except Exception as e:
        print("⚠️ Erro ao contar tokens:", e)
        return 0

# ==========================
# 🧹 NORMALIZAR QUEBRAS DE LINHA
# ==========================
def normalizar_quebras(texto: str) -> str:
    if not texto:
        return ""
    return texto.replace("\r\n", "\n").replace("\r", "\n")

# ==========================
# 🌐 ROTAS
# ==========================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/processar", methods=["POST"])
def processar():
    dados = request.get_json()
    print("📨 Mensagem recebida do front:", dados)

    # Garantia de variáveis
    gpt_msg = ""
    gemini_msg = ""
    gpt_tokens = 0
    gem_tokens = 0

    # ------------------------------
    # 📡 Enviar ao n8n
    # ------------------------------
    try:
        resposta = requests.post(
            N8N_WEBHOOK_URL,
            json=dados,
            timeout=60
        )
        resposta.raise_for_status()
    except Exception as e:
        print("❌ Erro ao comunicar com o n8n:", e)
        return jsonify({"status": "erro", "mensagem": str(e)}), 500

    # ------------------------------
    # 📥 Ler JSON do n8n
    # ------------------------------
    try:
        data = resposta.json()
    except Exception:
        return jsonify({"status": "erro", "mensagem": "Resposta inválida do n8n"}), 500

    print("✅ Resposta recebida do n8n:", data)

    # ------------------------------
    # 🔥 EXTRAÇÃO BLINDADA
    # ------------------------------
    for item in data:
        if isinstance(item, dict) and "output" in item:
            out = item["output"]
            if isinstance(out, dict):
                gpt_msg = out.get("IA_msgGPT", "")
                gemini_msg = out.get("IA_msgGem", "")

    # Normalizar quebras ANTES de tudo
    gpt_msg = normalizar_quebras(gpt_msg)
    gemini_msg = normalizar_quebras(gemini_msg)

    print("🔍 GPT extraído:", gpt_msg[:80])
    print("🔍 Gemini extraído:", gemini_msg[:80])

    # ------------------------------
    # 🔢 CONTAR TOKENS
    # ------------------------------
    gpt_tokens = contar_tokens(gpt_msg)
    gem_tokens = contar_tokens(gemini_msg)

    print(f"🔢 Tokens GPT: {gpt_tokens}")
    print(f"🔢 Tokens Gemini: {gem_tokens}")

    # ------------------------------
    # 🧠 SALVAR NO HISTÓRICO
    # ------------------------------
    conversation_history.append({
        "timestamp": datetime.now().isoformat(),
        "user_input": dados.get("entrada", ""),
        "gpt_response": gpt_msg,
        "gemini_response": gemini_msg,
        "tokens": {
            "gpt": gpt_tokens,
            "gemini": gem_tokens
        }
    })

    # ------------------------------
    # 🚀 ENVIAR PARA O FRONT
    # ------------------------------
    socketio.emit("resposta", {
        "gpt_msg": gpt_msg,
        "gemini_msg": gemini_msg,
        "gpt_tokens": gpt_tokens,
        "gem_tokens": gem_tokens
    })

    return jsonify({"status": "ok"})

# ==========================
# 💾 SALVAR JSON NO DISCO
# ==========================
def salvar_conversa_em_json():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    pasta = os.path.join(base_dir, "JSON_Conversas")

    os.makedirs(pasta, exist_ok=True)

    nome_arquivo = f"conversa_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json"
    caminho = os.path.join(pasta, nome_arquivo)

    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(
            {"conversation": conversation_history},
            f,
            ensure_ascii=False,
            indent=2
        )

    return caminho

@app.route("/salvar_conversa", methods=["POST"])
def salvar_conversa():
    try:
        caminho = salvar_conversa_em_json()
        return jsonify({
            "status": "ok",
            "arquivo": caminho
        })
    except Exception as e:
        return jsonify({
            "status": "erro",
            "mensagem": str(e)
        }), 500

# ==========================
# ▶️ START
# ==========================
if __name__ == "__main__":
    socketio.run(app, debug=True, port=3000)
