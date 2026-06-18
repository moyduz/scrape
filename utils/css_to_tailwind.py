"""
Deterministic CSS property → Tailwind class converter.
Handles the full set of Framer-generated inline styles.
Returns None for properties it can't map (caller can use style={{ }} fallback).
"""
import re


# ── Lookup tables ──────────────────────────────────────────────────────────────

_DISPLAY = {
    "flex": "flex", "inline-flex": "inline-flex", "grid": "grid",
    "block": "block", "inline-block": "inline-block", "inline": "inline",
    "none": "hidden", "contents": "contents", "table": "table",
    "table-cell": "table-cell", "table-row": "table-row",
}
_FLEX_DIR = {
    "row": "flex-row", "column": "flex-col",
    "row-reverse": "flex-row-reverse", "column-reverse": "flex-col-reverse",
}
_ALIGN_ITEMS = {
    "center": "items-center", "flex-start": "items-start", "start": "items-start",
    "flex-end": "items-end", "end": "items-end", "baseline": "items-baseline",
    "stretch": "items-stretch",
}
_JUSTIFY = {
    "center": "justify-center", "space-between": "justify-between",
    "flex-start": "justify-start", "start": "justify-start",
    "flex-end": "justify-end", "end": "justify-end",
    "space-around": "justify-around", "space-evenly": "justify-evenly",
}
_ALIGN_CONTENT = {
    "center": "content-center", "space-between": "content-between",
    "flex-start": "content-start", "flex-end": "content-end",
    "space-around": "content-around", "stretch": "content-stretch",
}
_POSITION = {
    "fixed": "fixed", "absolute": "absolute", "relative": "relative",
    "sticky": "sticky", "static": "static",
}
_OVERFLOW = {
    "hidden": "overflow-hidden", "visible": "overflow-visible",
    "scroll": "overflow-scroll", "auto": "overflow-auto", "clip": "overflow-clip",
}
_OVERFLOW_X = {k: v.replace("overflow-", "overflow-x-") for k, v in _OVERFLOW.items()}
_OVERFLOW_Y = {k: v.replace("overflow-", "overflow-y-") for k, v in _OVERFLOW.items()}
_FONT_WEIGHT = {
    "100": "font-thin", "200": "font-extralight", "300": "font-light",
    "400": "font-normal", "500": "font-medium", "600": "font-semibold",
    "700": "font-bold", "800": "font-extrabold", "900": "font-black",
}
_TEXT_ALIGN = {
    "left": "text-left", "center": "text-center", "right": "text-right",
    "justify": "text-justify",
}
_OBJECT_FIT = {
    "cover": "object-cover", "contain": "object-contain",
    "fill": "object-fill", "none": "object-none", "scale-down": "object-scale-down",
}
_BLEND = {
    "multiply": "mix-blend-multiply", "screen": "mix-blend-screen",
    "overlay": "mix-blend-overlay", "darken": "mix-blend-darken",
    "lighten": "mix-blend-lighten", "normal": "mix-blend-normal",
}
_WHITESPACE = {
    "nowrap": "whitespace-nowrap", "pre": "whitespace-pre",
    "pre-wrap": "whitespace-pre-wrap", "pre-line": "whitespace-pre-line",
    "normal": "whitespace-normal",
}
_CURSOR = {
    "pointer": "cursor-pointer", "default": "cursor-default",
    "not-allowed": "cursor-not-allowed", "grab": "cursor-grab",
    "auto": "cursor-auto", "text": "cursor-text",
}
_ISOLATION = {"isolate": "isolate", "auto": "isolation-auto"}
_APPEARANCE = {"none": "appearance-none", "auto": "appearance-auto"}
_VISIBILITY = {"hidden": "invisible", "visible": "visible", "collapse": "collapse"}
_FLEX_WRAP = {
    "wrap": "flex-wrap", "nowrap": "flex-nowrap", "wrap-reverse": "flex-wrap-reverse",
}
_VERTICAL_ALIGN = {
    "top": "align-top", "middle": "align-middle", "bottom": "align-bottom",
    "baseline": "align-baseline", "text-top": "align-text-top",
    "text-bottom": "align-text-bottom",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _norm_color(v: str) -> str:
    """Normalise colour value for use inside Tailwind brackets: remove spaces."""
    return re.sub(r"\s+", "", v)


def _color_class(prefix: str, value: str) -> str:
    """bg-[…] or text-[…] etc, with shortcuts for black/white/transparent."""
    n = _norm_color(value)
    shortcuts = {
        "rgb(255,255,255)": "white", "#fff": "white", "#ffffff": "white",
        "rgb(0,0,0)": "black", "#000": "black", "#000000": "black",
        "transparent": "transparent",
    }
    if n in shortcuts:
        return f"{prefix}-{shortcuts[n]}"
    # rgba with nice alpha → e.g. bg-white/60
    m = re.match(r"rgba\((\d+),(\d+),(\d+),([\d.]+)\)", n)
    if m:
        r, g, b, a = int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4))
        if r == g == b == 255:
            return f"{prefix}-white/{int(a * 100)}"
        if r == g == b == 0:
            return f"{prefix}-black/{int(a * 100)}"
    return f"{prefix}-[{n}]"


