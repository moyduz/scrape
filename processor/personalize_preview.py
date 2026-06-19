import re
from urllib.parse import quote
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

FRAMER_BADGE_PATTERNS = (
    "__framer-badge",
    "framer badge",
    "made in framer",
    "get it button",
)

BAD_LINK_HOSTS = (
    "framer.com",
    "framer.link",
)

TEMPLATE_NAME_HINTS = (
    "Untitled UI",
    "Framer Template",
    "Kontra",
    "Solene",
    "Influence",
    "Dermato",
)

PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}")


def _norm(value: object) -> str:
    return str(value or "").strip()


def _safe_get(tag: Tag, key: str) -> object:
    attrs = getattr(tag, "attrs", None)
    if not isinstance(attrs, dict):
        return ""
    return attrs.get(key, "")


def _classes(tag: Tag) -> list[str]:
    classes = _safe_get(tag, "class") or []
    if isinstance(classes, str):
        return classes.split()
    return classes


def _contains_bad_framer_text(text: str) -> bool:
    text = text.lower()
    return any(pattern in text for pattern in FRAMER_BADGE_PATTERNS)


def _is_bad_framer_link(href: str) -> bool:
    href = href.lower()
    return any(host in href for host in BAD_LINK_HOSTS)


def clean_framer_artifacts(soup: BeautifulSoup) -> dict:
    """Remove Framer badges/editor artifacts while keeping Framer CSS/assets."""
    removed = {
        "comments": 0,
        "badges": 0,
        "bad_links": 0,
        "meta": 0,
        "preloads": 0,
        "hidden_editor_nodes": 0,
    }

    for comment in list(soup.find_all(string=lambda text: isinstance(text, Comment))):
        if _contains_bad_framer_text(str(comment)) or "framer" in str(comment).lower():
            comment.extract()
            removed["comments"] += 1

    for tag in list(soup.find_all(True)):
        tag_id = _norm(_safe_get(tag, "id")).lower()
        classes = " ".join(_classes(tag)).lower()
        data_name = _norm(_safe_get(tag, "data-framer-name")).lower()
        aria = _norm(_safe_get(tag, "aria-label")).lower()
        text = tag.get_text(" ", strip=True).lower()

        is_badge = (
            tag_id == "__framer-badge-container"
            or "__framer-badge" in tag_id
            or "__framer-badge" in classes
            or _contains_bad_framer_text(data_name)
            or _contains_bad_framer_text(aria)
            or (tag.name in {"a", "button", "div", "span"} and _contains_bad_framer_text(text))
        )
        if is_badge:
            tag.decompose()
            removed["badges"] += 1
            continue

        href = _norm(_safe_get(tag, "href"))
        if href and _is_bad_framer_link(href):
            tag.decompose()
            removed["bad_links"] += 1
            continue

        if tag.name == "meta":
            name = _norm(_safe_get(tag, "name")).lower()
            prop = _norm(_safe_get(tag, "property")).lower()
            content = _norm(_safe_get(tag, "content")).lower()
            if (
                name == "generator" and "framer" in content
                or "framer-search-index" in name
                or "framer-search-index" in prop
                or "framer" in name and "search" in name
            ):
                tag.decompose()
                removed["meta"] += 1
                continue

        if tag.name == "link":
            rel = " ".join(_safe_get(tag, "rel") or []).lower() if isinstance(_safe_get(tag, "rel"), list) else _norm(_safe_get(tag, "rel")).lower()
            href = _norm(_safe_get(tag, "href")).lower()
            if ("modulepreload" in rel or "preload" in rel) and "framerusercontent.com/sites/" in href and href.endswith(".mjs"):
                tag.decompose()
                removed["preloads"] += 1
                continue

        attrs = getattr(tag, "attrs", None) or {}
        hidden_editor_attr = any(str(key).lower().startswith("data-framer-editor") for key in attrs)
        if hidden_editor_attr:
            tag.decompose()
            removed["hidden_editor_nodes"] += 1

    for style_tag in soup.find_all("style"):
        css = style_tag.string
        if not css:
            continue
        cleaned = re.sub(r"@supports\s*\([^{}]*\)\s*\{\s*#__framer-badge-container\s*\{[^{}]*\}\s*\}", "", css, flags=re.IGNORECASE)
        cleaned = re.sub(r"#__framer-badge-container\s*\{[^{}]*\}", "", cleaned, flags=re.IGNORECASE)
        if cleaned != css:
            style_tag.string.replace_with(cleaned)
            removed["badges"] += 1

    return removed


