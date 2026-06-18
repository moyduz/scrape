import json
import os
from pathlib import Path
from typing import Any

import httpx

DEFAULT_API_BASE_URL = "http://localhost:8000/api"


def load_json_file(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def compact_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v not in (None, "", [], {})}


def build_demo_payload(
    *,
    business: dict[str, Any],
    demo: dict[str, Any],
    outreach: dict[str, Any] | None = None,
    lead: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "business": compact_dict(business),
        "demo": compact_dict(demo),
    }
    if outreach:
        payload["outreach"] = compact_dict(outreach)
    if lead:
        payload["lead"] = compact_dict(lead)
    return payload


def save_payload(payload: dict[str, Any], output_path: str | Path) -> str:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def register_demo_site(
    payload: dict[str, Any],
    *,
    api_base_url: str | None = None,
    api_token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    base_url = (api_base_url or os.environ.get("MOY_APP_API_BASE_URL") or DEFAULT_API_BASE_URL).rstrip("/")
    token = api_token or os.environ.get("MOY_APP_API_TOKEN")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = httpx.post(
        f"{base_url}/outbound/demo-sites",
        json=payload,
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()
