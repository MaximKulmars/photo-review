const state = {
  category: "exact",
  page: 1,
  selected: new Set(),
  quarantineSelected: new Set(),
  latestJob: null,
  photoItem: null,
  photoMode: null,
};
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 401) {
    location.href = "/login";
    throw new Error("Требуется вход");
  }
  let data = {};
  try { data = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(data.detail || data.failures?.[0]?.error || "Не удалось выполнить действие");
  return data;
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => node.classList.remove("show"), 3200);
}

function bytes(value) {
  if (!value) return "0 Б";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  let number = value, unit = 0;
  while (number >= 1024 && unit < units.length - 1) { number /= 1024; unit++; }
  return `${number.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

function displayDate(value) {
  if (!value) return "Не указана";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ru-RU");
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
}

function showView(name) {
  $$(".view").forEach(node => node.classList.toggle("active", node.id === `view-${name}`));
  $$(".nav-item").forEach(node => node.classList.remove("active"));
  document.querySelector(`[data-view="${name}"]`)?.classList.add("active");
  $("#sidebar").classList.remove("open");
  if (name === "dashboard") refreshSummary();
  if (name === "quarantine") loadQuarantine();
  if (name === "settings") loadSettings();
  if (name === "audit") loadAudit();
}

async function refreshSummary() {
  const data = await api("/api/summary");
  const pending = data.categories.filter(x => x.decision === "pending").reduce((sum, x) => sum + x.count, 0);
  $("#statActive").textContent = data.library.active || 0;
  $("#statPending").textContent = pending;
  $("#statQuarantine").textContent = data.library.quarantine || 0;
  $("#count-quarantine").textContent = data.library.quarantine || 0;
  $("#statUnsupported").textContent = data.unsupported || 0;
  $("#passwordWarning").classList.toggle("hidden", !data.warning);

  const map = Object.fromEntries(Object.keys(window.CATEGORIES).map(key => [key, 0]));
  data.categories.filter(x => x.decision === "pending").forEach(x => map[x.category] = x.count);
  Object.entries(map).forEach(([key, count]) => { $(`#count-${key}`).textContent = count; });
  $("#queueGrid").innerHTML = Object.entries(window.CATEGORIES).map(([key, label]) => `
    <button class="queue-card" data-category="${key}"><span>${escapeHtml(label)}</span><strong>${map[key]}</strong></button>
  `).join("");
  $$("#queueGrid .queue-card").forEach(node => node.onclick = () => openReview(node.dataset.category));
  renderJob(data.job);
}

function renderJob(job) {
  state.latestJob = job;
  if (!job) return;
  const names = { queued: "Ожидает", running: "Выполняется", paused: "Приостановлен", completed: "Завершён", cancelled: "Отменён", failed: "Ошибка" };
  $("#jobState").textContent = names[job.state] || job.state;
  $("#jobMessage").textContent = job.message || "—";
  $("#jobNumbers").textContent = `${job.processed} из ${job.total} · пропущено ${job.skipped}`;
  $("#jobErrors").textContent = job.errors ? `Ошибок: ${job.errors}` : "";
  $("#jobProgress").style.width = `${job.total ? (job.processed / job.total) * 100 : 0}%`;
  const actions = $("#jobActions");
  actions.innerHTML = "";
  if (job.state === "running") actions.innerHTML = `<button class="button" data-action="pause">Приостановить</button><button class="button danger" data-action="cancel">Отменить</button>`;
  if (job.state === "paused") actions.innerHTML = `<button class="button primary" data-action="resume">Продолжить</button><button class="button danger" data-action="cancel">Отменить</button>`;
  $$("[data-action]", actions).forEach(button => button.onclick = () => jobAction(button.dataset.action));
}

async function jobAction(action) {
  try {
    await api(`/api/jobs/${state.latestJob.id}/${action}`, { method: "POST" });
    await refreshSummary();
  } catch (error) { toast(error.message); }
}

async function openReview(category) {
  state.category = category;
  state.page = 1;
  state.selected.clear();
  showView("review");
  await loadReview();
}

async function loadReview() {
  const data = await api(`/api/review?category=${encodeURIComponent(state.category)}&page=${state.page}`);
  $("#reviewTitle").textContent = window.CATEGORIES[state.category];
  $("#reviewSubtitle").textContent = `${data.total} фотографий ожидают решения`;
  $("#reviewEmpty").classList.toggle("hidden", data.items.length !== 0);
  $("#reviewCards").innerHTML = data.items.map(item => photoCard(item, "review")).join("");
  wireCards("#reviewCards", state.selected, data.items, "review");
  const pages = Math.ceil(data.total / data.page_size);
  $("#pagination").innerHTML = pages > 1 ? `
    <button class="button" id="prevPage" ${state.page === 1 ? "disabled" : ""}>Назад</button>
    <span>Страница ${state.page} из ${pages}</span>
    <button class="button" id="nextPage" ${state.page === pages ? "disabled" : ""}>Далее</button>` : "";
  $("#prevPage")?.addEventListener("click", () => { state.page--; state.selected.clear(); loadReview(); });
  $("#nextPage")?.addEventListener("click", () => { state.page++; state.selected.clear(); loadReview(); });
}

