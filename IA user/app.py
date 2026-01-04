# ===============================
# 🔧 CORREÇÃO DE CONCORRÊNCIA
# ===============================
import eventlet
eventlet.monkey_patch() 

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
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

N8N_WEBHOOK_URL = "https://n8ndev.intelibox.com.br/webhook/tccautoia"
MAX_AI_LOOPS = 5

PRICE_GPT_OUTPUT_1M = 1.60 
PRICE_GEMINI_OUTPUT_1M = 2.50 

# Globais
session_costs = { "gpt_total": 0.0, "gemini_total": 0.0 }
conversation_history = []

# ==========================
# 🛠️ UTILITÁRIOS
# ==========================
def contar_tokens(texto, modelo="gpt-4o-mini"):
    try:
        if not texto: return 0
        enc = tiktoken.encoding_for_model(modelo)
        return len(enc.encode(texto))
    except: return 0

def normalizar_quebras(texto: str) -> str:
    if not texto: return ""
    return texto.replace("\r\n", "\n").replace("\r", "\n")

def gerar_entrada_ai_user(gpt_msg, gemini_msg):
    return (
        "Considere as respostas abaixo e aja como o cliente jurídico. Seja breve.\n\n"
        f"GPT disse:\n{gpt_msg}\n\n"
        f"Gemini disse:\n{gemini_msg}\n\n"
        "Sua resposta:"
    )

def limpar_dado_json(dado):
    """
    Tenta transformar string JSON em dicionário e extrair texto/classificação.
    Resolve o problema do 'IA_user' vir como JSON sujo.
    """
    if not dado: return "", ""
    
    try:
        # Se for string, tenta carregar como JSON
        obj = dado
        if isinstance(dado, str):
            # Se parecer JSON, converte
            if dado.strip().startswith("{"):
                obj = json.loads(dado)
            else:
                return dado, "" # É texto puro

        # Se for dicionário, extrai os campos conhecidos
        if isinstance(obj, dict):
            # Tenta várias chaves possíveis para a mensagem
            msg = obj.get("IA_msgGPT") or obj.get("IA_msgGEM") or obj.get("IA_msgCliente") or obj.get("message") or str(obj)
            classe = obj.get("classificacao", "")
            return msg, classe
            
    except Exception as e:
        print(f"⚠️ Erro parse JSON: {e}")
    
    return str(dado), ""

