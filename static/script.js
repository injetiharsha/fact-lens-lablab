const AGENTS = [
  "System",
  "Intake Agent",
  "Web Research Agent",
  "Primary Source Agent",
  "Skeptic Agent",
  "Rebuttal Research Agent",
  "Source Quality Agent",
  "Consensus Moderator Agent",
];

const agentsEl = document.querySelector("#agents");
const resultEl = document.querySelector("#result");
const sourcesPanel = document.querySelector("#sources-panel");
const sourcesEl = document.querySelector("#sources");
const form = document.querySelector("#verify-form");
const submit = document.querySelector("#submit");

const cards = new Map();

function initCards() {
  agentsEl.innerHTML = "";
  cards.clear();
  AGENTS.forEach((agent) => {
    const card = document.createElement("article");
    card.className = "agent-card";
    card.innerHTML = `
      <span class="status">waiting</span>
      <h3>${agent}</h3>
      <p>Ready.</p>
    `;
    agentsEl.appendChild(card);
    cards.set(agent, card);
  });
}

function updateCard(event) {
  const card = cards.get(event.agent) || cards.get("System");
  const status = card.querySelector(".status");
  const body = card.querySelector("p");
  status.textContent = event.status;
  status.className = `status ${event.status}`;
  body.textContent = event.message;
}

function renderResult(data) {
  resultEl.classList.remove("hidden");
  document.querySelector("#verdict").textContent = data.verdict.replaceAll("_", " ");
  document.querySelector("#confidence").textContent = `${data.confidence}%`;
  document.querySelector("#explanation").textContent = `${data.final_explanation} ${data.recommendation}`;
  resultEl.dataset.verdict = data.verdict;

  sourcesEl.innerHTML = "";
  (data.sources || []).forEach((source) => {
    const row = document.createElement("div");
    row.className = "source";
    row.innerHTML = `
      <a href="${source.url}" target="_blank" rel="noreferrer">${source.title}</a>
      <p>${source.snippet}</p>
      <p>Source type: ${source.source_type} | Credibility: ${source.credibility}/100</p>
    `;
    sourcesEl.appendChild(row);
  });
  sourcesPanel.classList.toggle("hidden", !(data.sources || []).length);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  initCards();
  resultEl.classList.add("hidden");
  sourcesPanel.classList.add("hidden");
  submit.disabled = true;
  submit.textContent = "Running";

  const body = new FormData();
  const file = document.querySelector("#file").files[0];
  body.append("text", document.querySelector("#claim").value);
  body.append("input_type", file ? file.type || file.name.split(".").pop() : "text");
  if (file) body.append("file", file);

  try {
    const response = await fetch("/api/verify", { method: "POST", body });
    const data = await response.json();
    (data.events || []).forEach(updateCard);
    renderResult(data);
  } catch (err) {
    updateCard({ agent: "System", status: "failed", message: err.message || "Request failed" });
  } finally {
    submit.disabled = false;
    submit.textContent = "Run Crew";
  }
});

initCards();