function photoCard(item, mode) {
  const findingId = item.id;
  const mediaId = item.media_id || item.id;
  const inputValue = mode === "review" ? findingId : mediaId;
  return `<article class="photo-card" data-id="${inputValue}">
    <input type="checkbox" value="${inputValue}" aria-label="Выбрать">
    ${item.suggested_best ? '<span class="best">Предложено оставить</span>' : ""}
    <button type="button" class="photo-open" aria-label="Открыть фотографию">
      <img class="thumb" loading="lazy" src="/thumbnail/${mediaId}" alt="">
    </button>
    <div class="photo-info">
      <div class="path">${escapeHtml(item.relative_path)}</div>
      ${item.reason ? `<div class="reason">${escapeHtml(item.reason)}</div>` : ""}
      <div class="meta"><span>${item.width || "?"}×${item.height || "?"}</span><span>${bytes(item.size)}</span></div>
    </div>
  </article>`;
}

function wireCards(container, selected, items, mode) {
  const itemMap = new Map(items.map(item => [
    Number(mode === "review" ? item.id : (item.media_id || item.id)),
    item,
  ]));
  $$(`${container} .photo-card`).forEach(card => {
    const checkbox = card.querySelector("input");
    checkbox.onchange = () => {
      const id = Number(checkbox.value);
      checkbox.checked ? selected.add(id) : selected.delete(id);
      card.classList.toggle("selected", checkbox.checked);
    };
    card.querySelector(".photo-open").onclick = () => {
      const item = itemMap.get(Number(card.dataset.id));
      if (item) openPhoto(item, mode);
    };
  });
}

function openPhoto(item, mode) {
  const mediaId = item.media_id || item.id;
  state.photoItem = item;
  state.photoMode = mode;
  const image = $("#photoDialogImage");
  image.onerror = () => {
    image.onerror = null;
    image.src = `/thumbnail/${mediaId}`;
  };
  image.src = `/photo/${mediaId}`;
  image.alt = item.relative_path || "Фотография";
  $("#photoDialogPath").textContent = item.relative_path || "Без названия";
  $("#photoDialogReason").textContent = item.reason || (mode === "quarantine" ? "Файл находится в карантине" : "Причина не указана");
  $("#photoDialogDate").textContent = displayDate(item.captured_at);
  $("#photoDialogSize").textContent = bytes(item.size);
  $("#photoDialogDimensions").textContent = `${item.width || "?"} × ${item.height || "?"}`;
  $("#photoDialogActions").innerHTML = mode === "review"
    ? `<button class="button" type="button" data-photo-action="later">Решить позже</button>
       <button class="button primary" type="button" data-photo-action="keep">Оставить</button>
       <button class="button danger" type="button" data-photo-action="quarantine">В карантин</button>`
    : `<button class="button primary" type="button" data-photo-action="restore">Восстановить</button>`;
  $$("[data-photo-action]", $("#photoDialogActions")).forEach(button => {
    button.onclick = () => photoAction(button.dataset.photoAction);
  });
  $("#photoDialog").showModal();
}

async function photoAction(action) {
  const item = state.photoItem;
  if (!item) return;
  const buttons = $$("[data-photo-action]", $("#photoDialogActions"));
  buttons.forEach(button => button.disabled = true);
  try {
    if (action === "restore") {
      await restoreMediaIds([item.media_id || item.id]);
      $("#photoDialog").close();
      await loadQuarantine();
    } else {
      const data = await api("/api/review/action", {
        method: "POST",
        body: JSON.stringify({ finding_ids: [item.id], action }),
      });
      if (data.failures?.length) throw new Error(data.failures[0].error || "Не удалось обработать фотографию");
      $("#photoDialog").close();
      toast(action === "keep" ? "Фотография оставлена" : action === "later" ? "Решение отложено" : "Фотография перемещена в карантин");
      state.selected.delete(Number(item.id));
      await loadReview();
    }
    await refreshSummary();
  } catch (error) {
    toast(error.message);
    buttons.forEach(button => button.disabled = false);
  }
}

async function reviewAction(action) {
  if (!state.selected.size) return toast("Сначала выберите фотографии");
  try {
    const data = await api("/api/review/action", { method: "POST", body: JSON.stringify({ finding_ids: [...state.selected], action }) });
    if (data.failures?.length) toast(`Не обработано файлов: ${data.failures.length}`);
    else toast("Готово");
    state.selected.clear();
    await loadReview();
    await refreshSummary();
  } catch (error) { toast(error.message); }
}

async function loadQuarantine() {
  const data = await api("/api/quarantine");
  state.quarantineSelected.clear();
  $("#quarantineSummary").textContent = `${data.items.length} файлов, ${bytes(data.total_size)}. Их можно восстановить до окончательного удаления.`;
  $("#quarantineEmpty").classList.toggle("hidden", data.items.length !== 0);
  $("#quarantineCards").innerHTML = data.items.map(item => photoCard(item, "quarantine")).join("");
  wireCards("#quarantineCards", state.quarantineSelected, data.items, "quarantine");
}

