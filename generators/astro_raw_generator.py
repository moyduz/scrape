import os
import re
import shutil
from pathlib import Path
from html import escape
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from rich.console import Console

from config.settings import ASTRO_DIR
from processor.personalize_preview import clean_framer_css, personalize_soup
from processor.quality_check import run_static_quality_checks, save_quality_report

MOYDUS_APP_URL = os.environ.get("MOYDUS_APP_URL", "https://app.moydus.com").rstrip("/")


MOYDUS_MARK_SVG = """<svg aria-hidden=\"true\" viewBox=\"0 0 1024 1024\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\" style=\"width:21px;height:21px;display:block\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M477.71 91.6205C395.196 104.976 326.239 146.882 283.679 209.535C268.059 232.531 203.694 345.244 197.694 360.108C187.812 384.597 182.362 422.156 184.191 453.201C185.061 467.952 186.728 480.975 187.901 482.148C189.07 483.319 219.195 433.21 254.848 370.798C290.5 308.386 325.737 249.131 333.155 239.117C360.703 201.932 389.683 178.565 426.978 163.466C459.966 150.11 466.78 149.475 579.018 149.274L684.379 149.088L677.755 143.573C654.762 124.423 607.112 103.227 568.912 95.1595C545.587 90.2308 497.753 88.3762 477.71 91.6205ZM471.243 168.475C441.996 172.747 403.604 191.129 379.103 212.595L368.297 222.063L516.883 222.204C677.625 222.358 680.113 222.547 717.665 237.582C753.761 252.032 787.281 276.832 804.423 301.768C816.528 319.376 891.329 449.337 897.813 464.023C903.175 476.176 903.835 476.76 905.434 470.782C908.665 458.7 907.298 412.323 903.051 389.918C892.326 333.337 866.361 283.762 826.416 243.586C783.27 200.194 728.696 174.823 664.507 168.324C631.672 164.999 494.314 165.107 471.243 168.475ZM246.649 205.593C215.373 225.943 198.013 241.867 177.716 268.822C120.397 344.947 102.985 449.564 132.905 538.061C141.274 562.823 217.423 695.237 236.558 718.309C252.724 737.799 282.99 759.482 306.118 768.142C314.425 771.253 322.265 773.798 323.538 773.798C324.813 773.798 293.842 718.342 254.716 650.56C209.502 572.229 180.914 519.769 176.273 506.594C169.916 488.564 168.803 480.55 167.72 445.02C165.748 380.496 169.63 369.117 228.516 266.875C245.644 237.136 263.188 207.634 267.505 201.313C271.822 194.992 274.348 189.823 273.119 189.823C271.889 189.823 259.978 196.92 246.649 205.593ZM696.536 251.021C696.536 252.168 706.14 269.587 717.881 289.731C773.463 385.1 832.662 489.053 836.374 497.799C855.833 543.671 857.511 602.063 840.886 654.864C834.988 673.592 777.513 777.042 751.607 815.554C746.661 822.906 743.141 829.448 743.784 830.094C745.686 831.998 785.128 806.473 802.005 792.417C833.864 765.884 864.103 723.07 881.039 680.524C897.159 640.029 900.514 621.318 900.428 572.38C900.35 527.201 896.13 503.555 882.357 471.096C876.789 457.97 811.56 344.736 798.201 325.002C786.955 308.391 768.287 288.717 753.453 277.844C733.764 263.414 696.536 245.868 696.536 251.021ZM805.544 578.152C691.573 778.74 682.458 793.298 654.616 819.185C620.871 850.558 584.195 867.899 538.792 873.944C514.735 877.147 353.968 876.088 338.58 872.624L330.475 870.799L340.341 879.805C352.615 891.014 392.236 912.018 415.574 919.688C485.993 942.839 563.693 937.711 627.557 905.7C672.109 883.371 713.494 845.318 740.139 802.185C771.899 750.771 816.283 672.543 821.978 657.941C831.795 632.77 835.685 609.479 835.369 577.788C834.878 528.217 833.908 528.228 805.544 578.152ZM113 573.378C113 625.252 122.161 665.3 144 708.911C172.259 765.338 230.615 819.602 285.9 840.86C325.305 856.011 327.836 856.257 443.941 856.257H552.003L573.959 848.711C597.748 840.533 620.452 827.904 639.649 812.164L651.823 802.185L493.852 800.779L335.878 799.376L316.306 792.16C284.057 780.27 264.09 768.139 241.816 746.902C230.199 735.828 214.244 716.971 206.358 704.997C183.216 669.858 127.429 570.904 122.069 555.483C119.354 547.678 116.204 541.289 115.067 541.289C113.929 541.289 113 555.729 113 573.378Z\" fill=\"currentColor\"/></svg>"""


