// All icons, not a hand-picked set: extensions name their menu icon in add_page().
window.lucide.createIcons({ icons: window.lucide.icons });
// Icons are drawn; show the page (see .cloak in dashboard.css).
document.body.classList.remove("cloak");

const toggle = document.querySelector("[data-nav-toggle]");
const panel = document.querySelector("[data-nav-panel]");
const root = document.documentElement;

// The CSS decides the default per breakpoint; this only reports it and flips it.
const syncExpanded = () => toggle.setAttribute("aria-expanded", String(getComputedStyle(panel).display !== "none"));

syncExpanded();

toggle.addEventListener("click", () => {
  root.classList.toggle("nav-toggled");
  syncExpanded();
});

// .nav-toggled means "opened" on phones and "closed" on desktop, so crossing the breakpoint (a
// rotated tablet, a resized window) returns to that size's default instead of inverting it.
// Keep the width in step with the @media query in dashboard.css.
matchMedia("(min-width: 640px)").addEventListener("change", () => {
  root.classList.remove("nav-toggled");
  syncExpanded();
});
