(() => {
  const registry = {
    shelf: [
      { id: "shelf.createAlbum", label: "Создать альбом", icon: "+", group: "main", status: "ready" },
      { id: "shelf.importPhotos", label: "Импортировать фотографии", icon: "⇪", group: "main", status: "ready" },
      { id: "shelf.openUnsorted", label: "Просмотреть «Неразобранное»", icon: "□", group: "main", status: "ready" },
      { id: "shelf.startKiosk", label: "Запустить фотокиоск", icon: "▶", group: "kiosk", status: "planned" },
    ],
    album: [
      { id: "album.addPhotos", label: "Добавить фотографии", icon: "⇪", group: "main", status: "ready" },
      { id: "album.rename", label: "Переименовать", icon: "✎", group: "main", status: "ready" },
      { id: "album.changeCover", label: "Изменить обложку", icon: "▧", group: "main", status: "planned" },
      { id: "album.editDescription", label: "Изменить описание", icon: "☰", group: "main", status: "planned" },
      { id: "album.editDate", label: "Изменить дату", icon: "◷", group: "main", status: "planned" },
      { id: "album.moveToShelf", label: "Переместить на другую полку", icon: "⇄", group: "main", status: "planned" },
      { id: "album.merge", label: "Объединить с альбомом", icon: "⧉", group: "main", status: "planned" },
      { id: "album.export", label: "Экспортировать альбом", icon: "⇩", group: "main", status: "planned" },
      { id: "album.share", label: "Поделиться", icon: "↗", group: "main", status: "planned" },
      { id: "album.startKiosk", label: "Запустить фотокиоск", icon: "▶", group: "kiosk", status: "planned" },
      { id: "album.quarantine", label: "Отправить в карантин", icon: "!", group: "danger", status: "planned", destructive: true },
    ],
    photo: [
      { id: "photo.addTags", label: "Добавить метки", icon: "⌑", group: "main", status: "planned" },
      { id: "photo.addToSection", label: "Добавить в раздел", icon: "▤", group: "main", status: "planned" },
      { id: "photo.setAsAlbumCover", label: "Сделать обложкой альбома", icon: "▧", group: "main", status: "planned" },
      { id: "photo.editDateTime", label: "Изменить дату и время", icon: "◷", group: "main", status: "planned" },
      { id: "photo.moveToAlbum", label: "Переместить в другой альбом", icon: "⇄", group: "main", status: "planned" },
      { id: "photo.rotate", label: "Повернуть", icon: "↻", group: "main", status: "planned" },
      { id: "photo.downloadOriginal", label: "Скачать оригинал", icon: "⇩", group: "main", status: "planned" },
      { id: "photo.showInfo", label: "Показать информацию", icon: "i", group: "info", status: "ready" },
      { id: "photo.quarantine", label: "Отправить в карантин", icon: "!", group: "danger", status: "ready", destructive: true },
    ],
    selection: [
      { id: "selection.addTags", label: "Добавить метки", icon: "⌑", group: "main", status: "planned" },
      { id: "selection.addToSection", label: "Добавить в раздел", icon: "▤", group: "main", status: "planned" },
      { id: "selection.move", label: "Переместить", icon: "⇄", group: "main", status: "planned" },
      { id: "selection.export", label: "Экспортировать", icon: "⇩", group: "main", status: "planned" },
      { id: "selection.quarantine", label: "Отправить в карантин", icon: "!", group: "danger", status: "ready", destructive: true },
    ],
    quarantine: [
      { id: "quarantine.restore", label: "Восстановить", icon: "↩", group: "main", status: "ready" },
      { id: "quarantine.showOrigin", label: "Показать исходное расположение", icon: "⌖", group: "main", status: "ready" },
      { id: "quarantine.deleteForever", label: "Удалить окончательно", icon: "!", group: "danger", status: "ready", destructive: true },
    ],
  };
  const groupOrder = ["main", "info", "kiosk", "danger"];
  const running = new Set();
  let activeMenu = null;
  let currentAlbum = null;

  function isInactive(action) { return action.status === "planned" || action.status === "disabled"; }
  function actionsFor(entity, context = {}) {
    return (registry[entity] || []).map(action => {
      if (action.id === "album.changeCover" && Number(context.mediaCount || 0) === 0) {
        return { ...action, status: "disabled", note: "В пустом альбоме пока нет фотографий для обложки." };
      }
      return action;
    });
  }
  function closeMenu() { activeMenu?.remove(); activeMenu = null; }
  function setShelfImportError(message = "") {
    const error = document.querySelector("#shelfImportError");
    if (!error) return;
    error.textContent = message;
    error.classList.toggle("hidden", !message);
  }
  function ensureShelfImportDialog() {
    let dialog = document.querySelector("#shelfImportDialog");
    if (dialog) return dialog;
    document.body.insertAdjacentHTML("beforeend", `<dialog id="shelfImportDialog"><form id="shelfImportForm"><div class="dialog-heading"><div><h2>Импортировать фотографии</h2><p class="muted" id="shelfImportShelf"></p></div><button type="button" class="icon-button" id="shelfImportClose" aria-label="Закрыть">×</button></div><fieldset><legend>Куда добавить</legend><label class="radio"><input type="radio" name="destination" value="existing" checked> В существующий альбом</label><label>Альбом<select id="shelfImportAlbum"></select></label><label class="radio"><input type="radio" name="destination" value="new"> В новый альбом</label><label>Название нового альбома<input id="shelfImportAlbumName" maxlength="120" autocomplete="off" placeholder="Например, Поездка в Гюмри"></label><label class="radio"><input type="radio" name="destination" value="unsorted"> В «Неразобранное»</label></fieldset><label>Фотографии<input id="shelfImportFiles" type="file" multiple required accept=".jpg,.jpeg,.png,.webp,.gif,.bmp,.tif,.tiff,.heic,.heif,image/jpeg,image/png,image/webp,image/gif,image/bmp,image/tiff,image/heic,image/heif"></label><p class="notice danger hidden" id="shelfImportError" role="alert"></p><div class="dialog-actions"><button type="button" class="button" id="shelfImportCancel">Отмена</button><button class="button primary" id="shelfImportSubmit" type="submit">Импортировать</button></div></form></dialog>`);
    dialog = document.querySelector("#shelfImportDialog");
    const close = () => dialog.close();
    document.querySelector("#shelfImportClose").onclick = close;
    document.querySelector("#shelfImportCancel").onclick = close;
    dialog.addEventListener("close", () => {
      document.querySelector("#shelfImportForm").reset();
      setShelfImportError();
      document.querySelector("#shelfImportSubmit").disabled = false;
    });
    document.querySelector("#shelfImportForm").onsubmit = submitShelfImport;
    return dialog;
  }
  async function openShelfImportDialog(shelfName) {
    if (!shelfName) return toast("Сначала откройте полку");
    const dialog = ensureShelfImportDialog();
    const albums = await api(`/api/library/albums?year=${encodeURIComponent(shelfName)}`);
    const items = albums.items || [];
    document.querySelector("#shelfImportShelf").textContent = `Полка «${shelfName}»`;
    document.querySelector("#shelfImportAlbum").innerHTML = items.length
      ? items.map(item => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("")
      : '<option value="">На полке пока нет альбомов</option>';
    const existing = document.querySelector('#shelfImportForm input[value="existing"]');
    existing.disabled = !items.length;
    document.querySelector("#shelfImportAlbum").disabled = !items.length;
    document.querySelector('#shelfImportForm input[value="new"]').checked = !items.length;
    setShelfImportError();
    dialog.showModal();
    requestAnimationFrame(() => document.querySelector("#shelfImportFiles").focus());
  }
  async function submitShelfImport(event) {
    event.preventDefault();
    const shelfName = libraryShelf;
    const files = [...document.querySelector("#shelfImportFiles").files];
    const destination = new FormData(event.target).get("destination");
    if (!files.length) return setShelfImportError("Выберите фотографии.");
    document.querySelector("#shelfImportSubmit").disabled = true;
    setShelfImportError();
    try {
      if (destination === "unsorted") {
        document.querySelector("#shelfImportDialog").close();
        return uploadUnsortedPhotos(files);
      }
      let albumId = Number(document.querySelector("#shelfImportAlbum").value);
      if (destination === "new") {
        const name = document.querySelector("#shelfImportAlbumName").value.trim();
        if (!name) {
          document.querySelector("#shelfImportSubmit").disabled = false;
          return setShelfImportError("Введите название нового альбома.");
        }
        const created = await api("/api/library/albums", {
          method: "POST",
          body: JSON.stringify({ year: shelfName, name }),
        });
        albumId = Number(created.id);
      }
      if (!albumId) {
        document.querySelector("#shelfImportSubmit").disabled = false;
        return setShelfImportError("Выберите альбом или создайте новый.");
      }
      document.querySelector("#shelfImportDialog").close();
      await album(albumId, true);
      albumUpload.albumId = albumId;
      startAlbumUpload(files);
    } catch (error) {
      setShelfImportError(error.message || "Не удалось импортировать фотографии.");
      document.querySelector("#shelfImportSubmit").disabled = false;
    }
  }
  function renderMenu(entity, context, anchor) {
    closeMenu();
    const menu = document.createElement("div");
    menu.className = "entity-action-menu";
    menu.setAttribute("role", "menu");
    let previousGroup = null;
    groupOrder.flatMap(group => actionsFor(entity, context).filter(action => action.group === group)).forEach(action => {
      if (previousGroup && previousGroup !== action.group) menu.append(Object.assign(document.createElement("div"), { className: "entity-action-separator" }));
      previousGroup = action.group;
      const button = document.createElement("button");
      button.type = "button";
      button.className = `entity-action${action.destructive ? " danger" : ""}`;
      button.disabled = isInactive(action) || running.has(action.id);
      button.innerHTML = `<span class="entity-action-icon">${escapeHtml(action.icon)}</span><span><strong>${escapeHtml(action.label)}</strong>${action.status === "planned" ? "<small>Запланировано</small>" : action.note ? `<small>${escapeHtml(action.note)}</small>` : ""}</span>`;
      button.onclick = async event => { event.stopPropagation(); await runAction(action, context); closeMenu(); };
      menu.append(button);
    });
    document.body.append(menu);
    const rect = anchor.getBoundingClientRect();
    menu.style.left = `${Math.min(rect.left, window.innerWidth - menu.offsetWidth - 12)}px`;
    menu.style.top = `${Math.min(rect.bottom + 6, window.innerHeight - menu.offsetHeight - 12)}px`;
    activeMenu = menu;
  }
  function addMenuButton(container, entity, context, label) {
    container.querySelector(":scope > .entity-menu-button")?.remove();
    const button = document.createElement("button");
    button.type = "button";
    button.className = "entity-menu-button";
    button.textContent = "⋯";
    button.setAttribute("aria-label", label);
    button.onclick = event => { event.preventDefault(); event.stopPropagation(); renderMenu(entity, context(), button); };
    container.oncontextmenu = event => { event.preventDefault(); renderMenu(entity, context(), button); };
    container.append(button);
  }
  async function runAction(action, context) {
    if (isInactive(action)) return toast(action.note || "Это действие запланировано.");
    if (running.has(action.id)) return;
    running.add(action.id);
    try {
      if (action.id === "shelf.createAlbum") return document.querySelector("#createAlbumButton")?.click();
      if (action.id === "shelf.importPhotos") return openShelfImportDialog(context.shelf);
      if (action.id === "shelf.openUnsorted") return loadUnsorted(1);
      if (action.id === "album.addPhotos") return openAlbumUpload(context.albumId);
      if (action.id === "album.rename") return renameAlbum(context.albumId, context.name);
      if (action.id === "photo.showInfo") return showPhotoInformation(context.photo);
      if (action.id === "photo.quarantine") return quarantinePhotos([context.photo.id]);
      if (action.id === "selection.quarantine") return quarantinePhotos(context.mediaIds);
      if (action.id === "quarantine.restore") return restoreQuarantineItem(context.photo.id);
      if (action.id === "quarantine.showOrigin") return toast(context.photo.relative_path || "Исходное расположение не найдено");
      if (action.id === "quarantine.deleteForever") return deleteQuarantineItem(context.photo.id);
      toast("Действие выполнится здесь, когда сценарий будет реализован.");
    } catch (error) {
      toast(error.message || "Не удалось выполнить действие");
    } finally {
      running.delete(action.id);
    }
  }
  function openAlbumUpload(albumId) {
    if (albumUpload?.active) return toast("Дождитесь завершения текущей загрузки");
    albumUpload.albumId = Number(albumId);
    document.querySelector("#albumUploadDialog")?.showModal();
  }
  async function renameAlbum(albumId, currentName) {
    const name = prompt("Новое название альбома", currentName || "");
    if (name === null) return;
    await api(`/api/library/albums/${albumId}`, { method: "PATCH", body: JSON.stringify({ name }) });
    toast("Альбом переименован");
    if (libraryShelf) await shelf(libraryShelf, false);
    if (currentAlbum?.id === Number(albumId)) await album(albumId, false);
  }
  function showPhotoInformation(photo) { toast(`${photo.relative_path || photo.file_name || "Без названия"} · ${displayDate(photo.captured_at)} · ${bytes(photo.size)}`); }
  async function quarantinePhotos(mediaIds) {
    if (!mediaIds.length || !confirm(`Отправить в карантин: ${mediaIds.length}?`)) return;
    await quarantineMediaIds(mediaIds);
    toast("Отправлено в карантин");
    document.querySelector("#photoDialog")?.close();
    if (currentAlbum?.id) await album(currentAlbum.id, false);
    await refreshSummary();
  }
  async function restoreQuarantineItem(mediaId) { await restoreMediaIds([mediaId]); await loadQuarantine(); await refreshSummary(); }
  function deleteQuarantineItem(mediaId) {
    state.quarantineSelected = new Set([Number(mediaId)]);
    document.querySelector("#deleteDescription").textContent = "Будет безвозвратно удалён 1 файл.";
    document.querySelector("#confirmDialog").showModal();
  }
  function wireShelfMenu(shelfName) { const bar = document.querySelector("#view-photo-year .context-bar .button-row"); if (bar) addMenuButton(bar, "shelf", () => ({ shelf: shelfName }), "Действия с полкой"); }
  function wireAlbumMenus(items) {
    document.querySelectorAll("#albumGrid [data-album-card]").forEach(card => {
      const item = items.find(candidate => Number(candidate.id) === Number(card.dataset.albumCard));
      if (item) addMenuButton(card, "album", () => ({ albumId: item.id, name: item.name, mediaCount: item.media_count }), "Действия с альбомом");
    });
  }
  function wirePhotoMenus(items, root = "#libraryMedia") {
    document.querySelectorAll(`${root} .photo-card`).forEach(card => {
      const id = Number(card.dataset.media);
      const item = items.find(candidate => Number(candidate.id || candidate.media_id) === id);
      if (item) addMenuButton(card, "photo", () => ({ photo: item }), "Действия с фотографией");
    });
  }
  function wireSelectionMenu() {
    const bar = document.querySelector("#view-photo-album .context-bar .button-row");
    if (!bar || document.querySelector("#selectedPhotoActions")) return;
    const button = document.createElement("button");
    button.type = "button";
    button.id = "selectedPhotoActions";
    button.className = "button";
    button.textContent = "Выбранные фото";
    button.onclick = event => {
      event.stopPropagation();
      const mediaIds = [...document.querySelectorAll("#libraryMedia .photo-card input:checked")].map(input => Number(input.dataset.selectId)).filter(Number.isFinite);
      if (!mediaIds.length) return toast("Сначала выберите фотографии");
      renderMenu("selection", { mediaIds }, button);
    };
    bar.append(button);
  }
  const baseHome = home;
  home = async function (push = false) { await baseHome(push); };
  const baseShelf = shelf;
  shelf = async function (year, push = false) { await baseShelf(year, push); wireShelfMenu(year); const data = await api(`/api/library/albums?year=${encodeURIComponent(year)}`); wireAlbumMenus(data.items || []); };
  const baseAlbum = album;
  album = async function (id, push = false) {
    await baseAlbum(id, push);
    const albums = libraryShelf ? await api(`/api/library/albums?year=${encodeURIComponent(libraryShelf)}`) : { items: [] };
    const current = (albums.items || []).find(item => Number(item.id) === Number(id));
    currentAlbum = { id: Number(id), name: current?.name || document.querySelector("#albumTitle")?.textContent || "Альбом", mediaCount: current?.media_count || albumPaging?.total || 0 };
    const bar = document.querySelector("#view-photo-album .context-bar .button-row");
    if (bar) addMenuButton(bar, "album", () => ({ albumId: currentAlbum.id, name: currentAlbum.name, mediaCount: currentAlbum.mediaCount }), "Действия с альбомом");
    wireSelectionMenu();
    wirePhotoMenus(albumPaging?.items || galleryItems || []);
  };
  const baseRenderPagedAlbum = renderPagedAlbum;
  renderPagedAlbum = function () {
    baseRenderPagedAlbum();
    document.querySelectorAll("#libraryMedia .photo-card").forEach(card => {
      const id = Number(card.dataset.media);
      if (!card.querySelector("input")) card.insertAdjacentHTML("afterbegin", `<input type="checkbox" data-select-id="${id}" aria-label="Выбрать">`);
    });
    wirePhotoMenus(albumPaging.items || []);
  };
  const baseOpenPhoto = openPhoto;
  openPhoto = function (item, mode) {
    baseOpenPhoto(item, mode);
    if (mode !== "library") return;
    document.querySelector("#galleryPopover")?.remove();
    const anchor = document.querySelector("#galleryDotMenu") || document.querySelector(".gallery-close");
    if (anchor) anchor.onclick = event => { event.preventDefault(); event.stopPropagation(); renderMenu("photo", { photo: item }, anchor); };
  };
  const baseLoadQuarantine = loadQuarantine;
  loadQuarantine = async function () {
    await baseLoadQuarantine();
    const data = await api("/api/quarantine");
    document.querySelectorAll("#quarantineCards .photo-card").forEach(card => {
      const item = (data.items || []).find(candidate => Number(candidate.id) === Number(card.dataset.media));
      if (item) addMenuButton(card, "quarantine", () => ({ photo: item }), "Действия с объектом в карантине");
    });
  };
  document.addEventListener("click", event => { if (!event.target.closest(".entity-action-menu,.entity-menu-button")) closeMenu(); });
  window.addEventListener("resize", closeMenu);
  window.PhotoHomeActionRegistry = { actionsFor };
})();
