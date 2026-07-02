"""Normalize intel/social text before LLM prompts (direct port from v1 — sound).

Lives under providers/llm rather than core: it's provider-input preparation,
not domain logic (review §4 flagged vendor parsing living in ``core``).
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001faff"
    "\U00002700-\U000027bf"
    "\U0001f600-\U0001f64f"
    "\U0001f680-\U0001f6ff"
    "\U00002600-\U000026ff"
    "]+",
    flags=re.UNICODE,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
# Zero-width and bidi control chars, written with escapes so no invisible
# characters live in this source file.
_ZERO_WIDTH_RE = re.compile("[\\u200b-\\u200f\\u202a-\\u202e\\u2060-\\u206f\\ufeff]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TRACKING_QUERY_PREFIXES = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "igshid")


def sanitize_llm_url(url: str) -> str:
    """Drop tracking query params; leave the destination URL intact."""
    stripped = url.strip()
    if not stripped:
        return stripped
    try:
        parsed = urlparse(stripped)
    except ValueError:
        return stripped
    if not parsed.scheme or not parsed.netloc:
        return stripped
    filtered = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not any(key.lower().startswith(prefix) for prefix in _TRACKING_QUERY_PREFIXES)
    ]
    return urlunparse(parsed._replace(query=urlencode(filtered, doseq=True)))


def sanitize_llm_text(text: str, *, preserve_newlines: bool = True) -> str:
    """Strip HTML, emojis, markdown images, and noisy whitespace for LLM input."""
    if not text:
        return ""

    cleaned = html.unescape(text)
    cleaned = _ZERO_WIDTH_RE.sub("", cleaned)
    cleaned = _CONTROL_RE.sub("", cleaned)
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    cleaned = _MARKDOWN_IMAGE_RE.sub("", cleaned)
    cleaned = _MARKDOWN_LINK_RE.sub(
        lambda match: f"{match.group(1)} ({sanitize_llm_url(match.group(2))})",
        cleaned,
    )
    cleaned = _EMOJI_RE.sub("", cleaned)
    cleaned = _URL_RE.sub(lambda m: sanitize_llm_url(m.group(0).rstrip(".,);]")), cleaned)

    if preserve_newlines:
        lines = []
        for line in cleaned.splitlines():
            collapsed = re.sub(r"[ \t]+", " ", line).strip()
            if collapsed:
                lines.append(collapsed)
        return "\n".join(lines)
    return re.sub(r"\s+", " ", cleaned).strip()
