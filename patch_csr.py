import re
from bs4 import BeautifulSoup
import copy

def merge_csr_into_ssr(ssr_html, hydrated_html):
    ssr_soup = BeautifulSoup(ssr_html, "lxml")
    hydrated_soup = BeautifulSoup(hydrated_html, "lxml")
    
    # Find all containers in hydrated that have children
    containers = hydrated_soup.find_all(class_=re.compile(r'framer-[a-z0-9]+-container'))
    
    merged_count = 0
    for container in containers:
        classes = container.get("class", [])
        if not classes: continue
        container_class = next((c for c in classes if "-container" in c), None)
        if not container_class: continue
        
        # If it has meaningful children in hydrated
        if len(container.contents) > 0:
            # Find all matching containers in SSR
            ssr_containers = ssr_soup.find_all(class_=container_class)
            for ssr_container in ssr_containers:
                # If SSR container is empty or only has whitespace
                if not ssr_container.contents or all(isinstance(c, str) and c.strip() == '' for c in ssr_container.contents):
                    # Copy contents
                    ssr_container.clear()
                    for child in container.children:
                        ssr_container.append(copy.copy(child))
                    merged_count += 1
                    
    print(f"Merged {merged_count} missing CSR components into SSR.")
    return str(ssr_soup)

with open('ssr_test.html', 'r') as f:
    ssr = f.read()
    
# I need to save html_post_scroll_desktop.html first
