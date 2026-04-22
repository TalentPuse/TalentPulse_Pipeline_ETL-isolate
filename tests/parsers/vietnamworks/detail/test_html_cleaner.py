from src.parsers.vietnamworks.detail.html_cleaner import strip_html


def test_strip_html_none_and_empty():
    assert strip_html(None) is None
    assert strip_html("") is None
    assert strip_html("   ") is None


def test_strip_html_plain_passthrough():
    assert strip_html("hello world") == "hello world"


def test_strip_html_br_to_newline():
    assert strip_html("a<br>b<br/>c") == "a\nb\nc"


def test_strip_html_preserves_bullets():
    out = strip_html("<ul><li>Python</li><li>SQL</li></ul>")
    assert "- Python" in out
    assert "- SQL" in out


def test_strip_html_collapses_blank_runs():
    out = strip_html("<p>a</p><p></p><p></p><p>b</p>")
    # Allow at most 1 blank line between
    assert "a\n\nb" in out or "a\nb" in out
    assert "\n\n\n" not in out


def test_strip_html_handles_entities():
    out = strip_html("Java &amp; Python")
    assert out == "Java & Python"
