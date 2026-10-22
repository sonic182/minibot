# Layout recipes

CSS and template recipes for `minibot/adapters/http/static/dashboard.css`. Each one names the
real selector that already implements it; read that rule before copying the recipe. Breakpoint
overrides go in the stylesheet's single `@media (min-width: 640px)` block.

## Contents

- [Repeating collection: flex chips](#repeating-collection-flex-chips)
- [Repeating collection that must be a grid](#repeating-collection-that-must-be-a-grid)
- [Fixed set of boxes](#fixed-set-of-boxes)
- [Page shell and section spacing](#page-shell-and-section-spacing)
- [Shrinking children](#shrinking-children)
- [Tables of identifiers](#tables-of-identifiers)
- [Troubleshooting](#troubleshooting)

## Repeating collection: flex chips

Default for node names, tags and identifiers, which vary from 8 to 40 characters: fixed grid
tracks either clip them or widen the page. Reference: `.nodes` / `.nodes li` (`graph.html`,
`mcp.html`).

```css
.nodes {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.nodes li {
  padding: 0.15rem var(--gap-sm);
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  overflow-wrap: anywhere;
}
```

## Repeating collection that must be a grid

No rule uses this yet. Guard the track so one tile never exceeds the viewport:

```css
grid-template-columns: repeat(auto-fill, minmax(min(100%, 14rem), 1fr));
```

`min(100%, …)` is the guard; without it a 14rem minimum overflows a narrower screen.

## Fixed set of boxes

A flex column on phones, an explicit column count at the breakpoint. Reference: `.panels`
(2 columns; `dashboard.html`, `mcp.html`, `task_detail.html`) and `.status-grid` (3 columns;
`dashboard.html`).

```css
.status-grid {
  display: flex;
  flex-direction: column;
  gap: var(--gap);
}

@media (min-width: 640px) {
  .status-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
```

Never write a bare `1fr`: it means `minmax(auto, 1fr)`, and the `auto` minimum refuses to shrink
below the content, so one long unbreakable token stretches the column past the viewport.

## Page shell and section spacing

`base.html` owns the shell; pages never re-declare it. At the breakpoint `.shell` becomes
`14rem minmax(0, 1fr)`; `.nav-toggled .shell` (class toggled on `<html>` by `nav.js`) narrows
the sidebar track to `3.5rem`.

`main` is itself a grid: `grid-template-columns: minmax(0, 1fr)`, `gap: var(--gap)`,
`max-width: 68rem`. Space sections with that gap, never with per-section `margin-bottom`.
`main:has(> .chat)` is the one page-specific override.

## Shrinking children

Layout bugs here come from the children, not the container.

- `min-width: 0` on every grid or flex item that holds text; the default `min-width: auto` will
  not shrink below its content. The shared box rule `.panel, .status-card, .message, .entry`
  sets it, together with border, radius, padding and `--surface`.
- `overflow-wrap: anywhere` on long unbreakable strings: extension module paths
  (`minibot.extensions.integrations.mcp`), graph node ids, session ids (`telegram:100864173`),
  KV titles. See `.extensions li`, `.status-card p`, `.nodes li`.
- Stored text goes in a `<pre>`; `.message pre, .entry pre, .panel pre` apply
  `white-space: pre-wrap; overflow-wrap: anywhere;` so newlines survive without escaping the box.

## Tables of identifiers

Wrap every table in `<div class="table-wrap">`. `.table-wrap` owns `overflow-x: auto`, so a wide
table scrolls inside it instead of widening the page, and `.table-wrap table|th|td` carry the
generic table styling; do not add a per-page class for that.

For identifiers that must stay readable on a phone, follow `.edges` (`graph.html`): stacked,
labelled cards on a phone, real columns on desktop, driven by `data-label` in the template.

```html
<div class="table-wrap">
  <table class="edges">
    ...
    <td data-label="Source">{{ edge.source }}</td>
```

```css
/* phone: every cell is its own labelled row */
.edges, .edges tbody, .edges tr, .edges td { display: block; width: 100%; }
.edges thead { display: none; }
.edges td { overflow-wrap: anywhere; }
.edges td::before { content: attr(data-label) ": "; }

@media (min-width: 640px) {
  .edges { display: table; }
  .edges tbody { display: table-row-group; }
  .edges thead { display: table-header-group; }
  .edges tr { display: table-row; }
  .edges td { display: table-cell; }
  .edges td::before { content: none; }
  .table-wrap td { overflow-wrap: normal; white-space: nowrap; }
  .table-wrap .edges { width: auto; }
}
```

The stylesheet's rules also reset the card padding, margin and border on `.edges tr` and restore
cell padding on `.edges td` at the breakpoint. Two traps the recipe encodes:

- **Undo `overflow-wrap: anywhere` at desktop.** It drops each column's min-content width to one
  character, so the auto table layout starves every column after the first and text wraps letter
  by letter. Pair the `normal` reset with `white-space: nowrap` and let `.table-wrap` scroll.
- **Reset the phone `width: 100%`.** It otherwise stretches the desktop table and dumps all slack
  into the first column. The reset needs `.table-wrap .edges` to outrank `.table-wrap table`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Page scrolls sideways on a phone | Fixed grid tracks, an item without `min-width: 0`, or a long unbroken string | Find the widest element, not the page. Use chips or `minmax(min(100%, …), 1fr)`, add `min-width: 0`, add `overflow-wrap: anywhere` |
| Table text wraps one character per line on desktop | Phone `overflow-wrap: anywhere` still active | Reset to `normal` plus `white-space: nowrap` inside the media query |
| One column is enormous, the rest squeezed | Table still `width: 100%` at desktop | Set `width: auto` so it hugs its content; `.table-wrap` scrolls |
| A column grows past the viewport | Bare `1fr` track | Use `minmax(0, 1fr)` |
