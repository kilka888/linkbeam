(() => {
  "use strict";

  const state = {
    me: null,
    peers: [],
    selectedPeer: null,
    knownIncoming: new Set(),
    knownTransfers: new Set(),
    historyFilter: "all",
  };

  const $ = (sel) => document.querySelector(sel);

  const radarBlips = $("#radarBlips");
  const emptyHint = $("#emptyHint");
  const scanStatus = $("#scanStatus");
  const selectedPeerLabel = $("#selectedPeerLabel");
  const dropzone = $("#dropzone");
  const fileInput = $("#fileInput");
  const transfersEl = $("#transfers");
  const incomingStack = $("#incomingStack");
  const historyList = $("#historyList");

  // ------------------------------------------------------------- helpers --

  function formatBytes(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0, v = bytes;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
  }

  function osIcon(os) {
    const o = (os || "").toLowerCase();
    if (o.includes("windows")) return "🪟";
    if (o.includes("darwin") || o.includes("mac")) return "🍎";
    if (o.includes("linux")) return "🐧";
    return "💻";
  }

  function timeAgo(ts) {
    const diff = Date.now() / 1000 - ts;
    if (diff < 60) return "только что";
    if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`;
    return new Date(ts * 1000).toLocaleDateString();
  }

  async function api(path, options = {}) {
    const res = await fetch(path, options);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error || `${res.status}`);
    }
    return res.json();
  }

  // ------------------------------------------------------------- identity --

  async function loadMe() {
    state.me = await api("/api/me");
    $("#deviceName").textContent = state.me.name;
    $("#deviceId").textContent = `${osIcon(state.me.os)} ${state.me.id}`;
    $("#autoAccept").checked = !!state.me.auto_accept;
  }

  $("#editNameBtn").addEventListener("click", async () => {
    const name = prompt("Как это устройство должно называться для других?", state.me.name);
    if (!name || !name.trim()) return;
    await api("/api/me", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });
    await loadMe();
  });

  $("#autoAccept").addEventListener("change", async (e) => {
    await api("/api/me", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_accept: e.target.checked }),
    });
  });

  $("#openFolderBtn").addEventListener("click", () => {
    api("/api/me/open-folder", { method: "POST" }).catch(() => {});
  });

  // ----------------------------------------------------------------- radar --

  function renderPeers(peers) {
    state.peers = peers;
    radarBlips.innerHTML = "";
    emptyHint.style.display = peers.length ? "none" : "block";
    scanStatus.textContent = peers.length ? `найдено: ${peers.length}` : "поиск…";

    const radius = 42; // percent of radar box
    const tpl = $("#blipTemplate");

    peers.forEach((peer, i) => {
      const angle = (2 * Math.PI * i) / Math.max(peers.length, 1) - Math.PI / 2;
      const x = 50 + radius * Math.cos(angle);
      const y = 50 + radius * Math.sin(angle);

      const node = tpl.content.firstElementChild.cloneNode(true);
      node.style.left = `${x}%`;
      node.style.top = `${y}%`;
      node.querySelector(".blip-name").textContent = `${osIcon(peer.os)} ${peer.name}`;
      if (state.selectedPeer && state.selectedPeer.id === peer.id) {
        node.classList.add("selected");
      }
      node.addEventListener("click", () => selectPeer(peer));
      radarBlips.appendChild(node);
    });
  }

  function selectPeer(peer) {
    state.selectedPeer = peer;
    selectedPeerLabel.textContent = `${osIcon(peer.os)} ${peer.name}`;
    selectedPeerLabel.classList.add("active");
    renderPeers(state.peers);
  }

  async function pollPeers() {
    try {
      const peers = await api("/api/peers");
      renderPeers(peers);
    } catch (e) { /* ignore transient errors */ }
  }

  $("#manualPeerForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const address = $("#manualAddress").value.trim();
    const port = parseInt($("#manualPort").value, 10);
    if (!address || !port) return;
    try {
      const peer = await api("/api/peers/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ address, port }),
      });
      const exists = state.peers.find((p) => p.id === peer.id);
      if (!exists) state.peers.push(peer);
      renderPeers(state.peers);
      selectPeer(peer);
      e.target.reset();
    } catch (err) {
      alert(`Не удалось подключиться: ${err.message}`);
    }
  });

  // ------------------------------------------------------------ sending --

  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) sendFile(fileInput.files[0]);
    fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("dragover"); })
  );
  ["dragleave", "drop"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("dragover"); })
  );
  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) sendFile(file);
  });

  async function sendFile(file) {
    if (!state.selectedPeer) {
      alert("Сначала выбери устройство в списке слева.");
      return;
    }
    const peer = state.selectedPeer;
    const form = new FormData();
    form.append("file", file);
    form.append("peer_id", peer.id);
    form.append("address", peer.address);
    form.append("port", peer.port);
    form.append("peer_name", peer.name);

    const row = addTransferRow(file.name, peer.name, "ожидание подтверждения…");

    try {
      const { send_id } = await api("/api/send", { method: "POST", body: form });
      pollSend(send_id, row);
    } catch (err) {
      updateTransferRow(row, 0, `ошибка: ${err.message}`, "error");
    }
  }

  function addTransferRow(filename, peerName, statusText) {
    const tpl = $("#transferTemplate");
    const node = tpl.content.firstElementChild.cloneNode(true);
    node.querySelector(".transfer-filename").textContent = filename;
    node.querySelector(".transfer-peer").textContent = `→ ${peerName}`;
    node.querySelector(".transfer-status").textContent = statusText;
    transfersEl.prepend(node);
    return node;
  }

  function updateTransferRow(row, progress, statusText, cls) {
    row.querySelector(".transfer-bar-fill").style.width = `${progress}%`;
    const statusEl = row.querySelector(".transfer-status");
    statusEl.textContent = statusText;
    statusEl.className = `transfer-status ${cls || ""}`;
  }

  async function pollSend(sendId, row) {
    try {
      const s = await api(`/api/send/status/${sendId}`);
      if (s.status === "waiting_accept") {
        updateTransferRow(row, 0, "ожидание подтверждения…");
        setTimeout(() => pollSend(sendId, row), 800);
      } else if (s.status === "sending") {
        updateTransferRow(row, s.progress, `${s.progress}%`);
        setTimeout(() => pollSend(sendId, row), 400);
      } else if (s.status === "completed") {
        updateTransferRow(row, 100, "готово ✓");
        loadHistory();
      } else if (s.status === "rejected") {
        updateTransferRow(row, 0, "отклонено", "rejected");
        loadHistory();
      } else if (s.status === "timeout") {
        updateTransferRow(row, 0, "нет ответа", "error");
        loadHistory();
      } else {
        updateTransferRow(row, 0, s.error || "ошибка", "error");
        loadHistory();
      }
    } catch (e) {
      updateTransferRow(row, 0, "ошибка связи", "error");
    }
  }

  // ------------------------------------------------------------ incoming --

  async function pollPending() {
    try {
      const pending = await api("/api/pending");
      const ids = new Set(pending.map((p) => p.transfer_id));

      // remove cards for requests that no longer exist (accepted/expired elsewhere)
      [...incomingStack.children].forEach((card) => {
        if (!ids.has(card.dataset.id)) card.remove();
      });

      pending.forEach((req) => {
        if (state.knownIncoming.has(req.transfer_id)) return;
        state.knownIncoming.add(req.transfer_id);
        addIncomingCard(req);
      });
    } catch (e) { /* ignore */ }
  }

  function addIncomingCard(req) {
    const tpl = $("#incomingTemplate");
    const node = tpl.content.firstElementChild.cloneNode(true);
    node.dataset.id = req.transfer_id;
    node.querySelector(".incoming-title").textContent = `${req.sender_name} хочет отправить файл`;
    node.querySelector(".incoming-code").textContent = `#${req.code}`;
    node.querySelector(".incoming-detail").textContent = `${req.filename} · ${formatBytes(req.size)}`;

    node.querySelector(".btn-accept").addEventListener("click", async () => {
      await api(`/api/pending/${req.transfer_id}/accept`, { method: "POST" });
      node.remove();
    });
    node.querySelector(".btn-decline").addEventListener("click", async () => {
      await api(`/api/pending/${req.transfer_id}/reject`, { method: "POST" });
      node.remove();
    });

    incomingStack.appendChild(node);
  }

  // ------------------------------------------------------------- history --

  $("#historyTabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab");
    if (!btn) return;
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    btn.classList.add("active");
    state.historyFilter = btn.dataset.filter;
    renderHistory(state._history || []);
  });

  $("#clearHistoryBtn").addEventListener("click", async () => {
    if (!confirm("Очистить всю историю передач?")) return;
    await api("/api/history/clear", { method: "POST" });
    loadHistory();
  });

  async function loadHistory() {
    const items = await api("/api/history");
    state._history = items;
    renderHistory(items);
  }

  function renderHistory(items) {
    const filtered = items.filter((it) => state.historyFilter === "all" || it.direction === state.historyFilter);
    historyList.innerHTML = "";
    if (!filtered.length) {
      historyList.innerHTML = `<p class="history-empty">Пока пусто. Как только файл будет отправлен или получен — он появится здесь.</p>`;
      return;
    }
    const tpl = $("#historyItemTemplate");
    filtered.forEach((it) => {
      const node = tpl.content.firstElementChild.cloneNode(true);
      node.querySelector(".history-icon").textContent = it.direction === "sent" ? "↑" : "↓";
      node.querySelector(".history-filename").textContent = it.filename;
      const statusTxt = it.status !== "completed" ? ` · ${it.status}` : "";
      node.querySelector(".history-meta").textContent =
        `${it.direction === "sent" ? "кому: " : "от: "}${it.peer} · ${formatBytes(it.size)}${statusTxt}`;
      node.querySelector(".history-time").textContent = timeAgo(it.timestamp);
      historyList.appendChild(node);
    });
  }

  // --------------------------------------------------------------- init --

  async function init() {
    await loadMe();
    await pollPeers();
    await loadHistory();
    setInterval(pollPeers, 3000);
    setInterval(pollPending, 1500);
    pollPending();
  }

  init();
})();
