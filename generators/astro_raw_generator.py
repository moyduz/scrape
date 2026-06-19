import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from rich.console import Console

from config.settings import ASTRO_DIR
from processor.personalize_preview import clean_framer_css, personalize_soup
from processor.quality_check import run_static_quality_checks, save_quality_report

MOYDUS_APP_URL = os.environ.get("MOYDUS_APP_URL", "https://app.moydus.com").rstrip("/")


def _build_claim_bar(business_profile: dict | None) -> str:
    """
    Returns an HTML snippet for the sticky claim bar shown at the top of demo pages.
    The "Claim this site" button links to the dashboard onboarding with the demo pre-filled.
    """
    if not business_profile:
        return ""

    name = business_profile.get("name", "Your business")
    claim_url = business_profile.get("claim_url") or business_profile.get("demo_claim_url")
    if not claim_url:
        website = business_profile.get("website", "")
        params = urlencode({k: v for k, v in {
            "source": "outbound_claim",
            "site_url": website,
        }.items() if v})
        claim_url = f"{MOYDUS_APP_URL}/onboarding/scan" + (f"?{params}" if params else "")

    return f"""<div id="moydus-claim-bar" style="position:fixed;top:0;left:0;right:0;z-index:99999;background:#0f0f0f;border-bottom:1px solid rgba(255,255,255,0.08);padding:10px 20px;display:flex;align-items:center;justify-content:space-between;gap:16px;font-family:system-ui,-apple-system,sans-serif;">
  <span style="color:rgba(255,255,255,0.6);font-size:13px;line-height:1.4;">
    <strong style="color:#fff;">This preview was built for {name}.</strong>
    Want to customize it and go live?
  </span>
  <a href="{claim_url}" target="_blank" rel="noopener noreferrer"
     style="flex-shrink:0;background:#fa5d19;color:#fff;text-decoration:none;font-size:13px;font-weight:600;padding:8px 16px;border-radius:8px;white-space:nowrap;transition:opacity .15s;"
     onmouseover="this.style.opacity='.85'" onmouseout="this.style.opacity='1'">
    Claim this site →
  </a>
</div>
<style>body{{padding-top:48px !important;}}</style>"""

console = Console()

_CSS_URL_RE = re.compile(r'url\(["\']?([^"\')\s]+)["\']?\)')

def _copy_asset(local_path: str, dest_dir: Path, public_prefix: str) -> str:
    """
    Copies the asset from local_path to dest_dir, and returns the absolute public URL.
    """
    src = Path(local_path)
    if not src.exists():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if not dest.exists():
        shutil.copy2(src, dest)
    return f"{public_prefix}/{src.name}"