def _build_claim_bar(business_profile: dict | None) -> str:
    """
    Returns a floating review widget for demo pages.
    It stays out of the template layout so it does not cover nav/header UI.
    """
    if not business_profile:
        return ""

    name = escape(str(business_profile.get("name") or "your business"))
    claim_url = business_profile.get("claim_url") or business_profile.get("demo_claim_url")
    if not claim_url:
        website = business_profile.get("website", "")
        params = urlencode({k: v for k, v in {
            "source": "outbound_claim",
            "site_url": website,
        }.items() if v})
        claim_url = f"{MOYDUS_APP_URL}/onboarding/scan" + (f"?{params}" if params else "")
    claim_url = escape(str(claim_url), quote=True)

    return f"""<div id=\"moydus-claim-widget\" data-expanded=\"true\" style=\"position:fixed;right:18px;bottom:18px;z-index:99999;width:min(340px,calc(100vw - 32px));font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#111827;transition:transform .22s ease,opacity .22s ease;\">
  <button id=\"moydus-claim-widget-toggle\" aria-label=\"Open Moydus preview actions\" style=\"display:none;position:absolute;right:0;bottom:0;width:54px;height:54px;border:0;border-radius:999px;background:#111827;color:#fff;align-items:center;justify-content:center;box-shadow:0 16px 42px rgba(15,23,42,.24);cursor:pointer;\">{MOYDUS_MARK_SVG}</button>
  <div id=\"moydus-claim-widget-card\" style=\"border:1px solid rgba(17,24,39,.10);background:rgba(255,255,255,.96);box-shadow:0 18px 50px rgba(15,23,42,.18);backdrop-filter:blur(14px);border-radius:18px;overflow:hidden;\">
    <div style=\"display:flex;align-items:center;gap:10px;padding:14px 14px 10px;\">
      <div style=\"width:34px;height:34px;border-radius:999px;background:#111827;color:#fff;display:flex;align-items:center;justify-content:center;box-shadow:inset 0 0 0 1px rgba(255,255,255,.12);\">{MOYDUS_MARK_SVG}</div>
      <div style=\"min-width:0;flex:1;\">
        <div style=\"font-size:13px;font-weight:700;line-height:1.2;color:#111827;\">Moydus preview</div>
        <div style=\"font-size:12px;line-height:1.35;color:#6b7280;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;\">Built for {name}</div>
      </div>
      <div style=\"height:8px;width:8px;border-radius:999px;background:#22c55e;box-shadow:0 0 0 4px rgba(34,197,94,.12);\"></div>
    </div>
    <div style=\"padding:0 14px 14px;\">
      <div style=\"font-size:14px;font-weight:650;line-height:1.35;color:#111827;margin-bottom:6px;\">Like this website?</div>
      <div style=\"font-size:12.5px;line-height:1.45;color:#4b5563;margin-bottom:12px;\">Approve it, request changes, or ask Moydus to connect your domain and take it live.</div>
      <div style=\"display:flex;gap:8px;align-items:center;\">
        <a href=\"{claim_url}\" target=\"_blank\" rel=\"noopener noreferrer\" style=\"flex:1;text-align:center;background:#111827;color:#fff;text-decoration:none;font-size:13px;font-weight:700;padding:10px 12px;border-radius:12px;line-height:1;box-shadow:0 8px 18px rgba(17,24,39,.18);\">Review site</a>
        <a href=\"mailto:hello@moydus.com?subject=I%20like%20my%20website%20preview\" style=\"text-align:center;background:#f3f4f6;color:#111827;text-decoration:none;font-size:13px;font-weight:700;padding:10px 12px;border-radius:12px;line-height:1;\">Email</a>
      </div>
    </div>
  </div>
</div>"""


