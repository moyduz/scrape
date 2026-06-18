"""
Network request interceptor — captures all asset URLs fired during page load.
Must be wired up via setup_network_capture() BEFORE page.goto().
"""
from urllib.parse import urlparse

_ASSET_EXTS = frozenset({
    ".css", ".js",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif", ".ico",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".webm", ".ogg",
})

_SKIP_HOSTS = frozenset({
    "analytics.google.com", "www.google-analytics.com",
    "www.googletagmanager.com", "cdn.segment.com",
    "connect.facebook.net", "sc-static.net", "stats.g.doubleclick.net",
    "www.clarity.ms", "bat.bing.com",
})


def _classify(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        if parsed.netloc.lower() in _SKIP_HOSTS:
            return None
        segment = parsed.path.rsplit("/", 1)[-1].lower()
        if "." not in segment:
            return None
        ext = "." + segment.rsplit(".", 1)[-1].split("?")[0]
        if ext not in _ASSET_EXTS:
            return None
        if ext == ".css":
            return "css"
        if ext == ".js":
            return "js"
        if ext in {".woff", ".woff2", ".ttf", ".otf", ".eot"}:
            return "font"
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif", ".ico"}:
            return "image"
        if ext in {".mp4", ".webm", ".ogg"}:
            return "video"
    except Exception:
        pass
    return None


def setup_network_capture(page) -> dict[str, list[str]]:
    """
    Registers a request listener BEFORE navigation.
    Returns a live dict that is populated as requests fire.

    Shape: {"css": [...], "js": [...], "font": [...], "image": [...], "video": [...], "all": [...]}
    """
    assets: dict[str, list[str]] = {
        "css": [], "js": [], "font": [], "image": [], "video": [], "all": [],
    }
    seen: set[str] = set()

    def _on_request(request):
        url = request.url
        if url in seen:
            return
        seen.add(url)
        assets["all"].append(url)
        kind = _classify(url)
        if kind:
            assets[kind].append(url)

    page.on("request", _on_request)
    return assets