def generate_raw_astro(
    slug: str,
    html: str,
    css: str,
    asset_map: dict[str, str],
    business_profile: dict | None = None,
) -> dict:
    """
    Generates a raw Astro clone project.
    Rewrites HTML and CSS asset URLs, copies assets, and produces an Astro structure.
    """
    output_dir = ASTRO_DIR / slug
    public_assets = output_dir / "public" / "assets"
    public_fonts = output_dir / "public" / "fonts"
    
    # 1. Parse HTML and rewrite DOM
    soup = BeautifulSoup(html, "html5lib")
    personalization_report = personalize_soup(soup, business_profile)
    
    # Remove script tags for safety and hydration issues
    for script in soup.find_all("script"):
        script.decompose()

    # Rewrite <img> tags
    for img in soup.find_all("img"):
        src = img.get("src")
        if src and src in asset_map:
            new_url = _copy_asset(asset_map[src], public_assets, "/assets")
            if new_url:
                img["src"] = new_url
        
        # Handle srcset properly instead of deleting
        srcset = img.get("srcset")
        if srcset:
            new_srcset = []
            for part in srcset.split(","):
                part = part.strip()
                if not part: continue
                url_space = part.split(" ")
                url = url_space[0]
                # Try to unescape &amp; just in case
                if url.replace("&amp;", "&") in asset_map:
                    url = url.replace("&amp;", "&")
                if url in asset_map:
                    new_url = _copy_asset(asset_map[url], public_assets, "/assets")
                    if new_url:
                        url_space[0] = new_url
                new_srcset.append(" ".join(url_space))
            img["srcset"] = ", ".join(new_srcset)

    # Rewrite <video> tags
    for video in soup.find_all("video"):
        poster = video.get("poster")
        if poster and poster in asset_map:
            new_url = _copy_asset(asset_map[poster], public_assets, "/assets")
            if new_url:
                video["poster"] = new_url
        
        src = video.get("src")
        if src and src in asset_map:
            new_url = _copy_asset(asset_map[src], public_assets, "/assets")
            if new_url:
                video["src"] = new_url

    # Rewrite <source> tags (for video/picture)
    for source in soup.find_all("source"):
        src = source.get("src")
        if src and src in asset_map:
            new_url = _copy_asset(asset_map[src], public_assets, "/assets")
            if new_url:
                source["src"] = new_url
                
        srcset = source.get("srcset")
        if srcset:
            new_srcset = []
            for part in srcset.split(","):
                part = part.strip()
                if not part: continue
                url_space = part.split(" ")
                url = url_space[0]
                if url.replace("&amp;", "&") in asset_map:
                    url = url.replace("&amp;", "&")
                if url in asset_map:
                    new_url = _copy_asset(asset_map[url], public_assets, "/assets")
                    if new_url:
                        url_space[0] = new_url
                new_srcset.append(" ".join(url_space))
            source["srcset"] = ", ".join(new_srcset)

    # 1.5 Strip Framer motion inline styles that hide elements
    for el in soup.find_all(style=True):
        style = el.get("style", "")
        style_nospaces = style.replace(" ", "")
        # Framer often hides things with opacity: 0 and transform: translate
        if "will-change:transform" in style_nospaces or "data-framer-appear-id" in el.attrs or "opacity:0" in style_nospaces:
            # Override opacity to 1 (handles opacity:0 and opacity:0.001)
            style = re.sub(r'opacity:\s*0(\.[0-9]+)?\s*;?', 'opacity: 1;', style)
            # Remove translate/scale transforms that push elements off-screen
            style = re.sub(r'transform:\s*(translate|scale)[^;]+;?', 'transform: none;', style)
            el["style"] = style

    # 2. Rewrite CSS URLs (fonts, background-images)
    def css_url_replacer(m: re.Match) -> str:
        url = m.group(1)
        if url.startswith("data:"):
            return m.group(0)
            
        local_path = asset_map.get(url)
        if not local_path:
            return m.group(0)
            
        # Determine if it's a font or an image based on extension
        ext = Path(local_path).suffix.lower()
        if ext in [".woff", ".woff2", ".ttf", ".eot", ".otf"]:
            new_url = _copy_asset(local_path, public_fonts, "/fonts")
        else:
            new_url = _copy_asset(local_path, public_assets, "/assets")
            
        if new_url:
            return f'url("{new_url}")'
        return m.group(0)

    rewritten_css = _CSS_URL_RE.sub(css_url_replacer, css)
    rewritten_css, css_cleanup_report = clean_framer_css(rewritten_css)
    personalization_report["css_cleanup"] = css_cleanup_report

    # 3. Create Astro files
    src_dir = output_dir / "src"
    pages_dir = src_dir / "pages"
    styles_dir = src_dir / "styles"
    
    pages_dir.mkdir(parents=True, exist_ok=True)
    styles_dir.mkdir(parents=True, exist_ok=True)
    
    # Save CSS
    css_path = styles_dir / "cloned.css"
    css_path.write_text(rewritten_css, encoding="utf-8")
    
    # Inject claim bar into <body> before closing tag
    claim_bar_html = _build_claim_bar(business_profile)
    body_tag = soup.find("body")
    if body_tag and claim_bar_html:
        claim_soup = BeautifulSoup(claim_bar_html, "html5lib")
        claim_node = claim_soup.find("div", {"id": "moydus-claim-bar"})
        claim_style = claim_soup.find("style")
        if claim_node:
            body_tag.append(claim_node)
        if claim_style:
            body_tag.append(claim_style)

    # Save Astro Page
    html_content = str(soup)

    astro_content = f"""---
import "../styles/cloned.css";
---
{html_content}
"""

    astro_path = pages_dir / "index.astro"
    astro_path.write_text(astro_content, encoding="utf-8")

    quality_report = run_static_quality_checks(astro_path, business_profile)
    quality_path = output_dir / "quality-report.json"
    save_quality_report(quality_report, quality_path)
    status = "passed" if quality_report.get("passed") else "failed"
    console.print(f"  -> Quality check {status}: {quality_path}")
    
    console.print(f"  -> Astro raw clone created at {output_dir}")
    
    return {
        "output_dir": str(output_dir),
        "astro_path": str(astro_path),
        "css_path": str(css_path),
        "personalization": personalization_report,
        "quality_report": quality_report,
        "quality_report_path": str(quality_path),
    }
