import base64
from pathlib import Path
from slugify import slugify


def extract_section_visuals(session, section_names: list[str], output_dir: Path) -> dict[str, dict]:
    """
    For each named Framer section, extracts:
    - screenshot (PNG, base64 for AI)
    - computed CSS (quick property snapshot)
    - source HTML + scoped CSS (exact Framer markup and class rules)

    Returns {name: {screenshot_path, screenshot_b64, css, source_html, source_css}}.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {}

    for name in section_names:
        safe_name = slugify(name)
        screenshot_path = output_dir / f"{safe_name}.png"

        success = session.screenshot_section(name, str(screenshot_path))
        computed_css = session.get_computed_styles(name)
        source = session.get_section_source(name)

        b64 = None
        if success and screenshot_path.exists():
            with open(screenshot_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()

        result[name] = {
            "screenshot_path": str(screenshot_path) if success else None,
            "screenshot_b64": b64,
            "css": computed_css,
            "source_html": source.get("html", ""),
            "source_css": source.get("css", ""),
        }

    return result
