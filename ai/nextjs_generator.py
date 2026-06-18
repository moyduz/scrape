import json
import re
from ai.openai_client import get_client
from utils.css_inliner import inline_framer_css
from utils import html_to_jsx


_COMPONENT_SYSTEM_PROMPT = """
You convert pre-processed Framer HTML into a Next.js 14 + Tailwind component.

The HTML already has Tailwind classes pre-computed by a Python tool.
Each element has a `data-tw` attribute containing the correct Tailwind classes.

━━━ YOUR ONLY JOBS ━━━
1. Replace every `data-tw="..."` with `className="..."`
2. If an element also has `class="..."` (semantic classes), merge: className="<data-tw> <class>"
3. If an element has `style="..."` remaining, convert it to style={{ camelCaseProp: "value" }}
4. Replace <a href="..."> → <Link href="..."> (import Link from 'next/link')
5. Replace <img ...> → <Image ...> (import Image from 'next/image')
   - data-framer-background-image-wrapper parent → add className="relative overflow-hidden" to parent; use <Image fill className="object-cover" />
   - Regular img → <Image src width height className />
6. Self-close empty elements: <div></div> → keep, but <br> → <br />
7. Wrap everything in: export default function ComponentName() { return (...) }
8. Remove data-framer-* attributes (noise). Keep id, aria-*, data-styles-preset if present.

━━━ DO NOT ━━━
- Do not change any className values
- Do not restructure the HTML
- Do not add or remove elements
- Do not add comments
- Do not simplify or summarize

━━━ OUTPUT ━━━
Raw .tsx only. No markdown fences. No explanation.
""".strip()


def _build_component_message(section: dict, visual: dict | None, enriched_html: str) -> list[dict]:
    content = []

    # 1. Screenshot — visual reference
    if visual and visual.get("screenshot_b64"):
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{visual['screenshot_b64']}",
                "detail": "high",
            },
        })

    text_parts = []

    # 2. Pre-processed HTML (data-tw attributes already contain Tailwind classes)
    if enriched_html.strip():
        text_parts.append(
            "=== HTML (data-tw attributes contain pre-computed Tailwind classes) ===\n"
            "Move every data-tw value to className. Fix JSX syntax. That is all.\n\n"
            + enriched_html.strip()[:28000]
        )
    else:
        # Fallback: use section data
        section_data = {k: v for k, v in section.items() if k not in ("screenshot_b64", "computed_css")}
        text_parts.append(
            "=== SECTION DATA ===\n" + json.dumps(section_data, ensure_ascii=False, indent=2)
        )

    component_name = _component_name(section.get("type", "Section"))
    text_parts.append(
        f"Component name: `{component_name}`. Move data-tw → className. Fix JSX. Done."
    )

    content.append({"type": "text", "text": "\n\n".join(text_parts)})
    return content


def _component_name(section_type: str) -> str:
    return {
        "navbar": "Navbar",
        "hero": "Hero",
        "features": "Features",
        "testimonials": "Testimonials",
        "pricing": "Pricing",
        "faq": "FAQ",
        "cta": "CTA",
        "footer": "Footer",
        "blog": "Blog",
        "gallery": "Gallery",
        "text": "TextSection",
    }.get(section_type, section_type.capitalize())


def _strip_code_fences(code: str) -> str:
    code = code.strip()
    if code.startswith("```"):
        code = code.split("\n", 1)[-1]
    if code.endswith("```"):
        code = code.rsplit("```", 1)[0]
    return code.strip()


def _css_prop_to_camel(prop: str) -> str:
    """font-size → fontSize, background-color → backgroundColor, --my-var → --my-var"""
    prop = prop.strip()
    if prop.startswith("--"):
        return prop  # CSS custom properties stay as-is
    parts = prop.split("-")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _css_string_to_react_obj(css: str) -> str:
    """
    Converts a CSS inline style string to a React style object string.
    e.g. 'font-size: 17px; color: red' → '{ fontSize: "17px", color: "red" }'
    """
    props = []
    # Split on semicolons, but be careful of values containing semicolons (rare in inline)
    for decl in css.split(";"):
        decl = decl.strip()
        if not decl:
            continue
        colon = decl.find(":")
        if colon == -1:
            continue
        prop = _css_prop_to_camel(decl[:colon])
        value = decl[colon + 1:].strip()
        # Escape any existing quotes in value
        value = value.replace('"', '\\"')
        if prop.startswith("--"):
            # CSS custom properties need quoted key
            props.append(f'"{prop}": "{value}"')
        else:
            props.append(f'{prop}: "{value}"')
    return "{ " + ", ".join(props) + " }"


