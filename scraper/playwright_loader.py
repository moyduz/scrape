from playwright.sync_api import sync_playwright
from scraper.network_capture import setup_network_capture
from scraper.dom_walker import walk_dom as _walk_dom

_RESPONSIVE_VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "tablet":  {"width": 768,  "height": 1024},
    "mobile":  {"width": 390,  "height": 844},
}

_FONT_URLS_JS = """
() => {
    const urls = new Set();
    const urlRe = /url\\(["']?([^"')\\s]+)["']?\\)/g;
    for (const sheet of document.styleSheets) {
        try {
            for (const rule of sheet.cssRules) {
                if (rule.constructor.name === 'CSSFontFaceRule') {
                    const src = rule.style.getPropertyValue('src') || rule.style.src || '';
                    let m;
                    while ((m = urlRe.exec(src)) !== null) {
                        const url = m[1];
                        if (!url.startsWith('data:')) urls.add(url);
                    }
                }
            }
        } catch(e) {}
    }
    return Array.from(urls);
}
"""

_FONT_FACE_CSS_JS = """
() => {
    let css = "";
    for (const sheet of document.styleSheets) {
        try {
            for (const rule of sheet.cssRules) {
                if (rule.constructor.name === 'CSSFontFaceRule') {
                    css += rule.cssText + "\\n";
                }
            }
        } catch(e) {}
    }
    return css;
}
"""

_BG_URLS_JS = """
() => {
    const urls = new Set();
    const urlRe = /url\\(["']?([^"')\\s]+)["']?\\)/g;
    for (const el of document.querySelectorAll('*')) {
        const bg = window.getComputedStyle(el).backgroundImage;
        if (!bg || bg === 'none') continue;
        let m;
        while ((m = urlRe.exec(bg)) !== null) {
            const url = m[1];
            if (!url.startsWith('data:')) urls.add(url);
        }
    }
    return Array.from(urls);
}
"""

_CSS_EXTRACT_JS = """
() => {
    let css = "";
    for (const sheet of document.styleSheets) {
        try {
            for (const rule of sheet.cssRules) {
                css += rule.cssText + "\\n";
            }
        } catch(e) {}
    }
    return css;
}
"""

_STYLE_PROPS = [
    "backgroundColor", "color", "paddingTop", "paddingBottom",
    "paddingLeft", "paddingRight", "fontFamily", "fontSize",
    "display", "gridTemplateColumns", "gap", "borderRadius",
    "maxWidth", "alignItems", "justifyContent", "flexDirection",
    "backgroundImage",
]


