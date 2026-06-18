"""
Framer CSS processing: expand CSS class rules into inline styles.

For each element, merge its CSS class rules + existing inline style into
one complete style="" attribute. No Tailwind conversion — styles stay as CSS
so html_to_jsx can write them verbatim as style={{ camelCase: "value" }}.
Zero information loss.
"""
import re
from bs4 import BeautifulSoup


# Properties that are animation/interaction noise — not visual structure
_SKIP_PROPS = {
    "animation", "animation-name", "animation-duration", "animation-fill-mode",
    "animation-timing-function", "animation-iteration-count", "animation-delay",
    "transition", "transition-property", "transition-duration",
    "transition-timing-function", "transition-delay",
    "will-change",
    "content",                      # ::before/::after pseudo content
    "-webkit-font-smoothing", "-moz-osx-font-smoothing",
    "user-select", "-webkit-user-select",
    # Framer rich-text wrapper CSS defaults — CSS noise in plain React context
    "padding-inline-start",         # Framer text indent ("2ch")
    "counter-increment",            # Framer internal list counter
    "unicode-bidi",                 # always "initial"
    "margin-block",                 # always "initial"
    "margin-inline",                # always "initial"
    "border-collapse",              # table reset applied to text elements
    "border-spacing",               # table reset
    "table-layout",                 # table reset
    "word-break",                   # "normal" = CSS default
    "min-width",                    # "16ch" Framer text sizing constraint
    "list-style",                   # "none" on non-list elements
    "vertical-align",               # "top" Framer text default
}

# background-image is kept UNLESS it's a data: URI (handled per-value below)

# Framer class prefixes that are purely structural/viewport noise
_FRAMER_NOISE_PREFIXES = ("framer-", "hidden-", "svelte-")
_FRAMER_NOISE_EXACT = {"ssr-variant"}

# Framer CSS custom properties that map to real CSS properties
_FRAMER_VAR_TO_CSS = {
    "--framer-text-color":      "color",
    "--framer-font-size":       "font-size",
    "--framer-font-family":     "font-family",
    "--framer-line-height":     "line-height",
    "--framer-letter-spacing":  "letter-spacing",
    "--framer-font-style":      "font-style",
    "--framer-font-weight":     "font-weight",
    "--framer-text-transform":  "text-transform",
    "--framer-text-decoration": "text-decoration",
    "--framer-text-alignment":  "text-align",
}


def _collect_css_vars(css: str) -> dict[str, str]:
    """
    Collect ALL CSS custom property definitions from any rule (not just :root).
    Framer defines font/color vars in scoped class rules, not :root.
    Last-write-wins so more specific rules (deeper in CSS) take precedence.
    """
    vars_dict: dict[str, str] = {}
    for match in re.finditer(r"\{([^}]+)\}", css):
        for decl in match.group(1).split(";"):
            decl = decl.strip()
            if ":" not in decl:
                continue
            k, _, v = decl.partition(":")
            k = k.strip()
            v = v.strip()
            if k.startswith("--") and v:
                vars_dict[k] = v
    # Resolve any var() references within the collected vars themselves
    for k, v in list(vars_dict.items()):
        if "var(" in v:
            # Extract fallback values from nested var() references
            for _ in range(5):
                new = re.sub(r"var\([^,)]+,\s*([^)]+)\)", r"\1", v)
                if new == v:
                    break
                v = new.strip()
            vars_dict[k] = v
    return vars_dict


def _resolve_var(value: str, vars_dict: dict[str, str]) -> str:
    """
    Resolve var() references: try actual variable values first, then
    extract fallback values. Multiple passes handle nested var()s.
    """
    if "var(" not in value:
        return value

    # Substitute actual variable values when available
    if vars_dict:
        for _ in range(5):
            def _replace(m: re.Match, _vd: dict = vars_dict) -> str:
                vname = m.group(1).strip()
                fallback = m.group(2)
                if vname in _vd:
                    return _vd[vname]
                if fallback:
                    return fallback.strip()
                return m.group(0)  # keep as-is

            new = re.sub(
                r"var\(\s*([^,)]+?)(?:,\s*([^)]+?))?\s*\)",
                _replace,
                value,
            )
            if new == value:
                break
            value = new.strip()

    # Second pass: extract any remaining fallback values
    for _ in range(5):
        new = re.sub(r"var\([^,)]+,\s*([^)]+)\)", r"\1", value)
        if new == value:
            break
        value = new.strip()

    return value.strip()


def _should_skip(prop: str, value: str) -> bool:
    if prop in _SKIP_PROPS:
        return True
    # Drop values that still contain unresolved var() — useless in React inline styles
    if "var(" in value:
        return True
    # Drop background-image only when it embeds a data: URI (huge + breaks JSX strings)
    if prop == "background-image" and "data:" in value:
        return True
    return False


