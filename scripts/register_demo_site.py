#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from integrations.moy_app import register_demo_site


def main() -> None:
    parser = argparse.ArgumentParser(description="Register a generated Moydus preview demo in moy-app.")
    parser.add_argument("payload", help="Path to outbound payload JSON")
    parser.add_argument("--api-base-url", default=None, help="moy-app API base URL, e.g. https://app.moydus.com/api")
    parser.add_argument("--api-token", default=None, help="Optional bearer token")
    parser.add_argument("--response-output", default=None, help="Optional path to save API response JSON")
    args = parser.parse_args()

    payload_path = Path(args.payload)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    result = register_demo_site(payload, api_base_url=args.api_base_url, api_token=args.api_token)

    if args.response_output:
        out = Path(args.response_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
