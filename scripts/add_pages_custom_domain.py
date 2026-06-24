#!/usr/bin/env python3
"""Attach a custom domain to a Cloudflare Pages project + auto-create CNAME.

All credentials are read from .env automatically.

Example:
  .venv/bin/python scripts/add_pages_custom_domain.py \
    --project-name austin-dermatology-studio \
    --domain austin-dermatology.moydus.com \
    --wait
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from integrations.cloudflare import attach_demo_domain, _pages_token, _req, _account_id
from rich.console import Console

console = Console()


def get_domain_status(project_name: str, domain: str) -> str:
    from integrations.cloudflare import list_pages_domains
    for d in list_pages_domains(project_name):
        if d["name"] == domain:
            return d.get("status", "unknown")
    return "not_found"


def main() -> None:
    p = argparse.ArgumentParser(description="Attach custom domain to Cloudflare Pages + CNAME")
    p.add_argument("--project-name", required=True)
    p.add_argument("--domain", required=True, help="e.g. austin-dermatology.moydus.com")
    p.add_argument("--wait", action="store_true", help="Poll until domain becomes active")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.dry_run:
        console.print(f"[dim]DRY RUN: would attach {args.domain} → {args.project_name}[/dim]")
        return

    console.print(f"[bold]Attaching[/bold] {args.domain} → {args.project_name}...")
    result = attach_demo_domain(args.project_name, args.domain)

    if result["already_existed"]:
        console.print("  [yellow]~ Pages domain already registered[/yellow]")
    elif result["pages_ok"]:
        console.print("  [green]✓ Pages domain registered[/green]")
    else:
        console.print("  [red]✗ Pages domain failed[/red]")
        sys.exit(1)

    if result["dns_ok"]:
        console.print("  [green]✓ CNAME created / already exists[/green]")
    else:
        console.print("  [yellow]~ CNAME skipped (check DNS manually)[/yellow]")

    if args.wait:
        console.print("\nWaiting for SSL provisioning...")
        for _ in range(20):
            status = get_domain_status(args.project_name, args.domain)
            console.print(f"  status: {status}")
            if status == "active":
                console.print(f"\n[bold green]✓ https://{args.domain} is LIVE![/bold green]")
                return
            time.sleep(15)
        console.print("[yellow]Timed out — SSL still provisioning, check back in a few minutes.[/yellow]")
    else:
        console.print(f"\nDone. https://{args.domain} will be live in ~2-5 min (SSL provisioning).")


if __name__ == "__main__":
    main()
