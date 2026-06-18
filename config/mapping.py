SECTION_TYPE_HINTS = {
    "hero": ["hero", "banner", "intro", "landing", "above the fold"],
    "navbar": ["nav", "navbar", "navigation", "menu", "topbar", "header", "default"],
    "features": [
        "feature", "benefit", "capability", "service", "solution",
        "about", "how it works", "how we work", "process", "step", "workflow",
        "what we do", "our work", "science", "ingredient", "storytelling",
        "mission", "value", "result", "stat", "number", "team", "why",
        "problem", "difference", "advantage", "detail",
    ],
    "testimonials": ["testimonial", "testemonial", "review", "quote", "feedback", "social proof", "trust", "said"],
    "pricing": ["price", "pricing", "plan", "tier", "product", "shop", "offer", "bundle", "package"],
    "faq": ["faq", "question", "answer", "accordion", "asked"],
    "footer": ["footer", "bottom", "contact", "legal", "phone"],
    "cta": ["cta", "call-to-action", "signup", "subscribe", "trial", "audit", "get started", "get free", "free audit", "book a", "schedule", "heading"],
    "gallery": ["gallery", "portfolio", "showcase", "grid", "customer", "partner", "logo", "brand", "client", "press"],
    "blog": ["blog", "article", "post", "news", "insight", "resource"],
}


def guess_section_type(name: str) -> str:
    name_lower = name.lower()
    for section_type, keywords in SECTION_TYPE_HINTS.items():
        if any(kw in name_lower for kw in keywords):
            return section_type
    return "unknown"
