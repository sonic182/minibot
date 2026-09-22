// Infinite scroll for server-rendered lists: when the "next page" link comes into view, fetch that
// page and move its [data-pager-list] children into ours. Without JS the link still works.
const list = document.querySelector("[data-pager-list]");
const link = document.querySelector("[data-pager-next]");

if (list && link && "IntersectionObserver" in window) {
  let loading = false;
  const observer = new IntersectionObserver(
    async ([entry]) => {
      if (!entry.isIntersecting || loading) return;
      loading = true;
      observer.unobserve(link);
      try {
        const response = await fetch(link.href);
        if (!response.ok) throw new Error(response.statusText);
        const page = new DOMParser().parseFromString(await response.text(), "text/html");
        list.append(...(page.querySelector("[data-pager-list]")?.children ?? []));
        const next = page.querySelector("[data-pager-next]");
        if (next) {
          link.href = next.getAttribute("href");
          // Re-observing fires again right away if the link is still on screen after a short page.
          observer.observe(link);
        } else {
          (link.closest(".pager") ?? link).remove();
        }
      } catch {
        // Leave the link in place as a manual fallback.
      } finally {
        loading = false;
      }
    },
    { rootMargin: "200px" },
  );
  observer.observe(link);
}
