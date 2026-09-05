"""Compact, semantic rendering of HTML for LLM consumption.

Turns a page into an indented accessibility-tree-like text that keeps what an agent
needs to act on (links and their targets, forms, inputs, headings, lists, tables)
while dropping presentation markup (classes, styles, ``data-*``, scripts, SVG).
"""

from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

_DROP = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "canvas",
        "iframe",
        "meta",
        "link",
        "base",
        "-comment",
    }
)

_TRANSPARENT = frozenset(
    {
        "html",
        "head",
        "body",
        "div",
        "span",
        "center",
        "font",
        "small",
        "b",
        "i",
        "u",
        "em",
        "strong",
        "mark",
        "picture",
        "figure",
        "label",
        "tbody",
        "thead",
        "tfoot",
        "colgroup",
        "fieldset",
        "dl",
    }
)

_BOOLEAN_ATTRS = ("checked", "selected", "disabled", "required")
_WHITESPACE = re.compile(r"\s+")


def html_to_compact(html: str, *, base_url: str | None = None) -> str:
    """Render ``html`` as compact semantic text.

    ``base_url`` is accepted for callers that will later want absolute URLs; v1
    preserves link targets exactly as written, which costs fewer tokens on pages
    with many same-origin links.
    """
    del base_url
    if not html or not html.strip():
        return ""
    root = LexborHTMLParser(html).root
    if root is None:
        return ""
    lines: list[str] = []
    _render(root, 0, lines)
    return "\n".join(lines)


def _render(node: LexborNode, depth: int, out: list[str]) -> None:
    tag = node.tag
    if tag in _DROP or _is_hidden(node):
        return
    if tag == "-text":
        text = _norm(node.text_content or "")
        if text:
            out.append(_indent(depth) + _quote(text))
        return
    if tag in _TRANSPARENT:
        _render_children(node, depth, out)
        return
    if tag == "table":
        _render_table(node, depth, out)
        return
    if tag == "pre":
        _render_pre(node, depth, out)
        return

    attrs = node.attributes
    if tag in _VOID_RENDERERS:
        line = _VOID_RENDERERS[tag](attrs)
        if line:
            out.append(_indent(depth) + line)
        return

    sub: list[str] = []
    _render_children(node, depth + 1, sub)
    text = _collapse(sub)
    if text is not None:
        line = _leaf_line(tag, attrs, text)
        if line:
            out.append(_indent(depth) + line)
        return

    header = _leaf_line(tag, attrs, "") or tag
    out.append(_indent(depth) + header)
    out.extend(sub)


def _render_children(node: LexborNode, depth: int, out: list[str]) -> None:
    child = node.child
    while child is not None:
        _render(child, depth, out)
        child = child.next


def _render_table(node: LexborNode, depth: int, out: list[str]) -> None:
    grid = [(row, [cell for cell in _iter_elements(row) if cell.tag in {"td", "th"}]) for row in _table_rows(node)]
    grid = [(row, cells) for row, cells in grid if cells]
    if len(grid) == 1 and len(grid[0][1]) == 1:
        # Layout table wrapping a single cell: it carries no tabular meaning of its own.
        _render_children(grid[0][1][0], depth, out)
        return

    rows: list[str] = []
    for _, cells in grid:
        rendered = [_render_cell(cell) for cell in cells]
        if all(text is not None for text in rendered):
            values = " | ".join(text or "" for text in rendered)
            rows.append(_indent(depth + 1) + f"| {values} |")
            continue
        if len(cells) == 1:
            # Single-cell row: the layout table adds nesting, the cell's own content carries the meaning.
            _render_children(cells[0], depth + 1, rows)
            continue
        rows.append(_indent(depth + 1) + "tr")
        for cell in cells:
            _render_nested_cell(cell, depth + 2, rows)
    if not rows:
        return
    out.append(_indent(depth) + "table")
    out.extend(rows)


def _render_cell(cell: LexborNode) -> str | None:
    """Return the cell's text when it is a plain run, else None to force nested rendering."""
    sub: list[str] = []
    _render_children(cell, 0, sub)
    return _collapse(sub)


