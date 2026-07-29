const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = { category: "quality", page: 1, selected: new Set(), quarantineSelected: new Set(), latestJob: null, photoItem: null, photoMode: null, photoIndex: -1, reviewItems: [], reviewTotal: 0, reviewComplete: false, reviewLoading: false, scanScope: "", picker: null, pickerPath: "", pickerCache: new Map(), transfer: null };
const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" }[c]));
const bytes = value => value ? (value < 1024 * 1024 ? `${Math.round(value / 1024)} КБ` : `${(value / 1024 / 1024).toFixed(1)} МБ`) : "0 Б";
const displayDate = value => value ? new Date(value).toLocaleDateString("ru-RU") : "—";
function toast(message) { const target = $("#toast"); target.textContent = message; target.classList.add("show"); clearTimeout(toast.timer); toast.timer = setTimeout(() => target.classList.remove("show"), 3400); }
async function api(url, options = {}) { const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options }); const data = await response.json().catch(() => ({})); if (!response.ok) { const detail = Array.isArray(data.detail) ? "Проверьте выбранные файлы и папку назначения" : data.detail; const error = new Error(detail || data.failures?.[0]?.error || "Не удалось выполнить действие"); error.data = data; error.status = response.status; throw error; } return data; }
function rememberView(view) {
  if (["dashboard", "quarantine", "settings", "audit"].includes(view)) {
    history.replaceState(null, "", `#view=${encodeURIComponent(view)}`);
  }
}
function showView(view) { $$(".view").forEach(node => node.classList.toggle("active", node.id === `view-${view}`)); $$(".nav-item").forEach(node => node.classList.toggle("active", node.dataset.view === view)); $("#sidebar").classList.remove("open"); rememberView(view); if (view === "quarantine") loadQuarantine(); if (view === "settings") loadSettings(); if (view === "audit") loadAudit(); }

async function refreshSummary() { const data = await api("/api/summary"); state.latestJob = data.job; const counts = Object.fromEntries((data.categories || []).map(item => [item.category, item.count])); Object.keys(window.CATEGORIES).forEach(key => $(`#count-${key}`).textContent = counts[key] || 0); $("#count-quarantine").textContent = data.library.quarantine || 0; $("#statActive").textContent = data.library.active || 0; $("#statQuarantine").textContent = data.library.quarantine || 0; $("#statUnsupported").textContent = data.unsupported || 0; $("#statPending").textContent = Object.entries(counts).filter(([key]) => key !== "quality").reduce((sum, [, value]) => sum + value, 0); $("#passwordWarning").classList.toggle("hidden", !data.warning); renderJob(data.job); $("#queueGrid").innerHTML = Object.entries(window.CATEGORIES).map(([key, label]) => `<button class="queue-card" data-category="${key}"><span>${escapeHtml(label)}</span><strong>${counts[key] || 0}</strong></button>`).join(""); $$("#queueGrid button").forEach(button => button.onclick = () => openReview(button.dataset.category)); }
function renderJob(job) { const active = job || { state: "—", message: "Анализ ещё не запускался", total: 0, processed: 0, errors: 0 }; $("#jobState").textContent = active.state; $("#jobMessage").textContent = active.message || ""; $("#jobNumbers").textContent = `${active.processed || 0} из ${active.total || 0}`; $("#jobErrors").textContent = active.errors ? `Ошибок: ${active.errors}` : ""; $("#jobProgress").style.width = `${active.total ? Math.min(100, (active.processed || 0) / active.total * 100) : 0}%`; $("#jobActions").innerHTML = ["queued", "running", "paused"].includes(active.state) ? (active.state === "paused" ? `<button class="button" data-job="resume">Продолжить</button>` : `<button class="button" data-job="pause">Пауза</button>`) + `<button class="button danger" data-job="cancel">Остановить</button>` : ""; $$("[data-job]").forEach(button => button.onclick = async () => { await api(`/api/jobs/${active.id}/${button.dataset.job}`, { method: "POST" }); refreshSummary(); }); }
function openReview(category) { state.category = category; state.page = 1; state.selected.clear(); state.reviewItems = []; state.reviewTotal = 0; state.reviewComplete = false; history.replaceState(null, "", `#review=${encodeURIComponent(category)}`); showView("review"); loadReview(true); }
function card(item, mode) { const mediaId = item.media_id || item.id, selectId = mode === "review" ? item.id : mediaId; return `<article class="photo-card" data-id="${item.id}" data-media="${mediaId}"><input type="checkbox" data-select-id="${selectId}" aria-label="Выбрать"><button class="photo-open" type="button"><img class="thumb" src="/thumbnail/${mediaId}" alt=""></button>${item.suggested_best ? '<span class="best">Лучший кадр</span>' : ""}<div class="photo-info"><div class="path">${escapeHtml(item.relative_path)}</div><div class="reason">${escapeHtml(item.reason || "")}</div><div class="meta"><span>${displayDate(item.captured_at)}</span><span>${bytes(item.size)}</span></div></div></article>`; }
function wireCards(root, selected, items, mode) { $$(".photo-card", $(root)).forEach((node, index) => { const item = items[index], checkbox = $("input", node); checkbox.onchange = () => { const key = mode === "review" ? Number(item.id) : Number(item.media_id || item.id); checkbox.checked ? selected.add(key) : selected.delete(key); node.classList.toggle("selected", checkbox.checked); }; $(".photo-open", node).onclick = () => openPhoto(item, mode); }); }
async function loadReview() { const data = await api(`/api/review?category=${encodeURIComponent(state.category)}&page=${state.page}`); $("#reviewTitle").textContent = window.CATEGORIES[state.category]; $("#reviewSubtitle").textContent = data.total ? `Показано ${data.items.length} из ${data.total}. Только результаты последнего успешного анализа.` : "Последний анализ не нашёл файлов в этой категории."; $("#reviewCards").innerHTML = data.items.map(item => card(item, "review")).join(""); $("#reviewEmpty").classList.toggle("hidden", data.items.length !== 0); wireCards("#reviewCards", state.selected, data.items, "review"); const isQuality = state.category === "quality"; ["#actionQuality", "#actionKeep", "#actionLater", "#actionQuarantine"].forEach(selector => $(selector).classList.toggle("hidden", isQuality)); $("#pagination").innerHTML = data.total > data.page_size ? `<button class="button" ${data.page <= 1 ? "disabled" : ""} data-page="${data.page - 1}">Назад</button><span>${data.page}</span><button class="button" ${data.page * data.page_size >= data.total ? "disabled" : ""} data-page="${data.page + 1}">Далее</button>` : ""; $$("[data-page]").forEach(button => button.onclick = () => { state.page = Number(button.dataset.page); state.selected.clear(); loadReview(); }); }
function openPhoto(item, mode) { state.photoItem = item; state.photoMode = mode; const mediaId = item.media_id || item.id; $("#photoDialogImage").src = `/photo/${mediaId}`; $("#photoDialogPath").textContent = item.relative_path || "Без названия"; $("#photoDialogReason").textContent = item.reason || (mode === "quarantine" ? "Файл находится в карантине" : "Причина не указана"); $("#photoDialogDate").textContent = displayDate(item.captured_at); $("#photoDialogSize").textContent = bytes(item.size); $("#photoDialogDimensions").textContent = `${item.width || "?"} × ${item.height || "?"}`; const reviewButtons = state.category === "quality" ? "" : `<button class="button" data-photo-action="quality">Считать качественной</button><button class="button" data-photo-action="later">Решить позже</button><button class="button primary" data-photo-action="keep">Оставить</button><button class="button danger" data-photo-action="quarantine">В карантин</button>`; $("#photoDialogActions").innerHTML = mode === "review" ? `<button class="button" data-photo-action="copy">Копировать</button><button class="button" data-photo-action="move">Перенести</button>${reviewButtons}` : `<button class="button primary" data-photo-action="restore">Восстановить</button>`; $$("[data-photo-action]", $("#photoDialogActions")).forEach(button => button.onclick = () => photoAction(button.dataset.photoAction)); $("#photoDialog").showModal(); }
async function photoAction(action) { const item = state.photoItem; if (!item) return; if (action === "copy" || action === "move") return beginTransfer(action, [item.media_id || item.id]); if (action === "restore") { await restoreMediaIds([item.media_id || item.id]); $("#photoDialog").close(); return loadQuarantine(); } const data = await api("/api/review/action", { method: "POST", body: JSON.stringify({ finding_ids: [item.id], action }) }); if (data.failures?.length) throw new Error(data.failures[0].error); $("#photoDialog").close(); state.selected.delete(Number(item.id)); await loadReview(); await refreshSummary(); }
function selectedReviewIds() { return $$("#reviewCards input:checked").map(input => Number(input.dataset.selectId)).filter(Number.isFinite); }
async function reviewAction(action) { const selected = selectedReviewIds(); if (!selected.length) return toast("Сначала выберите фотографии"); await api("/api/review/action", { method: "POST", body: JSON.stringify({ finding_ids: selected, action }) }); state.selected.clear(); toast(action === "quality" ? "Фотографии подтверждены как качественные" : "Готово"); await loadReview(); refreshSummary(); }