def _parse_css_rules(css: str, vars_dict: dict[str, str]) -> dict[str, dict[str, str]]:
    """
    Parse .classname { ... } rules from a CSS string.
    Also recurses into @media blocks so desktop-specific rules are captured.
    Extracts the leaf class name from compound/descendant selectors.
    """
    rules: dict[str, dict[str, str]] = {}

    def _parse_block(block: str) -> None:
        for match in re.finditer(r"\.([a-zA-Z][\w-]*)\s*\{([^}]+)\}", block):
            cls = match.group(1)
            props: dict[str, str] = {}
            for decl in match.group(2).split(";"):
                decl = decl.strip()
                if ":" not in decl:
                    continue
                k, _, v = decl.partition(":")
                k = k.strip().lower()
                if k.startswith("--"):
                    continue  # CSS custom property definitions are Framer internals
                v = _resolve_var(v.strip(), vars_dict)
                if k and v and not _should_skip(k, v):
                    props[k] = v
            if props:
                if cls in rules:
                    rules[cls].update(props)
                else:
                    rules[cls] = props

    _parse_block(css)

    for media_match in re.finditer(r"@media[^{]+\{((?:[^{}]|\{[^}]*\})*)\}", css):
        _parse_block(media_match.group(1))

    return rules


def _collapse_char_spans(soup) -> None:
    """
    Framer renders text character-by-character for animations.
    Collapse 3+ single-char spans inside a parent into plain text.
    """
    for parent in soup.find_all(True):
        children = list(parent.children)
        char_spans = [
            c for c in children
            if getattr(c, "name", None) == "span"
            and len(c.get_text()) <= 2
            and not c.get("data-framer-name")
            and not c.get("id")
        ]
        if len(char_spans) >= 3:
            full_text = parent.get_text()
            for c in list(parent.children):
                c.extract()
            parent.append(full_text)


def _fix_framer_flex_layout(soup) -> None:
    """
    Framer uses flex items with `width: 1px; flex: 1 0 0px` for equal-width columns
    in a flex ROW layout. If the parent container was captured with flex-flow: column
    (e.g., from a scroll animation state), those items collapse to 1px wide and stack
    vertically. Detect and fix by switching the parent to row.
    """
    for tag in soup.find_all(True):
        style = tag.get("style", "") or ""
        # Must be a flex column container
        if "display" not in style or "flex" not in style:
            continue
        if "column" not in style:
            continue
        if not re.search(r"(?:flex-flow|flex-direction)\s*:\s*column", style):
            continue

        # Count direct children that look like flex row items (width: 1px, flex-grow)
        row_item_count = 0
        for child in tag.children:
            cs = getattr(child, "attrs", {}).get("style", "") or ""
            if "width: 1px" in cs and re.search(r"flex:\s*1\b", cs):
                row_item_count += 1

        if row_item_count >= 2:
            tag["style"] = re.sub(
                r"(flex-flow|flex-direction)\s*:\s*column",
                lambda m: f"{m.group(1)}: row",
                style,
            )


def _remove_decorative_elements(soup) -> None:
    """Remove elements whose sole purpose is a data: URI background-image."""
    for tag in soup.find_all(True):
        style = tag.get("style", "")
        if "data:image" in style:
            if re.search(r"background-image\s*:[^;]+data:image", style):
                tag.decompose()


def inline_framer_css(html: str, css: str) -> str:
    """
    Merge every CSS class rule + existing inline style into one style=""
    attribute on each element. Inline style wins on conflicts.

    Returns HTML where every element has a complete, self-contained style=""
    attribute — ready for html_to_jsx to convert to style={{ ... }}.
    """
    root_vars = _collect_css_vars(css)
    rules = _parse_css_rules(css, root_vars)
    soup = BeautifulSoup(html, "lxml")

    _collapse_char_spans(soup)
    _remove_decorative_elements(soup)
    _fix_framer_flex_layout(soup)

    for tag in soup.find_all(True):
        classes: list[str] = tag.get("class") or []

        # ── Merge: class rules first, then existing inline style wins ─────────
        merged: dict[str, str] = {}

        for cls in classes:
            if cls in rules:
                merged.update(rules[cls])

        existing = tag.get("style", "")
        if existing:
            for decl in existing.split(";"):
                decl = decl.strip()
                if ":" not in decl:
                    continue
                k, _, v = decl.partition(":")
                k = k.strip().lower()
                v = _resolve_var(v.strip(), root_vars)

                if k.startswith("--"):
                    # Map known --framer-* vars to real CSS properties
                    mapped = _FRAMER_VAR_TO_CSS.get(k)
                    if mapped:
                        if v and not _should_skip(mapped, v):
                            merged[mapped] = v
                    continue

                # Skip near-zero opacity — Framer animation initial state.
                # The CSS class rule's opacity (if any) takes effect instead,
                # defaulting to 1 when no class rule sets it.
                if k == "opacity":
                    try:
                        if float(v) < 0.1:
                            continue
                    except ValueError:
                        pass

                if k and v and not _should_skip(k, v):
                    merged[k] = v

        # ── Write merged styles back as style="" ──────────────────────────────
        if merged:
            tag["style"] = "; ".join(f"{k}: {v}" for k, v in merged.items())
        elif "style" in tag.attrs:
            del tag.attrs["style"]

        # ── Strip Framer-internal class noise ─────────────────────────────────
        clean = [
            c for c in classes
            if c not in _FRAMER_NOISE_EXACT
            and not any(c.startswith(p) for p in _FRAMER_NOISE_PREFIXES)
        ]
        if clean:
            tag["class"] = clean
        elif "class" in tag.attrs:
            del tag.attrs["class"]

    return str(soup)
