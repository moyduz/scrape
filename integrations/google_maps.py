"""
Google Maps business scraper for the Moydus outbound pipeline.

Two modes:
  1. gosom  — uses the gosom/google-maps-scraper Go binary (recommended for bulk)
  2. api    — uses SerpAPI or DataForSEO (paid, most reliable for production)

Usage:
    from integrations.google_maps import scrape_category

    businesses = scrape_category(
        category="locksmith",
        city="Austin",
        state="TX",
        limit=50,
        mode="gosom",  # or "api"
    )
    for b in businesses:
        print(b.name, b.website, b.phone)
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from rich.console import Console

console = Console()

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BusinessResult:
    name: str
    category: str = ""
    phone: str = ""
    website: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    country: str = "US"
    google_maps_url: str = ""
    place_id: str = ""
    rating: float | None = None
    review_count: int = 0
    email: str = ""          # gosom doesn't return email; enrichment step needed
    extra: dict = field(default_factory=dict)

    def has_website(self) -> bool:
        return bool(self.website and self.website.startswith("http"))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "phone": self.phone,
            "email": self.email,
            "website": self.website,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "country": self.country,
            "google_maps_url": self.google_maps_url,
            "place_id": self.place_id,
            "rating": self.rating,
            "review_count": self.review_count,
        }


# ---------------------------------------------------------------------------
# gosom mode
# ---------------------------------------------------------------------------

GOSOM_DEFAULT_BINARY = os.environ.get(
    "GOSOM_BINARY",
    str(Path.home() / "go" / "bin" / "google-maps-scraper"),
)


def _gosom_query(category: str, city: str, state: str) -> str:
    """Build a Google Maps search query string."""
    return f"{category} in {city}, {state}"


def scrape_via_gosom(
    category: str,
    city: str,
    state: str,
    country: str = "US",
    limit: int = 50,
    binary: str = GOSOM_DEFAULT_BINARY,
    lang: str = "en",
    depth: int = 1,
) -> list[BusinessResult]:
    """
    Run gosom/google-maps-scraper and parse its JSON output.

    Install: go install github.com/gosom/google-maps-scraper@latest
    """
    if not Path(binary).exists():
        raise FileNotFoundError(
            f"gosom binary not found at {binary}. "
            "Install with: go install github.com/gosom/google-maps-scraper@latest"
        )

    query = _gosom_query(category, city, state)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        input_file = f.name
        f.write(query + "\n")

    output_file = tempfile.mktemp(suffix=".json")

    try:
        cmd = [
            binary,
            "--input", input_file,
            "--results-file", output_file,
            "--exit-on-inactivity", "3m",
            "--lang", lang,
            "--depth", str(depth),
            "--json",
        ]
        console.print(f"[cyan]Running gosom:[/cyan] {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            console.print(f"[red]gosom error:[/red] {result.stderr}")
            return []

        output_path = Path(output_file)
        if not output_path.exists():
            console.print("[yellow]gosom produced no output file.[/yellow]")
            return []

        raw = json.loads(output_path.read_text(encoding="utf-8"))
        businesses = _parse_gosom_output(raw, category, city, state, country)

        # Filter: only keep businesses with a real website (needed for demo generation)
        with_sites = [b for b in businesses if b.has_website()]
        console.print(
            f"[green]{len(with_sites)}/{len(businesses)} businesses have websites.[/green]"
        )

        return with_sites[:limit]

    finally:
        Path(input_file).unlink(missing_ok=True)
        Path(output_file).unlink(missing_ok=True)


def _parse_gosom_output(
    raw: list[dict],
    category: str,
    city: str,
    state: str,
    country: str,
) -> list[BusinessResult]:
    results = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        results.append(BusinessResult(
            name=item.get("title") or item.get("name") or "",
            category=item.get("category") or category,
            phone=item.get("phone") or "",
            website=item.get("website") or "",
            address=item.get("address") or "",
            city=city,
            state=state,
            country=country,
            google_maps_url=item.get("link") or item.get("url") or "",
            place_id=item.get("place_id") or "",
            rating=float(item["rating"]) if item.get("rating") else None,
            review_count=int(item.get("reviews_count") or item.get("reviewCount") or 0),
        ))
    return results


# ---------------------------------------------------------------------------
# SerpAPI mode (paid, reliable)
# ---------------------------------------------------------------------------

def scrape_via_serpapi(
    category: str,
    city: str,
    state: str,
    country: str = "US",
    limit: int = 50,
    api_key: str | None = None,
) -> list[BusinessResult]:
    """
    Use SerpAPI Google Maps endpoint.
    Requires: pip install google-search-results
    Set SERPAPI_KEY env var or pass api_key.
    """
    try:
        from serpapi import GoogleSearch  # type: ignore
    except ImportError:
        raise ImportError("pip install google-search-results")

    key = api_key or os.environ.get("SERPAPI_KEY")
    if not key:
        raise ValueError("SERPAPI_KEY not set")

    query = f"{category} {city} {state}"
    params = {
        "engine": "google_maps",
        "q": query,
        "api_key": key,
        "type": "search",
        "hl": "en",
        "gl": country.lower(),
    }

    results = []
    while len(results) < limit:
        search = GoogleSearch(params)
        data = search.get_dict()
        places = data.get("local_results", [])

        if not places:
            break

        for place in places:
            results.append(BusinessResult(
                name=place.get("title") or "",
                category=place.get("type") or category,
                phone=place.get("phone") or "",
                website=place.get("website") or "",
                address=place.get("address") or "",
                city=city,
                state=state,
                country=country,
                google_maps_url=place.get("links", {}).get("directions") or "",
                place_id=place.get("place_id") or "",
                rating=float(place["rating"]) if place.get("rating") else None,
                review_count=int(place.get("reviews") or 0),
            ))

        next_page = data.get("serpapi_pagination", {}).get("next")
        if not next_page or len(results) >= limit:
            break
        params["start"] = data.get("serpapi_pagination", {}).get("next_page_token")

    with_sites = [b for b in results if b.has_website()]
    console.print(
        f"[green]{len(with_sites)}/{len(results)} businesses have websites.[/green]"
    )
    return with_sites[:limit]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

ScrapeMode = Literal["gosom", "serpapi"]


def scrape_category(
    category: str,
    city: str,
    state: str,
    country: str = "US",
    limit: int = 50,
    mode: ScrapeMode = "gosom",
    **kwargs,
) -> list[BusinessResult]:
    """
    Scrape Google Maps for businesses in a category + location.

    Returns only businesses that have a website URL (required for demo generation).

    Args:
        category:  Search term, e.g. "locksmith", "plumber", "dentist"
        city:      City name, e.g. "Austin"
        state:     State abbreviation, e.g. "TX"
        country:   ISO country code, default "US"
        limit:     Max results to return (only those with websites)
        mode:      "gosom" (free, local binary) or "serpapi" (paid API)
    """
    console.print(f"\n[bold]Scraping Google Maps:[/bold] {category} in {city}, {state} (mode={mode})")

    if mode == "gosom":
        return scrape_via_gosom(category, city, state, country, limit, **kwargs)
    if mode == "serpapi":
        return scrape_via_serpapi(category, city, state, country, limit, **kwargs)

    raise ValueError(f"Unknown mode: {mode}")