def _fix_style_strings(code: str) -> str:
    """
    Replace style="css-string" with style={{ jsObject }} in JSX.
    React requires style as an object, not a string.
    """
    def replace_style(m: re.Match) -> str:
        css = m.group(1)
        # Unescape HTML entities that Framer puts in HTML
        css = css.replace("&quot;", '"').replace("&#39;", "'").replace("&amp;", "&")
        obj = _css_string_to_react_obj(css)
        return f"style={{{obj}}}"

    # Match style="..." but NOT style={{ (already a JSX object)
    return re.sub(r'style="([^"]*)"', replace_style, code)


def _strip_data_url_classes(code: str) -> str:
    """
    Remove bg-[url(...)] Tailwind tokens that embed data: URIs.
    Uses bracket-aware parsing to handle URL-encoded SVG with nested brackets/quotes.
    """
    result: list[str] = []
    i = 0
    n = len(code)
    while i < n:
        # Detect start of a Tailwind arbitrary-value class containing a data: URI
        if code[i:i + 7] == "bg-[url" and "data:" in code[i:i + 30]:
            # Walk forward to find the matching ] that closes the [url(...)]
            depth = 0
            j = i
            while j < n:
                if code[j] == "[":
                    depth += 1
                elif code[j] == "]":
                    depth -= 1
                    if depth == 0:
                        j += 1  # skip the closing ]
                        break
                j += 1
            # Skip this token (and any trailing space)
            i = j
            if i < n and code[i] == " ":
                i += 1
            continue
        result.append(code[i])
        i += 1
    return "".join(result)


def _ensure_imports(code: str) -> str:
    """Prepend missing next/link and next/image imports."""
    needs_link = "<Link" in code and 'import Link' not in code
    needs_image = "<Image" in code and 'import Image' not in code

    lines = []
    if needs_image:
        lines.append('import Image from "next/image";')
    if needs_link:
        lines.append('import Link from "next/link";')

    if lines:
        # Insert after any existing import block, or at the top
        existing_imports_end = 0
        for i, line in enumerate(code.split("\n")):
            if line.strip().startswith("import "):
                existing_imports_end = i + 1
        parts = code.split("\n")
        code = "\n".join(parts[:existing_imports_end] + lines + parts[existing_imports_end:])

    return code


