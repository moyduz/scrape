import json
from ai.openai_client import get_client

_SYSTEM_PROMPT = """
You are a UI structure normalizer. You receive pre-processed page sections (with text, links, images, type hints, and computed CSS) and output a clean DSL JSON.

STRICT RULES:
- Do NOT generate new content. Only reorganize what exists.
- Respect the "type_hint" field unless it is clearly wrong.
- Remove duplicate text across sections.
- Separate navigation links from body text.
- Image limits: hero=1, navbar=1, features=3, cta=0, others=2.
- For each section, extract a "styles" object from the computed_css field if present.

OUTPUT FORMAT — use the flat per-type schema below:

{
  "page": {
    "sections": [
      // navbar
      { "type": "navbar", "logo": "<img url or empty>", "nav": ["label", ...], "cta": {"label": "", "href": ""},
        "styles": {"bg": "", "text_color": ""} },

      // hero
      { "type": "hero", "title": "", "subtitle": "", "image": "<single img url or empty>", "cta": {"label": "", "href": ""},
        "styles": {"bg": "", "text_color": "", "padding_y": ""} },

      // features
      { "type": "features", "title": "", "items": [{"title": "", "body": "", "image": ""}],
        "styles": {"bg": "", "text_color": "", "columns": 3} },

      // testimonials
      { "type": "testimonials", "title": "", "items": [{"quote": "", "author": "", "role": ""}],
        "styles": {"bg": "", "text_color": ""} },

      // pricing
      { "type": "pricing", "title": "", "plans": [{"name": "", "price": "", "features": [], "cta": {"label": "", "href": ""}}],
        "styles": {"bg": "", "text_color": ""} },

      // faq
      { "type": "faq", "title": "", "items": [{"question": "", "answer": ""}],
        "styles": {"bg": "", "text_color": ""} },

      // cta
      { "type": "cta", "title": "", "subtitle": "", "cta": {"label": "", "href": ""},
        "styles": {"bg": "", "text_color": ""} },

      // footer
      { "type": "footer", "logo": "", "links": [], "copyright": "",
        "styles": {"bg": "", "text_color": ""} },

      // blog
      { "type": "blog", "title": "", "items": [{"title": "", "excerpt": "", "image": "", "href": ""}],
        "styles": {"bg": "", "text_color": ""} },

      // gallery (logo ticker / customer logos)
      { "type": "gallery", "title": "", "images": [],
        "styles": {"bg": "", "text_color": ""} },

      // generic fallback
      { "type": "text", "title": "", "body": "",
        "styles": {"bg": "", "text_color": ""} }
    ]
  }
}

For "styles":
- "bg": the backgroundColor from computed_css (use hex or rgba, empty string if transparent/none)
- "text_color": the color from computed_css
- "padding_y": paddingTop value if meaningful (not 0px)
- "columns": number of grid columns if gridTemplateColumns is set (as integer)

Output ONLY valid JSON. No markdown, no explanation.
""".strip()


def generate_dsl(sections: list[dict], model: str = "gpt-4o-mini") -> dict:
    client = get_client()

    # Strip base64 screenshots before sending to DSL generator (not needed here)
    sections_payload = []
    for s in sections:
        entry = {k: v for k, v in s.items() if k != "screenshot_b64"}
        sections_payload.append(entry)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(sections_payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content
    return json.loads(raw)
