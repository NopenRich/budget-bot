// ===================== IndexedDB слой =====================
const DB_NAME = "budget-db";
const DB_VERSION = 1;
let db;

const DEFAULT_CATEGORIES = [
  { name: "Еда", type: "expense" },
  { name: "Транспорт", type: "expense" },
  { name: "Жильё", type: "expense" },
  { name: "Здоровье", type: "expense" },
  { name: "Развлечения", type: "expense" },
  { name: "Одежда", type: "expense" },
  { name: "Связь/интернет", type: "expense" },
  { name: "Прочее", type: "expense" },
  { name: "Зарплата", type: "income" },
  { name: "Подработка", type: "income" },
  { name: "Подарки", type: "income" },
  { name: "Прочее", type: "income" },
];

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const _db = e.target.result;
      if (!_db.objectStoreNames.contains("records")) {
        const store = _db.createObjectStore("records", { keyPath: "client_id" });
        store.createIndex("record_date", "record_date");
        store.createIndex("synced", "synced");
      }
      if (!_db.objectStoreNames.contains("categories")) {
        _db.createObjectStore("categories", { keyPath: "key" });
      }
      if (!_db.objectStoreNames.contains("meta")) {
        _db.createObjectStore("meta", { keyPath: "key" });
      }
    };
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

function tx(storeName, mode = "readonly") {
  return db.transaction(storeName, mode).objectStore(storeName);
}

