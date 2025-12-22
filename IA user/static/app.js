const socket = io(window.location.origin);

const chatBox = document.getElementById("messages");
const input = document.getElementById("mensagem");

// --- Renderização de Mensagens ---
function renderUserMessage(text, origin = "👤") {
  const wrapper = document.createElement("div");
  wrapper.className = "msg user";
  const bubble = document.createElement("div");
  bubble.className = "bubble user-bubble";
  bubble.innerHTML = `<strong>${origin}</strong><br>${text}`;
  wrapper.appendChild(bubble);
  chatBox.appendChild(wrapper);
  chatBox.scrollTop = chatBox.scrollHeight;
}

function renderDualResponse({ gpt, gemini, from = "human", loop = 0 }) {
  const wrapper = document.createElement("div");
  wrapper.className = "dual-response";
  wrapper.innerHTML = `
    <div class="col gpt-box">
      <div class="col-header">🤖 GPT <span class="meta">${from === "ai_user" ? "Loop " + loop : ""}</span></div>
      <div class="bubble ia-bubble">${gpt}</div>
    </div>
    <div class="col gemini-box">
      <div class="col-header">🧠 Gemini</div>
      <div class="bubble ia-bubble">${gemini}</div>
    </div>
  `;
  chatBox.appendChild(wrapper);
  chatBox.scrollTop = chatBox.scrollHeight;
}

// ===============================
// 📊 ATUALIZAR PAINEL DE RUNS
// ===============================
function atualizarPainelRuns(loop, gptTok, gemTok) {
  const runsBody = document.getElementById("runs_body");
  
  const row = document.createElement("div");
  row.className = "run-row"; 
  row.style.display = "flex";
  row.style.justifyContent = "space-between";
  row.style.fontSize = "0.8em";
  row.style.marginBottom = "5px";
  
  row.innerHTML = `
    <span>#${loop}</span>
    <span style="color:#10a37f">${gptTok}</span>
    <span style="color:#4b8bf4">${gemTok}</span>
  `;
  
  runsBody.appendChild(row);
  runsBody.scrollTop = runsBody.scrollHeight; // Auto-scroll
}

// ===============================
// 💾 SALVAR JSON
// ===============================
function salvarConversaServidor() {
  const btn = document.querySelector("button[onclick='salvarConversaServidor()']");
  const textoOriginal = btn.innerText;
  
  btn.innerText = "💾 Salvando...";
  btn.disabled = true;

  fetch("/salvar_conversa", { 
    method: "POST",
    headers: { "Content-Type": "application/json" }
  })
  .then(response => response.json())
  .then(data => {
    if (data.status === "ok") {
      alert(`✅ Sucesso!\nArquivo salvo em: JSON_Conversas`);
      console.log("Arquivo:", data.arquivo);
    } else {
      alert("❌ Erro: " + data.mensagem);
    }
  })
  .catch(err => {
    console.error("Erro:", err);
    alert("Erro ao conectar com o servidor.");
  })
  .finally(() => {
    btn.innerText = textoOriginal;
    btn.disabled = false;
  });
}

// --- Envio de Mensagens ---
function enviarMensagem() {
  const texto = input.value.trim();
  if (!texto) return;
  
  renderUserMessage(texto, "👤 Você");
  input.value = "";
  
  fetch("/processar", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entrada: texto, user_type: "human" })
  }).catch(console.error);
}

// Listeners de Envio
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter") enviarMensagem();
});

// Botão de envio (caso não use o onclick no HTML)
document.getElementById("enviarBtn").addEventListener("click", enviarMensagem);

// Start Automático
window.onload = () => {
  fetch("/start_ai_conversation", { method: "POST" }).catch(console.error);
};

// ===============================
// 📩 SOCKET RECEBIDO (TUDO ACONTECE AQUI)
// ===============================
socket.on("resposta", (data) => {
  console.log("📩 DADOS:", data);

  // 1. Mensagem do User Simulado (se houver)
  if (data.user_type === "ai_user") {
    const texto = data.ai_user_msg || data.user_input;
    if (texto) renderUserMessage(texto, "👤 Cliente Simulado");
  }

  // 2. Respostas IAs
  renderDualResponse({
    gpt: data.gpt_msg || "",
    gemini: data.gemini_msg || "",
    from: data.user_type || "human",
    loop: data.loop_count ?? 0
  });

  // 3. Atualiza Tokens TOTAIS
  const gptCell = document.getElementById("gpt_tokens_cell");
  const gemCell = document.getElementById("gem_tokens_cell");
  if(gptCell) gptCell.innerText = (parseInt(gptCell.innerText) + (data.gpt_tokens || 0));
  if(gemCell) gemCell.innerText = (parseInt(gemCell.innerText) + (data.gem_tokens || 0));

  // 4. 🔥 ATUALIZA CUSTOS (NOVIDADE)
  if (data.custo_run_gpt !== undefined) {
    // GPT
    document.getElementById("display-gpt-run").innerText = 
      'US$ ' + parseFloat(data.custo_run_gpt).toFixed(5);
    document.getElementById("display-gpt-total").innerText = 
      'US$ ' + parseFloat(data.custo_total_gpt).toFixed(5);
    
    // Gemini
    document.getElementById("display-gem-run").innerText = 
      'US$ ' + parseFloat(data.custo_run_gem).toFixed(5);
    document.getElementById("display-gem-total").innerText = 
      'US$ ' + parseFloat(data.custo_total_gem).toFixed(5);
  }

  // 5. Atualiza lista de Runs
  atualizarPainelRuns(
    data.loop_count ?? 0, 
    data.gpt_tokens || 0, 
    data.gem_tokens || 0
  );
});

socket.on("connect", () => console.log("✅ Socket OK"));