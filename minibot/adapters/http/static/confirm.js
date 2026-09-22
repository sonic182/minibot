// Confirmation dialogs for destructive buttons:
//   <button data-confirm="dialog-id" data-confirm-value="…" data-confirm-label="…">
// A static file because the CSP blocks inline scripts, and delegated so rows appended by
// infinite scroll work too.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-confirm]");
  if (button) {
    const dialog = document.getElementById(button.dataset.confirm);
    dialog.querySelector("[data-confirm-id]").value = button.dataset.confirmValue;
    dialog.querySelector("[data-confirm-title]").textContent = button.dataset.confirmLabel;
    dialog.showModal();
    return;
  }
  event.target.closest("[data-confirm-cancel]")?.closest("dialog")?.close();
});
