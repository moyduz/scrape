import re
from bs4 import BeautifulSoup


_NOISE_TAGS = ["script", "style", "noscript", "svg", "path", "meta", "link"]
_NOISE_ATTRS = ["onclick", "onmouseover", "onload", "data-react-", "data-v-"]


def clean_html(html: str) -> str:
    """
    Strips scripts, styles, SVG noise, and tracking attributes from raw HTML.
    Returns clean HTML string ready for extraction.
    """
    soup = BeautifulSoup(html, "lxml")

    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()

    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if any(attr.startswith(prefix) for prefix in _NOISE_ATTRS):
                del tag.attrs[attr]

    return str(soup)


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\x00-\x7F]+", " ", text)
    return text.strip()