def _fix_nextjs_patterns(code: str) -> str:
    """Fix common issues in AI-generated Next.js code from Framer source."""

    # 0a. Remove <style>...</style> blocks inside JSX — invalid in React components
    code = re.sub(r'<style\b[^>]*>.*?</style>', '', code, flags=re.DOTALL)

    # 0b. Remove data-URI bg classes that break JSX string delimiters
    code = _strip_data_url_classes(code)

    # 1. style="string" → style={{ object }}
    code = _fix_style_strings(code)

    # 2. <Link ...><a [attrs]>content</a></Link> → <Link ... [attrs]>content</Link>
    def _merge_link_a(m: re.Match) -> str:
        link_props = m.group(1).strip()
        a_attrs = m.group(2).strip()
        content = m.group(3)
        class_match = re.search(r'className=(?:"[^"]*"|\'[^\']*\'|{[^}]*})', a_attrs)
        if class_match:
            link_props = f"{link_props} {class_match.group(0)}"
        return f"<Link {link_props}>{content}</Link>"

    code = re.compile(
        r'<Link\s+([^>]+?)>\s*<a\b([^>]*)>(.*?)</a>\s*</Link>',
        re.DOTALL,
    ).sub(_merge_link_a, code)

    # 3. Deprecated Next.js Image props
    code = re.sub(r'\blayout=["\']fill["\']', 'fill', code)
    code = re.sub(r'\bobjectFit=["\']cover["\']', 'className="object-cover"', code)
    code = re.sub(r'\bobjectFit=["\']contain["\']', 'className="object-contain"', code)

    # 4. SVG attribute names: kebab-case → camelCase (JSX requires camelCase)
    svg_attrs = {
        "stroke-linecap": "strokeLinecap",
        "stroke-linejoin": "strokeLinejoin",
        "stroke-width": "strokeWidth",
        "fill-rule": "fillRule",
        "clip-rule": "clipRule",
        "stop-color": "stopColor",
        "stop-opacity": "stopOpacity",
        "font-size": "fontSize",
        "font-family": "fontFamily",
        "text-anchor": "textAnchor",
    }
    for html_attr, jsx_attr in svg_attrs.items():
        code = re.sub(rf'\b{re.escape(html_attr)}=', f'{jsx_attr}=', code)

    # 5. style={{ "justify-content": "..." }} → style={{ justifyContent: "..." }}
    #    (CSS property names in React style objects must be camelCase)
    def _camel_style_obj(m: re.Match) -> str:
        inner = m.group(1)
        def _to_camel(prop_match: re.Match) -> str:
            prop = prop_match.group(1)
            camel = re.sub(r'-(.)', lambda x: x.group(1).upper(), prop)
            return f'"{camel}": '
        inner = re.sub(r'"([a-z][a-z-]+)":\s*', _to_camel, inner)
        return f'style={{{{{inner}}}}}'

    code = re.sub(r'style=\{\{(.*?)\}\}', _camel_style_obj, code, flags=re.DOTALL)

    # 6. Remove width/height from <Image fill ...> — they conflict in Next.js 14
    def _strip_fill_conflicts(m: re.Match) -> str:
        tag = m.group(0)
        if re.search(r'\bfill\b', tag):
            # Remove width/height in both {expr} and "string" form
            tag = re.sub(r'\s+(?:width|height)=(?:\{[^}]+\}|"[^"]*")', '', tag)
        return tag

    code = re.sub(r'<Image\b.*?/>', _strip_fill_conflicts, code, flags=re.DOTALL)

    # 7. Remove srcSet/decoding — not valid Next.js Image props
    def _clean_image_props(m: re.Match) -> str:
        tag = m.group(0)
        tag = re.sub(r'\s+srcSet="[^"]*"', '', tag)
        tag = re.sub(r'\s+decoding="[^"]*"', '', tag)
        tag = tag.replace('&amp;', '&')
        return tag

    code = re.sub(r'<Image\b.*?/>', _clean_image_props, code, flags=re.DOTALL)

    # 8. Remove Framer-only CSS props from style objects (cornerShape, etc.)
    code = re.sub(r',\s*cornerShape:\s*[\'"][^\'"]*[\'"]', '', code)
    code = re.sub(r'cornerShape:\s*[\'"][^\'"]*[\'"]\s*,?\s*', '', code)
    code = re.sub(r'\s+style=\{\{\s*\}\}', '', code)

    # 9. Numeric JSX attributes: string → number expression
    _NUMERIC_ATTRS = ("tabIndex", "aria-posinset", "aria-setsize", "aria-level",
                      "aria-rowcount", "aria-colcount", "aria-rowindex", "aria-colindex")
    for attr in _NUMERIC_ATTRS:
        code = re.sub(rf'{re.escape(attr)}="(\d+)"', lambda m, a=attr: f'{a}={{{m.group(1)}}}', code)

    # 10a. Strip all data-framer-* attributes (LLM keeps them despite system prompt)
    code = re.sub(r'\s+data-framer-[a-zA-Z0-9-]+=(?:"[^"]*"|\'[^\']*\'|\{[^}]*\})', '', code)
    # 10b. Strip Framer-internal / invalid HTML attrs
    _invalid_attrs = (
        "data-styles-preset", "name", "dir", "as",
        "_constraints", "parentsize", "rotation", "shadows", "borderradius",
        "visible", "locked", "withexternallayout", "layoutid",
    )
    for attr in _invalid_attrs:
        code = re.sub(rf'\s+{re.escape(attr)}="[^"]*"', '', code)
    # Strip any _private="..." attrs
    code = re.sub(r'\s+_[a-zA-Z][a-zA-Z0-9]*="[^"]*"', '', code)
    # Fix video attrs: playsinline → playsInline (boolean)
    code = re.sub(r'\bplaysinline\b', 'playsInline', code)
    # playsInline="" or playsInline="true" → playsInline (boolean shorthand)
    code = re.sub(r'\bplaysInline=(?:""|"true"|"playsInline")', 'playsInline', code)

    # 10. CSS custom properties (--*) in style objects → map known ones, strip rest
    _FRAMER_VAR_TO_CSS = {
        "--framer-text-color":      "color",
        "--framer-font-size":       "fontSize",
        "--framer-font-family":     "fontFamily",
        "--framer-line-height":     "lineHeight",
        "--framer-letter-spacing":  "letterSpacing",
        "--framer-font-style":      "fontStyle",
        "--framer-font-weight":     "fontWeight",
        "--framer-text-transform":  "textTransform",
        "--framer-text-decoration": "textDecoration",
        "--framer-text-alignment":  "textAlign",
    }

    def _replace_css_var(m: re.Match) -> str:
        prop = m.group(1)
        value = m.group(2)
        mapped = _FRAMER_VAR_TO_CSS.get(prop)
        if mapped:
            return f'{mapped}: "{value}"'
        return ""

    # Match ANY CSS custom property (--*) in style objects, not just --framer-*
    code = re.sub(
        r"""['"](--[^'"]+)['"]\s*:\s*['"]([^'"]*)['"]\s*,?\s*""",
        _replace_css_var,
        code,
    )
    # Clean up empty style objects that result from stripping all props
    code = re.sub(r'\s*style=\{\{\s*,?\s*\}\}', '', code)

    return code


