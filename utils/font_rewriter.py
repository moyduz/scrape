"""
Rewrites @font-face src URLs to local /fonts/ paths.
Copies font files from downloaded assets to Next.js public/fonts/.
"""
import re
import shutil
from pathlib import Path

_URL_RE = re.compile(r'url\(["\']?([^"\')\s]+)["\']?\)')


def rewrite_font_faces(
    font_face_css: str,
    asset_map: dict[str, str],
    public_fonts_dir: Path,
) -> str:
    """
    Rewrites every url(...) inside @font-face CSS to point to /fonts/<filename>.
    Copies the font file to public_fonts_dir.
    Returns rewritten CSS ready for globals.css.
    """
    public_fonts_dir.mkdir(parents=True, exist_ok=True)

    def replace_url(m: re.Match) -> str:
        url = m.group(1)
        if url.startswith("data:"):
            return m.group(0)
        local = asset_map.get(url)
        if not local:
            return m.group(0)
        src = Path(local)
        dest = public_fonts_dir / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
        return f'url("/fonts/{src.name}")'

    return _URL_RE.sub(replace_url, font_face_css)
