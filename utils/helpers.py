import json
import os
from datetime import datetime
from slugify import slugify


def save_json(data: dict | list, folder: str, filename: str) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def load_json(path: str) -> dict | list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def url_to_slug(url: str) -> str:
    return slugify(url.replace("https://", "").replace("http://", ""))[:60]


def timestamped_filename(base: str, ext: str = "json") -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base}_{ts}.{ext}"
