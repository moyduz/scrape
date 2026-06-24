"""
Cloudflare helpers for the Moydus outbound pipeline.

Env vars (all read from .env automatically by scripts):
  CLOUDFLARE_PAGES_TOKEN    — wrangler OAuth token  (pages:write)
  CLOUDFLARE_DNS_TOKEN      — API token             (Zone > DNS > Edit for moydus.com)
  CLOUDFLARE_ACCOUNT_ID     — account ID
  CLOUDFLARE_MOYDUS_ZONE_ID — moydus.com zone ID
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Auto-load .env
# ---------------------------------------------------------------------------

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# ---------------------------------------------------------------------------
# Low-level request
# ---------------------------------------------------------------------------

def _req(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"authorization": f"Bearer {token}", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return json.loads(body)          # return the error body so caller can inspect
        except Exception:
            raise RuntimeError(f"CF {exc.code}: {body}") from exc


def _pages_token() -> str:
    t = os.environ.get("CLOUDFLARE_PAGES_TOKEN")
    if not t:
        raise RuntimeError("CLOUDFLARE_PAGES_TOKEN not set")
    return t


def _dns_token() -> str:
    t = os.environ.get("CLOUDFLARE_DNS_TOKEN")
    if not t:
        raise RuntimeError("CLOUDFLARE_DNS_TOKEN not set")
    return t


def _account_id() -> str:
    a = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not a:
        raise RuntimeError("CLOUDFLARE_ACCOUNT_ID not set")
    return a


def _zone_id() -> str:
    z = os.environ.get("CLOUDFLARE_MOYDUS_ZONE_ID")
    if not z:
        raise RuntimeError("CLOUDFLARE_MOYDUS_ZONE_ID not set")
    return z

# ---------------------------------------------------------------------------
# Pages — project
# ---------------------------------------------------------------------------

def get_pages_project(project_name: str) -> dict | None:
    url = f"https://api.cloudflare.com/client/v4/accounts/{_account_id()}/pages/projects/{project_name}"
    result = _req("GET", url, _pages_token())
    if result.get("success"):
        return result["result"]
    return None


def create_pages_project(project_name: str) -> dict:
    """
    Create a CF Pages project in 'direct upload' mode (no git connection).
    GitHub Actions will push deployments via wrangler pages deploy.
    Idempotent: returns existing project if already created.
    """
    existing = get_pages_project(project_name)
    if existing:
        return existing

    url = f"https://api.cloudflare.com/client/v4/accounts/{_account_id()}/pages/projects"
    result = _req("POST", url, _pages_token(), {
        "name": project_name,
        "production_branch": "main",
    })
    if result.get("success"):
        return result["result"]
    raise RuntimeError(f"CF Pages project creation failed: {result.get('errors')}")


def delete_pages_project(project_name: str) -> bool:
    """Deletes the entire Pages project (all deployments + custom domains)."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{_account_id()}/pages/projects/{project_name}"
    result = _req("DELETE", url, _pages_token())
    return result.get("success", False)

# ---------------------------------------------------------------------------
# Pages — custom domains
# ---------------------------------------------------------------------------

def list_pages_domains(project_name: str) -> list[dict]:
    url = f"https://api.cloudflare.com/client/v4/accounts/{_account_id()}/pages/projects/{project_name}/domains"
    result = _req("GET", url, _pages_token())
    return result.get("result") or []


def add_pages_domain(project_name: str, domain: str) -> dict:
    url = f"https://api.cloudflare.com/client/v4/accounts/{_account_id()}/pages/projects/{project_name}/domains"
    return _req("POST", url, _pages_token(), {"name": domain})


def delete_pages_domain(project_name: str, domain_id: str) -> bool:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{_account_id()}"
        f"/pages/projects/{project_name}/domains/{domain_id}"
    )
    result = _req("DELETE", url, _pages_token())
    return result.get("success", False)

# ---------------------------------------------------------------------------
# DNS — moydus.com zone
# ---------------------------------------------------------------------------

