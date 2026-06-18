import re
from urllib.parse import urljoin


def extract_color_tokens(css: str) -> dict[str, str]:
    """Extracts CSS custom properties (color tokens) from stylesheet."""
    pattern = r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8}|rgba?\([^)]+\))"
    matches = re.findall(pattern, css)
    return {f"--{name}": value for name, value in matches}


_FONT_FACE_RE = re.compile(r'@font-face\s*\{[^}]+\}', re.IGNORECASE | re.DOTALL)
_URL_RE = re.compile(r'url\(["\']?([^"\')\s]+)["\']?\)')


def extract_font_face_urls(css: str, base_url: str = "") -> list[str]:
    """
    Extracts all font file source URLs declared in @font-face blocks.
    Resolves relative paths against base_url when provided.
    """
    urls: list[str] = []
    seen: set[str] = set()
    for block_match in _FONT_FACE_RE.finditer(css):
        for url_match in _URL_RE.finditer(block_match.group()):
            url = url_match.group(1)
            if url.startswith("data:") or url in seen:
                continue
            if not url.startswith(("http://", "https://", "//")):
                url = urljoin(base_url, url) if base_url else url
            seen.add(url)
            urls.append(url)
    return urls


_GENERIC_FONTS = {"sans-serif", "serif", "monospace", "system-ui", "cursive", "fantasy", "inherit", "initial", "unset"}


def extract_font_families(css: str) -> list[str]:
    pattern = r"font-family\s*:\s*([^;]+)"
    matches = re.findall(pattern, css)
    fonts = []
    for match in matches:
        for font in match.split(","):
            cleaned = font.strip().strip("'\"").strip()
            if not cleaned:
                continue
            # Skip CSS variables
            if cleaned.startswith("var("):
                continue
            # Skip anything with unbalanced parens (malformed CSS fragments)
            if ")" in cleaned or "(" in cleaned:
                continue
            # Skip placeholder/fallback fonts injected by Framer
            if "Placeholder" in cleaned:
                continue
            # Skip generic families
            if cleaned.lower() in _GENERIC_FONTS:
                continue
            # Skip system-style names (no spaces, all-caps, or purely numeric)
            if cleaned.startswith("-") or cleaned.startswith("__"):
                continue
            if cleaned not in fonts:
                fonts.append(cleaned)
    return fonts
