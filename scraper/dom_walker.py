"""
Recursive DOM walker — captures computed styles + layout rect for every VISIBLE element.
Hidden elements (display:none) are skipped — each viewport walk produces its own clean tree.
Caller walks at multiple viewports and gets separate trees per breakpoint.
"""

_SKIP_TAGS = frozenset({
    "script", "style", "noscript", "link", "meta", "head",
    "path", "defs", "symbol", "use", "g", "circle",
    "line", "polygon", "polyline", "ellipse",
})

_STYLE_PROPS = [
    "display", "position",
    "top", "left", "right", "bottom",
    "flexDirection", "flexWrap", "alignItems", "justifyContent",
    "flex", "flexGrow", "flexShrink", "flexBasis",
    "alignSelf", "justifySelf",
    "gridTemplateColumns", "gridTemplateRows",
    "gridColumn", "gridRow",
    "gap", "columnGap", "rowGap",
    "width", "height", "minWidth", "maxWidth", "minHeight", "maxHeight",
    "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
    "marginTop", "marginRight", "marginBottom", "marginLeft",
    "backgroundColor", "backgroundImage", "backgroundSize",
    "backgroundPosition", "backgroundRepeat", "backgroundClip",
    "color", "fontSize", "fontFamily", "fontWeight", "fontStyle",
    "lineHeight", "letterSpacing", "textAlign",
    "textDecoration", "textTransform", "whiteSpace",
    "border", "borderRadius", "boxShadow", "outline",
    "opacity", "overflow", "zIndex", "transform",
    "cursor", "pointerEvents", "visibility",
]

# Framer's typography CSS variables — captured verbatim alongside computed styles
_FRAMER_VARS = [
    "--framer-font-family",
    "--framer-font-size",
    "--framer-font-weight",
    "--framer-font-style",
    "--framer-letter-spacing",
    "--framer-line-height",
    "--framer-text-color",
    "--framer-text-alignment",
    "--framer-text-decoration",
    "--framer-text-transform",
]

_SKIP_VALUES = frozenset({
    "", "none", "normal", "auto", "0px", "transparent",
    "rgba(0, 0, 0, 0)", "inherit", "initial", "unset", "revert",
    "visible", "static", "ltr",
    "0px 0px", "0px 0px 0px 0px",
    "nowrap", "row", "stretch", "left",
    "rgb(0, 0, 0)",
    "0 1 auto",
    "repeat",
    "padding-box",
})

_KEEP_ATTRS = frozenset([
    "id", "src", "href", "alt", "title", "type", "role",
    "data-framer-name", "data-framer-component-type", "aria-label", "placeholder",
])