class PageSession:
    """
    Context manager that keeps the browser open for multi-step extraction.
    Single page load covers: HTML, CSS, screenshots, computed styles.
    """

    def __init__(self, url: str, timeout: int = 30000):
        self.url = url
        self.timeout = timeout
        self.html: str = ""
        self.html_pre_scroll: str = ""
        self.css: str = ""
        self.final_url: str = ""
        self.asset_urls: dict[str, list[str]] = {}
        self._pw = None
        self._browser = None
        self._page = None

    def __enter__(self) -> "PageSession":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._page = self._browser.new_page(viewport={"width": 1440, "height": 900})
        # Capture the true SSR HTML before React hydration destroys responsive DOM variants
        self.ssr_html = ""
        def handle_response(response):
            # Capture the main document response
            if response.url.rstrip("/") == self.url.rstrip("/") and response.status == 200 and not self.ssr_html:
                try:
                    self.ssr_html = response.text()
                except Exception:
                    pass

        self._page.on("response", handle_response)
        
        self.asset_urls = setup_network_capture(self._page)
        self._page.goto(self.url, wait_until="networkidle", timeout=self.timeout)

        # Snapshot before scroll — captures above-fold initial state (hydrated)
        self.html_pre_scroll = self._page.content()

        # Scroll at Desktop to trigger lazy images
        self._page.evaluate("""
        () => new Promise(resolve => {
            const step = 400;
            const delay = 40;
            let pos = 0;
            function tick() {
                window.scrollTo(0, pos);
                pos += step;
                if (pos <= document.body.scrollHeight + step) {
                    setTimeout(tick, delay);
                } else {
                    window.scrollTo(0, 0);
                    setTimeout(resolve, 500);
                }
            }
            tick();
        })
        """)
        
        # Scroll at Mobile to trigger mobile lazy images
        try:
            self._page.set_viewport_size({"width": 390, "height": 844})
            self._page.wait_for_timeout(500)
            self._page.evaluate("""
            () => new Promise(resolve => {
                const step = 400;
                const delay = 40;
                let pos = 0;
                function tick() {
                    window.scrollTo(0, pos);
                    pos += step;
                    if (pos <= document.body.scrollHeight + step) {
                        setTimeout(tick, delay);
                    } else {
                        window.scrollTo(0, 0);
                        setTimeout(resolve, 500);
                    }
                }
                tick();
            })
            """)
            # Click mobile menu to ensure it loads any assets
            self._page.evaluate("""
            () => {
                const svgs = document.querySelectorAll('header svg, nav svg');
                for(let svg of svgs) {
                    if (svg.clientWidth > 0 && svg.clientHeight > 0) {
                        svg.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                    }
                }
            }
            """)
            self._page.wait_for_timeout(1000)
            # Restore to desktop so CSS extraction has desktop styles primarily active
            self._page.set_viewport_size({"width": 1440, "height": 900})
            self._page.wait_for_timeout(500)
        except Exception:
            pass

        # USE SSR HTML AS THE SOURCE OF TRUTH (preserves all breakpoints)
        # If SSR capture failed for some reason, fallback to hydrated HTML
        self.html = self.ssr_html if self.ssr_html else self._page.content()
        # -----------------------------------------------

        self.final_url = self._page.url
        self.css = self._page.evaluate(_CSS_EXTRACT_JS)
        return self

    def __exit__(self, *args):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def _find_visible_element(self, name: str):
        """Returns the visible (desktop) variant among all elements with this framer name."""
        safe_name = name.replace('"', '\\"')
        js = f"""
        () => {{
            const candidates = Array.from(
                document.querySelectorAll('[data-framer-name="{safe_name}"]')
            );
            for (const c of candidates) {{
                const rect = c.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) return c;
            }}
            return candidates[0] || null;
        }}
        """
        return self._page.evaluate_handle(js)

    def screenshot_section(self, name: str, path: str) -> bool:
        try:
            handle = self._find_visible_element(name)
            el = handle.as_element()
            if el:
                el.screenshot(path=path)
                return True
        except Exception:
            pass
        return False

    def get_computed_styles(self, name: str) -> dict:
        props_js = ", ".join(f'"{p}": s.{p}' for p in _STYLE_PROPS)
        safe_name = name.replace('"', '\\"')
        return self._page.evaluate(f"""
        () => {{
            const candidates = Array.from(
                document.querySelectorAll('[data-framer-name="{safe_name}"]')
            );
            let el = candidates[0];
            for (const c of candidates) {{
                const rect = c.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {{ el = c; break; }}
            }}
            if (!el) return {{}};
            const s = window.getComputedStyle(el);
            return {{ {props_js} }};
        }}
        """)

    def walk_dom(self, max_depth: int = 25, max_nodes: int = 8000) -> dict:
        """Single-viewport DOM walk (desktop). Returns raw walk result."""
        return _walk_dom(self._page, max_depth=max_depth, max_nodes=max_nodes)

    def walk_dom_responsive(self, max_nodes: int = 8000) -> dict:
        """
        Walks DOM at desktop, tablet, and mobile viewports.
        Returns {"desktop": tree, "tablet": tree, "mobile": tree} as SEPARATE trees.

        Each tree contains only visible elements at that viewport —
        Framer uses JS-driven subtree swap for breakpoints, not just CSS.
        The renderer uses useMediaQuery to pick the correct tree at runtime.
        """
        self._page.set_viewport_size(_RESPONSIVE_VIEWPORTS["desktop"])
        self._page.wait_for_timeout(400)
        desktop = _walk_dom(self._page, max_nodes=max_nodes)

        self._page.set_viewport_size(_RESPONSIVE_VIEWPORTS["mobile"])
        self._page.wait_for_timeout(600)
        mobile = _walk_dom(self._page, max_nodes=max_nodes)

        # Restore desktop
        self._page.set_viewport_size(_RESPONSIVE_VIEWPORTS["desktop"])
        self._page.wait_for_timeout(300)

        return {
            "desktop": desktop,
            "mobile": mobile,
            "_meta": {
                "desktop_nodes": desktop.get("totalNodes", 0),
                "mobile_nodes": mobile.get("totalNodes", 0),
            },
        }

    def capture_iframes(self, max_depth: int = 10, max_nodes: int = 1000) -> list[dict]:
        """
        Walks the DOM inside every non-blank iframe on the page.
        Returns [{url, tree, totalNodes}, ...] for each accessible frame.
        """
        results = []
        for frame in self._page.frames:
            if frame == self._page.main_frame:
                continue
            url = frame.url or ""
            if not url or url in ("about:blank", ""):
                continue
            try:
                tree = _walk_dom(frame, max_depth=max_depth, max_nodes=max_nodes)
                results.append({"url": url, **tree})
            except Exception:
                pass
        return results

    def capture_responsive(self, max_nodes: int = 5000) -> dict[str, dict]:
        """Legacy: separate snapshot per viewport. Prefer walk_dom_responsive()."""
        results = {}
        for name, vp in _RESPONSIVE_VIEWPORTS.items():
            self._page.set_viewport_size(vp)
            self._page.wait_for_timeout(600)
            results[name] = _walk_dom(self._page, max_nodes=max_nodes)
        self._page.set_viewport_size(_RESPONSIVE_VIEWPORTS["desktop"])
        self._page.wait_for_timeout(300)
        return results

    def extract_font_urls(self) -> list[str]:
        """
        Returns all font file URLs declared in @font-face rules on this page.
        Uses the browser's live CSSOM so same-origin and CDN fonts are both captured.
        """
        return self._page.evaluate(_FONT_URLS_JS)

    def extract_background_image_urls(self) -> list[str]:
        """
        Returns all background-image url(...) values from computed styles across the DOM.
        Catches lazily-loaded backgrounds that network capture might miss.
        """
        return self._page.evaluate(_BG_URLS_JS)

    def extract_font_face_css(self) -> str:
        """Returns the raw CSS text of every @font-face rule on the page."""
        return self._page.evaluate(_FONT_FACE_CSS_JS)

    def extract_svg_sprite(self) -> str:
        """
        Extracts hidden SVG elements that contain <symbol> or <defs> — the sprite sheet.
        These are 0×0 invisible SVGs that hold icon definitions referenced by <use href="#id">.
        Returns the combined outerHTML so DOMPage can inject it as a hidden element.
        """
        return self._page.evaluate("""
        () => {
            const sprites = [];
            for (const svg of document.querySelectorAll('svg')) {
                const r = svg.getBoundingClientRect();
                // Capture SVGs that are hidden (0x0) but contain symbol/defs
                if (r.width === 0 && r.height === 0) {
                    if (svg.querySelector('symbol, defs')) {
                        sprites.push(svg.outerHTML);
                    }
                }
            }
            return sprites.join('\\n');
        }
        """)

    def capture_video_screenshots(self, output_dir: "Path") -> list[str]:
        """
        Screenshots every visible video element in DOM order.
        Returns a list of saved file paths (parallel to DOM walker video nodes).
        """
        from pathlib import Path as _Path
        output_dir = _Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        for i, el in enumerate(self._page.query_selector_all("video")):
            try:
                box = el.bounding_box()
                if not box or box["width"] == 0 or box["height"] == 0:
                    continue
                p = output_dir / f"video_{i}.png"
                el.screenshot(path=str(p))
                paths.append(str(p))
            except Exception:
                pass
        return paths

    def hover_interactive_elements(self) -> None:
        """
        Hovers over common interactive elements (nav links, buttons, dropdowns)
        so hover-triggered content (menus, tooltips, sub-nav) becomes visible
        before the next DOM/screenshot capture.
        """
        self._page.evaluate("""
        async () => {
            const selectors = [
                'nav a', 'nav button', 'header a', 'header button',
                '[data-framer-name*="nav"] a', '[data-framer-name*="menu"] a',
                '.menu a', '.dropdown > a', '[aria-haspopup] a',
            ];
            for (const sel of selectors) {
                for (const el of document.querySelectorAll(sel)) {
                    try {
                        el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
                        el.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));
                    } catch(e) {}
                }
            }
            await new Promise(r => setTimeout(r, 400));
        }
        """)

    def get_section_source(self, name: str) -> dict:
        """
        Returns the section's actual HTML (with inline styles) and all CSS rules
        that apply to classes used within that section subtree.
        Selects the desktop SSR variant by checking actual visibility at 1440px viewport.
        Scrolls the element into view first so Framer's IntersectionObserver animations
        are in their final (visible) state when the HTML is captured.
        """
        safe_name = name.replace('"', '\\"').replace("'", "\\'")
        return self._page.evaluate(f"""
        async () => {{
            // Find all elements with this framer name
            const candidates = Array.from(
                document.querySelectorAll('[data-framer-name="{safe_name}"]')
            );

            // Pick the one that is actually visible at the current (desktop) viewport.
            // Framer SSR hides non-matching breakpoint variants via display:none,
            // so getBoundingClientRect() returns 0x0 for hidden variants.
            let el = candidates[0];
            for (const c of candidates) {{
                const rect = c.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {{
                    el = c;
                    break;
                }}
            }}

            if (!el) return {{html: '', css: ''}};

            // Scroll the element into view so Framer's IntersectionObserver fires
            // and scroll-triggered animations reach their final state before capture.
            el.scrollIntoView({{ behavior: 'instant', block: 'center' }});
            await new Promise(r => setTimeout(r, 800));

            const html = el.outerHTML.substring(0, 60000);

            // Collect all CSS classes used in this subtree
            const classes = new Set();
            el.classList.forEach(c => classes.add(c));
            el.querySelectorAll('*').forEach(node => {{
                node.classList.forEach(c => classes.add(c));
            }});

            // Extract CSS rules that reference any of these classes
            let css = '';
            for (const sheet of document.styleSheets) {{
                try {{
                    for (const rule of sheet.cssRules) {{
                        // Regular rules
                        if (rule.selectorText) {{
                            for (const cls of classes) {{
                                if (rule.selectorText.includes('.' + cls)) {{
                                    css += rule.cssText + '\\n';
                                    break;
                                }}
                            }}
                        }}
                        // @media rules — recurse into them
                        else if (rule.cssRules) {{
                            let inner = '';
                            for (const inner_rule of rule.cssRules) {{
                                if (!inner_rule.selectorText) continue;
                                for (const cls of classes) {{
                                    if (inner_rule.selectorText.includes('.' + cls)) {{
                                        inner += inner_rule.cssText + '\\n';
                                        break;
                                    }}
                                }}
                            }}
                            if (inner) css += rule.conditionText
                                ? `@media ${{rule.conditionText}} {{\\n${{inner}}}}\\n`
                                : inner;
                        }}
                    }}
                }} catch(e) {{}}
            }}

            // Walk up to find a fixed/sticky ancestor ONLY.
            // This captures the fixed header wrapper that lives outside the named element.
            // We do NOT use generic semantic elements (main, section) as ancestors —
            // those are page-level containers that would pull in unrelated content.
            let ancestor = el.parentElement;
            let topAncestor = null;
            let semanticClasses = new Set(classes);

            for (let i = 0; i < 8 && ancestor && ancestor !== document.body; i++) {{
                const computed = window.getComputedStyle(ancestor);
                ancestor.classList.forEach(c => semanticClasses.add(c));
                if (computed.position === 'fixed' || computed.position === 'sticky') {{
                    topAncestor = ancestor;
                    break;
                }}
                ancestor = ancestor.parentElement;
            }}

            let semanticHtml = topAncestor ? topAncestor.outerHTML.substring(0, 80000) : '';

            // Re-collect CSS including ancestor classes
            let fullCss = '';
            for (const sheet of document.styleSheets) {{
                try {{
                    for (const rule of sheet.cssRules) {{
                        if (rule.selectorText) {{
                            for (const cls of semanticClasses) {{
                                if (rule.selectorText.includes('.' + cls)) {{
                                    fullCss += rule.cssText + '\\n';
                                    break;
                                }}
                            }}
                        }} else if (rule.cssRules) {{
                            let inner = '';
                            for (const ir of rule.cssRules) {{
                                if (!ir.selectorText) continue;
                                for (const cls of semanticClasses) {{
                                    if (ir.selectorText.includes('.' + cls)) {{
                                        inner += ir.cssText + '\\n'; break;
                                    }}
                                }}
                            }}
                            if (inner) fullCss += rule.conditionText
                                ? `@media ${{rule.conditionText}} {{\\n${{inner}}}}\\n` : inner;
                        }}
                    }}
                }} catch(e) {{}}
            }}

            // :root token definitions
            let tokens = '';
            for (const sheet of document.styleSheets) {{
                try {{
                    for (const rule of sheet.cssRules) {{
                        if (rule.selectorText === ':root') tokens += rule.cssText + '\\n';
                    }}
                }} catch(e) {{}}
            }}

            return {{
                html: semanticHtml || html,
                css: tokens + fullCss
            }};
        }}
        """)


# Backward-compatible helpers
def load_page(url: str, wait_until: str = "networkidle", timeout: int = 30000) -> tuple[str, str]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until=wait_until, timeout=timeout)
        html = page.content()
        final_url = page.url
        browser.close()
    return html, final_url


def load_page_with_css(url: str, timeout: int = 30000) -> tuple[str, str, str]:
    with PageSession(url, timeout) as session:
        return session.html, session.css, session.final_url
