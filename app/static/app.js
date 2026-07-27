const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = { category: "quality", page: 1, selected: new Set(), quarantineSelected: new Set(), latestJob: null, photoItem: null, photoMode: null, photoIndex: -1, reviewItems: [], reviewTotal: 0, reviewComplete: false, reviewLoading: false, scanScope: "", picker: null, pickerPath: "", pickerCache: new Map(), transfer: null };
const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" }[c]));
const bytes = value => value ? (value < 1024 * 1024 ? `${Math.round(value / 1024)} КБ` : `${(value / 1024 / 1024).toFixed(1)} МБ`) : "0 Б";
const displayDate = value => value ? new Date(value).toLocaleDateString("ru-RU") : "—";
function toast(message) { const target = $("#toast"); target.textContent = message; target.classList.add("show"); clearTimeout(toast.timer); toast.timer = setTimeout(() => target.classList.remove("show"), 3400); }
async function api(url, options = {}) { const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options }); const data = await response.json().catch(() => ({})); if (!response.ok) { const detail = Array.isArray(data.detail) ? "Проверьте выбранные файлы и папку назначения" : data.detail; const error = new Error(detail || data.failures?.[0]?.error || "Не удалось выполнить действие"); error.data = data; error.status = response.status; throw error; } return data; }
function showView(view) { $$(".view").forEach(node => node.classList.toggle("active", node.id === `view-${view}`)); $$(".nav-item").forEach(node => node.classList.toggle("active", node.dataset.view === view)); $("#sidebar").classList.remove("open"); if (view === "quarantine") loadQuarantine(); if (view === "settings") loadSettings(); if (view === "audit") loadAudit(); }

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
async function loadLibraryShelves() {
  const data = await api("/api/library/shelves");
  const items = data.items || [];
  $("#shelfGrid").innerHTML = items.map(item => `<button class="shelf-card" data-shelf="${escapeHtml(item.year)}"><strong>${escapeHtml(item.year)}</strong><span>${item.album_count} альбомов · ${item.media_count} фото</span></button>`).join("");
  $("#photoShelves").innerHTML = items.map(item => `<button class="nav-item" data-shelf="${escapeHtml(item.year)}">${escapeHtml(item.year)}</button>`).join("");
  $("#photoEmpty").classList.toggle("hidden", items.length > 0);
  $$('[data-shelf]').forEach(button => button.onclick = () => openLibraryShelf(button.dataset.shelf));
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
  activateView("photo-home");
  setActiveLibraryNav('[data-library-view="photo-home"]');
  loadLibraryShelves().catch(error => toast(error.message));
}
function openSortingView(view = "dashboard") {
  activateView(view);
  $("#photoNav").classList.add("hidden");
  $("#sortingNav").classList.remove("hidden");
  $$("#sortingNav .nav-item").forEach(node => node.classList.toggle("active", node.dataset.view === view));
}
$$(".top-tab").forEach(tab => tab.onclick = () => {
  $$(".top-tab").forEach(item => item.classList.toggle("active", item === tab));
  if (tab.dataset.section === "photos") {
    $("#photoNav").classList.remove("hidden");
    $("#sortingNav").classList.add("hidden");
    openPhotoHome();
  } else if (tab.dataset.section === "videos") {
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
openPhotoHome();
let libraryShelf = null;
function route(s,a,p){const h=a?`#shelf=${encodeURIComponent(s)}&album=${a}`:s?`#shelf=${encodeURIComponent(s)}`:"#photos";p?history.pushState(null,"",h):history.replaceState(null,"",h)}
async function home(p=false){if(p)route(null,null,true);activateView("photo-home");$("#photoNav").classList.remove("hidden");$("#sortingNav").classList.add("hidden");const d=await api("/api/library/shelves");$("#shelfGrid").innerHTML=d.items.map(i=>`<button class="shelf-card" data-shelf="${escapeHtml(i.year)}"><span class="shelf-cover">${i.cover_media_id?`<img src="/thumbnail/${i.cover_media_id}" onerror="this.src='/photo/${i.cover_media_id}'">`:""}</span><strong>${escapeHtml(i.year)}</strong><span>${i.album_count} альбомов · ${i.media_count} фото</span></button>`).join("");$("#photoShelves").innerHTML=d.items.map(i=>`<button class="nav-item" data-shelf="${escapeHtml(i.year)}">${escapeHtml(i.year)}</button>`).join("");document.querySelectorAll("[data-shelf]").forEach(b=>b.onclick=()=>shelf(b.dataset.shelf,true))}
async function shelf(s,p=false){libraryShelf=s;if(p)route(s,null,true);activateView("photo-year");$("#yearTitle").textContent=s;const d=await api(`/api/library/albums?year=${encodeURIComponent(s)}`);$("#albumGrid").innerHTML=d.items.map(i=>`<button class="album-card" data-album="${i.id}"><div class="album-cover">${i.cover_media_id?`<img src="/thumbnail/${i.cover_media_id}" onerror="this.src='/photo/${i.cover_media_id}'">`:""}</div><strong>${escapeHtml(i.name)}</strong><span>${i.media_count} фото</span></button>`).join("");document.querySelectorAll("[data-album]").forEach(b=>b.onclick=()=>album(+b.dataset.album,true))}
async function album(id,p=false){if(p)route(libraryShelf,id,true);activateView("photo-album");const d=await api(`/api/library/media?container_id=${id}`);$("#libraryMedia").innerHTML=d.items.map(i=>`<article class="photo-card"><button class="photo-open" data-media="${i.id}"><img class="thumb" src="/thumbnail/${i.id}" onerror="this.src='/photo/${i.id}'"></button><div class="photo-info"><div class="path">${escapeHtml(i.file_name)}</div></div></article>`).join("");document.querySelectorAll("[data-media]").forEach(b=>b.onclick=()=>openPhoto(d.items.find(i=>i.id===+b.dataset.media),"library"))}
window.onpopstate=()=>{const q=new URLSearchParams(location.hash.slice(1)),s=q.get("shelf"),a=+q.get("album");s&&a?(libraryShelf=s,album(a)):s?shelf(s):home()};$("#backToPhotos").onclick=()=>home(true);$("#backToYear").onclick=()=>shelf(libraryShelf,true);const oldOpen=openPhoto;openPhoto=(i,m)=>{if(m!=="library")return oldOpen(i,m);$("#photoDialogImage").src=`/photo/${i.id}`;$("#photoDialogPath").textContent=i.relative_path;$("#photoDialogReason").textContent="Оригинал остаётся на месте";$("#photoDialogActions").innerHTML=`<a class="button" href="/photo/${i.id}" target="_blank">Открыть оригинал</a><button class="button" id="closeLibraryPhoto">Закрыть</button>`;$("#closeLibraryPhoto").onclick=()=>$("#photoDialog").close();$("#photoDialog").showModal()};if(!location.hash)route(null,null,false);home();
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
