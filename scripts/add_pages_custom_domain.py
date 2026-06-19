#!/usr/bin/env python3
"""Attach a custom domain to a Cloudflare Pages project.

Requires CLOUDFLARE_API_TOKEN or CF_API_TOKEN with Pages write permission.
Example:
  CLOUDFLARE_API_TOKEN=... .venv/bin/python scripts/add_pages_custom_domain.py \
    --account-id 5e5f8a26d62e3255d96f4410baf43d73 \
    --project-name austin-dermatology-studio \
    --domain austin-dermatology.moydus.site
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def api_request(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        raise RuntimeError(f"Cloudflare API error {exc.code}: {body}") from exc
    return json.loads(body) if body else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Attach a custom domain to a Cloudflare Pages project")
    parser.add_argument("--account-id", default=os.environ.get("CLOUDFLARE_ACCOUNT_ID") or os.environ.get("CF_ACCOUNT_ID"))
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.account_id:
        raise SystemExit("Missing --account-id or CLOUDFLARE_ACCOUNT_ID/CF_ACCOUNT_ID")

    endpoint = (
        f"https://api.cloudflare.com/client/v4/accounts/{args.account_id}"
        f"/pages/projects/{args.project_name}/domains"
    )

    if args.dry_run:
        print(json.dumps({"method": "POST", "url": endpoint, "payload": {"name": args.domain}}, indent=2))
        return

    token = os.environ.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CF_API_TOKEN")
    if not token:
        raise SystemExit("Missing CLOUDFLARE_API_TOKEN or CF_API_TOKEN")

    result = api_request("POST", endpoint, token, {"name": args.domain})
    print(json.dumps(result, indent=2))

    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
