(() => {
  const label = {
    add: "\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0444\u043e\u0442\u043e\u0433\u0440\u0430\u0444\u0438\u0438",
    rename: "\u041f\u0435\u0440\u0435\u0438\u043c\u0435\u043d\u043e\u0432\u0430\u0442\u044c \u0430\u043b\u044c\u0431\u043e\u043c",
    prompt: "\u041d\u043e\u0432\u043e\u0435 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0430\u043b\u044c\u0431\u043e\u043c\u0430",
    renamed: "\u0410\u043b\u044c\u0431\u043e\u043c \u043f\u0435\u0440\u0435\u0438\u043c\u0435\u043d\u043e\u0432\u0430\u043d",
    failed: "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u0435\u0440\u0435\u0438\u043c\u0435\u043d\u043e\u0432\u0430\u0442\u044c \u0430\u043b\u044c\u0431\u043e\u043c."
  };
  async function renameAlbum(id, currentName) {
    const name = prompt(label.prompt, currentName);
    if (name === null) return;
    try {
      await api(`/api/library/albums/${id}`, { method: "PATCH", body: JSON.stringify({ name }) });
      await shelf(libraryShelf, false);
      toast(label.renamed);
    } catch (error) { alert(error.message || label.failed); }
  }
  function attachActions(container, id, name, addPhotos, context) {
    if (!container || container.querySelector(".album-actions-toggle")) return;
    container.classList.toggle("album-context-actions", context);
    const toggle = document.createElement("button");
    toggle.type = "button"; toggle.className = "album-actions-toggle"; toggle.textContent = "\u22ef";
    toggle.setAttribute("aria-label", "\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u044f \u0441 \u0430\u043b\u044c\u0431\u043e\u043c\u043e\u043c");
    const menu = document.createElement("div"); menu.className = "album-actions-menu hidden";
    const add = document.createElement("button"); add.type = "button"; add.textContent = label.add;
    const rename = document.createElement("button"); rename.type = "button"; rename.textContent = label.rename;
    menu.append(add, rename); container.append(toggle, menu);
    const close = () => menu.classList.add("hidden");
    toggle.onclick = event => { event.stopPropagation(); menu.classList.toggle("hidden"); };
    add.onclick = event => { event.stopPropagation(); close(); addPhotos(); };
    rename.onclick = event => { event.stopPropagation(); close(); renameAlbum(id, name); };
  }
  const renderShelf = shelf;
  shelf = async function (year, push = false) {
    await renderShelf(year, push);
    document.querySelectorAll("#albumGrid [data-album-card]").forEach(card => {
      const id = Number(card.dataset.albumCard), name = card.querySelector("strong").textContent;
      attachActions(card, id, name, async () => { await album(id, true); requestAnimationFrame(() => document.querySelector("#addAlbumPhotos")?.click()); }, false);
    });
  };
  const renderAlbum = album;
  album = async function (id, push = false) {
    await renderAlbum(id, push);
    const upload = document.querySelector("#addAlbumPhotos");
    if (!upload) return;
    upload.style.display = "none";
    attachActions(upload.parentElement, Number(id), document.querySelector("#albumTitle").textContent, () => upload.click(), true);
  };
})();