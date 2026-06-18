from playwright.sync_api import sync_playwright

def test():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto("https://dermato-wbs.framer.website/", wait_until="networkidle")
        
        # Try to click the hamburger menu (which is in the mobile variant, hidden on desktop)
        page.evaluate("""
            () => {
                const svgs = document.querySelectorAll('svg');
                for (let svg of svgs) {
                    const rect = svg.getBoundingClientRect();
                    // Even if rect is 0x0 because of display:none, we can try to click it
                    // Hamburger usually has a specific path or class
                    svg.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                }
            }
        """)
        page.wait_for_timeout(2000)
        html = page.content()
        with open("desktop_clicked.html", "w") as f:
            f.write(html)
        browser.close()

if __name__ == "__main__":
    test()
