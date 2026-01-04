const socket = io(window.location.origin);

const chatBox = document.getElementById("messages");
const input = document.getElementById("mensagem");
const typingIndicator = document.getElementById("typing-indicator");
const sendButton = document.getElementById("enviarBtn");
const runsBody = document.getElementById("runs_body");

const GPT_LOGO = "/static/logos/gpt.svg";
const GEMINI_LOGO = "/static/logos/gemini.png";

// Estado Local
let totalGPT = 0;
let totalGem = 0;
let runCount = 0;
let conversation = [];

/* -----------------------------
   Helpers
----------------------------- */

// 🔥 ESSA FUNÇÃO FALTAVA E QUEBRAVA O CÓDIGO 🔥
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
  typingIndicator.classList.remove("visible", "hidden");
  typingIndicator.classList.add(show ? "visible" : "hidden");
}

/* -----------------------------
   Render UI
----------------------------- */

function renderUserMessage(text) {
  conversation.push({
    role: "user",
    message: text,
    timestamp: new Date().toISOString()
  });

  const wrapper = document.createElement("div");
  wrapper.classList.add("msg", "user");

  const bubble = document.createElement("div");
  bubble.classList.add("bubble", "user-bubble");
  bubble.textContent = text;

  wrapper.appendChild(bubble);
  chatBox.appendChild(wrapper);
  chatBox.scrollTop = chatBox.scrollHeight;
}

// Cria Coluna da IA
function makeIAColumn({ title, logoSrc, text, colClass, classificacao, tokens }) {
  // Salva histórico
  conversation.push({
    role: title.toLowerCase(),
    message: text,
    classificacao: classificacao || "",
    tokens: Number(tokens || 0),
    timestamp: new Date().toISOString()
  });

  const col = document.createElement("div");
  col.classList.add("col", colClass);

  // Lógica de Classes (Verde/Vermelho)
  let badgeClass = "status-badge"; 
  let statusIcon = "";

  if (classificacao) {
      const lowerClass = classificacao.toLowerCase();
      if (lowerClass.includes("desqualificado")) {
          badgeClass += " status-disqualified"; // Vermelho
          statusIcon = "⛔ ";
      } else if (lowerClass.includes("qualificado")) {
          badgeClass += " status-qualified"; // Verde
          statusIcon = "✅ ";
      }
  }

  const classPart = classificacao 
    ? `<span class="${badgeClass}">${statusIcon}${escapeHTML(classificacao)}</span>` 
    : "";

  col.innerHTML = `
    <div class="col-header">
      <div class="ia-header-group">
          <img src="${logoSrc}" class="ia-logo" alt="${title}">
          <div class="col-title">${escapeHTML(title)}</div>
      </div>
      ${classPart}
    </div>
    <div class="bubble ia-bubble">${formatTextForBubble(text)}</div>
  `;

  return col;
}

/* -----------------------------
   Envio (humano)
----------------------------- */

async function enviarMensagem() {
  const texto = input.value.trim();
  if (!texto) return;

  renderUserMessage(texto);
  input.value = "";
  input.focus();

  toggleTyping(true);
  sendButton.disabled = true;

  try {
    await fetch("/processar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entrada: texto }) // Removemos loop, enviamos simples
    });
  } catch (e) {
    console.error("Erro ao enviar:", e);
    toggleTyping(false);
    sendButton.disabled = false;
  }
}

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter") enviarMensagem();
});
sendButton.addEventListener("click", enviarMensagem);

/* -----------------------------
   Receber resposta (Socket)
----------------------------- */

socket.on("resposta", (data) => {
  console.log("📩 Recebido:", data);

  // 1. Verifica Reset
  if (data.status === "reset") {
      totalGPT = 0; totalGem = 0; runCount = 0;
      if(runsBody) runsBody.innerHTML = "";
      
      document.getElementById("gpt_tokens_cell").innerText = "0";
      document.getElementById("gem_tokens_cell").innerText = "0";
      
      document.getElementById("display-gpt-total").innerText = "US$ 0.00000";
      document.getElementById("display-gem-total").innerText = "US$ 0.00000";

      toggleTyping(false);
      sendButton.disabled = false;
      alert("Memória Limpa!");
      return;
  }

  // 2. Extrai dados
  const gptMsg = data.gpt_msg || "";
  const gemMsg = data.gemini_msg || "";
  const gptTokens = Number(data.gpt_tokens || 0);
  const gemTokens = Number(data.gem_tokens || 0);
  const gptClass = data.gpt_classificacao || "";
  const gemClass = data.gem_classificacao || "";

  // 3. Atualiza Totais
  totalGPT += gptTokens;
  totalGem += gemTokens;

  const gptCell = document.getElementById("gpt_tokens_cell");
  const gemCell = document.getElementById("gem_tokens_cell");
  if (gptCell) gptCell.textContent = totalGPT;
  if (gemCell) gemCell.textContent = totalGem;

  // 4. 🔥 ATUALIZAÇÃO DE CUSTOS 🔥
  if (data.custo_run_gpt !== undefined) {
      // GPT
      document.getElementById("display-gpt-run").innerText = fmtMoney(data.custo_run_gpt);
      document.getElementById("display-gpt-total").innerText = fmtMoney(data.custo_total_gpt);
      
      // Gemini
      document.getElementById("display-gem-run").innerText = fmtMoney(data.custo_run_gem);
      document.getElementById("display-gem-total").innerText = fmtMoney(data.custo_total_gem);
  }

  // 5. Atualiza tabela lateral (Runs)
  runCount++;
  if (runsBody) {
    const runRow = document.createElement("div");
    runRow.classList.add("run-row");
    runRow.innerHTML = `
      <span>${runCount}</span>
      <span>${gptTokens}</span>
      <span>${gemTokens}</span>
    `;
    runsBody.appendChild(runRow);
  }

  // 6. Renderiza Chat (2 colunas)
  const wrapper = document.createElement("div");
  wrapper.classList.add("dual-response");

  wrapper.appendChild(makeIAColumn({
    title: "GPT",
    logoSrc: GPT_LOGO,
    text: gptMsg,
    colClass: "gpt-box",
    classificacao: gptClass,
    tokens: gptTokens
  }));

  wrapper.appendChild(makeIAColumn({
    title: "Gemini",
    logoSrc: GEMINI_LOGO,
    text: gemMsg,
    colClass: "gemini-box",
    classificacao: gemClass,
    tokens: gemTokens
  }));

  chatBox.appendChild(wrapper);
  chatBox.scrollTop = chatBox.scrollHeight;

  toggleTyping(false);
  sendButton.disabled = false;
});

socket.on("connect", () => {
  console.log("✅ Conectado ao Flask via Socket.IO");
});

/* -----------------------------
   Salvar conversa
----------------------------- */
async function salvarConversaServidor() {
  const btn = document.querySelector("button[onclick='salvarConversaServidor()']");
  if(btn) { btn.innerText = "💾 Salvando..."; btn.disabled = true; }

  try {
    const res = await fetch("/salvar_conversa", { method: "POST" });
    const data = await res.json();
    if (data.status === "ok") {
      alert("✅ Salvo em:\n" + data.arquivo);
    } else {
      alert("❌ Erro: " + data.mensagem);
    }
  } catch (e) {
    console.error(e);
    alert("Erro de conexão.");
  } finally {
      if(btn) { btn.innerText = "💾 Salvar conversa (JSON)"; btn.disabled = false; }
  }
}
window.salvarConversaServidor = salvarConversaServidor;