def _px_val(v: str) -> str:
    """0px → 0, 0 → 0, otherwise keep as-is."""
    if v in ("0", "0px", "0%", "0em", "0rem"):
        return "0"
    return v


def _size_class(prefix: str, value: str) -> str:
    v = value.strip()
    if v in ("100%",): return f"{prefix}-full"
    if v == "auto": return f"{prefix}-auto"
    if v in ("100vh", "100svh"): return f"{prefix}-screen"
    if v in ("min-content",): return f"{prefix}-min"
    if v in ("max-content",): return f"{prefix}-max"
    if v in ("fit-content",): return f"{prefix}-fit"
    if v in ("0", "0px"): return f"{prefix}-0"
    # Common fractions
    pct_m = re.match(r"([\d.]+)%", v)
    if pct_m:
        pct = float(pct_m.group(1))
        for val, frac in [(25, "1/4"), (33.33, "1/3"), (50, "1/2"),
                          (66.67, "2/3"), (75, "3/4"), (100, "full")]:
            if abs(pct - val) < 0.1:
                return f"{prefix}-{frac}"
    return f"{prefix}-[{v}]"


def _spacing(prefix: str, value: str) -> list[str]:
    """Unpack CSS shorthand padding/margin into Tailwind classes."""
    parts = value.split()
    if len(parts) == 1:
        v = _px_val(parts[0])
        return [f"{prefix}-[{v}]"] if v != "0" else [f"{prefix}-0"]
    if len(parts) == 2:
        y, x = _px_val(parts[0]), _px_val(parts[1])
        out = []
        out.append(f"{prefix}y-[{y}]" if y != "0" else f"{prefix}y-0")
        out.append(f"{prefix}x-[{x}]" if x != "0" else f"{prefix}x-0")
        return out
    if len(parts) == 4:
        labels = ["t", "r", "b", "l"]
        return [
            (f"{prefix}{lbl}-[{_px_val(p)}]" if _px_val(p) != "0" else f"{prefix}{lbl}-0")
            for lbl, p in zip(labels, parts)
        ]
    return [f"{prefix}-[{value}]"]


def _inset(value: str) -> list[str]:
    parts = value.split()
    if len(parts) == 1:
        v = _px_val(parts[0])
        return ["inset-0"] if v == "0" else [f"inset-[{v}]"]
    if len(parts) == 2:
        y, x = _px_val(parts[0]), _px_val(parts[1])
        oy = "inset-y-0" if y == "0" else f"inset-y-[{y}]"
        ox = "inset-x-0" if x == "0" else f"inset-x-[{x}]"
        return [oy, ox]
    return [f"inset-[{value}]"]


