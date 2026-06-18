import argparse
import json
import os
import shutil
from pathlib import Path
from rich.console import Console

from scraper.playwright_loader import PageSession
from scraper.asset_downloader import download_assets_sync
from extractor.framer_extractor import extract_framer, has_framer_attrs
from extractor.generic_extractor import extract_generic
from extractor.section_visual import extract_section_visuals
from processor.clean_dom import clean_html
from processor.section_builder import build_sections, deduplicate_sections
from processor.section_cleaner import clean_sections
from ai.dsl_generator import generate_dsl
from ai.nextjs_generator import generate_nextjs_scaffold
from utils.helpers import save_json, url_to_slug, timestamped_filename
from utils.css_extractor import extract_color_tokens, extract_font_families, extract_font_face_urls
from utils.dom_to_react import generate_dom_renderer
from utils.font_rewriter import rewrite_font_faces
from config.settings import (
    RAW_DIR, CLEANED_DIR, SECTIONS_DIR, DSL_DIR,
    SCREENSHOTS_DIR, NEXTJS_DIR, DOM_DIR, ASSETS_DIR,
    OPENAI_MODEL, NEXTJS_MODEL, PLAYWRIGHT_TIMEOUT,
)
from integrations.moy_app import (
    build_demo_payload,
    load_json_file,
    register_demo_site,
    save_payload,
)

console = Console()


def _patch_video_posters(tree: dict, shot_paths: list[str], counter: list) -> dict:
    """Recursively replace video poster attrs with local screenshot paths."""
    if not isinstance(tree, dict):
        return tree
    if tree.get("tag") == "video" and counter[0] < len(shot_paths):
        tree = {**tree, "attrs": {**tree.get("attrs", {}),
                                  "poster": f"/video-captures/{Path(shot_paths[counter[0]]).name}"}}
        counter[0] += 1
    if "children" in tree:
        tree = {**tree, "children": [_patch_video_posters(c, shot_paths, counter)
                                      for c in tree["children"]]}
    return tree


def _save_nextjs_files(files: dict[str, str], output_dir: Path) -> None:
    for filepath, content in files.items():
        full_path = output_dir / filepath
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")



def _build_business_payload(args: argparse.Namespace) -> dict:
    business = load_json_file(args.business_json)
    business.update({
        "name": args.business_name,
        "category": args.business_category,
        "phone": args.business_phone,
        "email": args.business_email,
        "website": args.business_website,
        "google_maps_url": args.google_maps_url,
        "place_id": args.place_id,
        "city": args.business_city,
        "state": args.business_state,
        "country": args.business_country,
    })
    return {k: v for k, v in business.items() if v not in (None, "", [], {})}


def _build_outreach_payload(args: argparse.Namespace) -> dict | None:
    outreach = load_json_file(args.outreach_json)
    outreach.update({
        "channel": args.outreach_channel,
        "recipient": args.outreach_recipient,
        "subject": args.outreach_subject,
        "campaign": args.outreach_campaign,
        "status": args.outreach_status,
    })
    outreach = {k: v for k, v in outreach.items() if v not in (None, "", [], {})}
    return outreach or None


def _build_lead_payload(args: argparse.Namespace) -> dict | None:
    lead = load_json_file(args.lead_json)
    lead.update({
        "email": args.lead_email,
        "phone": args.lead_phone,
        "name": args.lead_name,
        "marketing_consent": args.marketing_consent if args.marketing_consent else None,
    })
    lead = {k: v for k, v in lead.items() if v not in (None, "", [], {})}
    return lead or None


