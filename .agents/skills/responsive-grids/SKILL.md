---
name: responsive-grids
description: Repo-local guidance for building and reviewing responsive layouts in MiniBot's built-in web UI. Use this skill when adding or fixing grid/flex layouts in the Jinja2 templates and stylesheet under minibot/adapters/http, especially card grids, status panels, tables of long identifiers, and any layout that must reflow from phone to desktop without horizontal overflow.
---

# Responsive Grids

## Overview

MiniBot's web UI is server-rendered Jinja2 plus one hand-written stylesheet. There is no Sass, no
build step, no CSS framework, and no utility classes. Everything lives in three places:

- `minibot/adapters/http/templates/` — `base.html` holds the navbar, sidebar and `<main>`; every page
  does `{% extends "base.html" %}` and fills `{% block content %}`.
- `minibot/adapters/http/static/dashboard.css` — the whole stylesheet, mobile-first.
- `minibot/adapters/http/static/nav.js` — the sidebar toggle, vanilla JS. Keep JS at this size.

Write semantic classes for the page you are building. Do not add a utility-class system.

## Workflow

1. Identify the content type: repeating cards, a fixed set of panels, or tabular data.
2. Pick the primitive (below), mobile-first: the base rules are the phone layout.
3. Make the children able to shrink — this is where almost every bug here comes from.
4. Add overrides inside the single `@media (min-width: 640px)` block only if the content needs them.
5. Redeploy and check both widths (see Verification).

## Design tokens

Defined in `:root` at the top of `dashboard.css`:

- `--gap: 1rem` — the standard layout gap and box padding.
- `--gap-sm: 0.5rem` — tight gaps inside a component.
- `--radius: 0.5rem` / `--radius-sm: 0.4rem` — box and chip corners.
- `--border: #8886` — every border and separator. Alpha-based so it works in both themes.

Use tokens for layout spacing and radii. Keep genuinely one-off values inline (`0.15rem` padding on a
chip, `1.2rem` on the burger icon). Never use a radius token as spacing or a spacing token as a font
size. If a literal starts repeating across three or more rules, promote it to a token.

`color-scheme: light dark` is set on `:root`; the page inherits the reader's theme. Do not hardcode
background or text colors — only `--border` and the one status green (`.badge`) are literal.

## Choose the primitive

**Repeating collection of unknown length** — flex chips, not a grid. Node names, tags and identifiers
here vary from 8 to 40 characters, and fixed grid tracks either clip them or widen the page:

```css
.nodes {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}
```

If you truly need a grid for a repeating collection, guard the track against narrow screens:

```css
grid-template-columns: repeat(auto-fill, minmax(min(100%, 14rem), 1fr));
```

The `min(100%, …)` is what stops a single tile from being wider than the viewport.

**Fixed, meaningful set of boxes** — an explicit column count, but always `minmax(0, 1fr)`:

```css
@media (min-width: 640px) {
  .status-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
```

A bare `1fr` means `minmax(auto, 1fr)`, and that `auto` minimum refuses to shrink below the content.
One long unbreakable token then stretches the column past the viewport. Never write a bare `1fr`.

**Page shell** — one sidebar column plus content:

```css
.shell {
  display: grid;
  grid-template-columns: 14rem minmax(0, 1fr);
  align-items: start;
}
```

**Section spacing** — `<main>` is itself a grid with `gap: var(--gap)`. Do not add `margin-bottom` to
sections to space them apart; the parent gap already handles it, uniformly, at every width.

## Make children shrink correctly

Layout bugs here are caused by the children, not the container.

- Put `min-width: 0` on any grid or flex item that holds text. The default is `min-width: auto`,
  which will not shrink below its content. `.panel`, `.status-card`, `.message` and `.entry` all
  carry it.
- Put `overflow-wrap: anywhere` on long unbreakable strings. This UI is full of them: extension
  module paths (`minibot.extensions.integrations.rabbitmq`), graph nodes
  (`market:agencias_inmobiliarias_caracas`), session ids (`telegram:100864173`), KV titles.
- Message and entry bodies use `<pre>` with `white-space: pre-wrap; overflow-wrap: anywhere;` so
  stored text keeps its newlines without escaping the card.