# ==========================
# 🌐 ROTAS
# ==========================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/processar", methods=["POST"])
def processar():
    global conversation_history, session_costs
    
    dados = request.get_json(silent=True) or {}
    loop_count = int(dados.get("loop_count", 0))
    user_type = dados.get("user_type", "human")
    user_input = dados.get("entrada", "")

    print(f"\n📨 [LOOP {loop_count}] Processando...")

    # Reset
    if user_input.strip().lower() == "reset":
        conversation_history = []
        session_costs = { "gpt_total": 0.0, "gemini_total": 0.0 }
        try: requests.post(N8N_WEBHOOK_URL, json={"entrada": "reset"}, timeout=5)
        except: pass
        socketio.emit("resposta", {"status": "reset"})
        return jsonify({"status": "reset"})

    gpt_msg, gemini_msg = "", ""
    gpt_class, gem_class = "", ""
    ai_user_msg = ""

    try:
        resposta = requests.post(
            N8N_WEBHOOK_URL, 
            json={"entrada": user_input, "user_type": user_type}, 
            timeout=60
        )
        resposta.raise_for_status()
        data = resposta.json()

        if isinstance(data, list) and len(data) > 0:
            item = data[0]
            # O n8n pode devolver em 'output' ou 'json' ou na raiz
            out = item.get("output", item.get("json", item))
            
            # --- Extração com Limpeza (Resolve o problema do JSON no chat) ---
            gpt_msg, gpt_class = limpar_dado_json(out.get("IA_msgGPT"))
            
            # Gemini as vezes vem como 'IA_msgGEM' ou 'IA_msgGem'
            gemini_msg, gem_class = limpar_dado_json(out.get("IA_msgGEM") or out.get("IA_msgGem"))
            
            # Cliente Simulado (IA_user)
            ai_user_msg, _ = limpar_dado_json(out.get("IA_user"))

    except Exception as e:
        print("❌ Erro Conexão n8n:", e)
        gpt_msg = f"Erro: {str(e)}"

    # Normalização
    gpt_msg = normalizar_quebras(gpt_msg)
    gemini_msg = normalizar_quebras(gemini_msg)
    
    # Tokens e Custos
    gpt_tokens = contar_tokens(gpt_msg)
    gem_tokens = contar_tokens(gemini_msg)
    
    custo_gpt = (gpt_tokens / 1_000_000) * PRICE_GPT_OUTPUT_1M
    custo_gem = (gem_tokens / 1_000_000) * PRICE_GEMINI_OUTPUT_1M
    
    session_costs["gpt_total"] += custo_gpt
    session_costs["gemini_total"] += custo_gem

    # Salva Histórico
    conversation_history.append({
        "timestamp": datetime.now().isoformat(),
        "loop": loop_count,
        "tipo": user_type,
        "input": user_input,
        "user_simulado": ai_user_msg,
        "gpt": {"msg": gpt_msg, "class": gpt_class, "tokens": gpt_tokens, "custo": custo_gpt},
        "gemini": {"msg": gemini_msg, "class": gem_class, "tokens": gem_tokens, "custo": custo_gem}
    })

    # Envia ao Front
    socketio.emit("resposta", {
        "user_type": user_type,
        "user_input": user_input,    
        "ai_user_msg": ai_user_msg, # Agora vai limpo!
        "loop_count": loop_count,
        
        "gpt_msg": gpt_msg,
        "gpt_classificacao": gpt_class, 
        
        "gemini_msg": gemini_msg,
        "gem_classificacao": gem_class, 
        
        "gpt_tokens": gpt_tokens,
        "gem_tokens": gem_tokens,
        
        # Custos atualizados
        "custo_run_gpt": custo_gpt,
        "custo_run_gem": custo_gem,
        "custo_total_gpt": session_costs["gpt_total"],
        "custo_total_gem": session_costs["gemini_total"]
    })
    
    socketio.sleep(0) 

    # --- Lógica do Loop ---
    stop_loop = False
    termos = ["qualificado", "desqualificado", "encerrar"]
    if any(t in gpt_class.lower() for t in termos) or any(t in gem_class.lower() for t in termos):
        stop_loop = True

    # Só continua loop se for AI User e não tiver acabado
    if user_type == "ai_user" and not stop_loop and loop_count < MAX_AI_LOOPS:
        nova_entrada = gerar_entrada_ai_user(gpt_msg, gemini_msg)
        socketio.start_background_task(continuar_loop, nova_entrada, loop_count + 1)
    
    elif stop_loop:
        socketio.emit("aviso_sistema", {"msg": "🛑 Conversa Finalizada pelo sistema."})

    return jsonify({"status": "ok"})

def continuar_loop(nova_entrada, loop_count):
    socketio.sleep(4)
    try: requests.post("http://127.0.0.1:3000/processar", json={"entrada": nova_entrada, "user_type": "ai_user", "loop_count": loop_count})
    except: pass

@app.route("/salvar_conversa", methods=["POST"])
def salvar_conversa():
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        pasta = os.path.join(base_dir, "JSON_Conversas")
        os.makedirs(pasta, exist_ok=True)
        nome = f"conversa_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json"
        with open(os.path.join(pasta, nome), "w", encoding="utf-8") as f:
            json.dump({"historico": conversation_history}, f, ensure_ascii=False, indent=2)
        return jsonify({"status": "ok", "arquivo": nome})
    except Exception as e:
        return jsonify({"status": "erro", "mensagem": str(e)})

@app.route("/start_ai_conversation", methods=["POST"])
def start_ai_conversation():
    # Inicia o loop simulado
    socketio.start_background_task(requests.post, "http://127.0.0.1:3000/processar", json={"entrada": "Olá", "user_type": "ai_user", "loop_count": 0})
    return jsonify({"status": "started"})

if __name__ == "__main__":
    print("🚀 Servidor ON (Porta 3000)")
    socketio.run(app, debug=True, port=3000)