def _transform(value: str) -> list[str]:
    classes = []
    # translateX
    for sign, cls in [("-50%", "-translate-x-1/2"), ("50%", "translate-x-1/2")]:
        if f"translateX({sign})" in value:
            classes.append(cls)
            value = value.replace(f"translateX({sign})", "")
    m = re.search(r"translateX\(([-\d.]+(?:px|rem|em|%))\)", value)
    if m:
        classes.append(f"translate-x-[{m.group(1)}]")
        value = value[:m.start()] + value[m.end():]
    # translateY
    for sign, cls in [("-50%", "-translate-y-1/2"), ("50%", "translate-y-1/2")]:
        if f"translateY({sign})" in value:
            classes.append(cls)
            value = value.replace(f"translateY({sign})", "")
    m = re.search(r"translateY\(([-\d.]+(?:px|rem|em|%))\)", value)
    if m:
        classes.append(f"translate-y-[{m.group(1)}]")
        value = value[:m.start()] + value[m.end():]
    # scale
    m = re.search(r"scale\(([\d.]+)\)", value)
    if m:
        pct = int(float(m.group(1)) * 100)
        classes.append(f"scale-[{pct}]" if pct not in (0, 50, 75, 90, 95, 100, 105, 110, 125, 150) else f"scale-{pct}")
    # rotate
    m = re.search(r"rotate\(([-\d.]+)deg\)", value)
    if m:
        deg = m.group(1)
        classes.append(f"rotate-[{deg}deg]")
    return classes


def _border_radius(value: str) -> str:
    v = value.strip()
    if v in ("9999px", "9999rem", "100%", "50%"): return "rounded-full"
    if v in ("0", "0px"): return "rounded-none"
    # All-same shorthand
    parts = v.split()
    if len(parts) == 1:
        return f"rounded-[{v}]"
    return f"rounded-[{v}]"


def _backdrop_filter(value: str) -> str | None:
    m = re.match(r"blur\(([\d.]+px)\)", value.strip())
    if m:
        return f"backdrop-blur-[{m.group(1)}]"
    if value.strip() == "none":
        return "backdrop-blur-none"
    return None


def _flex_shorthand(value: str) -> list[str]:
    v = value.strip()
    if v == "none": return ["flex-none"]
    if v == "auto": return ["flex-auto"]
    if v == "1": return ["flex-1"]
    if v == "1 1 0%": return ["flex-1"]
    if v == "1 1 auto": return ["flex-auto"]
    # "0 0 auto" = flex-none
    m = re.match(r"([\d.]+)\s+([\d.]+)\s+(.+)", v)
    if m:
        grow, shrink, basis = m.groups()
        classes = []
        if grow == "1" and shrink == "1":
            classes.append("flex-1")
        elif grow == "0" and shrink == "0":
            classes.append("flex-none")
            if basis not in ("auto", "0%", "0"):
                classes.append(_size_class("w", basis))
        else:
            if grow != "1":
                classes.append(f"grow-[{grow}]" if grow != "0" else "grow-0")
            if shrink != "1":
                classes.append(f"shrink-[{shrink}]" if shrink != "0" else "shrink-0")
            if basis not in ("auto", "0%"):
                classes.append(_size_class("basis", basis))
        return classes
    return []


def _grid_template(axis: str, value: str) -> str:
    # repeat(N, 1fr)
    m = re.match(r"repeat\((\d+),\s*1fr\)", value)
    if m:
        return f"grid-{'cols' if axis == 'columns' else 'rows'}-{m.group(1)}"
    return f"grid-{'cols' if axis == 'columns' else 'rows'}-[{value}]"


# ── Main converter ──────────────────────────────────────────────────────────────