async function openFolderPicker(mode, initial = "") { state.picker = mode; state.pickerPath = initial; state.pickerCache.clear(); $("#folderDialogTitle").textContent = mode === "scan" ? "Выберите папку для анализа" : "Выберите папку назначения"; $("#newFolderButton").classList.toggle("hidden", mode !== "transfer"); $("#folderNew").classList.add("hidden"); await renderPicker(initial); $("#folderDialog").showModal(); }
async function folderData(path) { if (!state.pickerCache.has(path)) state.pickerCache.set(path, await api(`/api/folders?path=${encodeURIComponent(path)}`)); return state.pickerCache.get(path); }
async function renderPicker(path) { const data = await folderData(path); state.pickerPath = data.path; $("#folderBreadcrumbs").innerHTML = data.breadcrumbs.map(item => `<button class="crumb" data-path="${escapeHtml(item.path)}">${escapeHtml(item.name)}</button>`).join("<span>›</span>"); $$(".crumb").forEach(button => button.onclick = () => renderPicker(button.dataset.path)); $("#folderCurrentPath").textContent = data.path || "Весь архив"; $("#folderEntries").innerHTML = data.directories.length ? data.directories.map(item => `<button class="folder-entry" data-path="${escapeHtml(item.path)}">📁 ${escapeHtml(item.name)}</button>`).join("") : '<p class="muted">В этой папке нет видимых подпапок.</p>'; $$(".folder-entry").forEach(button => button.onclick = () => renderPicker(button.dataset.path)); const treePaths = ["", ...data.breadcrumbs.slice(1).map(item => item.path), ...data.directories.map(item => item.path)]; $("#folderTree").innerHTML = [...new Set(treePaths)].map(item => `<button class="tree-item ${item === data.path ? "active" : ""}" data-path="${escapeHtml(item)}">${item ? "└ " + escapeHtml(item.split("/").at(-1)) : "📁 Архив"}</button>`).join(""); $$(".tree-item").forEach(button => button.onclick = () => renderPicker(button.dataset.path)); }
$("#selectFolder").onclick = () => { const mode = state.picker, folder = state.pickerPath; $("#folderDialog").close(); if (mode === "scan") { state.scanScope = folder; $("#scanScope").value = folder; $("#scanScopeLabel").textContent = folder || "Весь архив"; $("#scanDialog").showModal(); } else { state.transfer.destination = folder; confirmTransfer(); } };
$("#newFolderButton").onclick = () => $("#folderNew").classList.toggle("hidden");
$("#createFolder").onclick = async () => { const name = $("#folderNewName").value.trim(); if (!name) return; try { const created = await api("/api/folders", { method: "POST", body: JSON.stringify({ parent: state.pickerPath, name }) }); state.pickerCache.clear(); $("#folderNewName").value = ""; $("#folderNew").classList.add("hidden"); await renderPicker(created.path); } catch (error) { toast(error.message); } };
async function openScan() { state.scanScope = ""; $("#scanScope").value = ""; $("#scanScopeLabel").textContent = "Весь архив"; await openFolderPicker("scan", ""); }
function beginTransfer(operation, mediaIds) { if (!mediaIds.length) return toast("Сначала выберите фотографии"); state.transfer = { operation, mediaIds, destination: "" }; openFolderPicker("transfer", ""); }
function confirmTransfer() { const transfer = state.transfer; $("#transferTitle").textContent = transfer.operation === "copy" ? "Копировать в папку" : "Перенести в папку"; $("#transferDescription").textContent = `${transfer.operation === "copy" ? "Будет скопировано" : "Будет перенесено"} файлов: ${transfer.mediaIds.length}. Папка назначения: /${transfer.destination || ""}.`; $("#transferDialog").showModal(); }
$("#transferConfirm").onclick = async () => { const transfer = state.transfer; const payload = rename_on_conflict => ({ operation: transfer.operation, media_ids: transfer.mediaIds, destination: transfer.destination, rename_on_conflict }); try { await api("/api/media/transfer", { method: "POST", body: JSON.stringify(payload(false)) }); $("#transferDialog").close(); $("#photoDialog").close(); state.selected.clear(); toast(transfer.operation === "copy" ? "Копирование завершено" : "Перенос завершён"); await loadReview(); refreshSummary(); } catch (error) { if (error.status === 409 && confirm(`${error.message}\n\nСоздать новые имена для совпадающих файлов?`)) { try { await api("/api/media/transfer", { method: "POST", body: JSON.stringify(payload(true)) }); $("#transferDialog").close(); $("#photoDialog").close(); state.selected.clear(); toast("Операция завершена с новыми именами"); await loadReview(); refreshSummary(); } catch (retry) { toast(retry.message); } } else toast(error.message); } };

async function loadQuarantine() { const data = await api("/api/quarantine"); state.quarantineSelected.clear(); $("#quarantineSummary").textContent = `${data.items.length} файлов, ${bytes(data.total_size)}. Их можно восстановить до окончательного удаления.`; $("#quarantineEmpty").classList.toggle("hidden", data.items.length !== 0); $("#quarantineCards").innerHTML = data.items.map(item => card(item, "quarantine")).join(""); wireCards("#quarantineCards", state.quarantineSelected, data.items, "quarantine"); }
async function restoreMediaIds(mediaIds) { try { await api("/api/quarantine/restore", { method: "POST", body: JSON.stringify({ media_ids: mediaIds, rename_on_conflict: false }) }); toast("Файлы восстановлены"); } catch (error) { if (!confirm(`${error.message}\n\nВосстановить под новыми именами?`)) throw error; await api("/api/quarantine/restore", { method: "POST", body: JSON.stringify({ media_ids: mediaIds, rename_on_conflict: true }) }); toast("Файлы восстановлены под новыми именами"); } }
async function loadSettings() { const data = await api("/api/settings"); Object.entries(data).forEach(([key, value]) => { if ($("#settingsForm").elements[key]) $("#settingsForm").elements[key].value = value; }); }
async function loadAudit() { const data = await api("/api/audit"); const names = { quarantine:"В карантин", restore:"Восстановлен", delete:"Удалён", keep:"Оставлен", later:"Отложен", quality:"Подтверждено качество", copy:"Скопирован", move:"Перенесён" }; $("#auditRows").innerHTML = data.items.map(item => `<tr><td>${escapeHtml(item.created_at)}</td><td>${names[item.action] || escapeHtml(item.action)}</td><td>${escapeHtml(item.relative_path)}</td></tr>`).join(""); }

$$(".nav-item").forEach(button => button.onclick = () => button.dataset.view === "review" ? openReview(button.dataset.category) : showView(button.dataset.view)); $("#menuButton").onclick = () => $("#sidebar").classList.toggle("open"); $("#openScan").onclick = openScan; $("#chooseScanFolder").onclick = () => { $("#scanDialog").close(); openFolderPicker("scan", state.scanScope); }; $$("[data-close]").forEach(button => button.onclick = () => button.closest("dialog").close()); $$("dialog").forEach(dialog => dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); })); $("#photoDialog").addEventListener("close", () => { $("#photoDialogImage").removeAttribute("src"); state.photoItem = null; }); $("#selectAll").onclick = () => $$("#reviewCards input").forEach(input => { input.checked = true; input.dispatchEvent(new Event("change")); }); $("#actionQuality").onclick = () => reviewAction("quality"); $("#actionKeep").onclick = () => reviewAction("keep"); $("#actionLater").onclick = () => reviewAction("later"); $("#actionQuarantine").onclick = () => reviewAction("quarantine"); $("#actionCopy").onclick = () => beginTransfer("copy", [...state.selected].map(Number)); $("#actionMove").onclick = () => beginTransfer("move", [...state.selected].map(Number)); $("#restoreSelected").onclick = async () => { if (!state.quarantineSelected.size) return toast("Сначала выберите фотографии"); try { await restoreMediaIds([...state.quarantineSelected]); await loadQuarantine(); refreshSummary(); } catch (error) { toast(error.message); } }; $("#deleteSelected").onclick = () => { if (!state.quarantineSelected.size) return toast("Сначала выберите фотографии"); $("#deleteDescription").textContent = `Будет безвозвратно удалено файлов: ${state.quarantineSelected.size}.`; $("#confirmDialog").showModal(); };
$("#scanForm").onsubmit = async event => { event.preventDefault(); const values = new FormData(event.target); try { await api("/api/jobs", { method:"POST", body:JSON.stringify({ scope: values.get("scope"), duplicate_scope: values.get("duplicate_scope") }) }); $("#scanDialog").close(); toast("Анализ запущен"); refreshSummary(); } catch (error) { toast(error.message); } }; $("#confirmForm").onsubmit = async event => { event.preventDefault(); try { await api("/api/quarantine/delete", { method:"POST", body:JSON.stringify({ media_ids:[...state.quarantineSelected], confirmation:new FormData(event.target).get("confirmation") }) }); $("#confirmDialog").close(); event.target.reset(); toast("Выбранные файлы удалены"); await loadQuarantine(); refreshSummary(); } catch (error) { toast(error.message); } }; $("#settingsForm").onsubmit = async event => { event.preventDefault(); const form = new FormData(event.target); try { await api("/api/settings", { method:"POST", body:JSON.stringify({ sensitivity:form.get("sensitivity"), blur_threshold:Number(form.get("blur_threshold")), dark_threshold:Number(form.get("dark_threshold")), similar_distance:Number(form.get("similar_distance")), ocr_min_chars:Number(form.get("ocr_min_chars")) }) }); toast("Настройки сохранены"); } catch (error) { toast(error.message); } };
$("#actionCopy").onclick = () => beginTransfer("copy", selectedReviewIds());
$("#actionMove").onclick = () => beginTransfer("move", selectedReviewIds());
var savedReviewCategory = new URLSearchParams(location.hash.slice(1)).get("review");
if (savedReviewCategory && window.CATEGORIES[savedReviewCategory]) openReview(savedReviewCategory);

var reviewObserver = new IntersectionObserver(entries => {
  if (entries.some(entry => entry.isIntersecting)) loadReview(false);
}, { rootMargin: "500px" });