def _generate_component(section: dict, visual: dict | None, model: str, client) -> str:
    source_html = (visual or {}).get("source_html", "")
    source_css = (visual or {}).get("source_css", "")
    component_name = _component_name(section.get("type", "Section"))

    # ── Deterministic path: HTML + CSS available → no AI needed ──────────────
    if source_html and source_css:
        enriched_html = inline_framer_css(source_html, source_css)
        code = html_to_jsx.convert(enriched_html, component_name)
        return _fix_nextjs_patterns(code)

    # ── AI fallback: only screenshot or only DSL data ─────────────────────────
    enriched_html = source_html  # may be empty
    has_source = bool(enriched_html or (visual and visual.get("screenshot_b64")))
    effective_model = model if has_source else "gpt-4o-mini"

    response = client.chat.completions.create(
        model=effective_model,
        messages=[
            {"role": "system", "content": _COMPONENT_SYSTEM_PROMPT},
            {"role": "user", "content": _build_component_message(section, visual, enriched_html)},
        ],
    )

    code = _strip_code_fences(response.choices[0].message.content)
    code = _fix_nextjs_patterns(code)
    code = _ensure_imports(code)
    return code


def _generate_page_tsx(sections: list[dict]) -> str:
    seen: set[str] = set()
    unique_names: list[str] = []
    for s in sections:
        name = _component_name(s["type"])
        if name not in seen:
            seen.add(name)
            unique_names.append(name)

    imports = "\n".join(f'import {n} from "@/components/{n}";' for n in unique_names)
    renders = "\n      ".join(f"<{_component_name(s['type'])} />" for s in sections)

    return f"""\
import type {{ Metadata }} from "next";
{imports}

export const metadata: Metadata = {{
  title: "Home",
}};

export default function Home() {{
  return (
    <main>
      {renders}
    </main>
  );
}}
"""


def _generate_layout_tsx(meta: dict) -> str:
    return """\
import type { Metadata } from "next";
import "../styles/globals.css";

export const metadata: Metadata = {
  title: "Home",
  description: "Generated site",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
"""


_FONT_NOISE = {
    "sans-serif", "serif", "monospace", "system-ui", "Arial",
    "Inter-Regular", "cursive", "fantasy",
}


def _clean_fonts(fonts: list[str]) -> list[str]:
    seen, result = set(), []
    for f in fonts:
        if f.startswith("var(") or "Placeholder" in f or ")" in f:
            continue
        if f in _FONT_NOISE:
            continue
        if f not in seen:
            seen.add(f)
            result.append(f)
    return result


def _generate_globals_css(meta: dict) -> str:
    return """\
*, *::before, *::after {
  box-sizing: content-box;
}

html, body {
  margin: 0;
  padding: 0;
  overflow-x: hidden;
}
"""


