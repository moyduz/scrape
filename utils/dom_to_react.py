"""
Pixel-perfect DOM renderer — dual-tree breakpoint model.

Framer uses JS-driven subtree swap for breakpoints (not CSS display:none).
Desktop and mobile are entirely separate React trees at runtime.
The renderer uses useMediaQuery to pick the correct tree.
"""
import json
import re

_CAMEL_RE = re.compile(r"-([a-z])")


def _to_camel(prop: str) -> str:
    if prop.startswith("--"):
        return prop
    return _CAMEL_RE.sub(lambda m: m.group(1).upper(), prop)


def _normalize_styles(styles: dict) -> dict:
    result = {}
    for k, v in styles.items():
        ck = _to_camel(k)
        # Font-family: strip escaped inner quotes from getComputedStyle output
        if ck == "fontFamily":
            v = v.replace('\\"', '"')
        result[ck] = v
    return result


def _normalize_tree(node: dict) -> dict:
    if not isinstance(node, dict):
        return node
    result = dict(node)
    if "styles" in result and isinstance(result["styles"], dict):
        result["styles"] = _normalize_styles(result["styles"])
    if "pseudo" in result and isinstance(result["pseudo"], dict):
        result["pseudo"] = {
            side: _normalize_styles(props)
            for side, props in result["pseudo"].items()
        }
    if "children" in result and isinstance(result["children"], list):
        result["children"] = [_normalize_tree(c) for c in result["children"]]
    return result


# NOTE: plain string, not f-string — braces are literal TypeScript syntax
_RENDERER_TSX = """\
"use client";
import { useState, useEffect } from "react";
import desktopData from "./dom_desktop.json";
import mobileData from "./dom_mobile.json";

type PseudoStyles = Record<string, string> & { content?: string };

type DOMNode = {
  tag: string;
  text?: string;
  attrs?: Record<string, string>;
  styles?: Record<string, string>;
  cssVars?: Record<string, string>;
  children?: DOMNode[];
  svgData?: string;
  pseudo?: { before?: PseudoStyles; after?: PseudoStyles };
};

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia(query);
    setMatches(mq.matches);
    const handler = (e: MediaQueryListEvent) => setMatches(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [query]);
  return matches;
}

function stripQuotes(s: string | undefined): string {
  return s ? s.replace(/^["']|["']$/g, "") : "";
}

function Node({ node }: { node: DOMNode }) {
  const {
    tag, text, attrs = {}, styles = {}, cssVars = {},
    children = [], svgData, pseudo,
  } = node;

  // Merge Framer CSS variables so var(--framer-*) resolves correctly
  const style = { ...styles, ...cssVars } as React.CSSProperties;

  if (svgData) {
    return (
      <span
        style={{ ...style, display: (style.display as string) ?? "inline-flex" }}
        dangerouslySetInnerHTML={{ __html: svgData }}
      />
    );
  }

  const { content: bContent, ...beforeStyle } = pseudo?.before ?? {};
  const { content: aContent, ...afterStyle } = pseudo?.after ?? {};

  const inner = (
    <>
      {bContent && bContent !== "none" && (
        <span style={beforeStyle as React.CSSProperties}>{stripQuotes(bContent)}</span>
      )}
      {text}
      {children.map((c, i) => (
        <Node key={i} node={c} />
      ))}
      {aContent && aContent !== "none" && (
        <span style={afterStyle as React.CSSProperties}>{stripQuotes(aContent)}</span>
      )}
    </>
  );

  if (tag === "img") {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={attrs.src ?? ""} alt={attrs.alt ?? ""} style={style} />;
  }
  if (tag === "video") {
    const poster = attrs.poster;
    const src = attrs.src;
    if (poster) {
      // eslint-disable-next-line @next/next/no-img-element
      return <img src={poster} alt="video" style={style} />;
    }
    return <video src={src} style={style} autoPlay muted loop playsInline />;
  }
  if (tag === "a") {
    return <a href={attrs.href ?? "#"} style={style} rel="noreferrer">{inner}</a>;
  }
  if (tag === "input") {
    return <input style={style} placeholder={attrs.placeholder} type={attrs.type ?? "text"} readOnly />;
  }
  if (tag === "br") return <br />;
  if (tag === "hr") return <hr style={style} />;

  const Tag = tag as React.ElementType;
  return <Tag style={style}>{inner}</Tag>;
}

export default function DOMPage({ svgSprite }: { svgSprite?: string }) {
  // Framer uses JS-driven subtree swap — desktop and mobile are separate trees.
  // useMediaQuery picks the correct tree at runtime (matches Framer's breakpoint).
  const isMobile = useMediaQuery("(max-width: 810px)");
  const data = isMobile ? mobileData : desktopData;
  return (
    <div style={{ margin: 0, padding: 0, overflowX: "hidden" }}>
      {svgSprite && (
        <div
          aria-hidden="true"
          style={{ position: "absolute", width: 0, height: 0, overflow: "hidden" }}
          dangerouslySetInnerHTML={{ __html: svgSprite }}
        />
      )}
      <Node node={data as unknown as DOMNode} />
    </div>
  );
}
"""

_SVG_SPRITE_PLACEHOLDER = "___SVG_SPRITE___"


def generate_dom_renderer(dom_result: dict, svg_sprite: str = "") -> tuple[str, str, str]:
    """
    Input: result from walk_dom_responsive() = {"desktop": tree, "mobile": tree, ...}
    OR a single tree dict (backward compat — treated as desktop-only).

    Returns (tsx_content, desktop_json, mobile_json).
    The TSX imports both JSON files and switches at runtime via useMediaQuery.
    """
    if "desktop" in dom_result and "mobile" in dom_result:
        desktop_raw = dom_result["desktop"].get("tree", dom_result["desktop"])
        mobile_raw  = dom_result["mobile"].get("tree", dom_result["mobile"])
    else:
        desktop_raw = dom_result.get("tree", dom_result)
        mobile_raw  = desktop_raw

    desktop_json = json.dumps(_normalize_tree(desktop_raw), ensure_ascii=False, separators=(",", ":"))
    mobile_json  = json.dumps(_normalize_tree(mobile_raw),  ensure_ascii=False, separators=(",", ":"))

    # Embed SVG sprite as a JS string constant inside the TSX
    safe_sprite = svg_sprite.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    tsx = _RENDERER_TSX.replace(
        "{ svgSprite }: { svgSprite?: string }",
        "() { const svgSprite =",
    ) if False else _RENDERER_TSX  # use template injection below

    # Inject sprite as a module-level constant so no prop is needed
    sprite_const = f'const SVG_SPRITE = `{safe_sprite}`;\n\n' if svg_sprite else 'const SVG_SPRITE = "";\n\n'
    tsx = _RENDERER_TSX.replace(
        'export default function DOMPage({ svgSprite }: { svgSprite?: string })',
        'export default function DOMPage()'
    ).replace(
        '{svgSprite && (',
        '{SVG_SPRITE && ('
    ).replace(
        'dangerouslySetInnerHTML={{ __html: svgSprite }}',
        'dangerouslySetInnerHTML={{ __html: SVG_SPRITE }}'
    )
    # Insert the constant after imports
    insert_after = 'import mobileData from "./dom_mobile.json";\n'
    tsx = tsx.replace(insert_after, insert_after + "\n" + sprite_const)

    return tsx, desktop_json, mobile_json
