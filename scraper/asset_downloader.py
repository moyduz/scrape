"""
Concurrent asset downloader.
Downloads captured URLs into a local directory tree, preserving host/path structure.
"""
import asyncio
import hashlib
from pathlib import Path
from urllib.parse import urlparse

import httpx

_TIMEOUT = httpx.Timeout(15.0)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def _url_to_dest(url: str, base_dir: Path) -> Path:
    parsed = urlparse(url)
    path = parsed.path.lstrip("/") or "index"
    path = path.replace("..", "DOTDOT")
    if parsed.query:
        p = Path(path)
        q_hash = hashlib.md5(parsed.query.encode()).hexdigest()[:6]
        path = f"{p.parent if str(p.parent) != '.' else ''}/{p.stem}_q{q_hash}{p.suffix}".lstrip('/')
    return base_dir / parsed.netloc / path


async def _fetch_one(
    client: httpx.AsyncClient, url: str, dest: Path
) -> tuple[str, bool]:
    if dest.exists():
        return url, True
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code == 200:
            dest.write_bytes(resp.content)
            return url, True
    except Exception:
        pass
    return url, False


async def _download_all(
    urls: list[str], base_dir: Path, max_concurrent: int
) -> dict[str, str]:
    sem = asyncio.Semaphore(max_concurrent)
    results: dict[str, str] = {}

    async def bounded(url: str):
        dest = _url_to_dest(url, base_dir)
        async with sem:
            _, ok = await _fetch_one(client, url, dest)
        if ok:
            results[url] = str(dest)

    async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT) as client:
        await asyncio.gather(*[bounded(u) for u in urls])

    return results


def download_assets_sync(
    urls: list[str], base_dir: Path, max_concurrent: int = 12
) -> dict[str, str]:
    """
    Synchronous entry point. Returns {url: local_path} for each successful download.
    Skips already-downloaded files.
    """
    if not urls:
        return {}
    return asyncio.run(_download_all(urls, base_dir, max_concurrent))