def clean_framer_css(css: str) -> tuple[str, dict]:
    """Remove public-facing Framer badge/editor CSS from cloned stylesheets."""
    removed = {"rules": 0}
    if not css:
        return css, removed

    blocked = (
        "#__framer-badge-container",
        "#__framer-editorbar",
        "#__framer-editorbar-container",
        "#__framer-editorbar-label",
        "#__framer-editorbar-button",
        "#__framer-editorbar-loading-spinner",
        "__framer-loading-spin",
    )

    lines = []
    skip_keyframes_depth = 0
    for line in css.splitlines():
        if skip_keyframes_depth:
            skip_keyframes_depth += line.count("{") - line.count("}")
            removed["rules"] += 1
            if skip_keyframes_depth <= 0:
                skip_keyframes_depth = 0
            continue

        if "@keyframes __framer-loading-spin" in line:
            skip_keyframes_depth = max(1, line.count("{") - line.count("}"))
            removed["rules"] += 1
            continue

        if any(token in line for token in blocked):
            removed["rules"] += 1
            continue
        lines.append(line)
    cleaned = "\n".join(lines)

    # Fallback for minified CSS where multiple rules can sit on one line.
    pattern = re.compile(
        r"[^{}]*(?:#__framer-badge-container|#__framer-editorbar[^,{\s]*|__framer-loading-spin)[^{]*\{[^{}]*\}",
        re.IGNORECASE,
    )
    while True:
        cleaned_next, count = pattern.subn("", cleaned)
        if count == 0:
            break
        removed["rules"] += count
        cleaned = cleaned_next

    return cleaned, removed


def _ensure_meta(soup: BeautifulSoup, selector_attrs: dict, content: str) -> None:
    head = soup.head or soup.find("head")
    if not head:
        html = soup.html or soup
        head = soup.new_tag("head")
        if html.contents:
            html.insert(0, head)
        else:
            html.append(head)

    tag = None
    for candidate in soup.find_all("meta"):
        if all(candidate.get(k) == v for k, v in selector_attrs.items()):
            tag = candidate
            break
    if not tag:
        tag = soup.new_tag("meta", attrs=selector_attrs)
        head.append(tag)
    tag["content"] = content


def _description_for_business(business: dict) -> str:
    name = _norm(business.get("name")) or "This business"
    category = _norm(business.get("category")) or _norm(business.get("industry")) or "local service"
    city = _norm(business.get("city"))
    state = _norm(business.get("state"))
    place = ", ".join(v for v in (city, state) if v)
    if place:
        return f"{name} provides {category} services in {place}. View the custom website preview prepared by Moydus."
    return f"{name} provides {category} services. View the custom website preview prepared by Moydus."