def _render_nested_cell(cell: LexborNode, depth: int, out: list[str]) -> None:
    """A cell holding a single element adds a level of nesting but no information."""
    sub: list[str] = []
    _render_children(cell, depth, sub)
    if len(sub) == 1:
        out.extend(sub)
        return
    _render(cell, depth, out)


def _table_rows(node: LexborNode) -> list[LexborNode]:
    """Direct rows of this table only - rows of a nested table belong to that table."""
    rows: list[LexborNode] = []
    for child in _iter_elements(node):
        if child.tag == "tr":
            rows.append(child)
        elif child.tag in {"thead", "tbody", "tfoot"}:
            rows.extend(row for row in _iter_elements(child) if row.tag == "tr")
    return rows


def _render_pre(node: LexborNode, depth: int, out: list[str]) -> None:
    """Preformatted text keeps its own line breaks; collapsing them would destroy code."""
    text = (node.text(deep=True) or "").strip("\n")
    if not text.strip():
        return
    out.append(_indent(depth) + "pre")
    indent = _indent(depth + 1)
    out.extend(indent + line.rstrip() for line in text.splitlines())


def _leaf_line(tag: str, attrs: dict[str, str | None], text: str) -> str:
    if tag == "a":
        return _join("a", _quote(text), attrs.get("href"))
    if tag == "button":
        return _join("button", _quote(text or attrs.get("value") or ""))
    if tag == "form":
        method = (attrs.get("method") or "GET").upper()
        return _join("form", method, attrs.get("action"))
    if tag == "select":
        return _join("select", attrs.get("name"), *_flags(attrs))
    if tag == "option":
        return _join("option", _quote(text), _value(attrs.get("value")), *_flags(attrs))
    if tag == "textarea":
        return _join("textarea", attrs.get("name"), _quote(attrs.get("placeholder") or text))
    if tag in {"video", "audio"}:
        return _join(tag, attrs.get("src"))
    label = _quote(text or attrs.get("aria-label") or attrs.get("title") or "")
    if not label:
        return ""
    return _join(tag, label)


def _input_line(attrs: dict[str, str | None]) -> str:
    return _join(
        "input",
        attrs.get("type") or "text",
        attrs.get("name"),
        _quote(attrs.get("placeholder") or attrs.get("aria-label") or ""),
        _value(attrs.get("value")),
        *_flags(attrs),
    )


def _img_line(attrs: dict[str, str | None]) -> str:
    alt = _quote(attrs.get("alt") or "")
    src = attrs.get("src")
    if not alt and not src:
        return ""
    return _join("img", alt, src)


_VOID_RENDERERS = {"input": _input_line, "img": _img_line}


def _iter_elements(node: LexborNode) -> list[LexborNode]:
    elements: list[LexborNode] = []
    child = node.child
    while child is not None:
        if child.tag not in {"-text", "-comment"}:
            elements.append(child)
        child = child.next
    return elements


def _collapse(lines: list[str]) -> str | None:
    """Join rendered children into one text run, or None when any child emits structure."""
    parts: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not (stripped.startswith('"') and stripped.endswith('"')):
            return None
        parts.append(stripped[1:-1])
    return " ".join(part for part in parts if part)


def _is_hidden(node: LexborNode) -> bool:
    attrs = node.attributes
    if "hidden" in attrs and node.tag != "input":
        return True
    if attrs.get("aria-hidden") == "true":
        return True
    style = (attrs.get("style") or "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def _flags(attrs: dict[str, str | None]) -> tuple[str, ...]:
    return tuple(name for name in _BOOLEAN_ATTRS if name in attrs)


def _value(value: str | None) -> str | None:
    text = _norm(value or "")
    return f"={text}" if text else None


def _quote(text: str) -> str:
    normalized = _norm(text)
    return f'"{normalized}"' if normalized else ""


def _join(*parts: str | None) -> str:
    return " ".join(part for part in parts if part)


def _norm(text: str) -> str:
    return _WHITESPACE.sub(" ", text.replace('"', "'")).strip()


def _indent(depth: int) -> str:
    return " " * depth