async function restoreMediaIds(mediaIds) {
  try {
    await api("/api/quarantine/restore", { method: "POST", body: JSON.stringify({ media_ids: mediaIds, rename_on_conflict: false }) });
    toast("Файлы восстановлены");
  } catch (error) {
    if (confirm(`${error.message}\n\nВосстановить конфликтующие файлы под новым именем?`)) {
      await api("/api/quarantine/restore", { method: "POST", body: JSON.stringify({ media_ids: mediaIds, rename_on_conflict: true }) });
      toast("Файлы восстановлены под новыми именами");
    } else {
      throw error;
    }
  }
}

async function restoreSelected() {
  if (!state.quarantineSelected.size) return toast("Сначала выберите фотографии");
  try {
    await restoreMediaIds([...state.quarantineSelected]);
  } catch (error) { toast(error.message); }
  await loadQuarantine();
  await refreshSummary();
}

async function loadSettings() {
  const data = await api("/api/settings");
  const form = $("#settingsForm");
  Object.entries(data).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value; });
}

async function loadAudit() {
  const data = await api("/api/audit");
  const actionNames = { quarantine: "В карантин", restore: "Восстановлен", delete: "Удалён", keep: "Оставлен", later: "Отложен" };
  $("#auditRows").innerHTML = data.items.map(item => `<tr><td>${escapeHtml(item.created_at)}</td><td>${actionNames[item.action] || escapeHtml(item.action)}</td><td>${escapeHtml(item.relative_path)}</td></tr>`).join("");
}

async function openScan() {
  try {
    const folders = await api("/api/folders");
    $("#folderSelect").innerHTML = folders.map(folder => `<option value="${escapeHtml(folder.path)}">${escapeHtml(folder.name)}</option>`).join("");
    $("#scanDialog").showModal();
  } catch (error) { toast(error.message); }
}

$$(".nav-item").forEach(button => button.onclick = () => button.dataset.view === "review" ? openReview(button.dataset.category) : showView(button.dataset.view));
$("#menuButton").onclick = () => $("#sidebar").classList.toggle("open");
$("#openScan").onclick = openScan;
$$("[data-close]").forEach(button => button.onclick = () => button.closest("dialog").close());
$$("dialog").forEach(dialog => dialog.addEventListener("click", event => {
  if (event.target === dialog) dialog.close();
}));
$("#photoDialog").addEventListener("close", () => {
  $("#photoDialogImage").removeAttribute("src");
  state.photoItem = null;
  state.photoMode = null;
});
$("#selectAll").onclick = () => $$("#reviewCards input").forEach(input => { input.checked = true; input.dispatchEvent(new Event("change")); });
$("#actionKeep").onclick = () => reviewAction("keep");
$("#actionLater").onclick = () => reviewAction("later");
$("#actionQuarantine").onclick = () => reviewAction("quarantine");
$("#restoreSelected").onclick = restoreSelected;
$("#deleteSelected").onclick = () => {
  if (!state.quarantineSelected.size) return toast("Сначала выберите фотографии");
  $("#deleteDescription").textContent = `Будет безвозвратно удалено файлов: ${state.quarantineSelected.size}.`;
  $("#confirmDialog").showModal();
};
$("#scanForm").onsubmit = async event => {
  event.preventDefault();
  const values = new FormData(event.target);
  try {
    await api("/api/jobs", { method: "POST", body: JSON.stringify({ scope: values.get("scope"), duplicate_scope: values.get("duplicate_scope") }) });
    $("#scanDialog").close();
    toast("Анализ запущен");
    refreshSummary();
  } catch (error) { toast(error.message); }
};
$("#confirmForm").onsubmit = async event => {
  event.preventDefault();
  const values = new FormData(event.target);
  try {
    await api("/api/quarantine/delete", { method: "POST", body: JSON.stringify({ media_ids: [...state.quarantineSelected], confirmation: values.get("confirmation") }) });
    $("#confirmDialog").close();
    event.target.reset();
    toast("Выбранные файлы удалены");
    await loadQuarantine();
    await refreshSummary();
  } catch (error) { toast(error.message); }
};
$("#settingsForm").onsubmit = async event => {
  event.preventDefault();
  const values = new FormData(event.target);
  const payload = {
    sensitivity: values.get("sensitivity"),
    blur_threshold: Number(values.get("blur_threshold")),
    dark_threshold: Number(values.get("dark_threshold")),
    similar_distance: Number(values.get("similar_distance")),
    ocr_min_chars: Number(values.get("ocr_min_chars")),
  };
  try { await api("/api/settings", { method: "POST", body: JSON.stringify(payload) }); toast("Настройки сохранены"); }
  catch (error) { toast(error.message); }
};

refreshSummary();
setInterval(() => {
  if ($("#view-dashboard").classList.contains("active") && ["queued", "running"].includes(state.latestJob?.state)) refreshSummary();
}, 2500);