def update_document_metadata(soup: BeautifulSoup, business: dict) -> dict:
    name = _norm(business.get("name"))
    if not name:
        return {"metadata_updated": False}

    category = _norm(business.get("category")) or _norm(business.get("industry")) or "Website Preview"
    city = _norm(business.get("city"))
    state = _norm(business.get("state"))
    place = ", ".join(v for v in (city, state) if v)
    title = f"{name} - {category} Website Preview"
    if place:
        title = f"{name} - {category} in {place}"
    description = _description_for_business(business)

    head = soup.head or soup.find("head")
    if not head:
        html = soup.html or soup
        head = soup.new_tag("head")
        html.insert(0, head)

    title_tag = soup.title
    if not title_tag:
        title_tag = soup.new_tag("title")
        head.append(title_tag)
    title_tag.string = title

    _ensure_meta(soup, {"name": "description"}, description)
    _ensure_meta(soup, {"property": "og:title"}, title)
    _ensure_meta(soup, {"property": "og:description"}, description)
    _ensure_meta(soup, {"name": "twitter:title"}, title)
    _ensure_meta(soup, {"name": "twitter:description"}, description)

    return {"metadata_updated": True, "title": title, "description": description}


def _replace_text_node(node: NavigableString, replacements: dict[str, str]) -> bool:
    text = str(node)
    new_text = text
    for old, new in replacements.items():
        if old and new:
            new_text = re.sub(re.escape(old), new, new_text, flags=re.IGNORECASE)
    if new_text != text:
        node.replace_with(new_text)
        return True
    return False


def replace_template_names(soup: BeautifulSoup, business: dict) -> int:
    name = _norm(business.get("name"))
    if not name:
        return 0

    count = 0
    for node in list(soup.find_all(string=True)):
        if isinstance(node, Comment):
            continue
        parent = getattr(node, "parent", None)
        if parent and parent.name in {"script", "style", "noscript"}:
            continue

        text = str(node)
        new_text = text
        for hint in TEMPLATE_NAME_HINTS:
            if not hint:
                continue
            new_text = re.sub(rf"\b{re.escape(hint)}\b", name, new_text, flags=re.IGNORECASE)

        if new_text != text:
            node.replace_with(new_text)
            count += 1
    return count

def _likely_logo_image(img: Tag) -> bool:
    attrs = " ".join(_norm(_safe_get(img, attr)) for attr in ("alt", "aria-label", "class", "id", "src")).lower()
    if "logo" in attrs:
        return True

    width = _norm(_safe_get(img, "width"))
    height = _norm(_safe_get(img, "height"))
    small = width.isdigit() and 80 <= int(width) <= 360 and (not height.isdigit() or int(height) <= 180)
    if not small:
        return False

    for parent in img.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name in {"main", "section", "footer"}:
            return False
        if parent.name in {"header", "nav"}:
            return True
        hint = " ".join(_norm(_safe_get(parent, attr)) for attr in ("class", "id", "data-framer-name")).lower()
        if "logo" in hint or "brand" in hint:
            return True
    return False


def _text_logo_data_uri(name: str) -> str:
    words = [word for word in name.split() if word]
    short_name = " ".join(words[:2]) if len(name) > 22 and len(words) >= 2 else name
    label = short_name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="420" height="90" viewBox="0 0 420 90">
  <rect width="420" height="90" fill="none"/>
  <text x="0" y="57" font-family="Georgia, 'Times New Roman', serif" font-size="38" font-weight="600" fill="#633b2c">{label}</text>
