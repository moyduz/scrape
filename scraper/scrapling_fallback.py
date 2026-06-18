from scrapling import Adaptor
from bs4 import BeautifulSoup


def extract_blocks(html: str) -> list[dict]:
    """
    Scrapling-based DOM extraction. Used when data-framer-name attrs are absent.
    Returns list of raw block dicts with tag, text, images, classes.
    """
    page = Adaptor(html)
    blocks = []

    for el in page.find_all("div"):
        text = el.text.strip() if el.text else ""
        if not text and not el.find_all("img"):
            continue

        imgs = [img.attrib.get("src", "") for img in el.find_all("img") if img.attrib.get("src")]

        blocks.append({
            "tag": el.tag,
            "classes": el.attrib.get("class", "").split(),
            "text": text[:300],
            "images": imgs,
        })

    return blocks


def heuristic_blocks(html: str) -> list[dict]:
    """
    Last-resort heuristic extraction via BeautifulSoup.
    Targets large text blocks or img+text combos.
    """
    soup = BeautifulSoup(html, "lxml")
    blocks = []

    for el in soup.find_all(["section", "article", "main", "div"]):
        text = el.get_text(strip=True)
        imgs = [img.get("src", "") for img in el.find_all("img") if img.get("src")]

        if len(text) < 20 and not imgs:
            continue

        blocks.append({
            "tag": el.name,
            "classes": el.get("class", []),
            "text": text[:300],
            "images": imgs,
        })

    return blocks
