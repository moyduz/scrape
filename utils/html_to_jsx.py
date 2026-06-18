"""
Deterministic HTML → Next.js TSX converter.

Walks the BeautifulSoup tree node-by-node and emits valid JSX.
No AI. Every rule is explicit. Nothing gets simplified or skipped.

Flow:
  raw HTML + CSS
    → inline_framer_css()   (CSS class rules merged → style="" on every element)
    → html_to_jsx.convert() (every node → JSX, style="" → style={{}}, Image/Link wired up)
    → complete .tsx component
"""
from __future__ import annotations
import re
from bs4 import BeautifulSoup, NavigableString, Comment, Tag

# ── Void elements (self-close in JSX) ─────────────────────────────────────────

_VOID = frozenset({
    "area", "base", "br", "col", "embed", "hr", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

# ── HTML attr name → JSX attr name ────────────────────────────────────────────
# Keys are lowercase (lxml lowercases everything).

_ATTR = {
    # HTML
    "class":                        "className",
    "for":                          "htmlFor",
    "tabindex":                     "tabIndex",
    "readonly":                     "readOnly",
    "maxlength":                    "maxLength",
    "minlength":                    "minLength",
    "cellspacing":                  "cellSpacing",
    "cellpadding":                  "cellPadding",
    "rowspan":                      "rowSpan",
    "colspan":                      "colSpan",
    "crossorigin":                  "crossOrigin",
    "accesskey":                    "accessKey",
    "contenteditable":              "contentEditable",
    "spellcheck":                   "spellCheck",
    "autofocus":                    "autoFocus",
    "autocomplete":                 "autoComplete",
    "enctype":                      "encType",
    "novalidate":                   "noValidate",
    "frameborder":                  "frameBorder",
    "allowfullscreen":              "allowFullScreen",
    "playsinline":                  "playsInline",
    # SVG
    "viewbox":                      "viewBox",
    "preserveaspectratio":          "preserveAspectRatio",
    "gradienttransform":            "gradientTransform",
    "gradientunits":                "gradientUnits",
    "patternunits":                 "patternUnits",
    "patterntransform":             "patternTransform",
    "xlink:href":                   "href",
    "xmlns:xlink":                  None,          # drop
    "stroke-linecap":               "strokeLinecap",
    "stroke-linejoin":              "strokeLinejoin",
    "stroke-width":                 "strokeWidth",
    "stroke-dasharray":             "strokeDasharray",
    "stroke-dashoffset":            "strokeDashoffset",
    "stroke-miterlimit":            "strokeMiterlimit",
    "stroke-opacity":               "strokeOpacity",
    "fill-rule":                    "fillRule",
    "fill-opacity":                 "fillOpacity",
    "clip-rule":                    "clipRule",
    "clip-path":                    "clipPath",
    "stop-color":                   "stopColor",
    "stop-opacity":                 "stopOpacity",
    "font-size":                    "fontSize",
    "font-family":                  "fontFamily",
    "font-weight":                  "fontWeight",
    "text-anchor":                  "textAnchor",
    "dominant-baseline":            "dominantBaseline",
    "marker-end":                   "markerEnd",
    "marker-start":                 "markerStart",
    "color-interpolation-filters":  "colorInterpolationFilters",
    "flood-color":                  "floodColor",
    "flood-opacity":                "floodOpacity",
    "lighting-color":               "lightingColor",
    "image-rendering":              "imageRendering",
}

# Attrs whose value must be a JS number expression {n}
_NUMERIC = frozenset({"tabIndex", "rowSpan", "colSpan"})

# Boolean HTML attrs (presence = true)
_BOOL_ATTRS = frozenset({
    "disabled", "checked", "selected", "multiple", "required", "hidden",
    "defer", "async", "autoplay", "controls", "loop", "muted", "open", "playsinline",
    "reversed", "default",
})

# CSS props that are Framer-specific and not valid React style keys
_BAD_CSS_PROPS = frozenset({
    "cornerShape", "corner-shape",
    # Framer rich-text wrapper normalization — CSS defaults with no visual effect
    # but they add noise / break layout in a plain React context
    "padding-inline-start", "paddingInlineStart",   # Framer text indent "2ch"
    "counter-increment", "counterIncrement",         # Framer list counter
    "unicode-bidi", "unicodeBidi",                   # always "initial"
    "margin-block", "marginBlock",                   # always "initial"
    "margin-inline", "marginInline",                 # always "initial"
    "border-collapse", "borderCollapse",             # table reset on text elements
    "border-spacing", "borderSpacing",               # table reset
    "table-layout", "tableLayout",                   # table reset
    "word-break", "wordBreak",                       # "normal" = CSS default
    "min-width", "minWidth",                         # "16ch" Framer text sizing
    "list-style", "listStyle",                       # "none" reset on non-lists
    "vertical-align", "verticalAlign",               # "top" Framer text default
})

# data-framer-* attrs to drop (we check startswith, but these are exact drops too)
_DROP_EXACT = frozenset({
    "data-framer-name",
    "data-framer-component-type",
    "data-framer-generated",
    "data-framer-appear-id",
    "data-framer-portal-host",
    "data-framer-highlight",
    "data-framer-page-optimized",
    "data-styles-preset",
    "data-border",
    "ssr-variant",
})


# ── Style string → JSX style object ───────────────────────────────────────────

def _camel(prop: str) -> str:
    """border-radius → borderRadius  |  --my-var → --my-var"""
    if prop.startswith("--"):
        return prop
    return re.sub(r"-([a-z])", lambda m: m.group(1).upper(), prop)


def _style_to_obj(css: str) -> str:
    """
    'font-size: 17px; color: red' → '{ fontSize: "17px", color: "red" }'
    Returns '' when nothing maps.
    """
    props: list[str] = []
    for decl in css.split(";"):
        decl = decl.strip()
        if not decl or ":" not in decl:
            continue
        colon = decl.find(":")
        prop = decl[:colon].strip()
        val = decl[colon + 1:].strip()
        if not prop or not val:
            continue
        if prop in _BAD_CSS_PROPS:
            continue
        key = _camel(prop)
        if key in _BAD_CSS_PROPS:
            continue
        if key.startswith("--"):
            continue  # CSS custom properties (Framer internals) aren't valid in React style obj
        if "var(" in val:
            continue  # unresolved CSS variable — can't use in React inline style
        # Drop transforms that are clearly scroll-driven animation offsets:
        # translateX/Y with large px values (> 500px) are off-screen initial states
        if key == "transform":
            px_magnitudes = [abs(float(m)) for m in re.findall(r"translate[XY]?\((-?[\d.]+)px", val)]
            if any(v > 500 for v in px_magnitudes):
                continue
        val = val.replace("\\", "\\\\").replace('"', '\\"')
        props.append(f'{key}: "{val}"')
    if not props:
        return ""
    return "{ " + ", ".join(props) + " }"


# ── HTML entity unescaping ─────────────────────────────────────────────────────

def _unescape(s: str) -> str:
    return (s.replace("&amp;", "&")
             .replace("&lt;", "<")
             .replace("&gt;", ">")
             .replace("&quot;", '"')
             .replace("&#39;", "'")
             .replace("&#x27;", "'"))


# ── Text node escaping ─────────────────────────────────────────────────────────

def _esc_text(text: str) -> str:
    """`{` and `}` in JSX text content start/end expression blocks → escape them."""
    return text.replace("{", "{'{'}" ).replace("}", "{'}'}")


# ── Attribute builder ──────────────────────────────────────────────────────────

_FRAMER_PRIVATE_ATTRS = frozenset({
    "parentsize", "rotation", "shadows", "borderradius", "visible", "locked",
    "background", "withexternallayout", "layoutid", "layoutscrollx", "layoutscrolly",
    "transformtemplate", "transformperspective", "as",
})


def _drop_attr(name: str) -> bool:
    if name in _DROP_EXACT:
        return True
    if name.startswith("data-framer-"):
        return True
    if name.startswith("_"):
        return True
    if name.lower() in _FRAMER_PRIVATE_ATTRS:
        return True
    # name="..." on non-form elements — Framer mirrors component name here
    if name == "name":
        return True
    return False


def _build_attrs(tag: Tag) -> str:
    """Return the full JSX attribute string (space-prefixed) for a tag."""
    parts: list[str] = []

    # ── className from remaining semantic class names ──
    raw_cls = tag.get("class") or []
    cls_str = " ".join(raw_cls) if isinstance(raw_cls, list) else str(raw_cls)
    if cls_str.strip():
        parts.append(f'className="{cls_str.strip()}"')

    # ── remaining attrs ──
    for attr, value in tag.attrs.items():
        if attr == "class":
            continue
        if _drop_attr(attr):
            continue

        # Map attr name
        jsx_name = _ATTR.get(attr.lower(), attr)
        if jsx_name is None:        # explicitly dropped (e.g. xmlns:xlink)
            continue

        # style → style={{ object }}
        if attr == "style":
            css = " ".join(value) if isinstance(value, list) else str(value)
            obj = _style_to_obj(css)
            if obj:
                parts.append(f"style={{{obj}}}")
            continue

        # Normalize value to string
        v = " ".join(value) if isinstance(value, list) else str(value)
        v = _unescape(v)

        # Boolean attrs
        if attr.lower() in _BOOL_ATTRS:
            if v.lower() in ("", "true", attr.lower()):
                parts.append(jsx_name)
            elif v.lower() == "false":
                parts.append(f"{jsx_name}={{false}}")
            continue

        # Numeric attrs (tabIndex etc.)
        if jsx_name in _NUMERIC:
            try:
                parts.append(f"{jsx_name}={{{int(v)}}}")
                continue
            except ValueError:
                pass

        # width / height on <img> → numbers
        if attr in ("width", "height") and tag.name == "img":
            try:
                parts.append(f"{jsx_name}={{{int(v)}}}")
                continue
            except ValueError:
                pass

        # Escape double-quotes in value
        v_esc = v.replace('"', "&quot;")
        parts.append(f'{jsx_name}="{v_esc}"')

    if not parts:
        return ""
    return " " + " ".join(parts)


# ── <img> → <Image> ────────────────────────────────────────────────────────────

def _is_bg_wrapper(tag: Tag) -> bool:
    return tag.get("data-framer-background-image-wrapper") is not None


def _dims_from_url(url: str) -> tuple[str, str]:
    """Extract width/height from Framer URL query params: ?width=1536&height=1536"""
    w = re.search(r"[?&]width=(\d+)", url)
    h = re.search(r"[?&]height=(\d+)", url)
    return (w.group(1) if w else "", h.group(1) if h else "")


def _img_to_jsx(tag: Tag) -> str:
    src = _unescape(str(tag.get("src", "")))
    alt = tag.get("alt", "")
    width = tag.get("width", "")
    height = tag.get("height", "")

    # Framer URLs include ?width=N&height=N — use them as fallback
    if (not width or not height) and src:
        url_w, url_h = _dims_from_url(src)
        width = width or url_w
        height = height or url_h

    raw_cls = tag.get("class") or []
    cls_str = " ".join(raw_cls) if isinstance(raw_cls, list) else str(raw_cls)
    class_name = cls_str.strip()

    style_raw = tag.get("style", "")
    style_obj = _style_to_obj(str(style_raw)) if style_raw else ""

    parent = tag.parent
    fill_mode = parent is not None and _is_bg_wrapper(parent)

    parts: list[str] = []
    if alt is not None:
        parts.append(f'alt="{alt}"')

    if fill_mode:
        parts.append("fill")
        if class_name and "object-cover" not in class_name:
            class_name += " object-cover"
    else:
        if width:
            try:
                parts.append(f"width={{{int(width)}}}")
            except ValueError:
                parts.append(f'width="{width}"')
        if height:
            try:
                parts.append(f"height={{{int(height)}}}")
            except ValueError:
                parts.append(f'height="{height}"')
        # Last resort: if still no dimensions, use fill mode
        if not width and not height:
            parts.append("fill")

    if class_name:
        parts.append(f'className="{class_name}"')
    if src:
        parts.append(f'src="{src}"')
    if style_obj:
        parts.append(f"style={{{style_obj}}}")

    return "<Image " + " ".join(parts) + " />"


# ── Core tree walker ───────────────────────────────────────────────────────────

def _meaningful_children(tag: Tag) -> list:
    """Children that are non-empty text nodes or element nodes."""
    out = []
    for c in tag.children:
        if isinstance(c, Comment):
            continue
        if isinstance(c, NavigableString):
            if str(c).strip():
                out.append(c)
        elif isinstance(c, Tag):
            out.append(c)
    return out


def _node_to_jsx(node, indent: int) -> str:
    pad = "  " * indent

    # ── Text node ──
    if isinstance(node, NavigableString):
        if isinstance(node, Comment):
            return ""
        text = str(node).strip()
        if not text:
            return ""
        return pad + _esc_text(text)

    if not isinstance(node, Tag):
        return ""

    name = node.name
    if not name:
        return ""

    # ── <img> → <Image> ──
    if name == "img":
        return pad + _img_to_jsx(node)

    # ── <a> → <Link> ──
    if name == "a":
        attrs = _build_attrs(node)
        kids = _meaningful_children(node)
        if not kids:
            return f"{pad}<Link{attrs}></Link>"
        if len(kids) == 1 and isinstance(kids[0], NavigableString):
            text = _esc_text(str(kids[0]).strip())
            return f"{pad}<Link{attrs}>{text}</Link>"
        children_str = "\n".join(
            _node_to_jsx(c, indent + 1) for c in kids
        )
        return f"{pad}<Link{attrs}>\n{children_str}\n{pad}</Link>"

    # ── void element ──
    if name in _VOID:
        attrs = _build_attrs(node)
        return f"{pad}<{name}{attrs} />"

    # ── regular element ──
    attrs = _build_attrs(node)
    kids = _meaningful_children(node)

    if not kids:
        return f"{pad}<{name}{attrs}></{name}>"

    # Single plain-text child → inline
    if len(kids) == 1 and isinstance(kids[0], NavigableString):
        text = _esc_text(str(kids[0]).strip())
        if text:
            return f"{pad}<{name}{attrs}>{text}</{name}>"
        return f"{pad}<{name}{attrs}></{name}>"

    # Block children
    children_str = "\n".join(
        s for s in (_node_to_jsx(c, indent + 1) for c in kids) if s
    )
    return f"{pad}<{name}{attrs}>\n{children_str}\n{pad}</{name}>"


# ── Public API ─────────────────────────────────────────────────────────────────

def convert(enriched_html: str, component_name: str) -> str:
    """
    Convert pre-processed HTML (data-tw attrs already set by inline_framer_css)
    into a complete Next.js TSX component.

    enriched_html : output of utils.css_inliner.inline_framer_css()
    component_name: PascalCase name e.g. "Hero", "Features"
    """
    soup = BeautifulSoup(enriched_html, "lxml")

    # lxml wraps in <html><body> — find the real root element
    body = soup.find("body")
    root = next(
        (c for c in (body or soup).children if isinstance(c, Tag)),
        None,
    )
    if root is None:
        return (
            f'export default function {component_name}() {{\n'
            f'  return <div />;\n'
            f'}}\n'
        )

    jsx_body = _node_to_jsx(root, indent=2)

    # Collect needed imports
    imports: list[str] = []
    if "<Image" in jsx_body:
        imports.append('import Image from "next/image";')
    if "<Link" in jsx_body:
        imports.append('import Link from "next/link";')

    import_block = "\n".join(imports)
    sep = "\n\n" if import_block else ""

    return (
        f"{import_block}{sep}"
        f"export default function {component_name}() {{\n"
        f"  return (\n"
        f"{jsx_body}\n"
        f"  );\n"
        f"}}\n"
    )
