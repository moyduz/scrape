from playwright.sync_api import sync_playwright
import time

def test():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto("https://dermato-wbs.framer.website/", wait_until="networkidle")
        
        # Click all likely hamburger icons
        page.evaluate("""
            () => {
                const header = document.querySelector('header, nav') || document.body;
                const svgs = header.querySelectorAll('svg');
                for (let svg of svgs) {
                    const rect = svg.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        console.log("Clicking SVG", svg);
                        svg.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                    }
                }
            }
        """)
        time.sleep(2)
        html = page.content()
        with open("mobile_test.html", "w") as f:
            f.write(html)
        browser.close()

if __name__ == "__main__":
    test()