def _handle_backend_registration(args: argparse.Namespace, slug: str, result: dict) -> None:
    if not args.register_backend and not args.outbound_payload_output:
        return

    if not args.preview_url:
        console.print(
            "[yellow]Skipping backend registration: --preview-url is required "
            "after deploy. Use --outbound-payload-output to write a payload now.[/yellow]"
        )
        if not args.outbound_payload_output:
            return

    business = _build_business_payload(args)
    if not business.get("name"):
        business["name"] = args.business_name or slug.replace("-", " ").title()
    if not business.get("website"):
        business["website"] = args.source_business_website or args.url

    demo = {
        "template_key": args.template_key or slug,
        "industry": args.industry or business.get("category"),
        "subdomain": args.subdomain,
        "preview_url": args.preview_url or "https://replace-after-deploy.example",
        "screenshot_url": args.screenshot_url,
        "deploy_provider": args.deploy_provider,
        "deploy_id": args.deploy_id,
        "status": args.demo_status,
        "metadata": {
            "source_url": args.url,
            "clone_mode": args.clone_mode,
            "output_dir": result.get("output_dir") or result.get("nextjs_output"),
        },
    }

    payload = build_demo_payload(
        business=business,
        demo=demo,
        outreach=_build_outreach_payload(args),
        lead=_build_lead_payload(args),
    )

    if args.outbound_payload_output:
        output_path = save_payload(payload, args.outbound_payload_output)
        console.print(f"[green]Outbound payload saved:[/green] {output_path}")

    if args.register_backend and args.preview_url:
        console.print("[bold]Registering demo in moy-app backend...")
        response = register_demo_site(
            payload,
            api_base_url=args.api_base_url,
            api_token=args.api_token,
        )
        console.print(f"[green]Backend registered demoSiteId={response.get('demoSiteId')}[/green]")
        result["moy_app_response"] = response

