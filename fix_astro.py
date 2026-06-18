import re

with open('data/astro/dermato-wbs-framer-website/src/pages/index.astro', 'r') as f:
    html = f.read()

ba_html = """<div class="css-1a3mypm" style="--thumb-size: 62px;"><div x="24" y="24" class="css-d32cmk"><div class="css-1jvuujq">Before</div></div><div x="24" y="24" class="css-iyuok8"><div class="css-1jvuujq">After</div></div><div class="css-wt6yvf" style="clip-path: inset(0px 0px 0px 85%);"><img src="https://framerusercontent.com/images/KLz3YY5GDbbEwOvVSAqGFLXE0eI.jpg?width=900&amp;height=1200" srcset="https://framerusercontent.com/images/KLz3YY5GDbbEwOvVSAqGFLXE0eI.jpg?scale-down-to=1024&amp;width=900&amp;height=1200 768w,https://framerusercontent.com/images/KLz3YY5GDbbEwOvVSAqGFLXE0eI.jpg?width=900&amp;height=1200 900w" alt="" class="css-rs75p9"></div><img src="https://framerusercontent.com/images/mtj5hoiyHHRIFp2NkDEtYGmM.jpg?width=900&amp;height=1200" srcset="https://framerusercontent.com/images/mtj5hoiyHHRIFp2NkDEtYGmM.jpg?scale-down-to=1024&amp;width=900&amp;height=1200 768w,https://framerusercontent.com/images/mtj5hoiyHHRIFp2NkDEtYGmM.jpg?width=900&amp;height=1200 900w" alt="" class="css-rs75p9"><input type="range" min="0" max="100" class="css-or5vhe"><div color="var(--token-4028d577-809d-4299-bdfb-7e33801e4ff9, rgb(255, 255, 255))" width="1" class="css-oieuvp" style="left: 85%;"><div class="css-flboer"></div></div></div>"""

video_html = """<video src="https://framerusercontent.com/assets/U7eWy10v7FhA88xMZeWxPMq0c.mp4" loop="" preload="auto" muted="" playsinline="" style="cursor:auto;width:100%;height:100%;border-radius:0px;display:block;object-fit:cover;background-color:var(--token-74fc5722-758f-474e-a741-0baa098d09c5, rgba(255, 255, 255, 0));object-position:50% 50%" autoplay=""></video>"""

# Find and replace contents of framer-oydi2j-container
# The container looks like: <div class="framer-oydi2j-container">...</div>
html = re.sub(
    r'(<div[^>]*class="[^"]*framer-oydi2j-container[^"]*"[^>]*>)(.*?)(</div>)',
    r'\g<1>' + ba_html.replace('\\', '\\\\') + r'\g<3>',
    html,
    flags=re.DOTALL
)

html = re.sub(
    r'(<div[^>]*class="[^"]*framer-1gpytvh-container[^"]*"[^>]*>)(.*?)(</div>)',
    r'\g<1>' + video_html.replace('\\', '\\\\') + r'\g<3>',
    html,
    flags=re.DOTALL
)

with open('data/astro/dermato-wbs-framer-website/src/pages/index.astro', 'w') as f:
    f.write(html)
    
print("Successfully injected missing components into index.astro!")
