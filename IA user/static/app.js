const socket = io(window.location.origin);
const chatBox = document.getElementById("messages");
const input = document.getElementById("mensagem");
const sendButton = document.getElementById("enviarBtn");
const runsBody = document.getElementById("runs_body");

const GPT_LOGO = "/static/logos/gpt.svg";
const GEMINI_LOGO = "/static/logos/gemini.png";
let totalGPT = 0, totalGem = 0, runCount = 0;

const fmtMoney = (val) => 'US$ ' + parseFloat(val || 0).toFixed(5);
const escapeHTML = (str) => String(str || "").replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[m]);
const formatText = (txt) => escapeHTML(txt).replace(/\n/g, "<br>");

function renderUserMessage(text, origin = "👤 Você") {
  const wrapper = document.createElement("div");
  wrapper.className = "msg user";
  const bubbleClass = origin.includes("Simulado") || origin.includes("Loop") ? "bubble user-bubble sim-bubble" : "bubble user-bubble";
  wrapper.innerHTML = `<div class="${bubbleClass}"><strong>${origin}</strong><br>${formatText(text)}</div>`;
  chatBox.appendChild(wrapper);
  chatBox.scrollTop = chatBox.scrollHeight;
}

function makeIAColumn({ title, logoSrc, text, colClass, classificacao, loopInfo }) {
  const col = document.createElement("div");
  col.className = `col ${colClass}`;

  let badgeClass = "status-badge"; 
  let statusIcon = "💬 ";

  if (classificacao) {
      const lower = classificacao.toLowerCase();
      if (lower.includes("desqualificado")) {
          badgeClass += " status-disqualified"; 
          statusIcon = "⛔ ";
      } else if (lower.includes("qualificado")) {
          badgeClass += " status-qualified";    
          statusIcon = "✅ ";
      }
  }

  const badgeHTML = classificacao ? `<span class="${badgeClass}">${statusIcon}${escapeHTML(classificacao)}</span>` : "";

  col.innerHTML = `
    <div class="col-header">
      <div class="ia-header-group">
          <img src="${logoSrc}" class="ia-logo">
          <div class="col-title">${title} <span class="meta">${loopInfo || ""}</span></div>
      </div>
      ${badgeHTML}
    </div>
    <div class="bubble ia-bubble">${formatText(text)}</div>
  `;
  return col;
}

async function enviarMensagem() {
  const texto = input.value.trim();
  if (!texto) return;
  renderUserMessage(texto);
  input.value = "";
  sendButton.disabled = true;
  try {
    await fetch("/processar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entrada: texto, user_type: "human" })
    });
  } catch (e) { console.error(e); sendButton.disabled = false; }
}

input.addEventListener("keydown", (e) => { if (e.key === "Enter") enviarMensagem(); });
sendButton.addEventListener("click", enviarMensagem);

socket.on("resposta", (data) => {
  if (data.status === "reset") { location.reload(); return; }

  if (data.user_type === "ai_user" && data.ai_user_msg) {
      renderUserMessage(data.ai_user_msg, `👤 Cliente Simulado (L${data.loop_count})`);
  }

  const wrapper = document.createElement("div");
  wrapper.className = "dual-response";
  
  wrapper.appendChild(makeIAColumn({
    title: "GPT", logoSrc: GPT_LOGO, text: data.gpt_msg, colClass: "gpt-box",
    classificacao: data.gpt_classificacao, loopInfo: `(L${data.loop_count})`
  }));
  
  wrapper.appendChild(makeIAColumn({
    title: "Gemini", logoSrc: GEMINI_LOGO, text: data.gemini_msg, colClass: "gemini-box",
    classificacao: data.gem_classificacao, loopInfo: ""
  }));
  
  chatBox.appendChild(wrapper);
  chatBox.scrollTop = chatBox.scrollHeight;

  totalGPT += (data.gpt_tokens || 0);
  totalGem += (data.gem_tokens || 0);
  
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

  if(runsBody) {
      runCount++;
      const runRow = document.createElement("div");
      runRow.className = "run-row";
      runRow.innerHTML = `<span>#${data.loop_count}</span><span>${data.gpt_tokens}</span><span>${data.gem_tokens}</span>`;
      runsBody.appendChild(runRow);
      runsBody.scrollTop = runsBody.scrollHeight;
  }
  sendButton.disabled = false;
});

socket.on("aviso_sistema", (data) => {
    const div = document.createElement("div");
    div.style.textAlign = "center"; div.style.color = "#ef4444"; div.style.margin = "15px"; div.style.fontWeight = "bold";
    div.innerHTML = data.msg;
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
});

window.salvarConversaServidor = async () => {
  const btn = document.querySelector(".btn-save");
  if(btn) btn.innerText = "💾 ...";
  try {
    const res = await fetch("/salvar_conversa", { method: "POST" });
    const data = await res.json();
    alert(data.status === "ok" ? "✅ Salvo: " + data.arquivo : "❌ " + data.mensagem);
  } catch(e) { alert("Erro conexão"); }
  if(btn) btn.innerText = "💾 Salvar JSON";
};

window.onload = () => { fetch("/start_ai_conversation", { method: "POST" }).catch(console.error); };