def find_dns_record(name: str, record_type: str = "CNAME") -> dict | None:
    """Find a DNS record by name in the moydus.com zone."""
    url = (
        f"https://api.cloudflare.com/client/v4/zones/{_zone_id()}/dns_records"
        f"?type={record_type}&name={name}"
    )
    result = _req("GET", url, _dns_token())
    records = result.get("result") or []
    return records[0] if records else None


def add_cname(name: str, content: str, proxied: bool = True) -> dict:
    url = f"https://api.cloudflare.com/client/v4/zones/{_zone_id()}/dns_records"
    return _req("POST", url, _dns_token(), {
        "type": "CNAME",
        "name": name,
        "content": content,
        "proxied": proxied,
        "ttl": 1,
    })


def delete_dns_record(record_id: str) -> bool:
    url = f"https://api.cloudflare.com/client/v4/zones/{_zone_id()}/dns_records/{record_id}"
    result = _req("DELETE", url, _dns_token())
    return result.get("success", False)

# ---------------------------------------------------------------------------
# High-level: attach domain to a Pages project + create CNAME
# ---------------------------------------------------------------------------

def attach_demo_domain(project_name: str, domain: str) -> dict:
    """
    Full setup: create Pages project (if needed) + custom domain + CNAME.
    Returns { pages_ok, dns_ok, already_existed }.
    """
    subdomain_prefix = domain.split(".")[0]
    pages_target = f"{project_name}.pages.dev"

    # 1. Ensure Pages project exists (direct-upload mode; GH Actions deploys to it)
    create_pages_project(project_name)

    # 2. Pages custom domain
    pages_result = add_pages_domain(project_name, domain)
    pages_ok = pages_result.get("success", False)
    already_existed = any(
        e.get("code") == 8000018 for e in (pages_result.get("errors") or [])
    )

    # 3. DNS CNAME
    dns_ok = False
    existing = find_dns_record(domain)
    if existing:
        dns_ok = True  # already there
    else:
        dns_result = add_cname(subdomain_prefix, pages_target)
        dns_ok = dns_result.get("success", False)

    return {"pages_ok": pages_ok or already_existed, "dns_ok": dns_ok, "already_existed": already_existed}


def deploy_pages_direct(project_name: str, source_dir: str | Path, branch: str = "main") -> dict:
    """
    Deploy a local directory to a CF Pages project via wrangler (direct upload).
    Creates the project first if it doesn't exist.
    Returns { project_name, deployment_url, success }.
    """
    create_pages_project(project_name)
    result = subprocess.run(
        ["wrangler", "pages", "deploy", str(source_dir),
         "--project-name", project_name,
         "--branch", branch,
         "--commit-dirty=true"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise RuntimeError(f"wrangler pages deploy failed:\n{output}")
    deployment_url = f"https://{project_name}.pages.dev"
    for line in output.splitlines():
        if "pages.dev" in line and "https://" in line:
            import re
            match = re.search(r"https://[^\s]+\.pages\.dev[^\s]*", line)
            if match:
                deployment_url = match.group(0).rstrip(".")
                break
    return {"project_name": project_name, "deployment_url": deployment_url, "success": True, "output": output}


def detach_demo_domain(project_name: str, domain: str, delete_project: bool = False) -> dict:
    """
    Cleanup: remove custom domain from Pages + delete CNAME.
    Optionally delete the entire Pages project.
    Returns { pages_domain_deleted, dns_deleted, project_deleted }.
    """
    pages_domain_deleted = False
    dns_deleted = False
    project_deleted = False

    # 1. Remove custom domain from Pages
    for dom in list_pages_domains(project_name):
        if dom["name"] == domain:
            pages_domain_deleted = delete_pages_domain(project_name, dom["id"])
            break

    # 2. Remove CNAME from moydus.com zone
    record = find_dns_record(domain)
    if record:
        dns_deleted = delete_dns_record(record["id"])

    # 3. Optionally nuke the whole Pages project
    if delete_project:
        project_deleted = delete_pages_project(project_name)

    return {
        "pages_domain_deleted": pages_domain_deleted,
        "dns_deleted": dns_deleted,
        "project_deleted": project_deleted,
    }
