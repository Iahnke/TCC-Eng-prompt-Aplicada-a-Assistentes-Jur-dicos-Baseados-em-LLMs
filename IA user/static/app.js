const socket = io(window.location.origin);

const chatBox = document.getElementById("messages");
const input = document.getElementById("mensagem");
const typingIndicator = document.getElementById("typing-indicator");
const sendButton = document.getElementById("enviarBtn");
const runsBody = document.getElementById("runs_body");

const GPT_LOGO = "/static/logos/gpt.svg";
const GEMINI_LOGO = "/static/logos/gemini.png";

let totalGPT = 0, totalGem = 0, runCount = 0;

// --- Helpers ---
const fmtMoney = (val) => 'US$ ' + parseFloat(val || 0).toFixed(5);

function escapeHTML(str) {
  if (!str) return "";
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTextForBubble(text) {
  const safe = escapeHTML(text || "");
  return safe.replaceAll("\n", "<br>");
}

function toggleTyping(show) {
  if(!typingIndicator) return;
  if (show) typingIndicator.classList.remove("hidden");
  else typingIndicator.classList.add("hidden");
}

// --- Renderização ---

function renderUserMessage(text, origin = "👤 Você") {
  const wrapper = document.createElement("div");
  wrapper.className = "msg user";
  
  // Se for simulado, usa estilo diferente
  const bubbleClass = origin.includes("Simulado") || origin.includes("Loop") 
    ? "bubble user-bubble sim-bubble" 
    : "bubble user-bubble";

  wrapper.innerHTML = `<div class="${bubbleClass}"><strong>${origin}</strong><br>${formatTextForBubble(text)}</div>`;
  chatBox.appendChild(wrapper);
  chatBox.scrollTop = chatBox.scrollHeight;
}

// Cria Coluna da IA com Badge
function makeIAColumn({ title, logoSrc, text, colClass, classificacao, tokens, loopInfo }) {
  const col = document.createElement("div");
  col.className = `col ${colClass}`;

  // Lógica de Cores da Badge
  let badgeClass = "status-badge"; 
  let statusIcon = "💬 "; // Ícone padrão

  if (classificacao) {
      const lower = classificacao.toLowerCase();
      if (lower.includes("desqualificado")) {
          badgeClass += " status-disqualified"; // Vermelho
          statusIcon = "⛔ ";
      } else if (lower.includes("qualificado")) {
          badgeClass += " status-qualified"; // Verde
          statusIcon = "✅ ";
      }
  }

  const badgeHTML = classificacao 
    ? `<span class="${badgeClass}">${statusIcon}${escapeHTML(classificacao)}</span>` 
    : "";

  col.innerHTML = `
    <div class="col-header">
      <div class="ia-header-group">
          <img src="${logoSrc}" class="ia-logo" alt="${title}">
          <div class="col-title">${title} <span class="meta">${loopInfo || ""}</span></div>
      </div>
      ${badgeHTML}
    </div>
    <div class="bubble ia-bubble">${formatTextForBubble(text)}</div>
  `;
  return col;
}

// --- Envio ---
async function enviarMensagem() {
  const texto = input.value.trim();
  if (!texto) return;

  renderUserMessage(texto);
  input.value = "";
  toggleTyping(true);
  sendButton.disabled = true;

  try {
    // Envia como humano (não inicia loop forçado, o backend decide ou usa botão Start)
    await fetch("/processar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entrada: texto, user_type: "human" })
    });
  } catch (e) {
    console.error(e);
    toggleTyping(false);
    sendButton.disabled = false;
  }
}

input.addEventListener("keydown", (e) => { if (e.key === "Enter") enviarMensagem(); });
sendButton.addEventListener("click", enviarMensagem);

// --- Socket ---
socket.on("resposta", (data) => {
  if (data.status === "reset") { location.reload(); return; }

  // 1. Mensagem Cliente Simulado (limpa)
  if (data.user_type === "ai_user") {
      const texto = data.ai_user_msg || data.user_input;
      if (texto) renderUserMessage(texto, `👤 Cliente Simulado (L${data.loop_count})`);
  }

  // 2. Atualiza Tokens e Custos
  totalGPT += (data.gpt_tokens || 0);
  totalGem += (data.gem_tokens || 0);
  
  // Atualiza painel lateral
  if(document.getElementById("gpt_tokens_cell")) {
      document.getElementById("gpt_tokens_cell").textContent = totalGPT;
      document.getElementById("gem_tokens_cell").textContent = totalGem;
      
      if (data.custo_run_gpt !== undefined) {
        document.getElementById("display-gpt-run").innerText = fmtMoney(data.custo_run_gpt);
        document.getElementById("display-gpt-total").innerText = fmtMoney(data.custo_total_gpt);
        document.getElementById("display-gem-run").innerText = fmtMoney(data.custo_run_gem);
        document.getElementById("display-gem-total").innerText = fmtMoney(data.custo_total_gem);
      }
  }

  // 3. Renderiza Chat IA
  const wrapper = document.createElement("div");
  wrapper.className = "dual-response";
  
  wrapper.appendChild(makeIAColumn({
    title: "GPT", logoSrc: GPT_LOGO, text: data.gpt_msg, colClass: "gpt-box",
    classificacao: data.gpt_classificacao, tokens: data.gpt_tokens, loopInfo: `(L${data.loop_count})`
  }));
  
  wrapper.appendChild(makeIAColumn({
    title: "Gemini", logoSrc: GEMINI_LOGO, text: data.gemini_msg, colClass: "gemini-box",
    classificacao: data.gem_classificacao, tokens: data.gem_tokens
  }));
  
  chatBox.appendChild(wrapper);
  chatBox.scrollTop = chatBox.scrollHeight;
  
  // 4. Lista de Runs
  if(runsBody) {
      runCount++;
      const runRow = document.createElement("div");
      runRow.className = "run-row";
      runRow.innerHTML = `<span>#${data.loop_count}</span><span>${data.gpt_tokens}</span><span>${data.gem_tokens}</span>`;
      runsBody.appendChild(runRow);
  }

  toggleTyping(false);
  sendButton.disabled = false;
});

socket.on("aviso_sistema", (data) => {
    const div = document.createElement("div");
    div.style.textAlign = "center"; div.style.color = "#ef4444"; div.style.margin = "15px"; div.style.fontWeight = "bold";
    div.innerHTML = data.msg;
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
});

// Botão Salvar
window.salvarConversaServidor = async () => {
  const btn = document.querySelector(".btn-save");
  if(btn) btn.innerText = "💾 ...";
  try {
    const res = await fetch("/salvar_conversa", { method: "POST" });
    const data = await res.json();
    if(data.status === "ok") alert("Salvo: " + data.arquivo);
    else alert("Erro: " + data.mensagem);
  } catch(e) { alert("Erro conexão"); }
  if(btn) btn.innerText = "💾 Salvar JSON";
};

// Start Auto (opcional)
window.onload = () => {
   fetch("/start_ai_conversation", { method: "POST" }).catch(console.error);
};