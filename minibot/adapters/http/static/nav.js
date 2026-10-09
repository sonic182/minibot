// All icons, not a hand-picked set: extensions name their menu icon in add_page().
window.lucide.createIcons({ icons: window.lucide.icons });
// Icons are drawn; show the page (see .cloak in dashboard.css).
document.body.classList.remove("cloak");

const toggle = document.querySelector("[data-nav-toggle]");
const root = document.documentElement;
const desktop = matchMedia("(min-width: 640px)");

const syncExpanded = () =>
  toggle.setAttribute("aria-expanded", String(desktop.matches !== root.classList.contains("nav-toggled")));

syncExpanded();

toggle.addEventListener("click", () => {
  root.classList.toggle("nav-toggled");
  syncExpanded();
});

// .nav-toggled means "opened" on phones and "closed" on desktop, so crossing the breakpoint (a
// rotated tablet, a resized window) returns to that size's default instead of inverting it.
// Keep the width in step with the @media query in dashboard.css.
desktop.addEventListener("change", () => {
  root.classList.remove("nav-toggled");
  syncExpanded();
});