def run_pipeline(
    url: str,
    skip_ai: bool = False,
    skip_nextjs: bool = False,
    clone_mode: str = "nextjs",
    business_profile: dict | None = None,
) -> dict:
    slug = url_to_slug(url)

    console.rule(f"[bold cyan]Scraping: {url}")

    # 1. Render — single browser session for everything
    console.print("[1/6] Rendering page with Playwright...")
    with PageSession(url, timeout=PLAYWRIGHT_TIMEOUT) as session:
        html = session.html
        css = session.css
        final_url = session.final_url

        save_json({"url": final_url, "html": html[:5000]}, str(RAW_DIR), timestamped_filename(slug))

        # 2. Clean
        console.print("[2/6] Cleaning DOM...")
        clean = clean_html(html)
        save_json({"html": clean[:5000]}, str(CLEANED_DIR), timestamped_filename(slug))

        # 3. Extract
        console.print("[3/6] Extracting sections...")
        if has_framer_attrs(clean):
            console.print("  -> Framer attributes detected. Using framer_extractor.")
            raw_sections = extract_framer(clean)
        else:
            console.print("  -> No Framer attributes. Using generic_extractor.")
            raw_sections = extract_generic(clean)

        # 4. Build + deduplicate
        console.print("[4/6] Building section objects...")
        sections = build_sections(raw_sections)
        sections = deduplicate_sections(sections)

        # 5. Rules engine
        console.print("[5/6] Applying rules engine (image filter + type hints)...")
        sections = clean_sections(sections)

        type_hints = [s.get("type_hint", "unknown") for s in sections]
        console.print(f"  -> {len(sections)} sections: {type_hints}")

        # Visual extraction (screenshots + computed CSS per section)
        section_names = [s["name"] for s in sections if s["name"] != "unknown"]
        screenshots_dir = SCREENSHOTS_DIR / slug
        console.print(f"  -> Capturing {len(section_names)} section screenshots...")

        # Hover interactive elements so menus/dropdowns are visible before capture
        session.hover_interactive_elements()
        visuals = extract_section_visuals(session, section_names, screenshots_dir)

        captured = sum(1 for v in visuals.values() if v.get("screenshot_path"))
        console.print(f"  -> {captured}/{len(section_names)} screenshots captured.")

        # Attach computed CSS to sections for DSL generator
        for s in sections:
            v = visuals.get(s["name"], {})
            s["computed_css"] = v.get("css", {})

        # DOM walker — separate trees per breakpoint (Framer uses JS subtree swap)
        DOM_DIR.mkdir(parents=True, exist_ok=True)
        console.print("[bold]  -> Walking DOM at desktop + mobile breakpoints...")
        dom_result = session.walk_dom_responsive()
        dom_tree = dom_result  # full result with desktop/mobile keys
        dom_path = DOM_DIR / timestamped_filename(slug)
        dom_path.write_text(
            __import__("json").dumps(dom_result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        meta = dom_result.get("_meta", {})
        console.print(
            f"  -> DOM: desktop={meta.get('desktop_nodes',0)}n "
            f"mobile={meta.get('mobile_nodes',0)}n → {dom_path.name}"
        )
        responsive = {"desktop": dom_result.get("desktop", {}), "mobile": dom_result.get("mobile", {})}

        # Iframe capture
        iframes = session.capture_iframes()
        if iframes:
            iframe_path = DOM_DIR / timestamped_filename(f"{slug}_iframes")
            iframe_path.write_text(
                __import__("json").dumps(iframes, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            console.print(f"  -> {len(iframes)} iframes captured.")

        # @font-face URLs (may differ from network-captured fonts — e.g. cached/CDN)
        _font_face_urls = session.extract_font_urls()
        # Full @font-face CSS block text — needed to rewrite src URLs to local /fonts/
        _font_face_css = session.extract_font_face_css()
        # Background-image URLs from computed styles (catches lazy-loaded backgrounds)
        _bg_image_urls = session.extract_background_image_urls()
        # SVG sprite sheet — hidden SVGs containing <symbol>/<defs> for icon <use href="#id">
        _svg_sprite = session.extract_svg_sprite()
        if _svg_sprite:
            console.print(f"  -> SVG sprite sheet captured ({len(_svg_sprite)} chars).")
        # Video screenshots — capture current frame for each video element
        _video_shot_dir = ASSETS_DIR / slug / "video_screenshots"
        _video_shots = session.capture_video_screenshots(_video_shot_dir)
        if _video_shots:
            console.print(f"  -> {len(_video_shots)} video frames captured.")

        # Pre-scroll vs post-scroll: report lazy-loaded sections count
        _pre_scroll_len = len(session.html_pre_scroll)
        _post_scroll_len = len(session.html)
        if _post_scroll_len > _pre_scroll_len * 1.05:
            console.print(
                f"  -> Lazy content detected: "
                f"{_post_scroll_len - _pre_scroll_len:+,} chars after scroll."
            )

        # Expose asset URLs captured during navigation (for download step below)
        _asset_urls = session.asset_urls

    save_json(sections, str(SECTIONS_DIR), timestamped_filename(slug))

    # Asset download — merge all sources:
    #   network capture + @font-face CSS rules + computed background-images
    _seen_urls: set[str] = set()
    _download_targets: list[str] = []
    for url in (
        _asset_urls.get("font", [])
        + _font_face_urls
        + _asset_urls.get("css", [])
        + _asset_urls.get("image", [])
        + _bg_image_urls
    ):
        if url not in _seen_urls:
            _seen_urls.add(url)
            _download_targets.append(url)
    if _download_targets:
        _font_total = len(set(_asset_urls.get("font", []) + _font_face_urls))
        _img_total = len(set(_asset_urls.get("image", []) + _bg_image_urls))
        console.print(
            f"[bold]  -> Downloading assets "
            f"({_font_total} fonts, "
            f"{len(_asset_urls.get('css', []))} CSS, "
            f"{_img_total} images)..."
        )
        asset_map = download_assets_sync(_download_targets, ASSETS_DIR / slug)
        console.print(f"  -> {len(asset_map)}/{len(_download_targets)} assets saved.")
    else:
        asset_map = {}

    if clone_mode == "astro-raw":
        console.print("[bold cyan]Running Astro Raw Clone Mode...[/bold cyan]")
        from bs4 import BeautifulSoup
        raw_soup = BeautifulSoup(html, "lxml")
        # Strip only scripts for raw clone to preserve SVGs and SSR styles
        for tag in raw_soup.find_all("script"):
            tag.decompose()
            
        from generators.astro_raw_generator import generate_raw_astro
        result = generate_raw_astro(slug, str(raw_soup), css, asset_map, business_profile=business_profile)
        return result

    # CSS tokens + font-face URLs from CSS text (regex fallback for browser-blocked CDNs)
    color_tokens = extract_color_tokens(css)
    fonts = extract_font_families(css)
    font_face_from_css = extract_font_face_urls(css, base_url=final_url)
    if color_tokens:
        console.print(f"  -> {len(color_tokens)} color tokens extracted.")

    # 6. DSL
    if skip_ai:
        console.print("[6/6] Skipping AI step (--skip-ai flag).")
        result = {
            "sections": sections,
            "color_tokens": color_tokens,
            "fonts": fonts,
            "asset_summary": {k: len(v) for k, v in _asset_urls.items()},
            "dom_nodes": dom_tree.get("totalNodes", 0),
            "responsive": {bp: t.get("totalNodes", 0) for bp, t in responsive.items()},
            "iframes": len(iframes),
        }
        return result

    console.print(f"[6/6] Generating DSL with {OPENAI_MODEL}...")
    dsl = generate_dsl(sections, model=OPENAI_MODEL)
    dsl["meta"] = {"color_tokens": color_tokens, "fonts": fonts, "source_url": final_url}
    save_json(dsl, str(DSL_DIR), timestamped_filename(slug))

    console.print("\n[bold green]DSL saved to data/dsl/")

    # 7. Next.js generation
    if not skip_nextjs:
        console.print("[7/7] Building Next.js project (DOMPage)...")
        nextjs_output_dir = NEXTJS_DIR / slug

        # Scaffold: package.json, tsconfig, postcss, next.config, globals.css
        scaffold_files = generate_nextjs_scaffold(dsl.get("meta", {}))
        _save_nextjs_files(scaffold_files, nextjs_output_dir)
        console.print(f"  -> {len(scaffold_files)} scaffold files written.")
        dsl["nextjs_output"] = str(nextjs_output_dir)

        # ── DOM renderer (pixel-perfect, dual-tree breakpoint model) ──────────
        # Patch video nodes in both desktop + mobile trees
        _patched = {
            "desktop": _patch_video_posters(dom_result.get("desktop", {}), _video_shots, [0]),
            "mobile":  _patch_video_posters(dom_result.get("mobile", {}),  _video_shots, [0]),
        }
        tsx_content, desktop_json, mobile_json = generate_dom_renderer(_patched, svg_sprite=_svg_sprite)

        comp_dir = nextjs_output_dir / "components"
        comp_dir.mkdir(parents=True, exist_ok=True)
        (comp_dir / "DOMPage.tsx").write_text(tsx_content, encoding="utf-8")
        (comp_dir / "dom_desktop.json").write_text(desktop_json, encoding="utf-8")
        (comp_dir / "dom_mobile.json").write_text(mobile_json, encoding="utf-8")

        # DOMPage is the PRIMARY route — overwrite app/page.tsx
        (nextjs_output_dir / "app" / "page.tsx").write_text(
            '"use client";\n'
            'import DOMPage from "@/components/DOMPage";\n\n'
            "export default function Page() {\n  return <DOMPage />;\n}\n",
            encoding="utf-8",
        )

        # Copy video screenshots to public/video-captures/
        if _video_shots:
            vid_public = nextjs_output_dir / "public" / "video-captures"
            vid_public.mkdir(parents=True, exist_ok=True)
            for p in _video_shots:
                shutil.copy2(p, vid_public / Path(p).name)
            console.print(f"  -> {len(_video_shots)} video frames → public/video-captures/")

        # ── Font rewriting ────────────────────────────────────────────────────
        # Rewrite @font-face src URLs to /fonts/<file> and copy font files
        public_fonts_dir = nextjs_output_dir / "public" / "fonts"
        rewritten_font_css = rewrite_font_faces(_font_face_css, asset_map, public_fonts_dir)
        if rewritten_font_css.strip():
            globals_css_path = nextjs_output_dir / "styles" / "globals.css"
            existing_globals = globals_css_path.read_text(encoding="utf-8") if globals_css_path.exists() else ""
            globals_css_path.write_text(rewritten_font_css + "\n" + existing_globals, encoding="utf-8")
            font_count = len(list(public_fonts_dir.glob("*"))) if public_fonts_dir.exists() else 0
            console.print(f"  -> {font_count} font files → public/fonts/")

        console.print(f"  -> DOM renderer written to data/nextjs/{slug}/components/DOMPage.tsx")

    _dom_meta = dom_result.get("_meta", {})
    dsl["meta"]["asset_summary"] = {k: len(v) for k, v in _asset_urls.items()}
    dsl["meta"]["dom_nodes"] = _dom_meta.get("desktop_nodes", 0)
    dsl["meta"]["responsive"] = {k: _dom_meta.get(f"{k}_nodes", 0) for k in ("desktop", "mobile")}
    dsl["meta"]["iframes"] = len(iframes)
    return dsl


def main():
    parser = argparse.ArgumentParser(description="Framer -> DSL -> Next.js pipeline")
    parser.add_argument("url", help="Target URL to scrape")
    parser.add_argument("--skip-ai", action="store_true", help="Skip OpenAI DSL generation")
    parser.add_argument("--skip-nextjs", action="store_true", help="Skip Next.js component generation")
    parser.add_argument("--clone-mode", choices=["nextjs", "astro-raw"], default="nextjs", help="Output mode")

    # moy-app outbound registration. Registration is optional and only runs when requested.
    parser.add_argument("--register-backend", action="store_true", help="POST generated demo metadata to moy-app")
    parser.add_argument("--outbound-payload-output", default=None, help="Write moy-app outbound payload JSON to this path")
    parser.add_argument("--api-base-url", default=None, help="moy-app API base URL, e.g. https://app.moydus.com/api")
    parser.add_argument("--api-token", default=None, help="Optional moy-app bearer token")
    parser.add_argument("--preview-url", default=None, help="Public deployed preview URL, e.g. https://acme.moydus.site")
    parser.add_argument("--screenshot-url", default=None, help="Public screenshot URL for email/CRM")
    parser.add_argument("--template-key", default=None, help="Template/reference key used for the demo")
    parser.add_argument("--industry", default=None, help="Industry label for the demo")
    parser.add_argument("--subdomain", default=None, help="Preview subdomain slug")
    parser.add_argument("--deploy-provider", default=None, help="Deploy provider name")
    parser.add_argument("--deploy-id", default=None, help="Deploy provider id")
    parser.add_argument("--demo-status", default="generated", help="Initial demo status")

    parser.add_argument("--business-json", default=None, help="Business JSON file from Google Maps/enrichment")
    parser.add_argument("--business-name", default=None)
    parser.add_argument("--business-category", default=None)
    parser.add_argument("--business-phone", default=None)
    parser.add_argument("--business-email", default=None)
    parser.add_argument("--business-website", default=None)
    parser.add_argument("--source-business-website", default=None, help="Original business website when target URL is a template")
    parser.add_argument("--google-maps-url", default=None)
    parser.add_argument("--place-id", default=None)
    parser.add_argument("--business-city", default=None)
    parser.add_argument("--business-state", default=None)
    parser.add_argument("--business-country", default=None)

    parser.add_argument("--outreach-json", default=None)
    parser.add_argument("--outreach-channel", choices=["email", "sms", "whatsapp", "manual"], default=None)
    parser.add_argument("--outreach-recipient", default=None)
    parser.add_argument("--outreach-subject", default=None)
    parser.add_argument("--outreach-campaign", default=None)
    parser.add_argument("--outreach-status", default="draft")

    parser.add_argument("--lead-json", default=None)
    parser.add_argument("--lead-name", default=None)
    parser.add_argument("--lead-email", default=None)
    parser.add_argument("--lead-phone", default=None)
    parser.add_argument("--marketing-consent", action="store_true")

    args = parser.parse_args()

    business_profile = _build_business_payload(args)
    result = run_pipeline(
        args.url,
        skip_ai=args.skip_ai,
        skip_nextjs=args.skip_nextjs,
        clone_mode=args.clone_mode,
        business_profile=business_profile,
    )
    slug = url_to_slug(args.url)
    _handle_backend_registration(args, slug, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
