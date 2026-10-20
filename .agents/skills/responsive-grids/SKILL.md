---
name: responsive-grids
description: Build and review responsive layouts in MiniBot's built-in web UI (Jinja2 templates and dashboard.css under minibot/adapters/http). Use when adding or changing a page template or stylesheet rule, card grids, status panels, tables of long identifiers, or fixing horizontal overflow on phones. Not for route handlers, registering a page (minibot-dev), or other Python code.
---

# Responsive Grids

The web UI is server-rendered Jinja2 plus one hand-written, mobile-first stylesheet: no Sass, no
build step, no CSS framework, no utility classes. Under `minibot/adapters/http/`:

- `templates/base.html` holds the navbar, `.shell`, sidebar and `<main>`. Every page extends it
  and fills `{% block content %}`; page JS goes in `{% block scripts %}` (rendered last).
- `static/dashboard.css` is the whole stylesheet. It has one width breakpoint,
  `@media (min-width: 640px)`.
- `static/*.js` holds small vanilla scripts loaded by `base.html` (`nav.js`, `pager.js`,
  `confirm.js`) and the chat page's `chat.js`. Third-party code is vendored in `static/vendor/`,
  never a CDN; upgrade it per `static/vendor/README.md`.
- `server.py` `render()` (exported as `minibot.adapters.http.render`) renders these templates for
  core routes and extension pages alike, so these rules apply to `mb.add_page` pages too. Wiring
  the route and menu entry belongs to the `minibot-dev` skill.

Repo-wide rules (git, lint, comments, docs) are owned by `AGENTS.md` and the `minibot-dev` skill.

## Core rules

- **Mobile first.** Base rules are the phone layout; desktop overrides go only in the existing
  640px block.
- **Never a bare `1fr`.** Write `minmax(0, 1fr)`.
- **Children must shrink.** `min-width: 0` on every text-bearing grid/flex item;
  `overflow-wrap: anywhere` on long identifiers.
- **Every table sits in `<div class="table-wrap">`.** In a table, undo `overflow-wrap: anywhere`
  at desktop.
- **Space sections with the `<main>` grid gap**, never per-section margins.
- **Use the `:root` tokens:** `--gap` (1rem, layout gap and box padding), `--gap-sm` (0.5rem,
  inside a component), `--radius` (0.6rem) / `--radius-sm` (0.4rem), `--border` (`#8886`),
  `--surface` / `--surface-strong` (`color-mix` of `currentColor`). Do not use a radius token as
  spacing or a spacing token as a font size. Keep true one-offs literal (`0.15rem` chip padding,
  `0.35rem` chip gap); promote a literal to a token once it repeats in three or more rules.
- **Inherit the theme.** `:root` sets `color-scheme: light dark`; do not hardcode text or
  background colors. The only literal colors are `--border`, the `.badge` green (`#2a2`), the
  error red (`#c33`) and alpha-only shadows and backdrops. Reuse them; add none.
- **Semantic classes only.** One container class for the layout, one per item, inner classes only
  where a part needs its own shrink or alignment. Delete a class when no rule uses it.
- **Autoescape stays on.** Do not apply `| safe` to stored values.
- **Guard page JS** with the same `{% if %}` as the markup it drives, so the empty state does not
  throw.

## Workflow

1. Find the existing page or selector closest to what you need (`rg -n '<selector>'` over
   `minibot/adapters/http`) and match it.
2. Classify the content and pick the primitive:
   - repeating items of unknown length: flex chips (`.nodes`);
   - a fixed, meaningful set of boxes: a flex column, then `repeat(N, minmax(0, 1fr))` at 640px
     (`.panels`, `.status-grid`);
   - tabular identifiers: `.table-wrap`, plus the stacked-card pattern of `.edges` when a phone
     must read every column.

   Read [references/LAYOUT_RECIPES.md](references/LAYOUT_RECIPES.md) for the CSS of each
   primitive, the table traps and the troubleshooting table.
3. Make the children shrink (core rules), then add desktop overrides only where content needs them.
4. Review the change against the checklist, then verify.

## Review checklist

- Every multi-column track uses `minmax(0, 1fr)`, and every repeating grid guards its minimum
  with `min(100%, …)`.
- Every text-bearing grid/flex item has `min-width: 0`; long identifiers have
  `overflow-wrap: anywhere`, reset inside tables at desktop.
- Every table is inside `.table-wrap`; no per-page class duplicates its styling.
- Spacing, radii and backgrounds come from the tokens; colors are limited to the literals above.
- No new width breakpoint, utility class, unused class, `| safe`, inline color or CDN script.

## Verify

1. If the change affects rendered markup, run the test file for that page:
   `tests/test_http_server.py` (dashboard, history), `tests/test_web_chat.py`, or
   `tests/test_<name>_page.py` (memory, scheduled, task, MCP); the graph page has none. Writing
   new tests is owned by the `minibot-testing` skill.
2. Load the page at phone width (~375px) and above 640px. Nothing may scroll sideways except
   inside `.table-wrap`.
3. In Docker, `docker-compose.yml` bind-mounts `./minibot` at `/app/minibot`, where the package is
   installed, so CSS and template edits reach the running container: static files are served from
   disk and Jinja reloads changed templates. Run `docker compose restart minibot` only after a
   Python change, or when an edit still does not show.
4. Static caching depends on `[runtime] environment`. With `"development"` (the default) or
   `"debug"`, static assets are sent with `Cache-Control: no-store`. With any other value the
   browser caches them normally, so use a private window or an empty-cache reload, or you will
   be looking at the old stylesheet. Check what the server is serving with
   `curl -s -u <user>:<pass> http://<host>:<port>/static/dashboard.css | head`; send a bearer
   token instead when the server is configured with one.