def _build_runtime_fixes() -> str:
    return """<style id=\"moydus-preview-runtime-style\">
#moydus-claim-widget[data-expanded=\"false\"]{width:54px!important;height:54px!important}
#moydus-claim-widget[data-expanded=\"false\"] #moydus-claim-widget-card{display:none!important}
#moydus-claim-widget[data-expanded=\"false\"] #moydus-claim-widget-toggle{display:flex!important}
@media(max-width:640px){#moydus-claim-widget{right:16px!important;bottom:16px!important;width:min(340px,calc(100vw - 32px))!important}}
</style>
<script id=\"moydus-preview-runtime\">
(function(){
  var widget = document.getElementById('moydus-claim-widget');
  if (widget) {
    var toggle = document.getElementById('moydus-claim-widget-toggle');
    var lastY = window.scrollY || 0;
    var userOpened = false;
    var setExpanded = function(open){ widget.setAttribute('data-expanded', open ? 'true' : 'false'); };
    if (toggle) toggle.addEventListener('click', function(){ userOpened = true; setExpanded(true); });
    window.addEventListener('scroll', function(){
      var y = window.scrollY || 0;
      if (y > 140 && y > lastY + 8) { userOpened = false; setExpanded(false); }
      if (y < 80 || y < lastY - 18) { setExpanded(true); }
      if (!userOpened) lastY = y;
    }, { passive: true });
  }

  var setupMobileNav = function(){
    if (window.innerWidth > 809) return;
    var all = Array.prototype.slice.call(document.querySelectorAll('a,button,div'));
    var hamburger = all.find(function(el){
      var r = el.getBoundingClientRect();
      var s = window.getComputedStyle(el);
      if (r.top > 90 || r.width < 28 || r.width > 60 || r.height < 28 || r.height > 60) return false;
      if (s.cursor !== 'pointer' && el.tagName !== 'BUTTON' && el.tagName !== 'A') return false;
      return el.querySelectorAll('div,span,svg,img').length >= 1;
    });
    var candidates = Array.prototype.slice.call(document.querySelectorAll('div'));
    var menu = candidates.find(function(el){
      if (el.id && el.id.indexOf('moydus') === 0) return false;
      var text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
      if (!/Home/.test(text) || !/About/.test(text) || !/Services?/.test(text) || !/Contact/.test(text)) return false;
      var r = el.getBoundingClientRect();
      if (r.top < 35 || r.top > 170 || r.height < 80 || r.height > 520 || r.width < 120 || r.width > 420) return false;
      return true;
    });
    if (!hamburger || !menu || menu.dataset.moydusMobileNavReady) return;
    menu.dataset.moydusMobileNavReady = 'true';
    menu.dataset.moydusMobileNavOpen = 'false';
    var originalDisplay = menu.style.display || window.getComputedStyle(menu).display || 'block';
    var setOpen = function(open){
      menu.dataset.moydusMobileNavOpen = open ? 'true' : 'false';
      menu.style.display = open ? (originalDisplay === 'none' ? 'block' : originalDisplay) : 'none';
      menu.style.pointerEvents = open ? 'auto' : 'none';
      hamburger.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    setOpen(false);
    hamburger.addEventListener('click', function(event){
      event.preventDefault();
      event.stopPropagation();
      setOpen(menu.dataset.moydusMobileNavOpen !== 'true');
    }, true);
    menu.addEventListener('click', function(event){ if (event.target.closest('a')) setOpen(false); });
    document.addEventListener('click', function(event){
      if (menu.dataset.moydusMobileNavOpen === 'true' && !menu.contains(event.target) && !hamburger.contains(event.target)) setOpen(false);
    });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setupMobileNav); else setupMobileNav();
  window.addEventListener('resize', setupMobileNav);
})();
</script>"""

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
        claim_node = claim_soup.find("div", {"id": "moydus-claim-widget"})
        if claim_node:
            body_tag.append(claim_node)
        runtime_soup = BeautifulSoup(_build_runtime_fixes(), "html5lib")
        for runtime_node in runtime_soup.find_all(["style", "script"]):
            body_tag.append(runtime_node)

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
