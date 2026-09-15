import { createIcons, Menu } from "https://cdn.jsdelivr.net/npm/lucide@1.46.0/+esm";

createIcons({ icons: { Menu } });

const toggle = document.querySelector("[data-nav-toggle]");
const root = document.documentElement;

toggle.setAttribute("aria-expanded", String(root.classList.contains("nav-open")));

toggle.addEventListener("click", () => {
  const open = root.classList.toggle("nav-open");
  toggle.setAttribute("aria-expanded", String(open));
});
