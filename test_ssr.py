from playwright.sync_api import sync_playwright

def test():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Disable JS to get pure SSR HTML without React messing it up
        context = browser.new_context(java_script_enabled=False)
        page = context.new_page()
        page.goto("https://dermato-wbs.framer.website/", wait_until="networkidle")
        html = page.content()
        with open("ssr_test.html", "w") as f:
            f.write(html)
        browser.close()

if __name__ == "__main__":
    test()