</svg>'''
    return "data:image/svg+xml," + quote(svg)


def personalize_logo(soup: BeautifulSoup, business: dict) -> dict:
    name = _norm(business.get("name"))
    logo_url = _norm(business.get("logo_url") or business.get("logo"))
    if not name:
        return {"logo_updated": False}

    logo_src = logo_url or _text_logo_data_uri(name)
    logo_updates = 0
    for img in soup.find_all("img"):
        if _likely_logo_image(img):
            img["src"] = logo_src
            img["alt"] = f"{name} logo"
            if img.has_attr("srcset"):
                del img["srcset"]
            logo_updates += 1

    if logo_updates:
        return {"logo_updated": True, "mode": "image" if logo_url else "generated_text_svg", "logo_images_updated": logo_updates}

    # Text-logo fallback: update the first short header/nav link/text block.
    for tag in soup.find_all(["a", "span", "div", "p", "h1"]):
        text = tag.get_text(" ", strip=True)
        if not text or len(text) > 36:
            continue
        location_hint = " ".join(_norm(_safe_get(tag, attr)) for attr in ("class", "id", "data-framer-name")).lower()
        if tag.name == "h1" or "logo" in location_hint or "brand" in location_hint or tag.find_parent(["header", "nav"]):
            tag.clear()
            tag.append(name)
            return {"logo_updated": True, "mode": "text"}

    body = soup.body or soup.find("body")
    if body:
        brand = soup.new_tag("div")
        brand["data-moydus-brand"] = "true"
        brand["style"] = "position:absolute;left:24px;top:24px;z-index:20;font-weight:700;"
        brand.string = name
        body.insert(0, brand)
        return {"logo_updated": True, "mode": "fallback"}

    return {"logo_updated": False}


def personalize_phone_and_ctas(soup: BeautifulSoup, business: dict) -> dict:
    phone = _norm(business.get("phone"))
    email = _norm(business.get("email"))
    stats = {"phone_text_replacements": 0, "tel_links": 0, "cta_links": 0}
    if phone:
        for a in soup.find_all("a", href=True):
            href = _norm(_safe_get(a, "href"))
            text = a.get_text(" ", strip=True).lower()
            if href.startswith("tel:") or "call" in text or PHONE_RE.search(text):
                a["href"] = f"tel:{phone}"
                if len(text) <= 32:
                    a.clear()
                    a.append(phone)
                stats["tel_links"] += 1

        for node in list(soup.find_all(string=True)):
            if isinstance(node, Comment):
                continue
            parent = getattr(node, "parent", None)
            if parent and parent.name in {"script", "style", "noscript"}:
                continue
            new_text, replacements = PHONE_RE.subn(phone, str(node))
            if replacements:
                node.replace_with(new_text)
                stats["phone_text_replacements"] += replacements

    contact_href = f"tel:{phone}" if phone else (f"mailto:{email}" if email else None)
    if contact_href:
        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True).lower()
            href = _norm(_safe_get(a, "href"))
            if href.startswith("#") or any(term in text for term in ("contact", "book", "call", "quote", "estimate", "schedule", "get started")):
                if not _is_bad_framer_link(href):
                    a["href"] = contact_href
                    stats["cta_links"] += 1
    return stats


def personalize_hero_copy(soup: BeautifulSoup, business: dict) -> dict:
    name = _norm(business.get("name"))
    category = _norm(business.get("category")) or _norm(business.get("industry"))
    city = _norm(business.get("city"))
    state = _norm(business.get("state"))
    if not name:
        return {"hero_updated": False}

    place = ", ".join(v for v in (city, state) if v)
    headline = name
    if category and place:
        subline = f"{category} services in {place}."
    elif category:
        subline = f"{category} services tailored for local customers."
    else:
        subline = "A custom website preview prepared for your business."

    h1s = soup.find_all("h1")
    if h1s:
        updated = 0
        for h1 in h1s:
            h1.clear()
            h1.append(headline)
            sibling = h1.find_next(["p", "h2", "h3"])
            if sibling and sibling.get_text(" ", strip=True):
                sibling.clear()
                sibling.append(subline)
            updated += 1
        return {"hero_updated": True, "hero_variants_updated": updated, "headline": headline, "subline": subline}

    return {"hero_updated": False}


def personalize_soup(soup: BeautifulSoup, business: dict | None) -> dict:
    business = business or {}
    report = {"business_name": _norm(business.get("name"))}
    report["framer_cleanup"] = clean_framer_artifacts(soup)
    report.update(update_document_metadata(soup, business))
    report["template_name_replacements"] = replace_template_names(soup, business)
    report.update(personalize_logo(soup, business))
    report.update(personalize_hero_copy(soup, business))
    report.update(personalize_phone_and_ctas(soup, business))
    return report
