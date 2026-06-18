import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("https://dermato-wbs.framer.website/")
        # Scroll to bottom to trigger lazy loading
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")
        
        # Find Before/After
        ba = soup.find(class_="css-1a3mypm")
        if ba and ba.parent:
            print("Before/After Parent:", ba.parent.name, ba.parent.get("class"), ba.parent.get("data-framer-name"))
            
        # Find Video
        video = soup.find("video", src="https://framerusercontent.com/assets/U7eWy10v7FhA88xMZeWxPMq0c.mp4")
        if video and video.parent:
            print("Video Parent:", video.parent.name, video.parent.get("class"), video.parent.get("data-framer-name"))
            
        await browser.close()

asyncio.run(main())
