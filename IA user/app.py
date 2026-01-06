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
# 🔧 CONFIGURAÇÃO
# ==========================
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

N8N_WEBHOOK_URL = "https://n8ndev.intelibox.com.br/webhook/tccautoia"
MAX_AI_LOOPS = 12
PRICE_GPT_OUTPUT_1M = 1.60 
PRICE_GEMINI_OUTPUT_1M = 2.50 

# Variáveis Globais
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
    if not dado: return "", ""
    try:
        obj = dado
        if isinstance(dado, str):
            dado = dado.strip()
            if dado.startswith("{") and dado.endswith("}"):
                obj = json.loads(dado)
            else:
                return dado, "" 

        if isinstance(obj, dict):
            msg = obj.get("IA_msgGPT") or obj.get("IA_msgGEM") or obj.get("IA_msgGem") or \
                  obj.get("IA_msgCliente") or obj.get("output") or obj.get("message") or \
                  obj.get("resumo") or str(obj)
            
            classe = obj.get("classificacao") or obj.get("classificacaoGPT") or \
                     obj.get("classificacaoGEM") or obj.get("classificacaoIAini") or ""
            
            return str(msg), str(classe)
    except: pass
    return str(dado), ""

def formatar_contexto_historico(historico):
    if not historico: return ""
    contexto = "--- HISTÓRICO RECENTE ---\n"
    for item in historico[-4:]:
        user_txt = item.get("user_simulado") or item.get("input")
        gpt_txt = item.get("gpt", {}).get("msg")
        gem_txt = item.get("gemini", {}).get("msg")
        contexto += f"Cliente: {user_txt}\n"
        if gpt_txt: contexto += f"Advogado GPT: {gpt_txt}\n"
        if gem_txt: contexto += f"Advogado Gemini: {gem_txt}\n"
        contexto += "-------------------------\n"
    return contexto

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

    # --- 🧹 CORREÇÃO DO RESET ---
    if user_input.strip().lower() == "reset":
        print("🗑️ Resetando memória...")
        
        # Usa .clear() para garantir que a lista GLOBAL seja esvaziada
        conversation_history.clear() 
        
        # Reseta custos
        session_costs["gpt_total"] = 0.0
        session_costs["gemini_total"] = 0.0
        
        # Avisa n8n (opcional, já que tiramos a memória de lá)
        try: requests.post(N8N_WEBHOOK_URL, json={"entrada": "reset"}, timeout=5)
        except: pass
        
        socketio.emit("resposta", {"status": "reset"})
        return jsonify({"status": "reset"})

    # --- TRAVA DE SEGURANÇA (Quem já acabou?) ---
    gpt_ja_acabou = False
    gem_ja_acabou = False
    gpt_msg_final = ""
    gpt_class_final = ""
    gem_msg_final = ""
    gem_class_final = ""

    termos_finais = ["qualificado", "desqualificado", "encerrar"]

    if conversation_history:
        ultimo = conversation_history[-1]
        
        # GPT
        if any(t in ultimo['gpt']['class'].lower() for t in termos_finais):
            gpt_ja_acabou = True
            gpt_msg_final = ultimo['gpt']['msg']
            gpt_class_final = ultimo['gpt']['class']

        # Gemini
        if any(t in ultimo['gemini']['class'].lower() for t in termos_finais):
            gem_ja_acabou = True
            gem_msg_final = ultimo['gemini']['msg']
            gem_class_final = ultimo['gemini']['class']

    # --- INJEÇÃO DE CONTEXTO ---
    contexto = formatar_contexto_historico(conversation_history)
    entrada_completa = f"{contexto}\n{user_input}" if contexto else user_input

    # Variáveis da Rodada Atual
    final_gpt_msg = gpt_msg_final
    final_gpt_class = gpt_class_final
    final_gem_msg = gem_msg_final
    final_gem_class = gem_class_final
    final_user_msg = ""
    resumo_encontrado = ""

    # Só chama n8n se alguém ainda estiver vivo
    if not (gpt_ja_acabou and gem_ja_acabou):
        try:
            resposta = requests.post(
                N8N_WEBHOOK_URL, 
                json={"entrada": entrada_completa, "user_type": user_type}, 
                timeout=90
            )
            resposta.raise_for_status()
            data = resposta.json()

            items_to_process = []
            if isinstance(data, list) and len(data) > 0:
                if "data" in data[0] and isinstance(data[0]["data"], list):
                    items_to_process = data[0]["data"]
                else:
                    items_to_process = data

            for item in items_to_process:
                out = item.get("output", item.get("json", item))
                
                # Só atualiza quem NÃO acabou
                if not gpt_ja_acabou:
                    g_msg, g_class = limpar_dado_json(out.get("IA_msgGPT"))
                    if g_msg and g_msg != "None": final_gpt_msg = g_msg
                    if g_class: final_gpt_class = g_class

                if not gem_ja_acabou:
                    gm_msg, gm_class = limpar_dado_json(out.get("IA_msgGEM") or out.get("IA_msgGem"))
                    if gm_msg and gm_msg != "None": final_gem_msg = gm_msg
                    if gm_class: final_gem_class = gm_class

                u_msg, _ = limpar_dado_json(out.get("IA_user"))
                if u_msg: final_user_msg = u_msg

                if "resumo" in out:
                    resumo_encontrado = out["resumo"]

        except Exception as e:
            print("❌ Erro n8n:", e)
            if not gpt_ja_acabou: final_gpt_msg = "Erro ao conectar"

    # --- INJEÇÃO DE RESUMO ---
    if resumo_encontrado and len(resumo_encontrado) > 10:
        msg_resumo = f"✅ **ANÁLISE FINAL DO CASO:**\n\n{resumo_encontrado}"
        
        # Aplica resumo se status for final
        if final_gpt_class and any(t in final_gpt_class.lower() for t in termos_finais):
            if "ANÁLISE FINAL" not in final_gpt_msg: final_gpt_msg = msg_resumo
            
        if final_gem_class and any(t in final_gem_class.lower() for t in termos_finais):
            if "ANÁLISE FINAL" not in final_gem_msg: final_gem_msg = msg_resumo

    # Normalização
    final_gpt_msg = normalizar_quebras(final_gpt_msg)
    final_gem_msg = normalizar_quebras(final_gem_msg)
    
    # Custos
    gpt_tokens = contar_tokens(final_gpt_msg) if not gpt_ja_acabou else 0
    gem_tokens = contar_tokens(final_gem_msg) if not gem_ja_acabou else 0
    
    custo_gpt = (gpt_tokens / 1_000_000) * PRICE_GPT_OUTPUT_1M
    custo_gem = (gem_tokens / 1_000_000) * PRICE_GEMINI_OUTPUT_1M
    
    session_costs["gpt_total"] += custo_gpt
    session_costs["gemini_total"] += custo_gem

    # Salva
    conversation_history.append({
        "timestamp": datetime.now().isoformat(),
        "loop": loop_count,
        "user_simulado": final_user_msg,
        "gpt": {"msg": final_gpt_msg, "class": final_gpt_class},
        "gemini": {"msg": final_gem_msg, "class": final_gem_class}
    })

    # Envia
    socketio.emit("resposta", {
        "user_type": user_type,
        "ai_user_msg": final_user_msg,
        "loop_count": loop_count,
        "gpt_msg": final_gpt_msg, "gpt_classificacao": final_gpt_class, 
        "gemini_msg": final_gem_msg, "gem_classificacao": final_gem_class, 
        "gpt_tokens": gpt_tokens, "gem_tokens": gem_tokens,
        "custo_run_gpt": custo_gpt, "custo_run_gem": custo_gem,
        "custo_total_gpt": session_costs["gpt_total"], "custo_total_gem": session_costs["gemini_total"]
    })
    
    socketio.sleep(0.2)

    # --- LOOP ---
    stop_loop = False
    
    s_gpt = final_gpt_class.lower()
    s_gem = final_gem_class.lower()
    
    alguem_vivo = "conversando" in s_gpt or "conversando" in s_gem
    
    if not alguem_vivo: stop_loop = True
    if loop_count >= MAX_AI_LOOPS: stop_loop = True

    if user_type == "ai_user" and not stop_loop:
        if final_user_msg:
            nova_entrada = gerar_entrada_ai_user(final_gpt_msg, final_gem_msg)
            socketio.start_background_task(continuar_loop, nova_entrada, loop_count + 1)
        else:
            # Fallback se não vier msg do user
            socketio.start_background_task(continuar_loop, "Continue a análise, por favor.", loop_count + 1)
    
    elif stop_loop:
        socketio.emit("aviso_sistema", {"msg": "🛑 Ciclo Encerrado."})

    return jsonify({"status": "ok"})

def continuar_loop(nova_entrada, loop_count):
    socketio.sleep(3)
    try: requests.post("http://127.0.0.1:5000/processar", json={"entrada": nova_entrada, "user_type": "ai_user", "loop_count": loop_count})
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
    except Exception as e: return jsonify({"status": "erro", "mensagem": str(e)})

@app.route("/start_ai_conversation", methods=["POST"])
def start_ai_conversation():
    socketio.start_background_task(requests.post, "http://127.0.0.1:5000/processar", json={"entrada": "Olá", "user_type": "ai_user", "loop_count": 0})
    return jsonify({"status": "started"})

if __name__ == "__main__":
    print("🚀 Servidor ON (Porta 5000)")
    socketio.run(app, debug=True, port=5000)