import re
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


def _classes(tag: Tag) -> list[str]:
    classes = tag.get("class") or []
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
        tag_id = _norm(tag.get("id")).lower()
        classes = " ".join(_classes(tag)).lower()
        data_name = _norm(tag.get("data-framer-name")).lower()
        aria = _norm(tag.get("aria-label")).lower()
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

        href = _norm(tag.get("href"))
        if href and _is_bad_framer_link(href):
            tag.decompose()
            removed["bad_links"] += 1
            continue

        if tag.name == "meta":
            name = _norm(tag.get("name")).lower()
            prop = _norm(tag.get("property")).lower()
            content = _norm(tag.get("content")).lower()
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
            rel = " ".join(tag.get("rel") or []).lower() if isinstance(tag.get("rel"), list) else _norm(tag.get("rel")).lower()
            href = _norm(tag.get("href")).lower()
            if ("modulepreload" in rel or "preload" in rel) and "framerusercontent.com/sites/" in href and href.endswith(".mjs"):
                tag.decompose()
                removed["preloads"] += 1
                continue

        hidden_editor_attr = any(str(key).lower().startswith("data-framer-editor") for key in tag.attrs)
        if hidden_editor_attr:
            tag.decompose()
            removed["hidden_editor_nodes"] += 1

    return removed


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

    replacements = {hint: name for hint in TEMPLATE_NAME_HINTS}
    count = 0
    for node in list(soup.find_all(string=True)):
        if isinstance(node, Comment):
            continue
        parent = getattr(node, "parent", None)
        if parent and parent.name in {"script", "style", "noscript"}:
            continue
        if _replace_text_node(node, replacements):
            count += 1
    return count


def _likely_logo_image(img: Tag) -> bool:
    attrs = " ".join(_norm(img.get(attr)) for attr in ("alt", "aria-label", "class", "id", "src")).lower()
    width = _norm(img.get("width"))
    height = _norm(img.get("height"))
    return "logo" in attrs or (width.isdigit() and int(width) <= 360 and (not height.isdigit() or int(height) <= 180))


def personalize_logo(soup: BeautifulSoup, business: dict) -> dict:
    name = _norm(business.get("name"))
    logo_url = _norm(business.get("logo_url") or business.get("logo"))
    if not name:
        return {"logo_updated": False}

    if logo_url:
        for img in soup.find_all("img"):
            if _likely_logo_image(img):
                img["src"] = logo_url
                img["alt"] = f"{name} logo"
                if img.has_attr("srcset"):
                    del img["srcset"]
                return {"logo_updated": True, "mode": "image"}

    # Text-logo fallback: update the first short header/nav link/text block.
    for tag in soup.find_all(["a", "span", "div", "p", "h1"]):
        text = tag.get_text(" ", strip=True)
        if not text or len(text) > 36:
            continue
        location_hint = " ".join(_norm(tag.get(attr)) for attr in ("class", "id", "data-framer-name")).lower()
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
            href = _norm(a.get("href"))
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
            href = _norm(a.get("href"))
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

    h1 = soup.find("h1")
    if h1:
        h1.clear()
        h1.append(headline)
        sibling = h1.find_next(["p", "h2", "h3"])
        if sibling and sibling.get_text(" ", strip=True):
            sibling.clear()
            sibling.append(subline)
        return {"hero_updated": True, "headline": headline, "subline": subline}

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