_WALK_JS = """
(opts) => {
    const SKIP_TAGS = new Set(opts.skipTags);
    const STYLE_PROPS = opts.styleProps;
    const SKIP_VALUES = new Set(opts.skipValues);
    const KEEP_ATTRS = new Set(opts.keepAttrs);
    const FRAMER_VARS = opts.framerVars;
    const MAX_DEPTH = opts.maxDepth;
    const MAX_NODES = opts.maxNodes;
    let nodeCount = 0;

    function extractStyles(computedStyle) {
        const styles = {};
        for (const prop of STYLE_PROPS) {
            const val = computedStyle[prop];
            if (val && !SKIP_VALUES.has(val)) styles[prop] = val;
        }
        return styles;
    }

    function extractCSSVars(computedStyle) {
        const vars = {};
        for (const v of FRAMER_VARS) {
            const val = computedStyle.getPropertyValue(v).trim();
            if (val) vars[v] = val;
        }
        return vars;
    }

    function walk(node, depth) {
        if (nodeCount >= MAX_NODES) return null;
        if (depth > MAX_DEPTH) return null;
        if (!node || !node.tagName) return null;

        const tag = node.tagName.toLowerCase();

        if (tag === 'svg') {
            const r = node.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) return null;
            nodeCount++;

            // Inline <use href="#id"> references so SVG renders without a sprite sheet.
            // Framer puts each icon as a real <svg id="..."> elsewhere in the DOM;
            // we clone the node and replace <use> with the referenced element's inner content.
            const clone = node.cloneNode(true);
            for (const use of Array.from(clone.querySelectorAll('use[href]'))) {
                const href = use.getAttribute('href') || '';
                if (!href.startsWith('#')) continue;
                const refEl = document.getElementById(href.slice(1));
                if (!refEl) continue;
                const refClone = refEl.cloneNode(true);
                // Copy viewBox / attributes from the referenced SVG to the clone's root
                for (const attr of refEl.attributes) {
                    if (attr.name !== 'id' && attr.name !== 'style' && attr.name !== 'width' && attr.name !== 'height') {
                        clone.setAttribute(attr.name, attr.value);
                    }
                }
                // Replace <use> with the children of the referenced SVG
                for (const child of Array.from(refClone.childNodes)) {
                    use.parentNode.insertBefore(child, use);
                }
                use.remove();
            }

            return {
                tag: 'svg',
                rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
                svgData: clone.outerHTML.slice(0, 10000),
            };
        }

        if (tag === 'video') {
            const r = node.getBoundingClientRect();
            nodeCount++;
            const sources = Array.from(node.querySelectorAll('source')).map(s => s.src).filter(Boolean);
            return {
                tag: 'video',
                rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
                attrs: { src: node.src || (sources[0] ?? ''), poster: node.poster || '' },
                styles: extractStyles(window.getComputedStyle(node)),
            };
        }

        if (!(node instanceof HTMLElement)) return null;
        if (SKIP_TAGS.has(tag)) return null;

        const computed = window.getComputedStyle(node);

        // Skip hidden nodes — each viewport tree is its own clean subtree.
        // The caller (walk_dom_responsive) walks at separate viewports;
        // the renderer uses useMediaQuery to pick the right tree at runtime.
        if (computed.display === 'none' || computed.visibility === 'hidden') return null;

        const rect = node.getBoundingClientRect();
        nodeCount++;

        const styles = extractStyles(computed);
        const cssVars = extractCSSVars(computed);

        const attrs = {};
        for (const attr of node.attributes) {
            if (KEEP_ATTRS.has(attr.name)) attrs[attr.name] = attr.value.slice(0, 300);
        }

        let text = '';
        for (const child of node.childNodes) {
            if (child.nodeType === 3) {
                const t = child.textContent.trim();
                if (t) text += (text ? ' ' : '') + t;
            }
        }
        text = text.slice(0, 300) || null;

        const children = [];
        // display:contents elements are transparent wrappers (Framer uses them heavily).
        // Don't charge depth for them so they don't exhaust the depth budget.
        const nextDepth = (computed.display === 'contents') ? depth : depth + 1;
        for (const child of node.children) {
            const childData = walk(child, nextDepth);
            if (childData) children.push(childData);
        }

        if (node.shadowRoot) {
            for (const child of node.shadowRoot.children) {
                const childData = walk(child, depth + 1);
                if (childData) children.push(childData);
            }
        }

        const pseudo = {};
        for (const p of ['::before', '::after']) {
            const ps = window.getComputedStyle(node, p);
            const content = ps.content;
            if (!content || content === 'none' || content === '""' || content === "''") continue;
            const pStyles = extractStyles(ps);
            pStyles.content = content;
            pseudo[p.slice(2)] = pStyles;
        }

        const result = {
            tag,
            rect: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                w: Math.round(rect.width),
                h: Math.round(rect.height),
            },
        };
        if (text) result.text = text;
        if (Object.keys(attrs).length > 0) result.attrs = attrs;
        if (Object.keys(styles).length > 0) result.styles = styles;
        if (Object.keys(cssVars).length > 0) result.cssVars = cssVars;
        if (children.length > 0) result.children = children;
        if (Object.keys(pseudo).length > 0) result.pseudo = pseudo;

        return result;
    }

    const bodyChildren = Array.from(document.body.children);
    const childTrees = bodyChildren.map(c => walk(c, 1)).filter(Boolean);

    return {
        tree: {
            tag: 'div',
            styles: { width: '100%', minHeight: '100vh' },
            children: childTrees,
        },
        totalNodes: nodeCount,
        viewport: { w: window.innerWidth, h: window.innerHeight },
        scrollHeight: document.documentElement.scrollHeight,
    };
}
"""


def _opts(max_depth: int, max_nodes: int) -> dict:
    return {
        "skipTags": list(_SKIP_TAGS),
        "styleProps": _STYLE_PROPS,
        "skipValues": list(_SKIP_VALUES),
        "keepAttrs": list(_KEEP_ATTRS),
        "framerVars": _FRAMER_VARS,
        "maxDepth": max_depth,
        "maxNodes": max_nodes,
    }


def walk_dom(page_or_frame, max_depth: int = 25, max_nodes: int = 8000) -> dict:
    """
    Walks visible DOM at the current viewport.
    Hidden elements (display:none) are excluded — each viewport produces its own tree.
    """
    return page_or_frame.evaluate(_WALK_JS, _opts(max_depth, max_nodes))
