#!/usr/bin/env python3
"""
Moydus Outbound Campaign Runner
================================
Full pipeline: Google Maps → scrape → Astro demo → deploy → moy-app register → email outreach

Usage:
    .venv/bin/python scripts/run_campaign.py \
        --category "locksmith" \
        --city "Austin" \
        --state "TX" \
        --template-url "https://some-locksmith-template.framer.website" \
        --template-key "locksmith" \
        --deploy-repo-dir "$HOME/Sites/moydus-demo-sites" \
        --deploy-remote "git@github.com:YOUR_ORG/moydus-demo-sites.git" \
        --push \
        --api-base-url "https://app.moydus.com/api" \
        --api-token "your-token" \
        --limit 20 \
        --outreach-channel email \
        --campaign "locksmith-austin-june-2026"

Dry-run (no deploy, no moy-app registration):
    ... --dry-run --limit 5
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from rich.console import Console
from rich.table import Table

from integrations.google_maps import BusinessResult, scrape_category
from integrations.git_deploy import deploy_to_git_branch
from integrations.moy_app import build_demo_payload, register_demo_site, save_payload
from main import run_pipeline
from utils.helpers import url_to_slug

console = Console()

DATA_DIR = ROOT_DIR / "data" / "campaigns"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def slugify_business(name: str) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60]


def inject_claim_url(output_dir: str | Path, claim_url: str) -> bool:
    import re
    index_path = Path(output_dir) / "src" / "pages" / "index.astro"
    if not claim_url or not index_path.exists():
        return False
    html = index_path.read_text(encoding="utf-8")
    updated = re.sub(
        r'(<a href=")([^"]+)("[^>]*>Review site</a>)',
        lambda match: f"{match.group(1)}{claim_url}{match.group(3)}"
        if "moydus-claim-widget" in html else match.group(0),
        html,
        count=1,
    )
    if updated == html:
        return False
    index_path.write_text(updated, encoding="utf-8")
    return True


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Moydus outbound campaign runner")

    # Lead sourcing
    p.add_argument("--category", required=True, help="Business category, e.g. locksmith")
    p.add_argument("--city", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--country", default="US")
    p.add_argument("--limit", type=int, default=20, help="Max businesses to process")
    p.add_argument("--maps-mode", choices=["gosom", "serpapi"], default="gosom")
    p.add_argument("--gosom-binary", default=None)

    # Template / demo generation
    p.add_argument("--template-url", required=True, help="Framer/reference URL to clone for all demos")
    p.add_argument("--template-key", default=None)
    p.add_argument("--clone-mode", choices=["astro-raw", "nextjs"], default="astro-raw")
    p.add_argument("--preview-base-domain", default="moydus.site")

    # Git deploy
    p.add_argument("--deploy-repo-dir", default=None, help="Local git repo for Cloudflare Pages branches")
    p.add_argument("--deploy-remote", default=None)
    p.add_argument("--push", action="store_true")

    # moy-app
    p.add_argument("--api-base-url", default=None)
    p.add_argument("--api-token", default=None)

    # Outreach
    p.add_argument("--outreach-channel", choices=["email", "sms", "whatsapp", "manual"], default=None)
    p.add_argument("--campaign", default=None, help="Campaign key for tracking")

    # Control
    p.add_argument("--dry-run", action="store_true", help="Scrape leads only, no demo generation")
    p.add_argument("--skip-if-no-email", action="store_true", help="Skip businesses without email")
    p.add_argument("--delay", type=float, default=5.0, help="Seconds between each business (rate limiting)")
    p.add_argument("--output-dir", default=None, help="Override output dir for payloads")
    p.add_argument("--allow-quality-fail", action="store_true", help="Deploy even if static preview quality checks fail")

    return p


def process_business(
    business: BusinessResult,
    args: argparse.Namespace,
    subdomain: str,
) -> dict:
    """Run the full pipeline for a single business. Returns result dict."""

    console.rule(f"[bold cyan]{business.name}[/bold cyan]")
    console.print(f"  Website : {business.website}")
    console.print(f"  Phone   : {business.phone}")
    console.print(f"  City    : {business.city}, {business.state}")

    business_data = business.to_dict()
    result = run_pipeline(
        url=args.template_url,
        skip_ai=True,
        skip_nextjs=args.clone_mode == "astro-raw",
        clone_mode=args.clone_mode,
        business_profile=business_data,
    )

    quality_report = result.get("quality_report")
    if quality_report and not quality_report.get("passed") and not args.allow_quality_fail:
        failures = ", ".join(
            check.get("name", "unknown") for check in quality_report.get("failures", [])
        )
        raise RuntimeError(
            f"Preview quality checks failed before deploy: {failures}. "
            "Fix the clone or pass --allow-quality-fail to override."
        )

    deploy_result: dict = {}
    output_dir = result.get("output_dir") or result.get("nextjs_output") or result.get("astro_output")
    preview_url = f"https://{subdomain}.{args.preview_base_domain}"

    if args.deploy_repo_dir and not args.dry_run:
        if output_dir and Path(output_dir).exists():
            deploy_result = deploy_to_git_branch(
                source_dir=output_dir,
                repo_dir=args.deploy_repo_dir,
                branch=f"demo/{subdomain}",
                remote_url=args.deploy_remote,
                commit_message=f"Generate preview for {business.name}",
                push=args.push,
            )
            console.print(f"[green]Deployed branch:[/green] demo/{subdomain}")
        else:
            console.print("[yellow]No output dir found for deploy.[/yellow]")

    # Build moy-app payload
    demo_payload = {
        "template_key": args.template_key or url_to_slug(args.template_url),
        "industry": args.category,
        "subdomain": subdomain,
        "preview_url": preview_url,
        "screenshot_url": None,
        "deploy_provider": "cloudflare_pages" if args.deploy_repo_dir else None,
        "deploy_id": deploy_result.get("commit_hash") or deploy_result.get("deploy_id"),
        "status": "generated",
        "metadata": {
            "source_url": args.template_url,
            "clone_mode": args.clone_mode,
            "output_dir": result.get("output_dir") or result.get("astro_output"),
            "git": deploy_result or None,
            "quality_report_path": result.get("quality_report_path"),
            "quality_passed": quality_report.get("passed") if quality_report else None,
        },
    }
    outreach_payload = None
    if args.outreach_channel:
        recipient = business.email or business.phone or None
        if recipient:
            outreach_payload = {
                "channel": args.outreach_channel,
                "recipient": recipient,
                "campaign": args.campaign or f"{args.category}-{args.city.lower()}-outbound",
                "status": "draft",
            }

    payload = build_demo_payload(
        business=business_data,
        demo=demo_payload,
        outreach=outreach_payload,
    )

    # Save payload locally always
    output_dir = Path(args.output_dir or str(DATA_DIR))
    output_dir.mkdir(parents=True, exist_ok=True)
    payload_path = save_payload(payload, output_dir / f"{subdomain}.json")
    console.print(f"[dim]Payload saved: {payload_path}[/dim]")

    # Register in moy-app
    backend_response = None
    if args.api_base_url and not args.dry_run:
        try:
            backend_response = register_demo_site(
                payload,
                api_base_url=args.api_base_url,
                api_token=args.api_token,
            )
            demo_site_id = backend_response.get("demoSiteId")
            claim_url = backend_response.get("claimUrl") or backend_response.get("claim_url")
            console.print(f"[green]moy-app registered: demoSiteId={demo_site_id}[/green]")
            if claim_url:
                console.print(f"[green]Claim URL: {claim_url}[/green]")
                if args.deploy_repo_dir and output_dir and inject_claim_url(output_dir, claim_url):
                    deploy_result = deploy_to_git_branch(
                        source_dir=output_dir,
                        repo_dir=args.deploy_repo_dir,
                        branch=f"demo/{subdomain}",
                        remote_url=args.deploy_remote,
                        commit_message=f"Attach claim URL for {business.name}",
                        push=args.push,
                    )
                    console.print("[green]Updated preview with public claim URL.[/green]")
            else:
                console.print("[yellow]moy-app registered, but no public claimUrl was returned yet.[/yellow]")
            result["moy_app"] = backend_response
        except Exception as e:
            console.print(f"[red]moy-app registration failed: {e}[/red]")

    result["preview_url"] = preview_url
    result["subdomain"] = subdomain
    result["business"] = business_data
    result["deploy"] = deploy_result
    result["payload_path"] = str(payload_path)
    result["backend_response"] = backend_response
    return result


def main() -> None:
    args = build_parser().parse_args()

    campaign_key = args.campaign or f"{args.category}-{args.city.lower()}-{args.state.lower()}"
    console.rule(f"[bold]Moydus Campaign: {campaign_key}[/bold]")

    # 1. Scrape Google Maps
    console.print(f"\n[bold]Step 1:[/bold] Scraping Google Maps ({args.maps_mode})...")
    gosom_kwargs = {}
    if args.gosom_binary:
        gosom_kwargs["binary"] = args.gosom_binary

    businesses = scrape_category(
        category=args.category,
        city=args.city,
        state=args.state,
        country=args.country,
        limit=args.limit,
        mode=args.maps_mode,
        **gosom_kwargs,
    )

    if not businesses:
        console.print("[red]No businesses with websites found. Exiting.[/red]")
        sys.exit(1)

    # Print summary table
    table = Table(title=f"Found {len(businesses)} businesses with websites")
    table.add_column("Business", style="cyan")
    table.add_column("Phone")
    table.add_column("Website", style="dim")
    table.add_column("Email")
    for b in businesses[:10]:
        table.add_row(b.name, b.phone, b.website[:40] if b.website else "", b.email)
    if len(businesses) > 10:
        table.add_row(f"... and {len(businesses) - 10} more", "", "", "")
    console.print(table)

    if args.dry_run:
        console.print("\n[yellow]Dry run — stopping before demo generation.[/yellow]")
        out = DATA_DIR / f"{campaign_key}-leads.json"
        out.write_text(
            json.dumps([b.to_dict() for b in businesses], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        console.print(f"[green]Leads saved to {out}[/green]")
        return

    # 2. Generate demos
    console.print(f"\n[bold]Step 2:[/bold] Generating demos for {len(businesses)} businesses...")
    results = []

    for i, business in enumerate(businesses):
        if args.skip_if_no_email and not business.email:
            console.print(f"[yellow]Skipping {business.name} — no email[/yellow]")
            continue

        subdomain = slugify_business(business.name)
        try:
            result = process_business(business, args, subdomain)
            results.append({
                "business": business.name,
                "status": "ok",
                "preview_url": result.get("preview_url"),
                "subdomain": result.get("subdomain"),
                "payload_path": result.get("payload_path"),
                "quality_passed": result.get("quality_report", {}).get("passed") if result.get("quality_report") else None,
                "demoSiteId": (result.get("backend_response") or {}).get("demoSiteId"),
            })
        except Exception as e:
            console.print(f"[red]Error processing {business.name}: {e}[/red]")
            results.append({"business": business.name, "status": "error", "error": str(e)})

        if i < len(businesses) - 1:
            console.print(f"[dim]Waiting {args.delay}s before next...[/dim]")
            time.sleep(args.delay)

    # Final summary
    console.rule("[bold]Campaign Summary[/bold]")
    ok = sum(1 for r in results if r["status"] == "ok")
    console.print(f"[green]✓ {ok} demos generated[/green]")
    console.print(f"[red]✗ {len(results) - ok} errors[/red]")

    summary_path = DATA_DIR / f"{campaign_key}-results.json"
    summary_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    console.print(f"\nResults saved: {summary_path}")


if __name__ == "__main__":
    main()
