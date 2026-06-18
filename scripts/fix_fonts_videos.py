"""
One-shot fixer for an already-generated Next.js DOM renderer project.

Usage:
    python scripts/fix_fonts_videos.py <url> <slug>

Example:
    python scripts/fix_fonts_videos.py https://influence.framer.media/ influence-framer-media

What it does:
  1. Launches Playwright on the URL, extracts @font-face CSS.
  2. Reconstructs URL→local-path map from already-downloaded assets.
  3. Rewrites @font-face src to /fonts/<file> and copies files to public/fonts/.
  4. Prepends the rewritten CSS to styles/globals.css.
  5. Screenshots all video elements, copies to public/video-captures/.
  6. Updates dom_page.json video nodes with local poster paths.
  7. Rebuilds DOMPage.tsx (regenerates from updated JSON).
"""
import json
import re
import shutil
import sys
from pathlib import Path

# Ensure project root is on sys.path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scraper.playwright_loader import PageSession
from scraper.asset_downloader import _url_to_dest
from utils.font_rewriter import rewrite_font_faces
from utils.dom_to_react import generate_dom_renderer
from config.settings import ASSETS_DIR, NEXTJS_DIR, DOM_DIR, PLAYWRIGHT_TIMEOUT


def _reconstruct_asset_map(base_dir: Path) -> dict[str, str]:
    """
    Rebuild {url: local_path} from files already downloaded to base_dir.
    Reverses the _url_to_dest() mapping.
    """
    result: dict[str, str] = {}
    for f in base_dir.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(base_dir)
        parts = rel.parts
        if len(parts) < 2:
            continue
        netloc = parts[0]
        path_str = "/".join(parts[1:])
        # Strip query-hash suffix added by _url_to_dest (_q<6hex> before extension)
        clean = re.sub(r"_q[0-9a-f]{6}(\.[^./]+)$", r"\1", path_str)
        url = f"https://{netloc}/{clean}"
        result[url] = str(f)
    return result


def _patch_video_posters(tree: dict, shot_paths: list[str], counter: list) -> dict:
    if not isinstance(tree, dict):
        return tree
    if tree.get("tag") == "video" and counter[0] < len(shot_paths):
        name = Path(shot_paths[counter[0]]).name
        tree = {**tree, "attrs": {**tree.get("attrs", {}), "poster": f"/video-captures/{name}"}}
        counter[0] += 1
    if "children" in tree:
        tree = {**tree, "children": [_patch_video_posters(c, shot_paths, counter)
                                      for c in tree["children"]]}
    return tree


def fix(url: str, slug: str) -> None:
    nextjs_dir = NEXTJS_DIR / slug
    assets_dir = ASSETS_DIR / slug

    print(f"→ Fixing {slug} at {nextjs_dir}")

    # ── 1. Extract @font-face CSS + video screenshots via Playwright ──────────
    print("→ Launching browser…")
    with PageSession(url, timeout=PLAYWRIGHT_TIMEOUT) as session:
        font_face_css = session.extract_font_face_css()
        video_shot_dir = assets_dir / "video_screenshots"
        video_shots = session.capture_video_screenshots(video_shot_dir)

    print(f"  @font-face CSS: {len(font_face_css)} chars")
    print(f"  Video frames:   {len(video_shots)}")

    # ── 2. Reconstruct asset map from downloaded files ───────────────────────
    asset_map = _reconstruct_asset_map(assets_dir)
    print(f"  Asset map:      {len(asset_map)} entries")

    # ── 3. Rewrite fonts → public/fonts ─────────────────────────────────────
    public_fonts = nextjs_dir / "public" / "fonts"
    rewritten_css = rewrite_font_faces(font_face_css, asset_map, public_fonts)
    if rewritten_css.strip():
        globals_path = nextjs_dir / "styles" / "globals.css"
        existing = globals_path.read_text(encoding="utf-8") if globals_path.exists() else ""
        # Remove any previously prepended @font-face block to avoid duplicates
        if "@font-face" in existing:
            # Strip everything before the first @tailwind directive
            existing = re.sub(r"^.*?(@tailwind)", r"\1", existing, flags=re.DOTALL)
        globals_path.write_text(rewritten_css + "\n" + existing, encoding="utf-8")
        font_count = len(list(public_fonts.glob("*"))) if public_fonts.exists() else 0
        print(f"  → {font_count} font files written to public/fonts/")
    else:
        print("  ⚠ No @font-face CSS to rewrite (check that asset_map has font URLs)")

    # ── 4. Copy video screenshots → public/video-captures ───────────────────
    if video_shots:
        vid_pub = nextjs_dir / "public" / "video-captures"
        vid_pub.mkdir(parents=True, exist_ok=True)
        for p in video_shots:
            shutil.copy2(p, vid_pub / Path(p).name)
        print(f"  → {len(video_shots)} frames → public/video-captures/")

    # ── 5. Re-walk DOM at desktop + mobile (dual-tree breakpoint model) ──────
    print("→ Re-walking DOM at desktop + mobile…")
    with PageSession(url, timeout=PLAYWRIGHT_TIMEOUT) as session2:
        dom_result = session2.walk_dom_responsive()

    meta = dom_result.get("_meta", {})
    print(f"  desktop={meta.get('desktop_nodes',0)}n  mobile={meta.get('mobile_nodes',0)}n")

    dom_path = DOM_DIR / f"{slug}_responsive.json"
    dom_path.write_text(json.dumps(dom_result, ensure_ascii=False, indent=2), encoding="utf-8")

    patched = {
        "desktop": _patch_video_posters(dom_result.get("desktop", {}), video_shots, [0]),
        "mobile":  _patch_video_posters(dom_result.get("mobile", {}),  video_shots, [0]),
    }
    tsx_content, desktop_json, mobile_json = generate_dom_renderer(patched)

    comp_dir = nextjs_dir / "components"
    comp_dir.mkdir(parents=True, exist_ok=True)
    (comp_dir / "DOMPage.tsx").write_text(tsx_content, encoding="utf-8")
    (comp_dir / "dom_desktop.json").write_text(desktop_json, encoding="utf-8")
    (comp_dir / "dom_mobile.json").write_text(mobile_json, encoding="utf-8")

    # Ensure /dom-page route exists
    route_dir = nextjs_dir / "app" / "dom-page"
    route_dir.mkdir(parents=True, exist_ok=True)
    (route_dir / "page.tsx").write_text(
        'import DOMPage from "@/components/DOMPage";\n\n'
        "export default function Page() {\n  return <DOMPage />;\n}\n",
        encoding="utf-8",
    )

    print("  → DOMPage.tsx + dom_page.json regenerated")
    print("✓ Done — restart next dev to pick up font and video changes")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    fix(sys.argv[1], sys.argv[2])
