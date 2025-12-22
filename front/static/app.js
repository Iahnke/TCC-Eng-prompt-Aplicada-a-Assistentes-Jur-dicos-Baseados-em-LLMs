const socket = io(window.location.origin);

const chatBox = document.getElementById("messages");
const input = document.getElementById("mensagem");
const typingIndicator = document.getElementById("typing-indicator");
const sendButton = document.getElementById("enviarBtn");
const runsBody = document.getElementById("runs_body");

const GPT_LOGO = "/static/logos/gpt.svg";
const GEMINI_LOGO = "/static/logos/gemini.png";

// Estado
let totalGPT = 0;
let totalGem = 0;
let runCount = 0;

// 🔥 Histórico completo da conversa (para JSON)
let conversation = [];

function toggleTyping(show) {
  typingIndicator.classList.remove("visible", "hidden");
  typingIndicator.classList.add(show ? "visible" : "hidden");
}

function renderUserMessage(text) {
  conversation.push({ role: "user", message: text });

  const wrapper = document.createElement("div");
  wrapper.classList.add("msg", "user");

  const bubble = document.createElement("div");
  bubble.classList.add("bubble", "user-bubble");
  bubble.textContent = text;

  wrapper.appendChild(bubble);
  chatBox.appendChild(wrapper);
  chatBox.scrollTop = chatBox.scrollHeight;
}

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
      body: JSON.stringify({ entrada: texto })
    });
  } catch {
    toggleTyping(false);
    sendButton.disabled = false;
  }
}

input.addEventListener("keydown", e => {
  if (e.key === "Enter") enviarMensagem();
});

sendButton.addEventListener("click", enviarMensagem);

function makeIAColumn({ title, logoSrc, text, colClass }) {
  conversation.push({ role: title.toLowerCase(), message: text });

  const col = document.createElement("div");
  col.classList.add("col", colClass);

  col.innerHTML = `
    <div class="col-header">
      <img src="${logoSrc}" class="ia-logo">
      <div class="col-title">${title}</div>
    </div>
    <div class="bubble ia-bubble">${text}</div>
  `;
  return col;
}

socket.on("resposta", (data) => {
  const gptMsg = data.gpt_msg || "";
  const gemMsg = data.gemini_msg || "";

  const gptTokens = Number(data.gpt_tokens || 0);
  const gemTokens = Number(data.gem_tokens || 0);

  // Totais
  totalGPT += gptTokens;
  totalGem += gemTokens;
  document.getElementById("gpt_tokens_cell").textContent = totalGPT;
  document.getElementById("gem_tokens_cell").textContent = totalGem;

  // Runs
  runCount++;
  const runRow = document.createElement("div");
  runRow.classList.add("run-row");
  runRow.innerHTML = `
    <span>${runCount}</span>
    <span>${gptTokens}</span>
    <span>${gemTokens}</span>
  `;
  runsBody.appendChild(runRow);

  // Chat
  const wrapper = document.createElement("div");
  wrapper.classList.add("dual-response");

  wrapper.appendChild(makeIAColumn({
    title: "GPT",
    logoSrc: GPT_LOGO,
    text: gptMsg,
    colClass: "gpt-box"
  }));

  wrapper.appendChild(makeIAColumn({
    title: "Gemini",
    logoSrc: GEMINI_LOGO,
    text: gemMsg,
    colClass: "gemini-box"
  }));

  chatBox.appendChild(wrapper);
  chatBox.scrollTop = chatBox.scrollHeight;


  toggleTyping(false);
  sendButton.disabled = false;
});

async function salvarConversaServidor() {
  const res = await fetch("/salvar_conversa", {
    method: "POST"
  });
  const data = await res.json();

  if (data.status === "ok") {
    alert("Conversa salva em:\n" + data.arquivo);
  } else {
    alert("Erro ao salvar JSON");
  }
}
