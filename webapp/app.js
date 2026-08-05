const ORG_URL = "/company/org.json";
const STATUS_URL = "/company/status.json";
const POLL_INTERVAL_MS = 4000;

const AVATAR_POOL = ["🧑‍💼", "👩‍💼", "🧑‍💻", "👨‍💻", "🧑‍🔧", "👩‍🔧", "🧑‍🎨", "👩‍🎨"];

const connStatusEl = document.getElementById("conn-status");
const lastUpdatedEl = document.getElementById("last-updated");
const officeEl = document.getElementById("office");
const orgChainEl = document.getElementById("org-chain");

let org = null;
let avatarAssignments = {};

function assignAvatar(id) {
  if (!avatarAssignments[id]) {
    const idx = Object.keys(avatarAssignments).length % AVATAR_POOL.length;
    avatarAssignments[id] = AVATAR_POOL[idx];
  }
  return avatarAssignments[id];
}

function renderOrgChain(orgData) {
  orgChainEl.innerHTML = "";
  const ceoNode = document.createElement("div");
  ceoNode.className = "org-node";
  ceoNode.innerHTML = `<div class="role">${orgData.ceo.title}</div><div class="name">${orgData.ceo.name}</div>`;

  const arrow = document.createElement("div");
  arrow.className = "org-arrow";
  arrow.textContent = "→";

  const hqNode = document.createElement("div");
  hqNode.className = "org-node";
  hqNode.innerHTML = `<div class="role">${orgData.hq.title}</div><div class="name">${orgData.hq.name}</div>`;

  const arrow2 = document.createElement("div");
  arrow2.className = "org-arrow";
  arrow2.textContent = "→";

  const deptsLabel = document.createElement("div");
  deptsLabel.className = "org-node";
  deptsLabel.innerHTML = `<div class="role">部門</div><div class="name">${orgData.departments.length}部門</div>`;

  orgChainEl.append(ceoNode, arrow, hqNode, arrow2, deptsLabel);
}

function renderOffice(orgData) {
  officeEl.innerHTML = "";
  orgData.departments.forEach((dept) => {
    officeEl.appendChild(buildRoom(dept));
  });
}

function buildRoom(dept) {
  const room = document.createElement("div");
  room.className = `room ${dept.status === "active" ? "active" : "planned"}`;
  room.dataset.deptId = dept.id;

  const header = document.createElement("div");
  header.className = "room-header";
  const headName = dept.head ? dept.head.name : "";
  header.innerHTML = `<span>${dept.name}</span><span class="head-name">${headName}</span>`;
  room.appendChild(header);

  const floor = document.createElement("div");
  floor.className = "room-floor";

  if (dept.status !== "active") {
    const overlay = document.createElement("div");
    overlay.className = "locked-overlay";
    const roles = [dept.head, ...dept.members].filter(Boolean).map((m) => m.name).join(" / ");
    overlay.innerHTML = `<div class="lock-icon">🔒</div><div>近日開設</div><div class="planned-roles">${roles}</div>`;
    floor.appendChild(overlay);
  } else {
    dept.members.forEach((member) => {
      floor.appendChild(buildCharacter(member));
    });
  }

  room.appendChild(floor);
  return room;
}

function buildCharacter(member) {
  const el = document.createElement("div");
  el.className = "character idle";
  el.dataset.agentId = member.id;
  el.style.left = `${10 + Math.random() * 70}%`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = "";

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = assignAvatar(member.id);

  const label = document.createElement("div");
  label.className = "label";
  label.textContent = member.name;

  el.append(bubble, avatar, label);
  return el;
}

function wander() {
  document.querySelectorAll(".character").forEach((el) => {
    const roomWidth = el.parentElement.getBoundingClientRect().width;
    const charWidth = el.getBoundingClientRect().width || 40;
    const maxLeftPercent = Math.max(5, 100 - (charWidth / roomWidth) * 100 - 5);
    const newLeft = 5 + Math.random() * maxLeftPercent;
    el.style.left = `${newLeft}%`;
  });
}

function applyStatus(statusData) {
  document.querySelectorAll(".character").forEach((el) => {
    const id = el.dataset.agentId;
    const info = statusData[id];
    const bubble = el.querySelector(".bubble");
    if (info && info.status === "working") {
      el.classList.remove("idle");
      el.classList.add("working");
      bubble.textContent = info.task || "作業中...";
    } else {
      el.classList.remove("working");
      el.classList.add("idle");
      bubble.textContent = "";
    }
  });
}

async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

async function pollStatus() {
  try {
    const statusData = await fetchJson(STATUS_URL);
    applyStatus(statusData);
    connStatusEl.textContent = "接続中";
    lastUpdatedEl.textContent = `最終更新: ${new Date().toLocaleTimeString("ja-JP")}`;
  } catch (err) {
    connStatusEl.textContent = "company/status.json を取得できません(サーバー経由で開いていますか？)";
  }
}

async function init() {
  try {
    org = await fetchJson(ORG_URL);
    document.getElementById("company-name").textContent = org.company_name;
    document.title = `${org.company_name} - オフィスビュー`;
    renderOrgChain(org);
    renderOffice(org);
    connStatusEl.textContent = "組織データを読み込みました";
    await pollStatus();
    setInterval(pollStatus, POLL_INTERVAL_MS);
    setInterval(wander, 3000);
  } catch (err) {
    officeEl.innerHTML = `<p class="loading">company/org.json を読み込めませんでした。webapp/README.md の手順に従ってローカルサーバー経由で開いてください。</p>`;
    connStatusEl.textContent = "読み込み失敗";
  }
}

init();