async function loadReview(reset = false) {
  if (state.reviewLoading || (!reset && state.reviewComplete)) return;
  if (reset) {
    state.page = 1;
    state.reviewItems = [];
    state.reviewTotal = 0;
    state.reviewComplete = false;
    state.selected.clear();
  }
  state.reviewLoading = true;
  try {
    const data = await api(`/api/review?category=${encodeURIComponent(state.category)}&page=${state.page}`);
    state.reviewTotal = data.total;
    state.reviewItems.push(...data.items);
    state.page += 1;
    state.reviewComplete = state.reviewItems.length >= data.total || data.items.length === 0;
    $("#reviewTitle").textContent = window.CATEGORIES[state.category];
    $("#reviewSubtitle").textContent = data.total ? `Показано ${state.reviewItems.length} из ${data.total}. Результаты последнего успешного анализа.` : "Последний анализ не нашёл файлов в этой категории.";
    $("#reviewCards").innerHTML = state.reviewItems.map(item => card(item, "review")).join("");
    $("#reviewEmpty").classList.toggle("hidden", state.reviewItems.length !== 0);
    wireCards("#reviewCards", state.selected, state.reviewItems, "review");
    const isQuality = state.category === "quality";
    ["#actionQuality", "#actionKeep", "#actionLater"].forEach(selector => $(selector).classList.toggle("hidden", isQuality));
    $("#actionQuarantine").classList.remove("hidden");
    $("#pagination").innerHTML = state.reviewComplete ? "" : '<div class="lazy-loader" id="reviewSentinel">Загружаем ещё фотографии…</div>';
    reviewObserver.disconnect();
    const sentinel = $("#reviewSentinel");
    if (sentinel) reviewObserver.observe(sentinel);
  } finally {
    state.reviewLoading = false;
  }
}

function openPhoto(item, mode) {
  state.photoItem = item;
  state.photoMode = mode;
  state.photoIndex = mode === "review" ? state.reviewItems.findIndex(candidate => candidate.id === item.id) : -1;
  const mediaId = item.media_id || item.id;
  $("#photoDialogImage").src = `/photo/${mediaId}`;
  $("#photoDialogPath").textContent = item.relative_path || "Без названия";
  $("#photoDialogReason").textContent = item.reason || (mode === "quarantine" ? "Файл находится в карантине" : "Причина не указана");
  $("#photoDialogDate").textContent = displayDate(item.captured_at);
  $("#photoDialogSize").textContent = bytes(item.size);
  $("#photoDialogDimensions").textContent = `${item.width || "?"} × ${item.height || "?"}`;
  const navigation = mode === "review" ? `<button class="button" data-photo-nav="previous" ${state.photoIndex <= 0 ? "disabled" : ""}>← Предыдущее</button><button class="button" data-photo-nav="next">Следующее →</button>` : "";
  const reviewButtons = state.category === "quality"
    ? '<button class="button danger" data-photo-action="quarantine">В карантин</button>'
    : '<button class="button" data-photo-action="quality">Считать качественной</button><button class="button" data-photo-action="later">Решить позже</button><button class="button primary" data-photo-action="keep">Оставить</button><button class="button danger" data-photo-action="quarantine">В карантин</button>';
  $("#photoDialogActions").innerHTML = mode === "review"
    ? `${navigation}<button class="button" data-photo-action="copy">Копировать</button><button class="button" data-photo-action="move">Перенести</button>${reviewButtons}`
    : '<button class="button primary" data-photo-action="restore">Восстановить</button>';
  $$('[data-photo-action]', $("#photoDialogActions")).forEach(button => button.onclick = () => photoAction(button.dataset.photoAction));
  $$('[data-photo-nav]', $("#photoDialogActions")).forEach(button => button.onclick = () => photoNavigate(button.dataset.photoNav));
  if (!$("#photoDialog").open) $("#photoDialog").showModal();
}

async function photoNavigate(direction) {
  let index = state.photoIndex + (direction === "next" ? 1 : -1);
  if (index >= state.reviewItems.length && !state.reviewComplete) {
    await loadReview(false);
  }
  if (index >= 0 && index < state.reviewItems.length) openPhoto(state.reviewItems[index], "review");
}

async function quarantineMediaIds(mediaIds) {
  const data = await api("/api/media/quarantine", { method: "POST", body: JSON.stringify({ media_ids: mediaIds }) });
  if (data.failures?.length) throw new Error(data.failures[0].error);
}

async function photoAction(action) {
  const item = state.photoItem;
  if (!item) return;
  if (action === "copy" || action === "move") return beginTransfer(action, [item.media_id || item.id]);
  if (action === "restore") { await restoreMediaIds([item.media_id || item.id]); $("#photoDialog").close(); return loadQuarantine(); }
  if (action === "quarantine" && state.category === "quality") await quarantineMediaIds([item.media_id || item.id]);
  else await api("/api/review/action", { method: "POST", body: JSON.stringify({ finding_ids: [item.id], action }) });
  $("#photoDialog").close();
  await loadReview(true);
  refreshSummary();
}

async function reviewAction(action) {
  const selected = selectedReviewIds();
  if (!selected.length) return toast("Сначала выберите фотографии");
  if (action === "quarantine" && state.category === "quality") await quarantineMediaIds(selected);
  else await api("/api/review/action", { method: "POST", body: JSON.stringify({ finding_ids: selected, action }) });
  state.selected.clear();
  toast(action === "quality" ? "Фотографии подтверждены как качественные" : action === "quarantine" ? "Фотографии перемещены в карантин" : "Готово");
  await loadReview(true);
  refreshSummary();
}
refreshSummary(); setInterval(() => { if (["queued", "running"].includes(state.latestJob?.state)) refreshSummary(); }, 2500);

// Media-library cards use existing thumbnails and fall back to the original safely.
const reviewOpenPhoto = openPhoto;
openPhoto = function (item, mode) {
  if (mode !== "library") return reviewOpenPhoto(item, mode);
  state.photoItem = item;
  state.photoMode = mode;
  const mediaId = item.id;
  $("#photoDialogImage").src = `/photo/${mediaId}`;
  $("#photoDialogPath").textContent = item.relative_path || "Без названия";
  $("#photoDialogReason").textContent = "Оригинал остаётся на месте";
  $("#photoDialogDate").textContent = displayDate(item.captured_at);
  $("#photoDialogSize").textContent = bytes(item.size);
  $("#photoDialogDimensions").textContent = `${item.width || "?"} × ${item.height || "?"}`;
  $("#photoDialogActions").innerHTML = "";
  if (!$("#photoDialog").open) $("#photoDialog").showModal();
};

libraryAlbum = async function (id) {
  $(".view.active").classList.remove("active");
  $("#view-photo-album").classList.add("active");
  const data = await api(`/api/library/media?container_id=${encodeURIComponent(id)}`);
  $("#libraryMedia").innerHTML = data.items.map(item => `<article class="photo-card"><button class="photo-open" type="button" data-library-id="${item.id}"><img class="thumb" loading="lazy" src="/thumbnail/${item.id}" onerror="this.onerror=null;this.src='/photo/${item.id}'" alt="${escapeHtml(item.file_name || item.relative_path)}"></button><div class="photo-info"><div class="path">${escapeHtml(item.file_name || item.relative_path)}</div></div></article>`).join("");
  $$('[data-library-id]').forEach(button => button.onclick = () => openPhoto(data.items.find(item => item.id === Number(button.dataset.libraryId)), "library"));
};

