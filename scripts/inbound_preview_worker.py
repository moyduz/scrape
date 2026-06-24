#!/usr/bin/env python3
"""
Inbound Preview Worker
======================
Polls /api/inbound/previews/pending for WebsiteProjects that need
an auto-generated Astro preview after the customer pays.

Flow:
  1. Fetch pending projects from moydu-app API
  2. Look up the template's demoUrl from Sanity (via template_key / slug)
  3. Run the scrape pipeline on that demoUrl → Astro output
  4. Create a private GitHub repo under GITHUB_ORG
  5. Push generated code via git_deploy
  6. Create CF Pages project → attach moydus.com subdomain
  7. PATCH preview_status=ready + preview_url back to API

Env vars required:
  MOYDUS_PREVIEW_WORKER_TOKEN   — Bearer token for /api/inbound/previews/*
  LARAVEL_API_URL               — e.g. https://api.moydus.com
  GITHUB_TOKEN                  — Fine-grained PAT: Contents+Admin RW
  GITHUB_ORG                    — e.g. moydus-clients (private repos go here)
  CLOUDFLARE_ACCOUNT_ID
  CLOUDFLARE_PAGES_TOKEN
  CLOUDFLARE_DNS_TOKEN
  CLOUDFLARE_MOYDUS_ZONE_ID
  SANITY_PROJECT_ID             — default: ttxgv4pp
  SANITY_DATASET                — default: production
"""

import argparse
import json
import os
import sys
import time
import tempfile
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env")

from integrations.cloudflare import attach_demo_domain
from integrations.git_deploy import deploy_to_git_branch
from main import run_pipeline
from utils.helpers import url_to_slug

console = Console()

# ── Config ────────────────────────────────────────────────────────────────────

LARAVEL_API_URL      = os.environ.get("LARAVEL_API_URL", "http://localhost:8000").rstrip("/")
WORKER_TOKEN         = os.environ.get("MOYDUS_PREVIEW_WORKER_TOKEN", "")
GITHUB_TOKEN         = os.environ.get("GITHUB_TOKEN", "")
GITHUB_ORG           = os.environ.get("GITHUB_ORG", "moydus-clients")
SANITY_PROJECT_ID    = os.environ.get("SANITY_PROJECT_ID", "ttxgv4pp")
SANITY_DATASET       = os.environ.get("SANITY_DATASET", "production")
MOYDUS_APP_URL       = os.environ.get("MOYDUS_APP_URL", "https://app.moydus.com").rstrip("/")
PREVIEW_BASE_DOMAIN  = os.environ.get("PREVIEW_BASE_DOMAIN", "moydus.com")
DEPLOY_REPO_BASE     = Path(os.environ.get("DEPLOY_REPO_BASE", str(ROOT_DIR / "deploy_repos")))

_AUTH = {"Authorization": f"Bearer {WORKER_TOKEN}", "Accept": "application/json"}


# ── Sanity template catalog ────────────────────────────────────────────────────

_sanity_cache: dict[str, dict] | None = None


def fetch_sanity_templates() -> dict[str, dict]:
    """Returns {slug: {title, demoUrl, categoryTitle, ...}}"""
    global _sanity_cache
    if _sanity_cache is not None:
        return _sanity_cache

    groq = '*[_type == "template" && (!defined(published) || published == true)] { _id, title, "slug": slug.current, demoUrl, "categoryTitle": primaryCategory->title, "categoryGroup": primaryCategory->group }'
    url  = f"https://{SANITY_PROJECT_ID}.api.sanity.io/v2026-02-13/data/query/{SANITY_DATASET}"
    try:
        res = requests.get(url, params={"query": groq}, timeout=10)
        res.raise_for_status()
        items = res.json().get("result", [])
        _sanity_cache = {t["slug"]: t for t in items if t.get("slug")}
        console.log(f"[dim]Loaded {len(_sanity_cache)} Sanity templates[/dim]")
    except Exception as exc:
        console.log(f"[yellow]Sanity fetch failed: {exc}[/yellow]")
        _sanity_cache = {}
    return _sanity_cache


