from finpipe.core.llm_sanitize import sanitize_llm_text, sanitize_llm_url


def test_sanitize_llm_url_strips_tracking_params():
    raw = "https://example.com/article?utm_source=twitter&id=42&fbclid=abc"
    assert sanitize_llm_url(raw) == "https://example.com/article?id=42"


def test_sanitize_llm_text_strips_html_and_emoji():
    raw = "<p>Big move &#128640; for $NVDA</p>"
    assert sanitize_llm_text(raw) == "Big move for $NVDA"


def test_sanitize_llm_text_rewrites_markdown_links():
    raw = "Read [story](https://news.com/x?utm_campaign=spam) now"
    assert sanitize_llm_text(raw) == "Read story (https://news.com/x) now"


def test_sanitize_llm_text_removes_markdown_images():
    raw = "Look ![chart](https://img.com/a.png) at this"
    assert sanitize_llm_text(raw) == "Look at this"


def test_sanitize_llm_text_collapses_blank_lines():
    raw = "line one\n\n\n  line two  \n"
    assert sanitize_llm_text(raw) == "line one\nline two"


def test_sanitize_llm_text_preserve_newlines_false():
    raw = "line one\n\nline two"
    assert sanitize_llm_text(raw, preserve_newlines=False) == "line one line two"