def css_prop_to_tailwind(prop: str, value: str) -> list[str]:
    """
    Convert one CSS property + value to a list of Tailwind classes.
    Returns [] if the property cannot be mapped.
    """
    prop = prop.strip().lower()
    value = value.strip()
    if not value or value in ("initial", "inherit", "revert", "unset"):
        return []

    # ── Layout ─────────────────────────────────────────────────────────────────
    if prop == "display":
        c = _DISPLAY.get(value)
        return [c] if c else []

    if prop == "flex-direction":
        c = _FLEX_DIR.get(value)
        return [c] if c else []

    if prop == "align-items":
        c = _ALIGN_ITEMS.get(value)
        return [c] if c else []

    if prop == "align-content":
        c = _ALIGN_CONTENT.get(value)
        return [c] if c else []

    if prop == "justify-content":
        c = _JUSTIFY.get(value)
        return [c] if c else []

    if prop in ("place-content",):
        # "place-content: center" → justify-center items-center
        parts = value.split()
        out = []
        if len(parts) >= 1:
            c = _JUSTIFY.get(parts[0])
            if c: out.append(c)
            c = _ALIGN_CONTENT.get(parts[0])
            if c: out.append(c)
        return out

    if prop == "flex-wrap":
        c = _FLEX_WRAP.get(value)
        return [c] if c else []

    if prop == "flex":
        return _flex_shorthand(value)

    if prop in ("flex-grow", "flex-expand"):
        if value == "0": return ["grow-0"]
        if value == "1": return ["grow"]
        return [f"grow-[{value}]"]

    if prop == "flex-shrink":
        if value == "0": return ["shrink-0"]
        if value == "1": return ["shrink"]
        return [f"shrink-[{value}]"]

    if prop == "flex-basis":
        return [_size_class("basis", value)]

    if prop == "gap":
        parts = value.split()
        if len(parts) == 1:
            return [f"gap-[{parts[0]}]"]
        if len(parts) == 2:
            return [f"gap-y-[{parts[0]}]", f"gap-x-[{parts[1]}]"]

    if prop == "column-gap":
        return [f"gap-x-[{value}]"]

    if prop == "row-gap":
        return [f"gap-y-[{value}]"]

    if prop == "order":
        return [f"order-[{value}]"]

    if prop == "grid-template-columns":
        return [_grid_template("columns", value)]

    if prop == "grid-template-rows":
        return [_grid_template("rows", value)]

    if prop == "grid-column":
        return [f"col-[{value}]"]

    if prop == "grid-row":
        return [f"row-[{value}]"]

    if prop == "grid-column-span":
        return [f"col-span-{value}"]

    # ── Positioning ─────────────────────────────────────────────────────────────
    if prop == "position":
        c = _POSITION.get(value)
        return [c] if c else []

    if prop == "inset":
        return _inset(value)

    if prop == "inset-inline":
        return _inset(value)

    for side, tw in [("top", "top"), ("right", "right"), ("bottom", "bottom"), ("left", "left")]:
        if prop == side:
            v = _px_val(value)
            if v == "0": return [f"{tw}-0"]
            if v == "50%": return [f"{tw}-1/2"]
            if v == "auto": return []
            return [f"{tw}-[{v}]"]

    if prop == "z-index":
        return [f"z-[{value}]"]

    # ── Sizing ──────────────────────────────────────────────────────────────────
    if prop == "width":
        return [_size_class("w", value)]

    if prop == "height":
        return [_size_class("h", value)]

    if prop == "min-width":
        if value in ("0", "0px"): return ["min-w-0"]
        return [_size_class("min-w", value)]

    if prop == "max-width":
        if value == "none": return ["max-w-none"]
        return [_size_class("max-w", value)]

    if prop == "min-height":
        if value in ("0", "0px"): return ["min-h-0"]
        return [_size_class("min-h", value)]

    if prop == "max-height":
        return [_size_class("max-h", value)]

    if prop == "aspect-ratio":
        if value == "1 / 1": return ["aspect-square"]
        if value == "16 / 9": return ["aspect-video"]
        return [f"aspect-[{value.replace(' ', '')}]"]

    # ── Spacing ─────────────────────────────────────────────────────────────────
    if prop == "padding":
        return _spacing("p", value)

    for side, lbl in [("padding-top", "t"), ("padding-right", "r"),
                       ("padding-bottom", "b"), ("padding-left", "l")]:
        if prop == side:
            v = _px_val(value)
            return [f"p{lbl}-0"] if v == "0" else [f"p{lbl}-[{v}]"]

    if prop == "margin":
        return _spacing("m", value)

    for side, lbl in [("margin-top", "t"), ("margin-right", "r"),
                       ("margin-bottom", "b"), ("margin-left", "l")]:
        if prop == side:
            if value == "auto": return [f"m{lbl}-auto"]
            v = _px_val(value)
            return [f"m{lbl}-0"] if v == "0" else [f"m{lbl}-[{v}]"]

    # ── Typography ──────────────────────────────────────────────────────────────
    if prop == "font-size":
        return [f"text-[{value}]"]

    if prop == "font-weight":
        c = _FONT_WEIGHT.get(value)
        return [c] if c else [f"font-[{value}]"]

    if prop == "line-height":
        if value == "1": return ["leading-none"]
        if value in ("1.5", "1.5em"): return ["leading-normal"]
        return [f"leading-[{value}]"]

    if prop == "letter-spacing":
        return [f"tracking-[{value}]"]

    if prop == "text-align":
        c = _TEXT_ALIGN.get(value)
        return [c] if c else []

    if prop == "text-transform":
        return {"uppercase": ["uppercase"], "lowercase": ["lowercase"],
                "capitalize": ["capitalize"], "none": ["normal-case"]}.get(value, [])

    if prop == "text-decoration":
        if "underline" in value: return ["underline"]
        if "line-through" in value: return ["line-through"]
        if "none" in value: return ["no-underline"]
        return []

    if prop == "white-space":
        c = _WHITESPACE.get(value)
        return [c] if c else []

    if prop == "text-overflow":
        return ["truncate"] if value == "ellipsis" else []

    if prop == "vertical-align":
        c = _VERTICAL_ALIGN.get(value)
        return [c] if c else []

    if prop == "word-break":
        return {"break-all": ["break-all"], "keep-all": ["break-keep"],
                "normal": ["break-normal"]}.get(value, [])

    # ── Colors ──────────────────────────────────────────────────────────────────
    if prop == "background-color":
        return [_color_class("bg", value)]

    if prop == "color":
        return [_color_class("text", value)]

    if prop == "border-color":
        return [_color_class("border", value)]

    if prop == "fill":
        if value == "currentColor": return ["fill-current"]
        return [_color_class("fill", value)]

    if prop == "stroke":
        if value == "currentColor": return ["stroke-current"]
        return [_color_class("stroke", value)]

    # ── Borders ─────────────────────────────────────────────────────────────────
    if prop == "border-radius":
        return [_border_radius(value)]

    for corner, tw_corner in [
        ("border-top-left-radius", "tl"), ("border-top-right-radius", "tr"),
        ("border-bottom-left-radius", "bl"), ("border-bottom-right-radius", "br"),
    ]:
        if prop == corner:
            return [f"rounded-{tw_corner}-[{value}]"]

    if prop == "border":
        if value in ("none", "0", "0px"): return ["border-0"]
        m = re.match(r"([\d.]+px)\s+\w+\s+(.+)", value)
        if m:
            w, c = m.groups()
            return ["border", f"border-[{w}]", _color_class("border", c)]
        return ["border"]

    if prop == "border-width":
        return [f"border-[{value}]"]

    for side, lbl in [("border-top-width", "t"), ("border-right-width", "r"),
                       ("border-bottom-width", "b"), ("border-left-width", "l")]:
        if prop == side:
            return [f"border-{lbl}-[{value}]"]

    if prop == "border-style":
        return {"solid": ["border-solid"], "dashed": ["border-dashed"],
                "dotted": ["border-dotted"], "none": ["border-none"]}.get(value, [])

    if prop == "outline":
        if value == "none": return ["outline-none"]
        return []

    # ── Visual effects ──────────────────────────────────────────────────────────
    if prop == "opacity":
        try:
            pct = round(float(value) * 100)
            std = {0, 5, 10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90, 95, 100}
            return [f"opacity-{pct}"] if pct in std else [f"opacity-[{value}]"]
        except ValueError:
            return []

    if prop == "backdrop-filter":
        c = _backdrop_filter(value)
        return [c] if c else []

    if prop == "filter":
        m = re.match(r"blur\(([\d.]+px)\)", value.strip())
        if m: return [f"blur-[{m.group(1)}]"]
        m = re.match(r"drop-shadow\((.+)\)", value.strip())
        if m: return [f"drop-shadow-[{m.group(1)}]"]
        return []

    if prop == "box-shadow":
        if value == "none": return ["shadow-none"]
        return [f"shadow-[{value.replace(' ', '_')}]"]

    if prop == "mix-blend-mode":
        c = _BLEND.get(value)
        return [c] if c else []

    if prop == "isolation":
        c = _ISOLATION.get(value)
        return [c] if c else []

    # ── Overflow ────────────────────────────────────────────────────────────────
    if prop == "overflow":
        # "hidden hidden" == just hidden
        parts = value.split()
        if len(parts) == 1:
            c = _OVERFLOW.get(parts[0])
            return [c] if c else []
        out = []
        cx = _OVERFLOW_X.get(parts[0])
        cy = _OVERFLOW_Y.get(parts[1] if len(parts) > 1 else parts[0])
        if cx: out.append(cx)
        if cy: out.append(cy)
        return out

    if prop == "overflow-x":
        c = _OVERFLOW_X.get(value)
        return [c] if c else []

    if prop == "overflow-y":
        c = _OVERFLOW_Y.get(value)
        return [c] if c else []

    # ── Transform ───────────────────────────────────────────────────────────────
    if prop == "transform":
        if value == "none": return []
        return _transform(value)

    # ── Object ──────────────────────────────────────────────────────────────────
    if prop == "object-fit":
        c = _OBJECT_FIT.get(value)
        return [c] if c else []

    if prop == "object-position":
        return {"center": ["object-center"], "top": ["object-top"], "bottom": ["object-bottom"],
                "left": ["object-left"], "right": ["object-right"]}.get(value, [f"object-[{value}]"])

    # ── Miscellaneous ────────────────────────────────────────────────────────────
    if prop == "cursor":
        c = _CURSOR.get(value)
        return [c] if c else []

    if prop == "pointer-events":
        return {"none": ["pointer-events-none"], "all": ["pointer-events-auto"],
                "auto": ["pointer-events-auto"]}.get(value, [])

    if prop == "appearance":
        c = _APPEARANCE.get(value)
        return [c] if c else []

    if prop == "visibility":
        c = _VISIBILITY.get(value)
        return [c] if c else []

    if prop == "user-select":
        return {"none": ["select-none"], "text": ["select-text"],
                "all": ["select-all"], "auto": ["select-auto"]}.get(value, [])

    if prop == "resize":
        return {"none": ["resize-none"], "both": ["resize"], "horizontal": ["resize-x"],
                "vertical": ["resize-y"]}.get(value, [])

    if prop == "list-style-type":
        return {"none": ["list-none"], "disc": ["list-disc"],
                "decimal": ["list-decimal"]}.get(value, [])

    if prop == "columns":
        return [f"columns-{value}"]

    if prop in ("float",):
        return {"left": ["float-left"], "right": ["float-right"],
                "none": ["float-none"]}.get(value, [])

    if prop == "clear":
        return {"left": ["clear-left"], "right": ["clear-right"],
                "both": ["clear-both"], "none": ["clear-none"]}.get(value, [])

    # Properties intentionally skipped (animation, transition, will-change, etc.)
    return []


def inline_style_to_tailwind(style_str: str) -> tuple[list[str], dict[str, str]]:
    """
    Parse a CSS inline style string and return:
    - list of Tailwind classes
    - dict of remaining properties that couldn't be mapped (for style={{ }} fallback)
    """
    classes: list[str] = []
    remaining: dict[str, str] = {}

    for decl in style_str.split(";"):
        decl = decl.strip()
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop = prop.strip().lower()
        val = val.strip()
        if not val:
            continue

        mapped = css_prop_to_tailwind(prop, val)
        if mapped:
            classes.extend(mapped)
        else:
            # Keep for style={{ }} fallback (skip animation/transition noise)
            skip = {
                "animation", "transition", "will-change", "content",
                "-webkit-font-smoothing", "-moz-osx-font-smoothing",
                "background-image", "list-style", "tabindex",
                "data-framer-layout-hint-center-x",
            }
            if prop not in skip and not prop.startswith("--"):
                remaining[prop] = val

    return classes, remaining
