from scraper.scrapling_fallback import extract_blocks, heuristic_blocks


def extract_generic(html: str) -> list[dict]:
    """
    Fallback extractor chain:
      1. Scrapling DOM extraction
      2. Heuristic BeautifulSoup extraction (if Scrapling yields nothing)
    """
    blocks = extract_blocks(html)

    if not blocks:
        blocks = heuristic_blocks(html)

    sections = []
    for i, block in enumerate(blocks):
        sections.append({
            "name": f"block_{i}",
            "text": block.get("text", ""),
            "images": block.get("images", []),
            "links": [],
            "classes": block.get("classes", []),
        })

    return sections
