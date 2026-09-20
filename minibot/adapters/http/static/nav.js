window.lucide.createIcons({ icons: { Menu: window.lucide.Menu } });

const toggle = document.querySelector("[data-nav-toggle]");
const root = document.documentElement;

if (window.innerWidth >= 640) root.classList.add("nav-open");

toggle.setAttribute("aria-expanded", String(root.classList.contains("nav-open")));

toggle.addEventListener("click", () => {
  const open = root.classList.toggle("nav-open");
  toggle.setAttribute("aria-expanded", String(open));
});