def generate_nextjs_scaffold(meta: dict) -> dict[str, str]:
    """
    Produces only the non-AI project files: package.json, config, CSS, layout.
    DOMPage + dom JSON are written separately in main.py.
    No AI calls.
    """
    files: dict[str, str] = {}
    files["package.json"] = json.dumps({
        "name": "scraped-site",
        "version": "0.1.0",
        "private": True,
        "scripts": {"dev": "next dev", "build": "next build", "start": "next start"},
        "dependencies": {"next": "^16", "react": "^19", "react-dom": "^19"},
        "devDependencies": {
            "@types/node": "^22",
            "@types/react": "^19",
            "@types/react-dom": "^19",
            "typescript": "^5",
        },
    }, indent=2)
    files["next.config.ts"] = """\
import type { NextConfig } from "next";
const nextConfig: NextConfig = {
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "**" },
    ],
  },
};
export default nextConfig;
"""
    files["tsconfig.json"] = json.dumps({
        "compilerOptions": {
            "target": "ES2017",
            "lib": ["dom", "dom.iterable", "esnext"],
            "allowJs": True,
            "skipLibCheck": True,
            "strict": True,
            "noEmit": True,
            "esModuleInterop": True,
            "module": "esnext",
            "moduleResolution": "bundler",
            "resolveJsonModule": True,
            "isolatedModules": True,
            "jsx": "preserve",
            "incremental": True,
            "plugins": [{"name": "next"}],
            "paths": {"@/*": ["./*"]},
        },
        "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
        "exclude": ["node_modules"],
    }, indent=2)
    files["app/layout.tsx"] = _generate_layout_tsx(meta)
    files["styles/globals.css"] = _generate_globals_css(meta)
    return files


def generate_nextjs(dsl: dict, visuals: dict | None = None, model: str = "gpt-4o") -> dict[str, str]:
    """
    DSL + visuals → complete Next.js project files.
    visuals: keyed by type_hint (e.g. "hero", "navbar") — built in main.py
    Each visual: {screenshot_b64, source_html, source_css, css}
    """
    client = get_client()
    files: dict[str, str] = {}
    sections = dsl.get("page", {}).get("sections", [])
    meta = dsl.get("meta", {})
    visuals = visuals or {}

    seen_types: set[str] = set()

    for section in sections:
        stype = section.get("type", "text")
        name = _component_name(stype)

        if stype in seen_types:
            continue
        seen_types.add(stype)

        visual = visuals.get(stype)
        files[f"components/{name}.tsx"] = _generate_component(section, visual, model, client)

    files["app/page.tsx"] = _generate_page_tsx(sections)
    files["app/layout.tsx"] = _generate_layout_tsx(meta)
    files["styles/globals.css"] = _generate_globals_css(meta)
    files["package.json"] = json.dumps({
        "name": "scraped-site",
        "version": "0.1.0",
        "private": True,
        "scripts": {"dev": "next dev", "build": "next build", "start": "next start"},
        "dependencies": {"next": "^16", "react": "^19", "react-dom": "^19"},
        "devDependencies": {
            "@types/node": "^22",
            "@types/react": "^19",
            "@types/react-dom": "^19",
            "@tailwindcss/postcss": "^4",
            "postcss": "^8",
            "tailwindcss": "^4",
            "typescript": "^5",
        },
    }, indent=2)

    files["postcss.config.js"] = """\
module.exports = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};
"""

    files["next.config.ts"] = """\
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "framerusercontent.com" },
      { protocol: "https", hostname: "**.framer.website" },
    ],
  },
};

export default nextConfig;
"""

    files["tsconfig.json"] = json.dumps({
        "compilerOptions": {
            "target": "ES2017",
            "lib": ["dom", "dom.iterable", "esnext"],
            "allowJs": True,
            "skipLibCheck": True,
            "strict": True,
            "noEmit": True,
            "esModuleInterop": True,
            "module": "esnext",
            "moduleResolution": "bundler",
            "resolveJsonModule": True,
            "isolatedModules": True,
            "jsx": "preserve",
            "incremental": True,
            "plugins": [{"name": "next"}],
            "paths": {"@/*": ["./*"]},
        },
        "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
        "exclude": ["node_modules"],
    }, indent=2)

    return files
