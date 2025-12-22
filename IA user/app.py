# ===============================
# 🔧 CORREÇÃO DE CONCORRÊNCIA
# ===============================
import eventlet
eventlet.monkey_patch() # 🔥 ESSENCIAL PARA O SOCKETIO NÃO TRAVAR

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
app.config['MAX_CONTENT_LENGTH'] = None

# async_mode='eventlet' permite lidar com requisições simultâneas
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

N8N_WEBHOOK_URL = "https://n8ndev.intelibox.com.br/webhook/tcc"
MAX_AI_LOOPS = 3

# Memória volátil para guardar o histórico antes de salvar no disco
conversation_history = []

# ==========================
# 💰 CONFIGURAÇÃO DE PREÇOS (Por 1 Milhão de Tokens)
# ==========================
PRICE_GPT_OUTPUT_1M = 1.60    # US$ 1,60 por 1M tokens de saída
PRICE_GEMINI_OUTPUT_1M = 2.50 # US$ 2,50 por 1M tokens de saída

# Acumuladores de Custo da Sessão (Memória RAM)
session_costs = {
    "gpt_total": 0.0,
    "gemini_total": 0.0
}

# ==========================
# 🔢 UTILITÁRIOS
# ==========================
def contar_tokens(texto, modelo="gpt-4o-mini"):
    try:
        if not texto: return 0
        enc = tiktoken.encoding_for_model(modelo)
        return len(enc.encode(texto))
    except Exception as e:
        print("⚠️ Erro ao contar tokens:", e)
        return 0

def normalizar_quebras(texto: str) -> str:
    if not texto: return ""
    return texto.replace("\r\n", "\n").replace("\r", "\n")

def gerar_entrada_ai_user(gpt_msg, gemini_msg):
    # Gera o prompt técnico para o próximo loop
    return (
        "Considere as respostas abaixo e responda como um cliente jurídico realista, "
        "dando continuidade natural à conversa.\n\n"
        f"GPT disse:\n{gpt_msg}\n\n"
        f"Gemini disse:\n{gemini_msg}\n\n"
        "Agora responda como o cliente."
    )

# ==========================
# 🌐 ROTAS
# ==========================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/processar", methods=["POST"])
def processar():
    dados = request.get_json()
    loop_count = int(dados.get("loop_count", 0))
    user_type = dados.get("user_type", "human")
    user_input = dados.get("entrada", "")

    print(f"📨 Entrada recebida (Loop {loop_count} - Tipo: {user_type})...")

    gpt_msg = ""
    gemini_msg = ""
    ai_user_msg = "" # Variável para guardar a fala "limpa" do cliente (se for IA)

    # ------------------------------
    # 📡 CHAMADA AO n8n
    # ------------------------------
    try:
        resposta = requests.post(N8N_WEBHOOK_URL, json={"entrada": user_input}, timeout=60)
        resposta.raise_for_status()
        data = resposta.json()

        # Extração dos dados do n8n
        for item in data:
            if isinstance(item, dict) and "output" in item:
                out = item["output"]
                if isinstance(out, dict):
                    gpt_msg = out.get("IA_msgGPT", "")
                    gemini_msg = out.get("IA_msgGem", "")

                    # 🔴 LÓGICA DE EXTRAÇÃO DO IA_USER
                    raw_ia_user = out.get("IA_user", "")
                    
                    if raw_ia_user:
                        try:
                            # Se vier como string (escapada), converte para dict
                            if isinstance(raw_ia_user, str):
                                user_data = json.loads(raw_ia_user)
                            else:
                                user_data = raw_ia_user
                            
                            # Tenta pegar o campo exato da mensagem do cliente
                            if "output" in user_data:
                                ai_user_msg = user_data["output"].get("IA_msgCliente", "")
                            else:
                                ai_user_msg = str(user_data) # Fallback
                                
                        except Exception as e:
                            print(f"⚠️ Erro parse IA_user: {e}")
                            ai_user_msg = str(raw_ia_user)

    except Exception as e:
        print("❌ Erro n8n:", e)
        return jsonify({"status": "erro", "mensagem": str(e)}), 500

    # Normalização e Contagem
    gpt_msg = normalizar_quebras(gpt_msg)
    gemini_msg = normalizar_quebras(gemini_msg)
    
    gpt_tokens = contar_tokens(gpt_msg)
    gem_tokens = contar_tokens(gemini_msg)

