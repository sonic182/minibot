const toggle = document.querySelector("[data-nav-toggle]");
const root = document.documentElement;

toggle.setAttribute("aria-expanded", String(root.classList.contains("nav-open")));

toggle.addEventListener("click", () => {
  const open = root.classList.toggle("nav-open");
  toggle.setAttribute("aria-expanded", String(open));
});