## Tables

Tables of identifiers are the hardest case. The pattern in `.edges` is the reference: stacked cards
on a phone, real columns on desktop, driven by `data-label` attributes in the template.

```html
<td data-label="Source">{{ edge.source }}</td>
```

```css
/* mobile: every cell is its own labelled row */
.edges, .edges tbody, .edges tr, .edges td { display: block; width: 100%; }
.edges thead { display: none; }
.edges td::before { content: attr(data-label) ": "; }

@media (min-width: 640px) {
  .edges { display: table; }
  .edges thead { display: table-header-group; }
  .edges td { display: table-cell; overflow-wrap: normal; white-space: nowrap; }
  .table-wrap .edges { width: auto; }
}
```

Two traps this encodes, both hit in real bugs:

- `overflow-wrap: anywhere` must be **undone** at desktop. It drops each column's min-content width
  to a single character, so the auto table layout starves every column after the first and text
  wraps letter by letter. Pair the reset with `white-space: nowrap` and let the wrapper scroll.
- The mobile `width: 100%` leaks into desktop and stretches the table, dumping all slack into the
  first column. Reset it — and note the selector needs `.table-wrap .edges` to outrank
  `.table-wrap table`.

Wrap every table in `<div class="table-wrap">`; it owns `overflow-x: auto`, so a wide table scrolls
by itself instead of widening the page. Generic table styling hangs off `.table-wrap`, so a table
gets it just by being wrapped — do not add a per-page class for that.

## Template guidance

- One container class for the layout, one class per item, inner classes only where a part needs its
  own shrink or alignment behavior.
- Do not add a class that no rule uses; if you remove the rules, remove the class.
- Templates are plain HTML plus Jinja. Escaping is automatic — do not reach for `| safe` on values
  that came out of the database.
- Keep the page inside `{% block content %}`; the navbar, sidebar and `<main>` grid come from
  `base.html` and should not be re-declared per page.
- Page-specific JS goes in `{% block scripts %}` (rendered last, before `</body>`), not in a new
  static file. Guard it with the same `{% if %}` as the markup it drives, or it will throw on the
  empty state.
- A page added by an extension renders through `minibot.adapters.http.render` and extends the same
  `base.html`, so these conventions apply to extension pages too.

## Review checklist

- Does every multi-column grid use `minmax(0, 1fr)` rather than a bare `1fr`?
- Does every text-bearing grid/flex item have `min-width: 0`?
- Do long identifiers have `overflow-wrap: anywhere`?
- Is any `overflow-wrap: anywhere` inside a table reset at the desktop breakpoint?
- Is every table inside `.table-wrap`?
- Is spacing coming from `--gap` / `--gap-sm` and radii from `--radius` / `--radius-sm`?
- Are sections spaced by the `<main>` grid gap rather than per-section margins?
- Are colors limited to `--border` and theme-inherited defaults?

## Common fixes

Page scrolls sideways on a phone:

- Find the widest element, not the page. Usually a grid with fixed tracks or an item without
  `min-width: 0`.
- Swap fixed tracks for flex chips, or use `minmax(min(100%, Npx), 1fr)`.
- Add `overflow-wrap: anywhere` to the long string inside it.

Table text wraps one character per line on desktop:

- An `overflow-wrap: anywhere` from the mobile rules is still active. Reset it to `normal` in the
  media query and add `white-space: nowrap`.

A column is enormous and the rest are squeezed:

- The table is stretched to `width: 100%` while the other columns can collapse. Reset the width so
  the table hugs its content and let `.table-wrap` scroll.

## Verification

`dashboard.css` is bind-mounted via `./minibot:/app/minibot`, but the daemon reads templates through
a cached Jinja environment, so redeploy after changes:

```
docker compose up -d --force-recreate minibot
curl -s -u <user>:<pass> http://minibot.minipc.com/static/dashboard.css | head
```

Then check the page itself at both widths. The browser caches `dashboard.css` aggressively and the
response carries no `Cache-Control`, so verify in a private window or with an empty-cache reload —
otherwise you will be looking at the previous stylesheet and think the fix did nothing.