// Single, deterministic navigation for the media library.
function activateView(view) {
  $$(".view").forEach(node => node.classList.remove("active"));
  const target = $(`#view-${view}`);
  if (target) target.classList.add("active");
}
function setActiveLibraryNav(selector) {
  $$("#photoNav .nav-item").forEach(node => node.classList.remove("active"));
  const current = selector && $(selector);
  if (current) current.classList.add("active");
}
function setActiveTopTab(section) {
  $$(".top-tab").forEach(tab => tab.classList.toggle("active", tab.dataset.section === section));
}
async function loadLibraryShelves() {
  const data = await api("/api/library/shelves");
  const items = data.items || [];
  $("#shelfGrid").innerHTML = items.map(item => `<button class="shelf-card" data-shelf="${escapeHtml(item.year)}"><strong>${escapeHtml(item.year)}</strong><span>${item.album_count} альбомов · ${item.media_count} фото</span></button>`).join("");
  $("#photoShelves").innerHTML = items.map(item => `<button class="nav-item" data-shelf="${escapeHtml(item.year)}">${escapeHtml(item.year)}</button>`).join("");
  $("#photoEmpty").classList.toggle("hidden", items.length > 0);
  $$('[data-shelf]').forEach(button => button.onclick = () => openLibraryShelf(button.dataset.shelf));
}
async function ensureLibraryShelves() {
  if (!$("#photoShelves").children.length) await loadLibraryShelves();
}
async function openLibraryShelf(shelf) {
  activateView("photo-year");
  $("#yearTitle").textContent = shelf;
  $("#yearCrumbs").textContent = `Фото · ${shelf}`;
  setActiveLibraryNav(null);
  const data = await api(`/api/library/albums?year=${encodeURIComponent(shelf)}`);
  $("#albumGrid").innerHTML = data.items.map(item => `<button class="album-card" data-album-id="${item.id}"><div class="album-cover">▧</div><strong>${escapeHtml(item.name)}</strong><span>${item.media_count} фото</span></button>`).join("");
  $$('[data-album-id]').forEach(button => button.onclick = () => openLibraryAlbum(Number(button.dataset.albumId), shelf));
}
async function openLibraryAlbum(id, shelf) {
  activateView("photo-album");
  $("#albumCrumbs").textContent = `Фото · ${shelf}`;
  const data = await api(`/api/library/media?container_id=${id}`);
  $("#libraryMedia").innerHTML = data.items.map(item => `<article class="photo-card"><button class="photo-open" type="button" data-library-media="${item.id}"><img class="thumb" loading="lazy" src="/thumbnail/${item.id}" onerror="this.onerror=null;this.src='/photo/${item.id}'" alt="${escapeHtml(item.file_name || item.relative_path)}"></button><div class="photo-info"><div class="path">${escapeHtml(item.file_name || item.relative_path)}</div></div></article>`).join("");
  $$('[data-library-media]').forEach(button => button.onclick = () => openPhoto(data.items.find(item => item.id === Number(button.dataset.libraryMedia)), "library"));
}
function openPhotoHome() {
  setActiveTopTab("photos");
  activateView("photo-home");
  setActiveLibraryNav('[data-library-view="photo-home"]');
  history.replaceState(null, "", "#photos");
  loadLibraryShelves().catch(error => toast(error.message));
}
function openSortingView(view = "dashboard") {
  setActiveTopTab("sorting");
  activateView(view);
  $("#photoNav").classList.add("hidden");
  $("#sortingNav").classList.remove("hidden");
  $$("#sortingNav .nav-item").forEach(node => node.classList.toggle("active", node.dataset.view === view));
  rememberView(view);
}
$$(".top-tab").forEach(tab => tab.onclick = () => {
  $$(".top-tab").forEach(item => item.classList.toggle("active", item === tab));
  if (tab.dataset.section === "photos") {
    history.replaceState(null, "", "#photos");
    $("#photoNav").classList.remove("hidden");
    $("#sortingNav").classList.add("hidden");
    openPhotoHome();
  } else if (tab.dataset.section === "videos") {
    history.replaceState(null, "", "#videos");
    $("#photoNav").addClass?.("hidden");
    $("#photoNav").classList.add("hidden");
    $("#sortingNav").classList.add("hidden");
    activateView("videos");
  } else {
    openSortingView();
  }
});
$$("#sortingNav .nav-item").forEach(button => button.onclick = () => {
  if (button.dataset.view === "review") openReview(button.dataset.category);
  else openSortingView(button.dataset.view);
});
$("#backToPhotos").onclick = openPhotoHome;
$("#backToYear").onclick = () => openPhotoHome();
$("#rescanLibrary").onclick = async () => {
  try { await api("/api/library/scan", { method: "POST", body: JSON.stringify({library_root: "photos"}) }); await loadLibraryShelves(); toast("Индекс обновлён"); }
  catch (error) { toast(error.message); }
};
let libraryShelf = null;
function route(s,a,p){const h=a?`#shelf=${encodeURIComponent(s)}&album=${a}`:s?`#shelf=${encodeURIComponent(s)}`:"#photos";p?history.pushState(null,"",h):history.replaceState(null,"",h)}
async function home(p=false){if(p)route(null,null,true);setActiveTopTab("photos");activateView("photo-home");$("#photoNav").classList.remove("hidden");$("#sortingNav").classList.add("hidden");const d=await api("/api/library/shelves");$("#shelfGrid").innerHTML=d.items.map(i=>`<button class="shelf-card" data-shelf="${escapeHtml(i.year)}"><span class="shelf-cover">${i.cover_media_id?`<img src="/thumbnail/${i.cover_media_id}" onerror="this.src='/photo/${i.cover_media_id}'">`:""}</span><strong>${escapeHtml(i.year)}</strong><span>${i.album_count} альбомов · ${i.media_count} фото</span></button>`).join("");$("#photoShelves").innerHTML=d.items.map(i=>`<button class="nav-item" data-shelf="${escapeHtml(i.year)}">${escapeHtml(i.year)}</button>`).join("");document.querySelectorAll("[data-shelf]").forEach(b=>b.onclick=()=>shelf(b.dataset.shelf,true))}
async function shelf(s,p=false){libraryShelf=s;if(p)route(s,null,true);setActiveTopTab("photos");await ensureLibraryShelves();activateView("photo-year");$("#yearTitle").textContent=s;const d=await api(`/api/library/albums?year=${encodeURIComponent(s)}`);$("#albumGrid").innerHTML=d.items.map(i=>`<article class="album-card" data-album-card="${i.id}"><button class="album-open" data-album="${i.id}"><div class="album-cover">${i.cover_media_id?`<img src="/thumbnail/${i.cover_media_id}" onerror="this.src='/photo/${i.cover_media_id}'">`:""}</div><strong>${escapeHtml(i.name)}</strong><span>${i.media_count} фото</span></button></article>`).join("");document.querySelectorAll("[data-album]").forEach(b=>b.onclick=()=>album(+b.dataset.album,true))}
async function album(id,p=false){if(p)route(libraryShelf,id,true);setActiveTopTab("photos");await ensureLibraryShelves();activateView("photo-album");const d=await api(`/api/library/media?container_id=${id}`);$("#libraryMedia").innerHTML=d.items.map(i=>`<article class="photo-card"><button class="photo-open" data-media="${i.id}"><img class="thumb" src="/thumbnail/${i.id}" onerror="this.src='/photo/${i.id}'"></button><div class="photo-info"><div class="path">${escapeHtml(i.file_name)}</div></div></article>`).join("");document.querySelectorAll("[data-media]").forEach(b=>b.onclick=()=>openPhoto(d.items.find(i=>i.id===+b.dataset.media),"library"))}
async function restoreRouteFromHash(){const raw=location.hash.slice(1);const q=new URLSearchParams(raw);const review=q.get("review");if(review&&window.CATEGORIES[review])return openReview(review);if(raw==="videos"){setActiveTopTab("videos");$("#photoNav").classList.add("hidden");$("#sortingNav").classList.add("hidden");return activateView("videos")}if(raw==="unsorted")return loadUnsorted(1);const view=q.get("view")||(["dashboard","quarantine","settings","audit"].includes(raw)?raw:"");if(["dashboard","quarantine","settings","audit"].includes(view))return openSortingView(view);const s=q.get("shelf"),a=Number(q.get("album"));if(s&&a){libraryShelf=s;return album(a)}if(s)return shelf(s);if(!raw)route(null,null,false);return home()}
window.onpopstate=()=>restoreRouteFromHash().catch(error=>toast(error.message));$("#backToPhotos").onclick=()=>home(true);$("#backToYear").onclick=()=>shelf(libraryShelf,true);const oldOpen=openPhoto;openPhoto=(i,m)=>{if(m!=="library")return oldOpen(i,m);$("#photoDialogImage").src=`/photo/${i.id}`;$("#photoDialogPath").textContent=i.relative_path;$("#photoDialogReason").textContent="Оригинал остаётся на месте";$("#photoDialogActions").innerHTML=`<a class="button" href="/photo/${i.id}" target="_blank">Открыть оригинал</a><button class="button" id="closeLibraryPhoto">Закрыть</button>`;$("#closeLibraryPhoto").onclick=()=>$("#photoDialog").close();$("#photoDialog").showModal()};
const galleryPhotoOpen = openPhoto;
openPhoto = (item, mode) => {
  if (mode !== "library") return galleryPhotoOpen(item, mode);
  $("#photoDialogImage").src = `/photo/${item.id}`;
  $("#photoDialogPath").textContent = item.relative_path;
  $("#photoDialogReason").textContent = "Оригинал остаётся на месте";
  $("#photoDialogActions").innerHTML = `<button class="button" id="galleryCopy">Копировать</button><button class="button" id="galleryMove">Переместить</button><button class="button danger" id="galleryQuarantine">В карантин</button><a class="button" href="/photo/${item.id}" target="_blank" rel="noopener">Открыть оригинал</a>`;
  $("#galleryCopy").onclick = () => beginTransfer("copy", [item.id]);
  $("#galleryMove").onclick = () => beginTransfer("move", [item.id]);
  $("#galleryQuarantine").onclick = async () => { if (!confirm("Переместить фотографию в карантин?")) return; await quarantineMediaIds([item.id]); $("#photoDialog").close(); await album(new URLSearchParams(location.hash.slice(1)).get("album")); };
  if (!$("#photoDialog").open) $("#photoDialog").showModal();
};
const galleryToolbar = $("#rescanLibrary").parentElement;
galleryToolbar.insertAdjacentHTML("beforeend", '<button class="button" id="gallerySorting">К сортировке</button>');
$("#gallerySorting").onclick = () => document.querySelector("[data-section=sorting]").click();
const galleryMenuOpen = openPhoto;
openPhoto = (item, mode) => {
  if (mode !== "library") { $("#photoDialog").classList.remove("gallery-mode"); return galleryMenuOpen(item, mode); }
  const dialog = $("#photoDialog");
  dialog.classList.add("gallery-mode");
  $("#photoDialogImage").src = `/photo/${item.id}`;
  $("#photoDialogPath").textContent = item.relative_path;
  $("#photoDialogReason").textContent = "Оригинал остаётся на месте";
  $("#photoDialogActions").innerHTML = "";
  $(".photo-dialog-preview").querySelectorAll(".gallery-dot-menu,.gallery-popover").forEach(node => node.remove());
  $(".photo-dialog-preview").insertAdjacentHTML("beforeend", `<button class="gallery-dot-menu" id="galleryDotMenu" aria-label="Действия с фотографией">⋯</button><section class="gallery-popover hidden" id="galleryPopover"><div class="path">${escapeHtml(item.relative_path)}</div><div class="meta"><span>${displayDate(item.captured_at)}</span><span>${bytes(item.size)}</span></div><div class="button-row"><button class="button" id="galleryCopy">Копировать</button><button class="button" id="galleryMove">Переместить</button><button class="button danger" id="galleryQuarantine">В карантин</button><a class="button" href="/photo/${item.id}" target="_blank" rel="noopener">Оригинал</a></div></section>`);
  $("#galleryDotMenu").onclick = () => $("#galleryPopover").classList.toggle("hidden");
  $("#galleryCopy").onclick = () => beginTransfer("copy", [item.id]);
  $("#galleryMove").onclick = () => beginTransfer("move", [item.id]);
  $("#galleryQuarantine").onclick = async () => { if (!confirm("Переместить фотографию в карантин?")) return; await quarantineMediaIds([item.id]); dialog.close(); };
  if (!dialog.open) dialog.showModal();
};
var galleryItems = [];
var galleryIndex = -1;
const albumWithGalleryItems = album;
album = async function (id, push = false) {
  await albumWithGalleryItems(id, push);
  const data = await api(`/api/library/media?container_id=${id}`);
  galleryItems = data.items;
  document.querySelectorAll("[data-media]").forEach(button => button.onclick = () => openPhoto(galleryItems.find(item => item.id === Number(button.dataset.media)), "library"));
};
const galleryViewerOpen = openPhoto;
openPhoto = (item, mode) => {
  if (mode !== "library") return galleryViewerOpen(item, mode);
  galleryIndex = galleryItems.findIndex(candidate => candidate.id === item.id);
  if (galleryIndex < 0) { galleryItems = [item]; galleryIndex = 0; }
  galleryViewerOpen(item, mode);
  const preview = $(".photo-dialog-preview");
  preview.querySelectorAll(".gallery-arrow,.gallery-filmstrip").forEach(node => node.remove());
  const thumbButtons = galleryItems.map((candidate, index) => `<button class="${index === galleryIndex ? "active" : ""}" data-gallery-index="${index}" aria-label="Открыть фото ${index + 1}"><img src="/thumbnail/${candidate.id}" onerror="this.src='/photo/${candidate.id}'" alt=""></button>`).join("");
  preview.insertAdjacentHTML("beforeend", `<button class="gallery-arrow prev" id="galleryPrevious" ${galleryIndex <= 0 ? "disabled" : ""} aria-label="Предыдущее фото">‹</button><button class="gallery-arrow next" id="galleryNext" ${galleryIndex >= galleryItems.length - 1 ? "disabled" : ""} aria-label="Следующее фото">›</button><nav class="gallery-filmstrip" aria-label="Фотографии альбома">${thumbButtons}</nav>`);
  $("#galleryPrevious").onclick = () => openPhoto(galleryItems[galleryIndex - 1], "library");
  $("#galleryNext").onclick = () => openPhoto(galleryItems[galleryIndex + 1], "library");
  document.querySelectorAll("[data-gallery-index]").forEach(button => button.onclick = () => openPhoto(galleryItems[Number(button.dataset.galleryIndex)], "library"));
  preview.querySelector(".gallery-filmstrip .active")?.scrollIntoView({block: "nearest", inline: "center"});
};
const galleryStageOpen = openPhoto;
openPhoto = (item, mode) => {
  galleryStageOpen(item, mode);
  if (mode !== "library") return;
  const preview = $(".photo-dialog-preview");
  const image = $("#photoDialogImage");
  let stage = preview.querySelector(".gallery-stage");
  if (!stage) { stage = document.createElement("div"); stage.className = "gallery-stage"; image.before(stage); stage.append(image); }
  const menu = preview.querySelector(".gallery-dot-menu");
  if (menu) stage.append(menu);
  document.querySelectorAll(".gallery-filmstrip img").forEach((thumb, index) => {
    const media = galleryItems[index];
    if (media) thumb.src = `/photo/${media.id}`;
  });
};
const galleryPreviewOpen = openPhoto;
openPhoto = (item, mode) => {
  galleryPreviewOpen(item, mode);
  if (mode !== "library") return;
  const strip = $(".gallery-filmstrip");
  document.querySelectorAll(".gallery-filmstrip img").forEach((thumb, index) => {
    const media = galleryItems[index];
    if (media) thumb.src = `/library-preview/${media.id}`;
  });
  strip.onwheel = event => {
    if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
      strip.scrollLeft += event.deltaY;
      event.preventDefault();
    }
  };
};
const albumTitleOpen = album;
album = async (id, push = false) => {
  await albumTitleOpen(id, push);
  const data = await api(`/api/library/albums?year=${encodeURIComponent(libraryShelf)}`);
  const current = data.items.find(item => item.id === id);
  $("#albumTitle").textContent = current?.name || "Альбом";
  $("#albumCrumbs").textContent = `Фото · ${libraryShelf || ""}`;
};
const albumFolderTitleOpen = album;
album = async (id, push = false) => {
  await albumFolderTitleOpen(id, push);
  const media = await api(`/api/library/media?container_id=${id}&page_size=1`);
  if (media.items[0]?.container_name) {
    $("#albumTitle").textContent = media.items[0].container_name;
  }
};
const galleryCloseOpen = openPhoto;
openPhoto = (item, mode) => {
  galleryCloseOpen(item, mode);
  if (mode !== "library") return;
  const stage = $(".gallery-stage");
  stage.querySelector(".gallery-close")?.remove();
  stage.insertAdjacentHTML("beforeend", '<button class="gallery-close" id="galleryClose" aria-label="Закрыть просмотр">×</button>');
  $("#galleryClose").onclick = () => $("#photoDialog").close();
};

