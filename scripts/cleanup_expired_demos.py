#!/usr/bin/env python3
"""
Moydus Outbound Demo Cleanup
=============================
Fetches expired DemoSites from moy-app backend, removes their
Cloudflare Pages custom domains + DNS records, then marks them deleted.

Run after `php artisan demos:expire` has updated statuses:
  .venv/bin/python scripts/cleanup_expired_demos.py
  .venv/bin/python scripts/cleanup_expired_demos.py --dry-run
  .venv/bin/python scripts/cleanup_expired_demos.py --delete-project  # also nukes Pages project
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Load .env
_ENV = ROOT_DIR / ".env"
if _ENV.exists():
    for _l in _ENV.read_text().splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            k, _, v = _l.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import httpx
from rich.console import Console
from rich.table import Table

from integrations.cloudflare import detach_demo_domain

console = Console()

# ---------------------------------------------------------------------------
# moy-app helpers
# ---------------------------------------------------------------------------

def _api_base() -> str:
    return (os.environ.get("MOY_APP_API_BASE_URL") or "https://moydu-app.test/api").rstrip("/")


def _headers() -> dict:
    token = os.environ.get("MOY_APP_API_TOKEN")
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch_expired_demos(limit: int = 100) -> list[dict]:
    """GET /api/outbound/demo-sites?status=expired"""
    resp = httpx.get(
        f"{_api_base()}/outbound/demo-sites",
        params={"status": "expired", "per_page": limit},
        headers=_headers(),
        timeout=15,
        verify=False,  # Herd .test TLD uses self-signed cert locally
    )
    resp.raise_for_status()
    data = resp.json()
    # Paginated response
    return data.get("data") or []


def mark_demo_deleted(demo_id: int) -> bool:
    """DELETE /api/outbound/demo-sites/{id}"""
    resp = httpx.delete(
        f"{_api_base()}/outbound/demo-sites/{demo_id}",
        headers=_headers(),
        timeout=15,
        verify=False,
    )
    return resp.status_code in (200, 204)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_demo(demo: dict, dry: bool, delete_project: bool) -> dict:
    demo_id   = demo["id"]
    subdomain = demo.get("subdomain") or ""
    preview   = demo.get("preview_url") or ""

    # Derive domain from preview_url (e.g. https://austin-dermatology.moydus.com)
    domain = preview.replace("https://", "").replace("http://", "").rstrip("/") if preview else ""

    # Pages project name == subdomain slug stored in the DB
    project_name = subdomain  # e.g. "austin-dermatology-studio"

    console.print(f"\n[cyan]{demo.get('business_name')}[/cyan]")
    console.print(f"  project: {project_name} | domain: {domain}")

    if dry:
        console.print("  [dim][dry-run] would detach domain + delete DNS + mark deleted[/dim]")
        return {"id": demo_id, "status": "dry-run"}

    result = {"id": demo_id, "status": "ok", "cf": {}}

    # Cloudflare cleanup
    if project_name and domain:
        try:
            cf = detach_demo_domain(project_name, domain, delete_project=delete_project)
            result["cf"] = cf
            console.print(
                f"  CF: pages_domain={'✓' if cf['pages_domain_deleted'] else '–'} "
                f"dns={'✓' if cf['dns_deleted'] else '–'} "
                f"project={'✓' if cf.get('project_deleted') else '–'}"
            )
        except Exception as e:
            console.print(f"  [yellow]CF cleanup warning: {e}[/yellow]")
            result["cf_error"] = str(e)
    else:
        console.print("  [dim]No project/domain info — skipping CF cleanup[/dim]")

    # Mark deleted in backend
    deleted = mark_demo_deleted(demo_id)
    result["deleted"] = deleted
    console.print(f"  backend: {'✓ marked deleted' if deleted else '✗ delete failed'}")

    return result


def main() -> None:
    p = argparse.ArgumentParser(description="Clean up expired Moydus outbound demo sites")
    p.add_argument("--dry-run", action="store_true", help="Show what would be cleaned without doing it")
    p.add_argument("--delete-project", action="store_true", help="Also delete the entire Cloudflare Pages project")
    p.add_argument("--limit", type=int, default=100, help="Max expired demos to process")
    p.add_argument("--delay", type=float, default=1.0, help="Seconds between each demo (rate limiting)")
    args = p.parse_args()

    console.rule("[bold]Moydus Demo Cleanup[/bold]")
    if args.dry_run:
        console.print("[yellow]DRY RUN — no changes will be made[/yellow]\n")

    # 1. Fetch expired demos
    try:
        demos = fetch_expired_demos(args.limit)
    except Exception as e:
        console.print(f"[red]Failed to fetch expired demos: {e}[/red]")
        sys.exit(1)

    if not demos:
        console.print("[green]No expired demos to clean up.[/green]")
        return

    console.print(f"Found [bold]{len(demos)}[/bold] expired demo(s) to clean up.")

    # 2. Process each
    results = []
    for i, demo in enumerate(demos):
        result = process_demo(demo, dry=args.dry_run, delete_project=args.delete_project)
        results.append(result)
        if i < len(demos) - 1:
            time.sleep(args.delay)

    # 3. Summary table
    console.rule("[bold]Summary[/bold]")
    table = Table()
    table.add_column("ID")
    table.add_column("Status")
    table.add_column("CF")
    table.add_column("Backend")
    for r in results:
        cf = r.get("cf", {})
        cf_str = f"dom={'✓' if cf.get('pages_domain_deleted') else '–'} dns={'✓' if cf.get('dns_deleted') else '–'}"
        table.add_row(
            str(r["id"]),
            r["status"],
            cf_str if r["status"] != "dry-run" else "dry-run",
            "✓" if r.get("deleted") else ("dry-run" if args.dry_run else "✗"),
        )
    console.print(table)

    ok = sum(1 for r in results if r["status"] in ("ok", "dry-run"))
    console.print(f"\n[green]{ok}/{len(results)} demos processed.[/green]")


if __name__ == "__main__":
    main()