def demo_url_for_template(template_key: str) -> str | None:
    """
    Resolve a Framer / live-demo URL from a Sanity template slug.
    Falls back to None if template has no demoUrl.
    """
    catalog = fetch_sanity_templates()
    template = catalog.get(template_key)
    if not template:
        # Try prefix match (e.g. "health" → "astro-health")
        for slug, t in catalog.items():
            if template_key in slug or slug in template_key:
                template = t
                break
    if template:
        return template.get("demoUrl") or None
    return None


# ── moydu-app API helpers ──────────────────────────────────────────────────────

def fetch_pending() -> list[dict]:
    res = requests.get(f"{LARAVEL_API_URL}/api/inbound/previews/pending", headers=_AUTH, timeout=15)
    res.raise_for_status()
    data = res.json()
    return data if isinstance(data, list) else data.get("data", [])


def patch_project(project_id: int, payload: dict) -> dict:
    res = requests.patch(
        f"{LARAVEL_API_URL}/api/inbound/previews/{project_id}",
        headers={**_AUTH, "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    res.raise_for_status()
    return res.json()


# ── GitHub repo creation ───────────────────────────────────────────────────────

def create_github_repo(repo_name: str, description: str = "") -> dict:
    """Create a private repo under GITHUB_ORG. Returns repo info dict."""
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN not set — cannot create GitHub repo")

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # Try org first, fall back to user repo
    payload = {
        "name": repo_name,
        "description": description,
        "private": True,
        "auto_init": False,
    }
    url = f"https://api.github.com/orgs/{GITHUB_ORG}/repos"
    res = requests.post(url, headers=headers, json=payload, timeout=15)

    if res.status_code == 404:
        # Org doesn't exist or no access — create under authenticated user
        console.log(f"[yellow]Org '{GITHUB_ORG}' not found, creating repo under user account[/yellow]")
        res = requests.post("https://api.github.com/user/repos", headers=headers, json=payload, timeout=15)

    if res.status_code == 422:
        # Repo already exists — fetch it
        owner = GITHUB_ORG
        r2 = requests.get(f"https://api.github.com/repos/{owner}/{repo_name}", headers=headers, timeout=10)
        if r2.ok:
            console.log(f"[dim]Repo already exists: {r2.json()['html_url']}[/dim]")
            return r2.json()

    res.raise_for_status()
    return res.json()


# ── CF Pages GitHub Actions workflow ──────────────────────────────────────────

_CF_PAGES_WORKFLOW = """\
name: Deploy to Cloudflare Pages

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: "npm"

      - name: Install dependencies
        run: npm ci

      - name: Build
        run: npm run build

      - name: Deploy to Cloudflare Pages
        run: npx wrangler pages deploy ./dist --project-name={project_name}
        env:
          CLOUDFLARE_API_TOKEN: ${{{{ secrets.CLOUDFLARE_API_TOKEN }}}}
          CLOUDFLARE_ACCOUNT_ID: ${{{{ secrets.CLOUDFLARE_ACCOUNT_ID }}}}
"""


def _inject_cf_pages_workflow(output_dir: str | Path, project_name: str) -> None:
    """Write a GitHub Actions workflow that deploys the Astro site to CF Pages."""
    workflow_dir = Path(output_dir) / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    workflow_content = _CF_PAGES_WORKFLOW.format(project_name=project_name)
    (workflow_dir / "deploy.yml").write_text(workflow_content, encoding="utf-8")
    console.log(f"[dim]  CF Pages workflow injected → .github/workflows/deploy.yml[/dim]")


# ── Core: process one project ──────────────────────────────────────────────────

def process_project(project: dict, dry_run: bool = False, skip_git: bool = False) -> dict:
    pid          = project["id"]
    template_key = project.get("template_key") or ""
    requirements = project.get("requirements") or {}
    domain_hint  = project.get("domain") or ""
    title        = project.get("title") or domain_hint or f"project-{pid}"

    # Slug for subdomain and repo name
    slug = url_to_slug(title) or f"project-{pid}"
    slug = slug[:50]  # GitHub repo name limit
    preview_subdomain = f"{slug}.{PREVIEW_BASE_DOMAIN}"
    preview_url       = f"https://{preview_subdomain}"

    console.rule(f"[bold]Project #{pid} — {title}[/bold]")
    console.print(f"  template_key : {template_key or '(none)'}")
    console.print(f"  preview_url  : {preview_url}")

    # Mark as generating
    if not dry_run:
        patch_project(pid, {"preview_status": "generating"})

    # ── 1. Resolve template demoUrl ────────────────────────────────────────────
    demo_url = None
    if template_key:
        demo_url = demo_url_for_template(template_key)

    if not demo_url:
        console.print(f"[yellow]  No demoUrl for template '{template_key}' — skipping generation[/yellow]")
        if not dry_run:
            patch_project(pid, {
                "preview_status": "failed",
                "preview_notes": f"No demoUrl found for template_key='{template_key}'",
            })
        return {"project_id": pid, "status": "skipped", "reason": "no_demo_url"}

    console.print(f"  demo_url     : {demo_url}")

    if dry_run:
        console.print("[dim]  [dry-run] Would run pipeline and deploy[/dim]")
        return {"project_id": pid, "status": "dry_run"}

    # ── 2. Build business profile from onboarding answers ─────────────────────
    # `requirements` is project.requirements (packageAnswers) from onboarding
    basics = requirements.get("basics") or {}
    project_answers = requirements.get("project") or {}
    business_profile = {
        "name":         project_answers.get("brand_name") or project_answers.get("company_name") or title,
        "phone":        project_answers.get("phone") or basics.get("phone") or "",
        "website":      basics.get("website") or domain_hint or "",
        "city":         project_answers.get("city") or "",
        "claim_url":    f"{MOYDUS_APP_URL}/dashboard",
    }

    # ── 3. Run pipeline (scrape/clone the demoUrl into Astro) ─────────────────
    try:
        result = run_pipeline(
            demo_url,
            skip_ai=True,         # fast clone — team will customize
            skip_nextjs=True,
            clone_mode="astro-raw",
            business_profile=business_profile,
        )
    except Exception as exc:
        console.print(f"[red]  Pipeline failed: {exc}[/red]")
        patch_project(pid, {"preview_status": "failed", "preview_notes": str(exc)[:500]})
        return {"project_id": pid, "status": "failed", "error": str(exc)}

    output_dir = result.get("output_dir")
    if not output_dir or not Path(output_dir).exists():
        msg = "Pipeline returned no output_dir"
        patch_project(pid, {"preview_status": "failed", "preview_notes": msg})
        return {"project_id": pid, "status": "failed", "error": msg}

    console.print(f"[green]  Pipeline done → {output_dir}[/green]")

    if skip_git:
        console.print("[dim]  --skip-git: skipping GitHub + CF deploy[/dim]")
        patch_project(pid, {"preview_status": "ready", "preview_url": preview_url})
        return {"project_id": pid, "status": "ok_local", "output_dir": output_dir}

    # ── 4. Create GitHub private repo ─────────────────────────────────────────
    repo_name = f"client-{slug}"
    try:
        repo_info  = create_github_repo(repo_name, description=f"Moydus client site — {title}")
        remote_url = repo_info["clone_url"].replace("https://", f"https://{GITHUB_TOKEN}@")
        html_url   = repo_info["html_url"]
        console.print(f"[green]  GitHub repo: {html_url}[/green]")
    except Exception as exc:
        console.print(f"[red]  GitHub repo creation failed: {exc}[/red]")
        patch_project(pid, {"preview_status": "failed", "preview_notes": f"GitHub error: {exc}"[:500]})
        return {"project_id": pid, "status": "failed", "error": str(exc)}

    # ── 5. Push to GitHub (with CF Pages CI workflow) ─────────────────────────
    _inject_cf_pages_workflow(output_dir, cf_project_name)

    deploy_repo_dir = DEPLOY_REPO_BASE / repo_name
    try:
        deploy_result = deploy_to_git_branch(
            source_dir=output_dir,
            repo_dir=deploy_repo_dir,
            branch="main",
            remote_url=remote_url,
            commit_message=f"Initial preview for {title}",
            push=True,
        )
        console.print(f"[green]  Pushed → {deploy_result.get('commit_hash', '?')[:8]}[/green]")
    except Exception as exc:
        console.print(f"[red]  Git push failed: {exc}[/red]")
        patch_project(pid, {"preview_status": "failed", "preview_notes": f"Git push error: {exc}"[:500]})
        return {"project_id": pid, "status": "failed", "error": str(exc)}

    # ── 6. CF Pages → attach subdomain ────────────────────────────────────────
    # CF Pages project name derived from slug
    cf_project_name = f"client-{slug}"
    try:
        attach_demo_domain(cf_project_name, preview_subdomain)
        console.print(f"[green]  CF domain attached: {preview_subdomain}[/green]")
    except Exception as exc:
        # Non-fatal — subdomain attachment can be done manually later
        console.print(f"[yellow]  CF domain warning: {exc}[/yellow]")

    # ── 7. Update WebsiteProject in moydu-app ─────────────────────────────────
    patch_project(pid, {
        "preview_status": "ready",
        "preview_url":    preview_url,
        "source_repo_url": html_url,
    })

    console.print(f"[bold green]  ✓ Done → {preview_url}[/bold green]")
    return {
        "project_id":     pid,
        "status":         "ok",
        "preview_url":    preview_url,
        "github_repo":    html_url,
        "commit_hash":    deploy_result.get("commit_hash"),
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Moydus inbound preview worker")
    parser.add_argument("--once",      action="store_true", help="Run once and exit (default: loop)")
    parser.add_argument("--interval",  type=int, default=60, help="Poll interval seconds (default: 60)")
    parser.add_argument("--dry-run",   action="store_true", help="Fetch pending but don't generate or push")
    parser.add_argument("--skip-git",  action="store_true", help="Generate Astro but skip GitHub + CF deploy")
    parser.add_argument("--project-id",type=int, default=None, help="Process a single project by ID")
    args = parser.parse_args()

    console.rule("[bold blue]Moydus Inbound Preview Worker[/bold blue]")
    console.print(f"  API          : {LARAVEL_API_URL}")
    console.print(f"  GitHub org   : {GITHUB_ORG}")
    console.print(f"  Base domain  : {PREVIEW_BASE_DOMAIN}")
    console.print(f"  Token set    : {'yes' if WORKER_TOKEN else '[red]NO[/red]'}")
    console.print(f"  GitHub token : {'yes' if GITHUB_TOKEN else '[yellow]not set — git push disabled[/yellow]'}")

    results: list[dict] = []

    def run_once():
        try:
            if args.project_id:
                # Fetch single project directly
                res = requests.get(
                    f"{LARAVEL_API_URL}/api/inbound/previews/{args.project_id}",
                    headers=_AUTH, timeout=15
                )
                res.raise_for_status()
                projects = [res.json()]
            else:
                projects = fetch_pending()

            if not projects:
                console.print("[dim]No pending projects.[/dim]")
                return

            console.print(f"[bold]{len(projects)} project(s) pending[/bold]")
            for project in projects:
                r = process_project(project, dry_run=args.dry_run, skip_git=args.skip_git or not GITHUB_TOKEN)
                results.append(r)

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            console.print(f"[red]Worker error: {exc}[/red]")

    if args.once or args.project_id:
        run_once()
    else:
        console.print(f"[dim]Polling every {args.interval}s — Ctrl+C to stop[/dim]")
        try:
            while True:
                run_once()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped.[/dim]")

    # Summary table
    if results:
        table = Table(title="Results", show_lines=True)
        table.add_column("ID")
        table.add_column("Status")
        table.add_column("Preview URL")
        for r in results:
            table.add_row(
                str(r.get("project_id", "?")),
                r.get("status", "?"),
                r.get("preview_url") or r.get("error") or "",
            )
        console.print(table)


if __name__ == "__main__":
    main()
