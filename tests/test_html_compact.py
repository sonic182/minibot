from __future__ import annotations

import pytest

from minibot.shared.html_compact import html_to_compact


def test_drops_script_style_and_svg() -> None:
    compact = html_to_compact(
        "<style>.a{color:red}</style><script>evil()</script><svg><path d='M0 0'/></svg><p>Hello</p>"
    )
    assert compact == 'p "Hello"'


def test_link_keeps_target() -> None:
    assert html_to_compact('<a href="/foo">Foo</a>') == 'a "Foo" /foo'


def test_nested_layout_tags_produce_no_wrappers() -> None:
    compact = html_to_compact(
        '<div class="container"><div class="row"><div class="col"><span>Hello</span></div></div></div>'
    )
    assert compact == '"Hello"'


def test_heading_hierarchy_is_preserved() -> None:
    compact = html_to_compact("<h1>Main</h1><h2>Section</h2><p>Text</p>")
    assert compact.splitlines() == ['h1 "Main"', 'h2 "Section"', 'p "Text"']


def test_inline_formatting_collapses_into_one_line() -> None:
    assert html_to_compact("<p>Great <b>laptop</b> for work.</p>") == 'p "Great laptop for work."'


def test_form_keeps_action_method_inputs_and_button() -> None:
    compact = html_to_compact(
        '<form action="/login" method="post">'
        '<input type="email" name="email" placeholder="Email">'
        '<input type="password" name="password" required>'
        '<input type="hidden" name="csrf" value="abc">'
        "<button>Login</button></form>"
    )
    assert compact.splitlines() == [
        "form POST /login",
        ' input email email "Email"',
        " input password password required",
        " input hidden csrf =abc",
        ' button "Login"',
    ]


def test_select_keeps_options_and_selection() -> None:
    compact = html_to_compact(
        '<select name="size"><option value="l" selected>Large</option><option value="s">Small</option></select>'
    )
    assert compact.splitlines() == [
        "select size",
        ' option "Large" =l selected',
        ' option "Small" =s',
    ]


def test_list_items_stay_separate() -> None:
    compact = html_to_compact("<ul><li>Fast</li><li>Light</li></ul>")
    assert compact.splitlines() == ["ul", ' li "Fast"', ' li "Light"']


def test_table_rows_render_as_pipe_rows() -> None:
    compact = html_to_compact(
        "<table><thead><tr><th>Model</th><th>Price</th></tr></thead>"
        "<tbody><tr><td>Symbioz</td><td>31,200</td></tr></tbody></table>"
    )
    assert compact.splitlines() == ["table", " | Model | Price |", " | Symbioz | 31,200 |"]


def test_table_cell_with_link_falls_back_to_nested_rendering() -> None:
    compact = html_to_compact(
        '<table><tr><td>Model</td></tr><tr><td><a href="/a">Austral</a></td><td>35,900</td></tr></table>'
    )
    assert compact.splitlines() == ["table", " | Model |", " tr", '  a "Austral" /a', '  "35,900"']


def test_single_cell_layout_table_is_transparent() -> None:
    compact = html_to_compact('<table><tr><td><a href="/a">Austral</a></td></tr></table>')
    assert compact == 'a "Austral" /a'


def test_nested_table_rows_are_not_duplicated() -> None:
    compact = html_to_compact("<table><tr><td><table><tr><td>Inner</td></tr></table></td></tr></table>")
    assert compact.count("Inner") == 1


def test_styling_and_tracking_attributes_are_dropped() -> None:
    compact = html_to_compact(
        '<a id="l" class="btn btn-lg" style="color:red" data-testid="go" onclick="track()" href="/go">Go</a>'
    )
    assert compact == 'a "Go" /go'
    for noise in ("btn", "color:red", "data-testid", "onclick", "id="):
        assert noise not in compact


def test_hidden_nodes_are_dropped() -> None:
    compact = html_to_compact(
        '<p style="display: none">secret</p><p hidden>gone</p><p aria-hidden="true">invisible</p><p>kept</p>'
    )
    assert compact == 'p "kept"'


def test_image_keeps_alt_and_src() -> None:
    assert html_to_compact('<img src="/a.png" alt="Cat" class="x">') == 'img "Cat" /a.png'


def test_preformatted_text_keeps_line_breaks() -> None:
    compact = html_to_compact("<pre><code>def f():\n    return 1</code></pre>")
    assert compact.splitlines() == ["pre", " def f():", "     return 1"]


@pytest.mark.parametrize(
    "html",
    ["<div><p>x</div>", "<p>unclosed", "<a href=>x</a>", "<<>>", "<p>a<", "<html>"],
)
def test_malformed_html_does_not_raise(html: str) -> None:
    assert isinstance(html_to_compact(html), str)


@pytest.mark.parametrize("html", ["", "   ", "\n\t"])
def test_empty_html_returns_empty_string(html: str) -> None:
    assert html_to_compact(html) == ""


def test_deeply_nested_html_does_not_raise_recursion_error() -> None:
    deep = "<div>" * 3000 + "x" + "</div>" * 3000
    assert isinstance(html_to_compact(deep), str)


def test_compact_is_far_smaller_than_raw_html_and_keeps_more_than_text() -> None:
    raw = _shop_page()
    compact = html_to_compact(raw)
    assert len(compact) < len(raw) / 3
    # Semantics plain text extraction would have lost.
    assert "/products/123" in compact
    assert "form POST /cart" in compact
    assert "input email username" in compact
    assert "| Symbioz | 31,200 |" in compact


def _shop_page() -> str:
    cards = "".join(
        f'<div class="flex flex-col gap-2 md:grid product-card" data-testid="product-{index}" '
        f'data-analytics-id="{index}0000">'
        f'<a class="font-bold hover:text-blue-500" href="/products/{index}">Item {index}</a>'
        f'<span class="price text-sm text-gray-500">{index}99 EUR</span></div>'
        for index in range(120, 130)
    )
    return f"""
    <html><head><title>Example Shop</title>
    <style>{".a{color:red}" * 200}</style>
    <script>{"track();" * 200}</script></head>
    <body class="antialiased"><nav class="navbar navbar-expand-lg">
    <a class="nav-link active" href="/products">Products</a>
    <a class="nav-link" href="/pricing">Pricing</a></nav>
    <main><h1 class="display-4">MacBook Air M4</h1>
    <div class="container"><div class="row">{cards}</div></div>
    <form method="POST" action="/cart" class="needs-validation">
    <input type="hidden" name="product" value="123">
    <input id="u" class="form-control" type="email" name="username" placeholder="Email address">
    <button type="submit" class="btn btn-primary">Buy</button></form>
    <table class="table"><tr><th>Model</th><th>Price</th></tr>
    <tr><td>Symbioz</td><td>31,200</td></tr></table>
    <svg viewBox="0 0 24 24"><path d="{"M0 0 " * 100}"/></svg>
    </main></body></html>
    """
