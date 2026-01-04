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

# URL do seu Webhook
N8N_WEBHOOK_URL = "https://n8ndev.intelibox.com.br/webhook/tcc_multi"

# ==========================
# 💰 CONFIGURAÇÃO DE PREÇOS (Por 1 Milhão de Tokens)
# ==========================
PRICE_GPT_OUTPUT_1M = 0.60    
PRICE_GEMINI_OUTPUT_1M = 0.30 

# Acumuladores Globais
session_costs = {
    "gpt_total": 0.0,
    "gemini_total": 0.0
}
conversation_history = []

# ==========================
# 🔢 UTILITÁRIOS
# ==========================
def contar_tokens(texto: str, modelo: str = "gpt-4o-mini") -> int:
    try:
        if not texto: return 0
        enc = tiktoken.encoding_for_model(modelo)
        return len(enc.encode(texto))
    except Exception as e:
        print("⚠️ Erro token:", e)
        return 0

def normalizar_quebras(texto: str) -> str:
    if not texto: return ""
    return texto.replace("\r\n", "\n").replace("\r", "\n")

# ==========================
# 🌐 ROTAS
# ==========================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/processar", methods=["POST"])
def processar():
    dados = request.get_json(silent=True) or {}
    print("📨 Entrada recebida:", dados)

    user_type = "human" # Força sempre humano, já que tiramos o loop
    user_input = dados.get("entrada", "")
    
    gpt_msg = ""
    gemini_msg = ""  
    gpt_class = ""
    gem_class = ""

    # ==========================
    # 🧹 LÓGICA DE RESET
    # ==========================
    if user_input.strip().lower() == "reset":
        print("🧹 Resetando...")
        global conversation_history, session_costs
        conversation_history = []
        session_costs["gpt_total"] = 0.0
        session_costs["gemini_total"] = 0.0
        
        try:
            requests.post(N8N_WEBHOOK_URL, json={"entrada": "reset"}, timeout=5)
        except: pass

        socketio.emit("resposta", {
            "status": "reset",
            "gpt_msg": "Memória Limpa.",
            "gemini_msg": "Memória Limpa."
        })
        return jsonify({"status": "reset"})

    # ==========================
    # 📡 ENVIA AO N8N
    # ==========================
    try:
        # Envia apenas a entrada do usuário
        resposta = requests.post(
            N8N_WEBHOOK_URL,
            json={"entrada": user_input}, 
            timeout=60
        )
        resposta.raise_for_status()
        data = resposta.json()
    except Exception as e:
        print("❌ Erro n8n:", e)
        return jsonify({"status": "erro", "mensagem": str(e)}), 500

    # ==========================
    # 🔥 EXTRAÇÃO (PARSING)
    # ==========================
    # O n8n retorna uma lista. O item 0 tem "outputGPT" e "outputGEM" como STRINGS JSON.
    if isinstance(data, list) and len(data) > 0:
        item = data[0]

        # --- GPT ---
        # Tenta pegar "outputGPT" ou fallback para "output.outputGPT"
        raw_gpt = item.get("outputGPT", item.get("output", {}).get("outputGPT"))
        if raw_gpt:
            try:
                # Se for string (o que é provável vindo do n8n), faz o parse
                gpt_data = json.loads(raw_gpt) if isinstance(raw_gpt, str) else raw_gpt
                gpt_msg = gpt_data.get("IA_msgGPT", "")
                gpt_class = gpt_data.get("classificacao", "")
            except Exception as e:
                print(f"⚠️ Erro parse GPT: {e}")
                gpt_msg = str(raw_gpt) # Fallback mostra o cru

        # --- Gemini ---
        raw_gem = item.get("outputGEM", item.get("output", {}).get("outputGEM"))
        if raw_gem:
            try:
                gem_data = json.loads(raw_gem) if isinstance(raw_gem, str) else raw_gem
                gemini_msg = gem_data.get("IA_msgGEM") or gem_data.get("IA_msgGem") or ""
                gem_class = gem_data.get("classificacao", "")
            except Exception as e:
                print(f"⚠️ Erro parse Gemini: {e}")
                gemini_msg = str(raw_gem)

    # Normaliza
    gpt_msg = normalizar_quebras(gpt_msg)
    gemini_msg = normalizar_quebras(gemini_msg)

    # Tokens
    gpt_tokens = contar_tokens(gpt_msg)
    gem_tokens = contar_tokens(gemini_msg)

    # ==========================
    # 💰 CÁLCULO DE CUSTOS
    # ==========================
    custo_run_gpt = (gpt_tokens / 1_000_000) * PRICE_GPT_OUTPUT_1M
    custo_run_gem = (gem_tokens / 1_000_000) * PRICE_GEMINI_OUTPUT_1M

    session_costs["gpt_total"] += custo_run_gpt
    session_costs["gemini_total"] += custo_run_gem

    # Salva no Histórico
    conversation_history.append({
        "timestamp": datetime.now().isoformat(),
        "user_input": user_input,
        "gpt_response": gpt_msg,
        "gemini_response": gemini_msg,
        "tokens": {"gpt": gpt_tokens, "gemini": gem_tokens}
    })

    # ==========================
    # 🚀 ENVIA AO FRONT
    # ==========================
    socketio.emit("resposta", {
        "gpt_msg": gpt_msg,
        "gemini_msg": gemini_msg,
        "gpt_classificacao": gpt_class,
        "gem_classificacao": gem_class,
        "gpt_tokens": gpt_tokens,
        "gem_tokens": gem_tokens,
        
        # Custos
        "custo_run_gpt": custo_run_gpt,
        "custo_run_gem": custo_run_gem,
        "custo_total_gpt": session_costs["gpt_total"],
        "custo_total_gem": session_costs["gemini_total"]
    })

    return jsonify({"status": "ok"})

# ==========================
# 💾 SALVAR JSON
# ==========================
def salvar_conversa_em_json():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    pasta = os.path.join(base_dir, "JSON_Conversas")
    os.makedirs(pasta, exist_ok=True)
    nome_arquivo = f"conversa_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json"
    caminho = os.path.join(pasta, nome_arquivo)
    
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump({"conversation": conversation_history}, f, ensure_ascii=False, indent=2)
    return caminho

@app.route("/salvar_conversa", methods=["POST"])
def salvar_conversa():
    try:
        caminho = salvar_conversa_em_json()
        return jsonify({"status": "ok", "arquivo": caminho})
    except Exception as e:
        return jsonify({"status": "erro", "mensagem": str(e)}), 500

if __name__ == "__main__":
    socketio.run(app, debug=True, port=3000)