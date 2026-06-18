from processor.clean_dom import clean_text


def build_sections(elements: list[dict]) -> list[dict]:
    """
    Normalizes raw extracted elements into a consistent section format
    for downstream DSL generation.
    """
    sections = []

    for el in elements:
        section = {
            "name": el.get("name", "unknown"),
            "text": clean_text(el.get("text", "")),
            "images": [img for img in el.get("images", []) if img],
            "links": el.get("links", []),
        }
        sections.append(section)

    return sections


def deduplicate_sections(sections: list[dict]) -> list[dict]:
    """
    Removes duplicate sections. Deduplicates by name first (Framer SSR variants
    render the same named section multiple times for each breakpoint), then falls
    back to text content for unnamed sections.
    """
    seen_names: set[str] = set()
    seen_text: set[str] = set()
    unique = []

    for s in sections:
        name = s.get("name", "").strip().lower()
        text_key = s["text"][:80]

        if name and name != "unknown":
            if name in seen_names:
                continue
            seen_names.add(name)
        else:
            if not text_key or text_key in seen_text:
                continue
            seen_text.add(text_key)

        unique.append(s)

    return unique