const albumUpload = { active: false, albumId: null };
const albumUploadButton = $("#addAlbumPhotos");
const albumUploadInput = $("#albumPhotoInput");
const albumUploadStatus = $("#albumUploadStatus");
function setAlbumUploadStatus(title, message, percent = 0, errors = []) {
  albumUploadStatus.classList.remove("hidden");
  $("#albumUploadTitle").textContent = title;
  $("#albumUploadMessage").textContent = message;
  $("#albumUploadProgress").style.width = `${Math.max(0, Math.min(100, percent))}%`;
  $("#albumUploadErrors").innerHTML = errors.map(error => `<li>${escapeHtml(error)}</li>`).join("");
}
function uploadAlbumPhotos(files) {
  const selected = [...files];
  if (!selected.length || albumUpload.active || !albumUpload.albumId) return;
  albumUpload.active = true;
  albumUploadButton.disabled = true;
  const albumName = $("#albumTitle").textContent || "альбом";
  setAlbumUploadStatus(`Добавляем ${selected.length} фотографий в альбом «${albumName}»`, `Загружено: 0 из ${selected.length}`, 0);
  const form = new FormData();
  selected.forEach(file => form.append("files", file, file.name));
  const request = new XMLHttpRequest();
  request.open("POST", `/api/library/albums/${albumUpload.albumId}/photos`);
  request.upload.onprogress = event => {
    if (!event.lengthComputable) return;
    const percent = event.loaded / event.total * 100;
    setAlbumUploadStatus(`Добавляем ${selected.length} фотографий в альбом «${albumName}»`, `Передано: ${Math.round(percent)}%`, percent);
  };
  request.onload = async () => {
    let data = {};
    try { data = JSON.parse(request.responseText || "{}"); } catch (_) { /* handled below */ }
    if (request.status < 200 || request.status >= 300) {
      setAlbumUploadStatus("Фотографии не добавлены", data.detail || "Не удалось выполнить загрузку.", 0);
    } else {
      const errors = (data.results || []).filter(result => result.status !== "success").map(result => result.message || `${result.original_name}: не удалось добавить файл.`);
      const title = data.successful_count ? `Добавлено: ${data.successful_count} из ${data.requested_count} фотографий` : "Фотографии не добавлены";
      setAlbumUploadStatus(title, errors.length ? "Некоторые файлы не были добавлены." : "Все фотографии добавлены.", 100, errors);
      if (data.successful_count) await album(albumUpload.albumId, false);
    }
    albumUpload.active = false;
    albumUploadButton.disabled = false;
    albumUploadInput.value = "";
  };
  request.onerror = () => {
    setAlbumUploadStatus("Фотографии не добавлены", "Не удалось связаться с сервером.", 0);
    albumUpload.active = false;
    albumUploadButton.disabled = false;
    albumUploadInput.value = "";
  };
  request.send(form);
}
const albumUploadDialog = $("#albumUploadDialog");
const albumDropZone = $("#albumDropZone");
function startAlbumUpload(files) {
  if (!files.length || albumUpload.active) return;
  if (albumUploadDialog.open) albumUploadDialog.close();
  uploadAlbumPhotos(files);
}
albumUploadButton.onclick = () => { if (!albumUpload.active) albumUploadDialog.showModal(); };
$("#albumUploadBrowse").onclick = () => albumUploadInput.click();
$("#albumUploadDialogClose").onclick = () => albumUploadDialog.close();
albumUploadInput.onchange = () => startAlbumUpload(albumUploadInput.files);
["dragenter", "dragover", "dragleave", "drop"].forEach(eventName => {
  albumDropZone.addEventListener(eventName, event => { event.preventDefault(); event.stopPropagation(); });
});
["dragenter", "dragover"].forEach(eventName => albumDropZone.addEventListener(eventName, () => albumDropZone.classList.add("drag-over")));
["dragleave", "drop"].forEach(eventName => albumDropZone.addEventListener(eventName, () => albumDropZone.classList.remove("drag-over")));
albumDropZone.addEventListener("drop", event => startAlbumUpload([...event.dataTransfer.files]));
const albumRenderWithUpload = album;
album = async (id, push = false) => {
  albumUpload.albumId = Number(id);
  await albumRenderWithUpload(id, push);
};
const albumPagedOpen = album;
const albumPaging = { id: null, page: 1, total: 0, loading: false, items: [], observer: null };
function renderPagedAlbum() {
  $("#libraryMedia").innerHTML = albumPaging.items.map(item => `<article class="photo-card"><button class="photo-open" data-media="${item.id}"><img class="thumb" src="/thumbnail/${item.id}" onerror="this.src='/photo/${item.id}'"></button><div class="photo-info"><div class="path">${escapeHtml(item.file_name)}</div></div></article>`).join("");
  galleryItems = albumPaging.items;
  document.querySelectorAll("#libraryMedia [data-media]").forEach(button => button.onclick = () => openPhoto(galleryItems.find(item => item.id === Number(button.dataset.media)), "library"));
  const hasMore = albumPaging.items.length < albumPaging.total;
  $("#libraryPagination").innerHTML = hasMore ? '<div class="lazy-loader" id="albumLazyLoader" aria-label="Загрузить ещё фотографии"><span></span></div>' : "";
  const loader = $("#albumLazyLoader");
  if (albumPaging.observer) albumPaging.observer.disconnect();
  albumPaging.observer = null;
  if (loader && "IntersectionObserver" in window) {
    albumPaging.observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) loadAlbumPage(false);
    }, { rootMargin: "320px 0px" });
    albumPaging.observer.observe(loader);
  } else if (loader) {
    const onScroll = () => {
      const rect = loader.getBoundingClientRect();
      if (rect.top < window.innerHeight + 320) {
        window.removeEventListener("scroll", onScroll);
        loadAlbumPage(false);
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }
}
async function loadAlbumPage(reset = false) {
  if (albumPaging.loading || !albumPaging.id) return;
  albumPaging.loading = true;
  const page = reset ? 1 : albumPaging.page + 1;
  try {
    const data = await api(`/api/library/media?container_id=${albumPaging.id}&page=${page}&page_size=48`);
    albumPaging.page = page;
    albumPaging.total = data.total;
    albumPaging.items = reset ? data.items : [...albumPaging.items, ...data.items];
    renderPagedAlbum();
  } finally {
    albumPaging.loading = false;
  }
}
album = async (id, push = false) => {
  albumPaging.id = Number(id);
  await albumPagedOpen(id, push);
  await loadAlbumPage(true);
};
const albumPagingGalleryOpen = openPhoto;
openPhoto = (item, mode) => {
  if (mode !== "library") return albumPagingGalleryOpen(item, mode);
  if (!item) return;
  if (albumPaging.items.length) galleryItems = albumPaging.items;
  albumPagingGalleryOpen(item, mode);
  const next = $("#galleryNext");
  const canLoadMore = albumPaging.id && albumPaging.items.length < albumPaging.total;
  if (next && canLoadMore && galleryIndex >= galleryItems.length - 1) {
    next.disabled = false;
    next.onclick = async () => {
      await loadAlbumPage(false);
      const nextItem = galleryItems[galleryIndex + 1];
      if (nextItem) openPhoto(nextItem, "library");
    };
  }
};

const unsorted = {
  page: 1,
  total: 0,
  pageSize: 48,
  items: [],
  selected: new Set(),
  loading: false,
  sourcesLoaded: false,
  facets: { years: [], months: [] },
  collapsed: new Set(JSON.parse(localStorage.getItem("unsortedCollapsedGroups") || "[]")),
  observer: null,
};
const monthNames = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];
function photoWord(count) {
  const lastTwo = Math.abs(count) % 100;
  const last = lastTwo % 10;
  if (lastTwo >= 11 && lastTwo <= 14) return "фотографий";
  if (last === 1) return "фотография";
  if (last >= 2 && last <= 4) return "фотографии";
  return "фотографий";
}
function unsortedQuery(page = 1) {
  const params = new URLSearchParams({ page, page_size: unsorted.pageSize, date_status: $("#unsortedDateStatus").value, sort: $("#unsortedSort").value });
  const source = $("#unsortedSource").value;
  if (source !== "__all") params.set("source_name", source);
  if ($("#unsortedYear").value) params.set("year", $("#unsortedYear").value);
  if ($("#unsortedMonth").value) params.set("month", $("#unsortedMonth").value);
  return params;
}
async function loadUnsortedSources() {
  const data = await api("/api/library/unsorted/sources");
  $("#unsortedSource").innerHTML = '<option value="__all">Все источники</option>' + (data.items || []).map(item => `<option value="${escapeHtml(item.source_name ?? "")}">${escapeHtml(item.label)} · ${item.count}</option>`).join("");
  unsorted.sourcesLoaded = true;
}
function ensureUnsortedFacets() {
  const years = (unsorted.facets.years || []).map(item => Number(item.year)).filter(Number.isFinite);
  const current = $("#unsortedYear").value;
  $("#unsortedYear").innerHTML = '<option value="">Все годы</option>' + years.map(year => `<option value="${year}">${year}</option>`).join("");
  $("#unsortedYear").value = years.includes(Number(current)) ? current : "";
  const months = new Set((unsorted.facets.months || []).map(item => Number(item.month)).filter(month => month >= 1 && month <= 12));
  const currentMonth = $("#unsortedMonth").value;
  $("#unsortedMonth").innerHTML = '<option value="">Все месяцы</option>' + monthNames.map((name, index) => {
    const value = index + 1;
    return `<option value="${value}" ${months.size && !months.has(value) ? "disabled" : ""}>${name}</option>`;
  }).join("");
  $("#unsortedMonth").value = months.has(Number(currentMonth)) ? currentMonth : "";
}
function unsortedCard(item) {
  const source = item.source_name || "Без источника";
  const dateNote = item.captured_at ? `Съёмка: ${displayDate(item.captured_at)}` : `Дата съёмки не определена · Импорт: ${displayDate(item.imported_at)}`;
  return `<article class="photo-card" data-unsorted-card="${item.id}"><input type="checkbox" data-unsorted-id="${item.id}" aria-label="Выбрать"><button class="photo-open" type="button"><img class="thumb" loading="lazy" src="/thumbnail/${item.id}" onerror="this.onerror=null;this.src='/photo/${item.id}'" alt="${escapeHtml(item.file_name || item.relative_path)}"></button><div class="photo-info"><div class="path">${escapeHtml(item.file_name || item.relative_path)}</div><div class="reason">${escapeHtml(source)}${item.source_relative_path ? ` · ${escapeHtml(item.source_relative_path)}` : ""}</div><div class="meta"><span>${escapeHtml(dateNote)}</span><span>${bytes(item.size)}</span></div></div></article>`;
}
function unsortedGroupKey(item) {
  const year = item.effective_year || "Без даты";
  const month = item.effective_month || -1;
  return `${year}-${month}`;
}
function renderUnsortedGroups() {
  const groups = [];
  for (const item of unsorted.items) {
    const key = unsortedGroupKey(item);
    let group = groups.find(candidate => candidate.key === key);
    if (!group) {
      const year = item.effective_year || "Без даты";
      const month = item.effective_month ? monthNames[item.effective_month - 1] : "Без месяца";
      group = { key, year, month, items: [] };
      groups.push(group);
    }
    group.items.push(item);
  }
  return groups.map(group => {
    const collapsed = unsorted.collapsed.has(group.key);
    return `<section class="unsorted-group ${collapsed ? "collapsed" : ""}" data-unsorted-group="${escapeHtml(group.key)}"><button class="unsorted-group-heading" type="button" data-unsorted-toggle="${escapeHtml(group.key)}" aria-expanded="${collapsed ? "false" : "true"}"><span class="unsorted-caret">${collapsed ? "▸" : "▾"}</span><h2>${escapeHtml(group.year)}</h2><span>${escapeHtml(group.month)} · ${group.items.length} ${photoWord(group.items.length)}</span></button><div class="cards">${group.items.map(unsortedCard).join("")}</div></section>`;
  }).join("");
}
function renderUnsorted() {
  $("#unsortedSummary").textContent = `${unsorted.total} ${photoWord(unsorted.total)} · выбрано ${unsorted.selected.size}`;
  $("#unsortedEmpty").classList.toggle("hidden", unsorted.items.length !== 0);
  $("#unsortedCards").innerHTML = renderUnsortedGroups();
  $$("[data-unsorted-toggle]").forEach(button => {
    button.onclick = () => {
      const key = button.dataset.unsortedToggle;
      unsorted.collapsed.has(key) ? unsorted.collapsed.delete(key) : unsorted.collapsed.add(key);
      localStorage.setItem("unsortedCollapsedGroups", JSON.stringify([...unsorted.collapsed]));
      renderUnsorted();
    };
  });
  $$("[data-unsorted-card]").forEach((node, index) => {
    const item = unsorted.items[index];
    const checkbox = $("[data-unsorted-id]", node);
    checkbox.checked = unsorted.selected.has(item.id);
    node.classList.toggle("selected", checkbox.checked);
    checkbox.onchange = () => {
      checkbox.checked ? unsorted.selected.add(item.id) : unsorted.selected.delete(item.id);
      node.classList.toggle("selected", checkbox.checked);
      $("#unsortedSummary").textContent = `${unsorted.total} ${photoWord(unsorted.total)} · выбрано ${unsorted.selected.size}`;
    };
    $(".photo-open", node).onclick = () => openPhoto(item, "unsorted");
  });
  const hasMore = unsorted.items.length < unsorted.total;
  $("#unsortedPagination").innerHTML = hasMore ? '<div class="lazy-loader" id="unsortedLazyLoader" aria-label="Загрузить ещё фотографии"><span></span></div>' : "";
  if (unsorted.observer) unsorted.observer.disconnect();
  unsorted.observer = null;
  const loader = $("#unsortedLazyLoader");
  if (loader) {
    unsorted.observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting) && !unsorted.loading) {
        loadUnsorted(unsorted.page + 1, true).catch(error => toast(error.message));
      }
    }, { rootMargin: "500px" });
    unsorted.observer.observe(loader);
  }
}
async function loadUnsorted(page = 1, append = false) {
  if (unsorted.loading) return;
  if (!append) history.replaceState(null, "", "#unsorted");
  setActiveTopTab("photos");
  activateView("photo-unsorted");
  setActiveLibraryNav('[data-library-view="photo-unsorted"]');
  $("#photoNav").classList.remove("hidden");
  $("#sortingNav").classList.add("hidden");
  unsorted.loading = true;
  try {
    await ensureLibraryShelves();
    if (!unsorted.sourcesLoaded) await loadUnsortedSources();
    const data = await api(`/api/library/unsorted?${unsortedQuery(page)}`);
    unsorted.page = page;
    unsorted.total = data.total;
    unsorted.items = append ? [...unsorted.items, ...data.items] : data.items;
    unsorted.facets = data.facets || unsorted.facets;
    if (!append) unsorted.selected.clear();
    ensureUnsortedFacets();
    renderUnsorted();
  } finally {
    unsorted.loading = false;
  }
}
function selectedUnsortedIds() {
  return [...unsorted.selected];
}
function setUnsortedCreateAlbumError(message = "") {
  $("#unsortedCreateAlbumError").textContent = message;
  $("#unsortedCreateAlbumError").classList.toggle("hidden", !message);
}
async function openUnsortedCreateAlbumDialog() {
  const ids = selectedUnsortedIds();
  if (!ids.length) return toast("Сначала выберите фотографии");
  const data = await api("/api/library/shelves?library_root=photos");
  const shelfYears = (data.items || []).map(item => String(item.year));
  const years = [...new Set(shelfYears)].filter(Boolean).sort((a, b) => b.localeCompare(a, "ru"));
  if (!years.length) return toast("Сначала создайте полку для альбома");
  const currentYear = $("#unsortedYear").value || years[0] || new Date().getFullYear();
  $("#unsortedCreateAlbumCount").textContent = `Будет перемещено: ${ids.length} ${photoWord(ids.length)}`;
  $("#unsortedCreateAlbumYear").innerHTML = years.map(year => `<option value="${escapeHtml(year)}">${escapeHtml(year)}</option>`).join("");
  $("#unsortedCreateAlbumYear").value = years.includes(String(currentYear)) ? String(currentYear) : years[0];
  $("#unsortedCreateAlbumName").value = "";
  setUnsortedCreateAlbumError();
  $("#unsortedCreateAlbumSubmit").disabled = false;
  $("#unsortedCreateAlbumDialog").showModal();
  requestAnimationFrame(() => $("#unsortedCreateAlbumName").focus());
}
async function submitUnsortedCreateAlbum(event) {
  event.preventDefault();
  const ids = selectedUnsortedIds();
  const year = $("#unsortedCreateAlbumYear").value;
  const name = $("#unsortedCreateAlbumName").value.trim();
  if (!ids.length) return setUnsortedCreateAlbumError("Сначала выберите фотографии.");
  if (!name) return setUnsortedCreateAlbumError("Введите название альбома.");
  $("#unsortedCreateAlbumSubmit").disabled = true;
  setUnsortedCreateAlbumError();
  try {
    const data = await api("/api/library/unsorted/create-album", {
      method: "POST",
      body: JSON.stringify({ year, name, media_ids: ids, rename_on_conflict: true }),
    });
    $("#unsortedCreateAlbumDialog").close();
    toast(`Альбом «${data.album.name}» создан`);
    unsorted.sourcesLoaded = false;
    await loadUnsorted(1);
    loadLibraryShelves?.().catch(() => {});
    refreshSummary();
  } catch (error) {
    setUnsortedCreateAlbumError(error.message);
    $("#unsortedCreateAlbumSubmit").disabled = false;
  }
}
function toDateTimeLocal(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}
function setUnsortedDateError(message = "") {
  $("#unsortedDateError").textContent = message;
  $("#unsortedDateError").classList.toggle("hidden", !message);
}
function openUnsortedDateDialog(item) {
  state.photoItem = item;
  $("#unsortedDatePath").textContent = item.relative_path || item.file_name || "";
  $("#unsortedDateInput").value = toDateTimeLocal(item.captured_at);
  setUnsortedDateError();
  $("#unsortedDateSubmit").disabled = false;
  $("#unsortedDateDialog").showModal();
  requestAnimationFrame(() => $("#unsortedDateInput").focus());
}
async function saveUnsortedCaptureDate(value) {
  const item = state.photoItem;
  if (!item) return;
  $("#unsortedDateSubmit").disabled = true;
  setUnsortedDateError();
  try {
    const updated = await api(`/api/library/unsorted/${item.id}/captured-at`, {
      method: "PATCH",
      body: JSON.stringify({ captured_at: value || null }),
    });
    Object.assign(item, updated);
    $("#unsortedDateDialog").close();
    $("#photoDialog").close();
    toast(value ? "Дата съёмки сохранена" : "Дата съёмки сброшена");
    await loadUnsorted(1);
  } catch (error) {
    setUnsortedDateError(error.message);
    $("#unsortedDateSubmit").disabled = false;
  }
}
function setupUnsortedControls() {
  const toolbar = $(".unsorted-toolbar");
  if (toolbar && !$("#unsortedSort")) {
    toolbar.insertAdjacentHTML("beforeend", `<label>Порядок<select id="unsortedSort"><option value="desc">Сначала новые</option><option value="asc">Сначала старые</option></select></label><div class="unsorted-filter-buttons"><button class="button primary" id="unsortedApplyFilters" type="button">Применить</button><button class="button" id="unsortedResetFilters" type="button">Сбросить</button></div>`);
  }
  if (!$("#unsortedScanStatus")) {
    $(".unsorted-toolbar").insertAdjacentHTML("afterend", `<section class="upload-status hidden" id="unsortedScanStatus" aria-live="polite"><strong id="unsortedScanTitle">Обновляем индекс</strong><div class="progress indeterminate"><span id="unsortedScanProgress"></span></div><p class="muted" id="unsortedScanMessage"></p></section>`);
  }
  if (!$("#unsortedCreateAlbum")) {
    $("#unsortedMove").insertAdjacentHTML("afterend", '<button class="button" id="unsortedCreateAlbum" type="button">Создать альбом</button>');
  }
  if (!$("#unsortedCreateAlbumDialog")) {
    document.body.insertAdjacentHTML("beforeend", `<dialog id="unsortedCreateAlbumDialog"><form id="unsortedCreateAlbumForm"><div class="dialog-heading"><div><h2>Создать альбом из выбранных</h2><p class="muted" id="unsortedCreateAlbumCount"></p></div><button type="button" class="icon-button" data-close aria-label="Закрыть">×</button></div><label>Полка<select id="unsortedCreateAlbumYear" required></select></label><label>Название альбома<input id="unsortedCreateAlbumName" maxlength="120" autocomplete="off" required placeholder="Например, Поездка в Гюмри"></label><p class="notice danger hidden" id="unsortedCreateAlbumError" role="alert"></p><div class="dialog-actions"><button type="button" class="button" data-close>Отмена</button><button class="button primary" id="unsortedCreateAlbumSubmit" type="submit">Создать и переместить</button></div></form></dialog>`);
  }
  if (!$("#unsortedDateDialog")) {
    document.body.insertAdjacentHTML("beforeend", `<dialog id="unsortedDateDialog"><form id="unsortedDateForm"><div class="dialog-heading"><div><h2>Дата съёмки</h2><p class="muted" id="unsortedDatePath"></p></div><button type="button" class="icon-button" data-close aria-label="Закрыть">×</button></div><label>Дата съёмки<input id="unsortedDateInput" type="text" inputmode="numeric" autocomplete="off" placeholder="2020, 2020-05 или 2020-05-14"></label><p class="muted">Можно указать только год, год и месяц, дату или дату со временем.</p><p class="notice danger hidden" id="unsortedDateError" role="alert"></p><div class="dialog-actions"><button type="button" class="button" id="unsortedDateClear">Сбросить дату</button><button type="button" class="button" data-close>Отмена</button><button class="button primary" id="unsortedDateSubmit" type="submit">Сохранить</button></div></form></dialog>`);
  }
  if (!$("#unsortedUploadDialog")) {
    document.body.insertAdjacentHTML("beforeend", `<dialog id="unsortedUploadDialog"><form id="unsortedUploadForm"><div class="dialog-heading"><div><h2>Добавить фотографии</h2><p class="muted">Выберите источник для новых неразобранных фотографий.</p></div><button type="button" class="icon-button" data-close aria-label="Закрыть">×</button></div><label>Источник<select id="unsortedUploadSource"></select></label><label class="radio"><input type="checkbox" id="unsortedUploadNewSourceToggle"> Новый источник</label><label id="unsortedUploadNewSourceLabel" class="hidden">Название источника<input id="unsortedUploadNewSource" maxlength="120" autocomplete="off" placeholder="Например, Bella Phone"></label><label>Фотографии<input id="unsortedUploadDialogFiles" type="file" multiple required accept=".jpg,.jpeg,.png,.webp,.gif,.bmp,.tif,.tiff,.heic,.heif,image/jpeg,image/png,image/webp,image/gif,image/bmp,image/tiff,image/heic,image/heif"></label><p class="notice danger hidden" id="unsortedUploadDialogError" role="alert"></p><div class="dialog-actions"><button type="button" class="button" data-close>Отмена</button><button class="button primary" id="unsortedUploadDialogSubmit" type="submit">Добавить</button></div></form></dialog>`);
  }
  $$("[data-close]").forEach(button => button.onclick = () => button.closest("dialog").close());
}
function setUnsortedUploadDialogError(message = "") {
  $("#unsortedUploadDialogError").textContent = message;
  $("#unsortedUploadDialogError").classList.toggle("hidden", !message);
}
async function openUnsortedUploadDialog() {
  const data = await api("/api/library/unsorted/sources");
  const sources = (data.items || []).map(item => item.source_name).filter(Boolean);
  const options = sources.length ? sources : ["Manual Import"];
  $("#unsortedUploadSource").innerHTML = options.map(source => `<option value="${escapeHtml(source)}">${escapeHtml(source)}</option>`).join("");
  $("#unsortedUploadNewSourceToggle").checked = false;
  $("#unsortedUploadNewSourceLabel").classList.add("hidden");
  $("#unsortedUploadNewSource").value = "";
  $("#unsortedUploadDialogFiles").value = "";
  $("#unsortedUploadDialogSubmit").disabled = false;
  setUnsortedUploadDialogError();
  $("#unsortedUploadDialog").showModal();
  requestAnimationFrame(() => $("#unsortedUploadDialogFiles").focus());
}
function uploadUnsortedPhotos(files, sourceName = "") {
  const selected = [...files];
  if (!selected.length) return;
  const form = new FormData();
  if (sourceName) form.append("source_name", sourceName);
  selected.forEach(file => form.append("files", file, file.name));
  $("#unsortedUploadStatus").classList.remove("hidden");
  $("#unsortedUploadTitle").textContent = `Добавляем ${selected.length} ${photoWord(selected.length)}`;
  $("#unsortedUploadMessage").textContent = "Передаём файлы";
  $("#unsortedUploadProgress").style.width = "20%";
  fetch("/api/library/unsorted/photos", { method: "POST", body: form })
    .then(async response => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Не удалось загрузить фотографии");
      const errors = (data.results || []).filter(item => item.status !== "success").map(item => item.message || "Файл не добавлен");
      $("#unsortedUploadTitle").textContent = data.successful_count ? `Добавлено: ${data.successful_count} из ${data.requested_count}` : "Фотографии не добавлены";
      $("#unsortedUploadMessage").textContent = errors.length ? "Некоторые файлы не были добавлены." : "Готово";
      $("#unsortedUploadProgress").style.width = "100%";
      $("#unsortedUploadErrors").innerHTML = errors.map(error => `<li>${escapeHtml(error)}</li>`).join("");
      unsorted.sourcesLoaded = false;
      return loadUnsorted(1);
    })
    .catch(error => {
      $("#unsortedUploadTitle").textContent = "Фотографии не добавлены";
      $("#unsortedUploadMessage").textContent = error.message;
      $("#unsortedUploadProgress").style.width = "0";
    });
}
setupUnsortedControls();
document.querySelector('[data-library-view="photo-unsorted"]').onclick = () => loadUnsorted(1).catch(error => toast(error.message));
document.querySelector('[data-library-view="photo-home"]').onclick = () => home(true);
$("#unsortedUploadNewSourceToggle").onchange = () => {
  $("#unsortedUploadNewSourceLabel").classList.toggle("hidden", !$("#unsortedUploadNewSourceToggle").checked);
  if ($("#unsortedUploadNewSourceToggle").checked) requestAnimationFrame(() => $("#unsortedUploadNewSource").focus());
};
$("#unsortedUploadForm").onsubmit = event => {
  event.preventDefault();
  const sourceName = $("#unsortedUploadNewSourceToggle").checked ? $("#unsortedUploadNewSource").value.trim() : $("#unsortedUploadSource").value;
  if (!sourceName) return setUnsortedUploadDialogError("Введите название источника.");
  const files = $("#unsortedUploadDialogFiles").files;
  if (!files.length) return setUnsortedUploadDialogError("Выберите фотографии.");
  $("#unsortedUploadDialogSubmit").disabled = true;
  $("#unsortedUploadDialog").close();
  uploadUnsortedPhotos(files, sourceName);
};
$("#unsortedCreateAlbum").onclick = () => openUnsortedCreateAlbumDialog().catch(error => toast(error.message));
$("#unsortedCreateAlbumForm").onsubmit = submitUnsortedCreateAlbum;
$("#unsortedDateForm").onsubmit = event => {
  event.preventDefault();
  saveUnsortedCaptureDate($("#unsortedDateInput").value).catch(error => setUnsortedDateError(error.message));
};
$("#unsortedDateClear").onclick = () => saveUnsortedCaptureDate(null).catch(error => setUnsortedDateError(error.message));
$("#unsortedApplyFilters").onclick = () => loadUnsorted(1).catch(error => toast(error.message));
$("#unsortedResetFilters").onclick = () => {
  $("#unsortedSource").value = "__all";
  $("#unsortedYear").value = "";
  $("#unsortedMonth").value = "";
  $("#unsortedDateStatus").value = "all";
  $("#unsortedSort").value = "desc";
  loadUnsorted(1).catch(error => toast(error.message));
};
$("#unsortedRefresh").onclick = async () => {
  const status = $("#unsortedScanStatus");
  const button = $("#unsortedRefresh");
  status.classList.remove("hidden");
  $("#unsortedScanStatus .progress").classList.add("indeterminate");
  $("#unsortedScanMessage").textContent = "Сканируем папку Unsorted и обновляем список фотографий";
  $("#unsortedScanProgress").style.width = "45%";
  button.disabled = true;
  try {
    const report = await api("/api/library/scan", { method: "POST", body: JSON.stringify({ library_root: "photos" }) });
    $("#unsortedScanStatus .progress").classList.remove("indeterminate");
    $("#unsortedScanProgress").style.width = "100%";
    $("#unsortedScanMessage").textContent = `Готово: обработано ${report.indexed} файлов, без изменений ${report.unchanged}, отсутствует ${report.missing}.`;
    unsorted.sourcesLoaded = false;
    await loadUnsorted(1);
    toast("Индекс обновлён");
  } catch (error) {
    $("#unsortedScanStatus .progress").classList.remove("indeterminate");
    $("#unsortedScanProgress").style.width = "0";
    $("#unsortedScanMessage").textContent = error.message;
  } finally {
    button.disabled = false;
  }
};
$("#addUnsortedPhotos").onclick = () => openUnsortedUploadDialog().catch(error => toast(error.message));
$("#unsortedPhotoInput").onchange = () => { uploadUnsortedPhotos($("#unsortedPhotoInput").files); $("#unsortedPhotoInput").value = ""; };
$("#unsortedSelectAll").onclick = () => {
  unsorted.items.forEach(item => unsorted.selected.add(item.id));
  renderUnsorted();
};
$("#unsortedMove").onclick = () => {
  const ids = selectedUnsortedIds();
  if (!ids.length) return toast("Сначала выберите фотографии");
  beginTransfer("move", ids);
  state.transfer.fromUnsorted = true;
};
$("#unsortedQuarantine").onclick = async () => {
  const ids = selectedUnsortedIds();
  if (!ids.length) return toast("Сначала выберите фотографии");
  await api("/api/library/unsorted/quarantine", { method: "POST", body: JSON.stringify({ media_ids: ids }) });
  toast("Фотографии перемещены в карантин");
  await loadUnsorted(1);
  refreshSummary();
};
const unsortedTransferConfirm = $("#transferConfirm").onclick;
$("#transferConfirm").onclick = async () => {
  const fromUnsorted = Boolean(state.transfer?.fromUnsorted);
  await unsortedTransferConfirm();
  if (fromUnsorted) {
    unsorted.sourcesLoaded = false;
    await loadUnsorted(1);
  }
};
const unsortedOpenPhoto = openPhoto;
openPhoto = (item, mode) => {
  if (mode !== "unsorted") return unsortedOpenPhoto(item, mode);
  state.photoItem = item;
  state.photoMode = mode;
  $("#photoDialog").classList.remove("gallery-mode");
  $("#photoDialogImage").src = `/photo/${item.id}`;
  $("#photoDialogPath").textContent = item.relative_path;
  $("#photoDialogReason").textContent = `${item.source_name || "Без источника"} · ${item.captured_at ? "Дата съёмки определена" : "Дата съёмки не определена, используется дата импорта"}`;
  $("#photoDialogDate").textContent = item.captured_at ? displayDate(item.captured_at) : displayDate(item.imported_at);
  $("#photoDialogSize").textContent = bytes(item.size);
  $("#photoDialogDimensions").textContent = `${item.width || "?"} × ${item.height || "?"}`;
  $("#photoDialogActions").innerHTML = `<button class="button" id="unsortedPhotoDate">Изменить дату</button><button class="button primary" id="unsortedPhotoMove">Переместить в альбом</button><button class="button danger" id="unsortedPhotoQuarantine">В карантин</button><a class="button" href="/photo/${item.id}" target="_blank" rel="noopener">Оригинал</a>`;
  $("#unsortedPhotoDate").onclick = () => openUnsortedDateDialog(item);
  $("#unsortedPhotoMove").onclick = () => { beginTransfer("move", [item.id]); state.transfer.fromUnsorted = true; };
  $("#unsortedPhotoQuarantine").onclick = async () => { await api("/api/library/unsorted/quarantine", { method: "POST", body: JSON.stringify({ media_ids: [item.id] }) }); $("#photoDialog").close(); await loadUnsorted(1); refreshSummary(); };
  if (!$("#photoDialog").open) $("#photoDialog").showModal();
};
restoreRouteFromHash().catch(error => toast(error.message));
