from bs4 import BeautifulSoup, Tag

# Layout/viewport wrappers — drill into these, don't treat as sections
_WRAPPER_NAMES = {
    "desktop", "mobile", "tablet", "main", "content",
    "container", "wrapper", "page", "layout", "frame",
    "body", "root", "app", "site",
}

# Short system elements — skip when text is thin
_NOISE_NAMES = {"light", "dark"}

# Text patterns that always indicate noise
_NOISE_TEXT_PATTERNS = [
    "create a free website with framer",
    "website builder loved by",
]

# Names that look like asset/file identifiers (not semantic section names)
def _looks_like_asset_name(name: str) -> bool:
    return "__" in name or (len(name) > 40 and "-" in name and name.replace("-", "").replace("_", "").isalnum())


def _is_framer_candidate(tag: Tag) -> bool:
    if tag.has_attr("data-framer-name"):
        return True
    if tag.has_attr("data-framer-appear-id"):
        return len(tag.get_text(strip=True)) > 50
    return False


def _is_noise(name: str, text: str) -> bool:
    if name.lower() in _NOISE_NAMES and len(text.strip()) < 100:
        return True
    if _looks_like_asset_name(name):
        return True
    text_lower = text.lower()
    return any(p in text_lower for p in _NOISE_TEXT_PATTERNS)


def _is_wrapper(name: str) -> bool:
    words = name.lower().strip().split()
    return bool(words) and words[0] in _WRAPPER_NAMES


def _get_framer_parent(tag: Tag) -> Tag | None:
    return tag.find_parent(
        lambda t: t.has_attr("data-framer-name") or t.has_attr("data-framer-appear-id")
    )


def _get_direct_framer_children(el: Tag) -> list[Tag]:
    """Returns framer candidates whose immediate framer parent is el."""
    return [
        child for child in el.find_all(_is_framer_candidate)
        if _get_framer_parent(child) is el
    ]


def _get_root_sections(tags: list[Tag]) -> list[Tag]:
    tag_set = set(id(t) for t in tags)
    return [
        tag for tag in tags
        if not any(id(ancestor) in tag_set for ancestor in tag.parents)
    ]


def _expand(el: Tag) -> list[Tag]:
    """
    Recursively expand wrapper elements into their meaningful children.
    Stops when it hits a real section (non-wrapper, non-noise).
    """
    name = (el.get("data-framer-name") or el.get("data-framer-appear-id", "")).strip()
    text = el.get_text(strip=True)

    if _is_noise(name, text):
        return []

    if _is_wrapper(name):
        children = _get_direct_framer_children(el)
        if children:
            result = []
            for child in children:
                result.extend(_expand(child))
            return result
        # Wrapper with no named children → keep only if it has real content
        return [el] if len(text) > 30 else []

    return [el]


def _el_to_dict(el: Tag) -> dict:
    name = el.get("data-framer-name") or el.get("data-framer-appear-id", "unknown")
    text = el.get_text(separator=" ", strip=True)[:300]
    images = [img.get("src", "") for img in el.find_all("img") if img.get("src")]
    links = [a.get("href", "") for a in el.find_all("a") if a.get("href")]
    return {
        "name": name,
        "text": text,
        "images": images,
        "links": links,
        "html_snippet": str(el)[:500],
    }


def extract_framer(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")

    all_candidates = soup.find_all(_is_framer_candidate)
    if not all_candidates:
        return []

    # Find true DOM roots among all framer candidates
    root_candidates = _get_root_sections(all_candidates)

    # Expand wrappers recursively
    final_elements: list[Tag] = []
    for el in root_candidates:
        final_elements.extend(_expand(el))

    # Safety dedup in case expansion produced overlapping elements
    final_elements = _get_root_sections(final_elements)

    return [_el_to_dict(el) for el in final_elements]


def has_framer_attrs(html: str) -> bool:
    soup = BeautifulSoup(html, "lxml")
    return bool(soup.find(attrs={"data-framer-name": True}))
