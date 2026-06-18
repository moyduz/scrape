import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
CLEANED_DIR = DATA_DIR / "cleaned"
SECTIONS_DIR = DATA_DIR / "sections"
DSL_DIR = DATA_DIR / "dsl"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
NEXTJS_DIR = DATA_DIR / "nextjs"
DOM_DIR = DATA_DIR / "dom"
ASSETS_DIR = DATA_DIR / "assets"
ASTRO_DIR = DATA_DIR / "astro"

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
NEXTJS_MODEL = os.environ.get("NEXTJS_MODEL", "gpt-4o")
PLAYWRIGHT_TIMEOUT = int(os.environ.get("PLAYWRIGHT_TIMEOUT", "30000"))

MOY_APP_API_BASE_URL = os.environ.get("MOY_APP_API_BASE_URL", "http://localhost:8000/api")
MOY_APP_API_TOKEN = os.environ.get("MOY_APP_API_TOKEN")
