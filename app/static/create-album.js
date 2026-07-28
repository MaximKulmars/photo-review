(() => {
  const dialog = document.querySelector("#createAlbumDialog");
  const form = document.querySelector("#createAlbumForm");
  const nameInput = document.querySelector("#createAlbumName");
  const errorBox = document.querySelector("#createAlbumError");
  const submitButton = document.querySelector("#createAlbumSubmit");
  const emptyState = document.querySelector("#albumEmpty");
  const createButton = document.querySelector("#createAlbumButton");

  if (!dialog || !form || !nameInput || !createButton || typeof shelf !== "function") return;

  function setError(message = "") {
    errorBox.textContent = message;
    errorBox.classList.toggle("hidden", !message);
  }

  function closeDialog() {
    if (dialog.open) dialog.close();
    form.reset();
    setError();
    submitButton.disabled = false;
  }

  function openDialog() {
    if (!libraryShelf) {
      toast("Сначала откройте полку");
      return;
    }
    form.reset();
    setError();
    document.querySelector("#createAlbumShelfLabel").textContent = `Полка «${libraryShelf}»`;
    dialog.showModal();
    requestAnimationFrame(() => nameInput.focus());
  }

  function updateEmptyState() {
    const hasAlbums = document.querySelectorAll("#albumGrid [data-album]").length > 0;
    emptyState?.classList.toggle("hidden", hasAlbums);
  }

  const renderShelf = shelf;
  shelf = async function (year, push = false) {
    await renderShelf(year, push);
    updateEmptyState();
  };

  createButton.addEventListener("click", openDialog);
  document.querySelector("#createAlbumClose")?.addEventListener("click", closeDialog);
  document.querySelector("#createAlbumCancel")?.addEventListener("click", closeDialog);
  dialog.addEventListener("close", () => {
    form.reset();
    setError();
    submitButton.disabled = false;
  });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    const name = nameInput.value.trim();
    if (!name) {
      setError("Введите название альбома.");
      nameInput.focus();
      return;
    }

    submitButton.disabled = true;
    setError();
    try {
      const created = await api("/api/library/albums", {
        method: "POST",
        body: JSON.stringify({ year: libraryShelf, name }),
      });
      closeDialog();
      await shelf(libraryShelf, false);
      const card = document.querySelector(`#albumGrid [data-album="${created.id}"]`);
      if (card) {
        card.classList.add("created");
        card.scrollIntoView({ block: "nearest", behavior: "smooth" });
        window.setTimeout(() => card.classList.remove("created"), 1800);
      }
      toast(`Альбом «${created.name}» создан`);
      loadLibraryShelves?.().catch(() => {});
    } catch (error) {
      setError(error.message || "Не удалось создать альбом.");
      submitButton.disabled = false;
      nameInput.focus();
    }
  });
})();
