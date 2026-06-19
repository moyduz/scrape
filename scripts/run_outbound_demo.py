#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from integrations.git_deploy import deploy_to_git_branch
from integrations.moy_app import build_demo_payload, load_json_file, register_demo_site, save_payload
from main import run_pipeline
from utils.helpers import url_to_slug


def clean(data: dict) -> dict:
    return {k: v for k, v in data.items() if v not in (None, "", [], {})}


def load_and_merge(path: str | None, values: dict) -> dict:
    data = load_json_file(path)
    data.update(values)
    return clean(data)


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
    parser = argparse.ArgumentParser(
        description="One-command Moydus outbound demo: URL -> Astro clone -> Git branch -> moy-app record."
    )
    parser.add_argument("url", help="Reference/template URL to clone")
    parser.add_argument("--clone-mode", choices=["astro-raw", "nextjs"], default="astro-raw")
    parser.add_argument("--skip-ai", action="store_true", default=True, help="Skip DSL AI by default for fast raw Astro previews")
    parser.add_argument("--with-ai", action="store_true", help="Enable DSL AI generation")
    parser.add_argument("--skip-nextjs", action="store_true")

    parser.add_argument("--business-json", default=None)
    parser.add_argument("--business-name", required=True)
    parser.add_argument("--business-category", default=None)
    parser.add_argument("--business-phone", default=None)
    parser.add_argument("--business-email", default=None)
    parser.add_argument("--business-website", default=None)
    parser.add_argument("--google-maps-url", default=None)
    parser.add_argument("--place-id", default=None)
    parser.add_argument("--business-city", default=None)
    parser.add_argument("--business-state", default=None)
    parser.add_argument("--business-country", default="US")

    parser.add_argument("--template-key", default=None)
    parser.add_argument("--industry", default=None)
    parser.add_argument("--subdomain", default=None)
    parser.add_argument("--preview-url", default=None)
    parser.add_argument("--preview-base-domain", default="moydus.site")
    parser.add_argument("--screenshot-url", default=None)
    parser.add_argument("--demo-status", default="generated")

    parser.add_argument("--deploy-repo-dir", required=True, help="Local git repo used for deploy branches")
    parser.add_argument("--deploy-remote", default=None, help="GitHub remote URL for deploy repo")
    parser.add_argument("--deploy-branch", default=None)
    parser.add_argument("--push", action="store_true", help="Push deploy branch to origin")
    parser.add_argument("--deploy-provider", default="github")
    parser.add_argument("--deploy-id", default=None)

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

    parser.add_argument("--api-base-url", default=None)
    parser.add_argument("--api-token", default=None)
    parser.add_argument("--register-backend", action="store_true")
    parser.add_argument("--payload-output", default=None)
    parser.add_argument("--response-output", default=None)
    parser.add_argument("--allow-quality-fail", action="store_true", help="Deploy even if static preview quality checks fail")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    slug = url_to_slug(args.url)
    subdomain = args.subdomain or slug
    branch = args.deploy_branch or f"demo/{subdomain}"
    preview_url = args.preview_url or f"https://{subdomain}.{args.preview_base_domain}"
    template_key = args.template_key or slug

    business = load_and_merge(args.business_json, {
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

    result = run_pipeline(
        args.url,
        skip_ai=not args.with_ai,
        skip_nextjs=args.skip_nextjs,
        clone_mode=args.clone_mode,
        business_profile=business,
    )

    output_dir = result.get("output_dir") or result.get("nextjs_output")
    if not output_dir:
        raise RuntimeError("Pipeline did not return an output_dir/nextjs_output to deploy")

    quality_report = result.get("quality_report")
    if quality_report and not quality_report.get("passed") and not args.allow_quality_fail:
        failures = ", ".join(check.get("name", "unknown") for check in quality_report.get("failures", []))
        raise RuntimeError(
            f"Preview quality checks failed before deploy: {failures}. "
            "Fix the clone or pass --allow-quality-fail to override."
        )

    deploy_result = deploy_to_git_branch(
        source_dir=output_dir,
        repo_dir=args.deploy_repo_dir,
        branch=branch,
        remote_url=args.deploy_remote,
        commit_message=f"Generate preview for {args.business_name}",
        push=args.push,
    )

    outreach = load_and_merge(args.outreach_json, {
        "channel": args.outreach_channel,
        "recipient": args.outreach_recipient or args.business_email or args.business_phone,
        "subject": args.outreach_subject,
        "campaign": args.outreach_campaign,
        "status": args.outreach_status,
    })

    lead = load_and_merge(args.lead_json, {
        "name": args.lead_name,
        "email": args.lead_email or args.business_email,
        "phone": args.lead_phone or args.business_phone,
        "marketing_consent": args.marketing_consent if args.marketing_consent else None,
    })

    payload = build_demo_payload(
        business=business,
        demo={
            "template_key": template_key,
            "industry": args.industry or args.business_category,
            "subdomain": subdomain,
            "preview_url": preview_url,
            "screenshot_url": args.screenshot_url,
            "deploy_provider": args.deploy_provider,
            "deploy_id": args.deploy_id or deploy_result.get("commit_hash"),
            "status": args.demo_status,
            "metadata": {
                "source_url": args.url,
                "clone_mode": args.clone_mode,
                "output_dir": output_dir,
                "git": deploy_result,
                "quality_report_path": result.get("quality_report_path"),
                "quality_passed": result.get("quality_report", {}).get("passed") if result.get("quality_report") else None,
            },
        },
        outreach=outreach,
        lead=lead,
    )

    payload_path = args.payload_output or str(ROOT_DIR / "data" / "outbound" / f"{subdomain}.json")
    save_payload(payload, payload_path)

    backend_response = None
    if args.register_backend:
        backend_response = register_demo_site(
            payload,
            api_base_url=args.api_base_url,
            api_token=args.api_token,
        )
        claim_url = backend_response.get("claimUrl") or backend_response.get("claim_url")
        if claim_url and inject_claim_url(output_dir, claim_url):
            deploy_result = deploy_to_git_branch(
                source_dir=output_dir,
                repo_dir=args.deploy_repo_dir,
                branch=branch,
                remote_url=args.deploy_remote,
                commit_message=f"Attach claim URL for {args.business_name}",
                push=args.push,
            )
        if args.response_output:
            save_payload(backend_response, args.response_output)

    print(json.dumps({
        "output_dir": output_dir,
        "preview_url": preview_url,
        "payload_path": payload_path,
        "deploy": deploy_result,
        "quality_report_path": result.get("quality_report_path"),
        "quality_passed": result.get("quality_report", {}).get("passed") if result.get("quality_report") else None,
        "backend_response": backend_response,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