function idbGetAll(storeName) {
  return new Promise((resolve, reject) => {
    const req = tx(storeName).getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function idbPut(storeName, value) {
  return new Promise((resolve, reject) => {
    const req = tx(storeName, "readwrite").put(value);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function idbGet(storeName, key) {
  return new Promise((resolve, reject) => {
    const req = tx(storeName).get(key);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function ensureDefaultCategories() {
  const existing = await idbGetAll("categories");
  if (existing.length > 0) return;
  for (const c of DEFAULT_CATEGORIES) {
    await idbPut("categories", { key: `${c.type}:${c.name}`, ...c });
  }
}

async function getSettings() {
  const url = await idbGet("meta", "backend_url");
  const tgid = await idbGet("meta", "telegram_id");
  const token = await idbGet("meta", "token");
  const lastSync = await idbGet("meta", "last_sync");
  return {
    backend_url: url?.value || "",
    telegram_id: tgid?.value || "",
    token: token?.value || "",
    last_sync: lastSync?.value || "1970-01-01T00:00:00",
  };
}

async function saveSettings(backend_url, telegram_id, token) {
  await idbPut("meta", { key: "backend_url", value: backend_url });
  await idbPut("meta", { key: "telegram_id", value: telegram_id });
  await idbPut("meta", { key: "token", value: token });
}

// ===================== Состояние формы добавления =====================
let selectedType = "expense";
let selectedCategory = null;

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

async function renderCategoryChips() {
  const container = document.getElementById("category-chips");
  const all = await idbGetAll("categories");
  const filtered = all.filter((c) => c.type === selectedType);
  container.innerHTML = "";
  filtered.forEach((c) => {
    const chip = document.createElement("div");
    chip.className = "chip" + (c.name === selectedCategory ? " selected" : "");
    chip.textContent = c.name;
    chip.onclick = () => {
      selectedCategory = c.name;
      renderCategoryChips();
    };
    container.appendChild(chip);
  });
  if (!filtered.find((c) => c.name === selectedCategory)) {
    selectedCategory = filtered.length ? filtered[0].name : null;
    renderCategoryChips();
  }
}

function initAddTab() {
  document.querySelectorAll(".type-btn").forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll(".type-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      selectedType = btn.dataset.type;
      selectedCategory = null;
      document.getElementById("amount-sign").textContent = selectedType === "expense" ? "−" : "+";
      document.getElementById("amount-sign").style.color =
        selectedType === "expense" ? "var(--expense)" : "var(--income)";
      renderCategoryChips();
    };
  });

  document.getElementById("new-category-btn").onclick = async () => {
    const input = document.getElementById("new-category-input");
    const name = input.value.trim();
    if (!name) return;
    await idbPut("categories", { key: `${selectedType}:${name}`, name, type: selectedType });
    input.value = "";
    selectedCategory = name;
    renderCategoryChips();
  };

  document.getElementById("date-input").value = new Date().toISOString().slice(0, 10);

  document.getElementById("save-record-btn").onclick = async () => {
    const amountRaw = document.getElementById("amount-input").value.replace(",", ".").trim();
    const amount = parseFloat(amountRaw);
    if (!amount || amount <= 0) {
      showToast("Введи сумму", true);
      return;
    }
    if (!selectedCategory) {
      showToast("Выбери категорию", true);
      return;
    }
    const record = {
      client_id: uuid(),
      type: selectedType,
      amount,
      category: selectedCategory,
      note: document.getElementById("note-input").value.trim(),
      record_date: document.getElementById("date-input").value,
      created_at: new Date().toISOString(),
      synced: false,
    };
    await idbPut("records", record);
    document.getElementById("amount-input").value = "";
    document.getElementById("note-input").value = "";
    showToast("Записано ✓");
    trySync();
  };
}

function showToast(text, isError = false) {
  const el = document.getElementById("save-toast");
  el.textContent = text;
  el.style.color = isError ? "var(--expense)" : "var(--income)";
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 1500);
}

// ===================== Таб "Итоги" =====================
let currentPeriod = "today";

function fmt(n) {
  return n.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function monthBounds(d = new Date()) {
  const first = new Date(d.getFullYear(), d.getMonth(), 1);
  const last = new Date(d.getFullYear(), d.getMonth() + 1, 0);
  return [first.toISOString().slice(0, 10), last.toISOString().slice(0, 10)];
}

async function renderStats() {
  const all = await idbGetAll("records");
  let from, to;
  if (currentPeriod === "today") {
    from = to = todayISO();
  } else {
    [from, to] = monthBounds();
  }
  const records = all.filter((r) => r.record_date >= from && r.record_date <= to);
  const income = records.filter((r) => r.type === "income").reduce((s, r) => s + r.amount, 0);
  const expense = records.filter((r) => r.type === "expense").reduce((s, r) => s + r.amount, 0);

  document.getElementById("stat-income").textContent = "+" + fmt(income);
  document.getElementById("stat-expense").textContent = "−" + fmt(expense);
  const balanceEl = document.getElementById("stat-balance");
  const balance = income - expense;
  balanceEl.textContent = (balance >= 0 ? "+" : "−") + fmt(Math.abs(balance));
  balanceEl.style.color = balance >= 0 ? "var(--income)" : "var(--expense)";

  const heatmapEl = document.getElementById("month-heatmap");
  heatmapEl.innerHTML = "";
  if (currentPeriod === "month") {
    heatmapEl.style.display = "grid";
    const byDay = {};
    records.filter((r) => r.type === "expense").forEach((r) => {
      byDay[r.record_date] = (byDay[r.record_date] || 0) + r.amount;
    });
    const max = Math.max(1, ...Object.values(byDay));
    const [first] = monthBounds();
    const daysInMonth = new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0).getDate();
    for (let d = 1; d <= daysInMonth; d++) {
      const dateStr = first.slice(0, 8) + String(d).padStart(2, "0");
      const val = byDay[dateStr] || 0;
      const intensity = val / max;
      const cell = document.createElement("div");
      cell.className = "heat-cell";
      cell.textContent = d;
      if (val > 0) {
        cell.style.background = `rgba(242, 201, 76, ${0.15 + intensity * 0.65})`;
        cell.title = `${dateStr}: ${fmt(val)}`;
      }
      heatmapEl.appendChild(cell);
    }
  } else {
    heatmapEl.style.display = "none";
  }

  const listEl = document.getElementById("records-list");
  listEl.innerHTML = "";
  const sorted = [...records].sort((a, b) => b.created_at.localeCompare(a.created_at));
  if (sorted.length === 0) {
    listEl.innerHTML = '<div class="empty-state">Записей пока нет</div>';
  }
  sorted.forEach((r) => {
    const li = document.createElement("li");
    li.className = "record-item" + (r.synced ? "" : " unsynced");
    li.innerHTML = `
      <div class="record-info">
        <span class="record-cat">${r.category}</span>
        <span class="record-meta">${r.record_date}${r.note ? " · " + r.note : ""}</span>
      </div>
      <span class="record-amount ${r.type}">${r.type === "expense" ? "−" : "+"}${fmt(r.amount)}</span>
    `;
    listEl.appendChild(li);
  });
}

function initStatsTab() {
  document.querySelectorAll(".period-btn").forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll(".period-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentPeriod = btn.dataset.period;
      renderStats();
    };
  });
}

// ===================== Таб "Графики" =====================
let pieChart, barChart;

async function renderCharts() {
  const monthInput = document.getElementById("chart-month-input").value; // YYYY-MM
  if (!monthInput) return;
  const [y, m] = monthInput.split("-").map(Number);
  const from = `${monthInput}-01`;
  const lastDay = new Date(y, m, 0).getDate();
  const to = `${monthInput}-${String(lastDay).padStart(2, "0")}`;

  const all = await idbGetAll("records");
  const records = all.filter((r) => r.type === "expense" && r.record_date >= from && r.record_date <= to);

  const byCat = {};
  records.forEach((r) => (byCat[r.category] = (byCat[r.category] || 0) + r.amount));
  const byDay = {};
  records.forEach((r) => (byDay[r.record_date] = (byDay[r.record_date] || 0) + r.amount));

  const palette = ["#E4572E", "#F2C94C", "#3FA796", "#7D8CE0", "#C77DE0", "#E07D9A", "#8A93A3", "#5EA8E0"];

  if (pieChart) pieChart.destroy();
  pieChart = new Chart(document.getElementById("pie-chart"), {
    type: "doughnut",
    data: {
      labels: Object.keys(byCat),
      datasets: [{ data: Object.values(byCat), backgroundColor: palette, borderWidth: 0 }],
    },
    options: {
      plugins: { legend: { position: "bottom", labels: { color: "#8A93A3", font: { size: 11 } } } },
    },
  });

  const days = [];
  for (let d = 1; d <= lastDay; d++) days.push(String(d).padStart(2, "0"));
  const values = days.map((d) => byDay[`${monthInput}-${d}`] || 0);

  if (barChart) barChart.destroy();
  barChart = new Chart(document.getElementById("bar-chart"), {
    type: "bar",
    data: { labels: days, datasets: [{ data: values, backgroundColor: "#E4572E" }] },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#8A93A3", font: { size: 9 } }, grid: { display: false } },
        y: { ticks: { color: "#8A93A3", font: { size: 10 } }, grid: { color: "#2C3240" } },
      },
    },
  });
}

function initChartsTab() {
  const now = new Date();
  const monthStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  document.getElementById("chart-month-input").value = monthStr;
  document.getElementById("chart-month-input").onchange = renderCharts;
}

// ===================== Таб "Настройки" + Синхронизация =====================
function setSyncIndicator(state) {
  const el = document.getElementById("sync-indicator");
  el.className = "sync-indicator " + state;
  el.textContent = { online: "синхронизировано", offline: "офлайн", pending: "есть несинхр." }[state];
}

async function refreshSyncIndicator() {
  const unsynced = (await idbGetAll("records")).filter((r) => !r.synced);
  if (!navigator.onLine) {
    setSyncIndicator("offline");
  } else if (unsynced.length > 0) {
    setSyncIndicator("pending");
  } else {
    setSyncIndicator("online");
  }
}

async function trySync(manual = false) {
  await refreshSyncIndicator();
  if (!navigator.onLine) {
    if (manual) setStatus("Нет соединения с интернетом.");
    return;
  }
  const settings = await getSettings();
  if (!settings.backend_url || !settings.telegram_id || !settings.token) {
    if (manual) setStatus("Сначала заполни настройки подключения.");
    return;
  }
  const all = await idbGetAll("records");
  const unsynced = all.filter((r) => !r.synced);

  try {
    const resp = await fetch(settings.backend_url.replace(/\/$/, "") + "/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        telegram_id: parseInt(settings.telegram_id, 10),
        token: settings.token,
        since: settings.last_sync,
        records: unsynced.map((r) => ({
          client_id: r.client_id,
          type: r.type,
          amount: r.amount,
          category: r.category,
          note: r.note,
          record_date: r.record_date,
        })),
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      if (manual) setStatus("Ошибка: " + (err.detail || resp.status));
      return;
    }
    const data = await resp.json();
    for (const r of unsynced) {
      r.synced = true;
      await idbPut("records", r);
    }
    for (const sr of data.server_records) {
      const existing = await idbGet("records", sr.client_id);
      if (!existing) {
        await idbPut("records", { ...sr, created_at: new Date().toISOString(), synced: true });
      }
    }
    await idbPut("meta", { key: "last_sync", value: data.server_time });
    if (manual) setStatus(`Готово. Отправлено: ${unsynced.length}, получено: ${data.server_records.length}.`);
    await refreshSyncIndicator();
    await renderStats();
  } catch (e) {
    if (manual) setStatus("Не удалось подключиться к серверу.");
  }
}

function setStatus(text) {
  document.getElementById("settings-status").textContent = text;
}

async function initSettingsTab() {
  const settings = await getSettings();
  document.getElementById("settings-url").value = settings.backend_url;
  document.getElementById("settings-tgid").value = settings.telegram_id;
  document.getElementById("settings-token").value = settings.token;

  document.getElementById("save-settings-btn").onclick = async () => {
    await saveSettings(
      document.getElementById("settings-url").value.trim(),
      document.getElementById("settings-tgid").value.trim(),
      document.getElementById("settings-token").value.trim()
    );
    setStatus("Настройки сохранены.");
    trySync();
  };

  document.getElementById("sync-now-btn").onclick = () => trySync(true);

  document.getElementById("reset-local-btn").onclick = async () => {
    if (!confirm("Удалить все записи с этого телефона и скачать актуальные с сервера? Несинхронизированные записи будут потеряны.")) {
      return;
    }
    const all = await idbGetAll("records");
    for (const r of all) {
      await new Promise((resolve, reject) => {
        const req = tx("records", "readwrite").delete(r.client_id);
        req.onsuccess = () => resolve();
        req.onerror = () => reject(req.error);
      });
    }
    await idbPut("meta", { key: "last_sync", value: "1970-01-01T00:00:00" });
    setStatus("Локальные данные очищены. Загружаю актуальные с сервера…");
    await trySync(true);
    await renderStats();
  };
}

// ===================== Табы =====================
function initTabs() {
  document.querySelectorAll(".tabbtn").forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll(".tabbtn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(btn.dataset.tab).classList.add("active");
      if (btn.dataset.tab === "tab-stats") renderStats();
      if (btn.dataset.tab === "tab-charts") renderCharts();
    };
  });
}

// ===================== Инициализация =====================
window.addEventListener("online", () => trySync());
window.addEventListener("offline", refreshSyncIndicator);

async function init() {
  db = await openDB();
  await ensureDefaultCategories();
  initTabs();
  initAddTab();
  initStatsTab();
  initChartsTab();
  await initSettingsTab();
  await renderCategoryChips();
  await refreshSyncIndicator();
  await renderStats();
  trySync();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
}

init();
