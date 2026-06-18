import json
import re
from datetime import datetime, timezone
from pathlib import Path

BLOCKED_PATTERNS = {
    "framer_badge": re.compile(r"__framer-badge|framer badge|made in framer", re.IGNORECASE),
    "framer_com_link": re.compile(r"https?://(?:www\.)?framer\.(?:com|link)", re.IGNORECASE),
    "framer_get_it_button": re.compile(r"get it button", re.IGNORECASE),
    "framer_generator_meta": re.compile(r"<meta[^>]+name=[\"']generator[\"'][^>]+framer", re.IGNORECASE),
    "framer_search_index": re.compile(r"framer-search-index", re.IGNORECASE),
}


def _norm(value: object) -> str:
    return str(value or "").strip()


def run_static_quality_checks(index_path: str | Path, business: dict | None = None) -> dict:
    path = Path(index_path)
    html = path.read_text(encoding="utf-8") if path.exists() else ""
    business = business or {}
    checks = []

    def add(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    add("index_exists", path.exists(), str(path))
    add("html_not_empty", len(html.strip()) > 500, f"{len(html)} bytes")

    for name, pattern in BLOCKED_PATTERNS.items():
        found = bool(pattern.search(html))
        add(f"no_{name}", not found, "blocked artifact found" if found else "")

    business_name = _norm(business.get("name"))
    if business_name:
        add("business_name_present", business_name.lower() in html.lower(), business_name)

    phone = _norm(business.get("phone"))
    if phone:
        phone_digits = re.sub(r"\D", "", phone)
        html_digits = re.sub(r"\D", "", html)
        add("phone_present", phone_digits in html_digits, phone)
        add("tel_link_present", f"tel:{phone}" in html or f"tel:{phone_digits}" in html, phone)

    passed = all(check["passed"] for check in checks)
    return {
        "passed": passed,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "index_path": str(path),
        "business": {k: business.get(k) for k in ("name", "category", "phone", "city", "state") if business.get(k)},
        "checks": checks,
        "failures": [check for check in checks if not check["passed"]],
    }


def save_quality_report(report: dict, output_path: str | Path) -> str:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
