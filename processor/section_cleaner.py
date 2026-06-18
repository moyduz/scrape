import re
from config.mapping import guess_section_type


_THUMBNAIL_MAX_PX = 120

_IMAGE_LIMITS = {
    "hero": 1,
    "navbar": 1,
    "cta": 0,
    "features": 3,
    "testimonials": 3,
    "footer": 1,
    "pricing": 0,
    "faq": 0,
    "blog": 4,
    "gallery": 10,
    "unknown": 2,
}

_SOCIAL_DOMAINS = {"linkedin", "instagram", "facebook", "twitter", "youtube", "tiktok"}
_LEGAL_KEYWORDS = {"privacy", "terms", "©", "copyright", "all rights"}


def _is_thumbnail(url: str) -> bool:
    for val in re.findall(r'[?&/](?:w(?:idth)?|h(?:eight)?)=?(\d+)', url):
        if int(val) < _THUMBNAIL_MAX_PX:
            return True
    for w, h in re.findall(r'/(\d+)x(\d+)/', url):
        if int(w) < _THUMBNAIL_MAX_PX or int(h) < _THUMBNAIL_MAX_PX:
            return True
    return False


def _filter_images(images: list[str], max_count: int) -> list[str]:
    return [img for img in images if not _is_thumbnail(img)][:max_count]


def _looks_like_footer(section: dict) -> bool:
    text = section.get("text", "").lower()
    links = section.get("links", [])
    has_social = any(any(d in link.lower() for d in _SOCIAL_DOMAINS) for link in links)
    has_legal = any(w in text for w in _LEGAL_KEYWORDS)
    return has_social or has_legal


def _looks_like_navbar(section: dict) -> bool:
    links = section.get("links", [])
    text = section.get("text", "")
    words = len(text.split())
    return len(links) >= 3 and words / max(len(links), 1) < 6


def _looks_like_hero(section: dict, index: int) -> bool:
    text = section.get("text", "")
    images = section.get("images", [])
    return index == 0 and (len(text) > 20 or bool(images))


def _looks_like_cta(section: dict) -> bool:
    text = section.get("text", "").lower()
    links = section.get("links", [])
    cta_words = {"get started", "sign up", "try free", "book", "schedule", "contact", "audit", "buy"}
    return len(text) < 120 and len(links) <= 2 and any(w in text for w in cta_words)


def _extract_nav_items(section: dict) -> list[str]:
    text = section.get("text", "")
    noise = {"the", "a", "an", "and", "or", "of", "to", "in", "for", "us", "our"}
    items = [w for w in text.split() if 2 <= len(w) < 20 and w.lower() not in noise]
    return items[:8]


def clean_section(section: dict, index: int = 0) -> dict:
    section = dict(section)

    # Priority order matters: footer must come before navbar (footer also has many links)
    guessed = guess_section_type(section.get("name", ""))

    if guessed != "unknown":
        section["type_hint"] = guessed
    elif _looks_like_footer(section):
        section["type_hint"] = "footer"
    elif _looks_like_navbar(section):
        section["type_hint"] = "navbar"
    elif _looks_like_cta(section):
        section["type_hint"] = "cta"
    elif _looks_like_hero(section, index):
        section["type_hint"] = "hero"
    else:
        section["type_hint"] = "unknown"

    limit = _IMAGE_LIMITS.get(section["type_hint"], 2)
    section["images"] = _filter_images(section.get("images", []), limit)

    if section["type_hint"] == "navbar":
        section["nav_items"] = _extract_nav_items(section)

    return section


def clean_sections(sections: list[dict]) -> list[dict]:
    return [clean_section(s, i) for i, s in enumerate(sections)]