# ------------------------------
    # 💰 CÁLCULO DE CUSTOS
    # ------------------------------
    # Fórmula: (Tokens / 1.000.000) * Preço
    custo_run_gpt = (gpt_tokens / 1_000_000) * PRICE_GPT_OUTPUT_1M
    custo_run_gem = (gem_tokens / 1_000_000) * PRICE_GEMINI_OUTPUT_1M

    # Atualiza o total acumulado
    session_costs["gpt_total"] += custo_run_gpt
    session_costs["gemini_total"] += custo_run_gem

    # ------------------------------
    # 🧠 ADICIONAR AO HISTÓRICO (O que faltava)
    # ------------------------------
    nova_interacao = {
        "timestamp": datetime.now().isoformat(),
        "user_type": user_type,
        "loop_count": loop_count,
        "user_input_raw": user_input, # O prompt técnico enviado
        "ai_user_message": ai_user_msg, # O que a Persona "falou" (se houver)
        "gpt_response": gpt_msg,
        "gemini_response": gemini_msg,
        "tokens": {
            "gpt": gpt_tokens,
            "gemini": gem_tokens
        },
        "costs": {
            "gpt_run": custo_run_gpt,
            "gemini_run": custo_run_gem
        }
    }
    conversation_history.append(nova_interacao)

    # ------------------------------
    # 🚀 ENVIAR AO FRONT
    # ------------------------------
    socketio.emit("resposta", {
        "user_type": user_type,
        "user_input": user_input,    
        "ai_user_msg": ai_user_msg,  
        "loop_count": loop_count,
        "gpt_msg": gpt_msg,
        "gemini_msg": gemini_msg,

        "gpt_tokens": gpt_tokens,
        "gem_tokens": gem_tokens,

        "custo_run_gpt": custo_run_gpt,
        "custo_run_gem": custo_run_gem,
        "custo_total_gpt": session_costs["gpt_total"],
        "custo_total_gem": session_costs["gemini_total"]
    })
    
    # Pausa minúscula para garantir o emit
    socketio.sleep(0) 

    # ------------------------------
    # 🔁 CONTINUA LOOP (AI → AI)
    # ------------------------------
    if user_type == "ai_user" and loop_count < MAX_AI_LOOPS:
        nova_entrada = gerar_entrada_ai_user(gpt_msg, gemini_msg)
        socketio.start_background_task(continuar_loop, nova_entrada, loop_count + 1)

    return jsonify({"status": "ok"})

# ==========================
# 🔁 LOOP CONTROLADO (BACKGROUND)
# ==========================
def continuar_loop(nova_entrada, loop_count):
    socketio.sleep(2) # Delay estético
    try:
        # Usa 127.0.0.1 para evitar overhead de DNS
        requests.post(
            "http://127.0.0.1:3000/processar",
            json={
                "entrada": nova_entrada,
                "user_type": "ai_user",
                "loop_count": loop_count
            },
            timeout=100
        )
    except Exception as e:
        print("⚠️ Erro no loop background:", e)

# ==========================
# 💾 SALVAR JSON NO SERVIDOR
# ==========================
@app.route("/salvar_conversa", methods=["POST"])
def salvar_conversa():
    try:
        if not conversation_history:
            return jsonify({"status": "aviso", "mensagem": "Nada para salvar."})

        # Configuração de caminhos
        base_dir = os.path.dirname(os.path.abspath(__file__))
        pasta_destino = os.path.join(base_dir, "JSON_Conversas")
        os.makedirs(pasta_destino, exist_ok=True)
        
        nome_arquivo = f"conversa_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json"
        caminho_completo = os.path.join(pasta_destino, nome_arquivo)
        
        # Estrutura final
        dados_finais = {
            "conversation": conversation_history
        }
        
        # Escrita no disco
        with open(caminho_completo, "w", encoding="utf-8") as f:
            json.dump(dados_finais, f, ensure_ascii=False, indent=2)
            
        print(f"💾 Arquivo salvo em: {caminho_completo}")
        
        return jsonify({
            "status": "ok", 
            "mensagem": f"Salvo com sucesso!",
            "arquivo": nome_arquivo
        })

    except Exception as e:
        print(f"❌ Erro ao salvar arquivo: {e}")
        return jsonify({"status": "erro", "mensagem": str(e)}), 500

# ==========================
# ▶️ INICIAR LOOP MANUALMENTE
# ==========================
@app.route("/start_ai_conversation", methods=["POST"])
def start_ai_conversation():
    try:
        requests.post("http://127.0.0.1:3000/processar", json={
            "entrada": "Olá, sou um cliente com um problema jurídico real e gostaria de orientação.",
            "user_type": "ai_user",
            "loop_count": 0
        }, timeout=1) 
        return jsonify({"status": "ok"})
    except:
        # Ignora erro de timeout, pois a intenção é só disparar
        return jsonify({"status": "ok"})

if __name__ == "__main__":
    print("🚀 Servidor rodando com Eventlet na porta 3000...")
    socketio.run(app, debug=True, port=3000)