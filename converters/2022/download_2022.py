import os
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

URL = "https://www.da.gov.ph/price-monitoring/"
HEADERS = {"User-Agent": "Mozilla/5.0 (DSA-PublicMarketsPriceMap)"}
os.makedirs("pdf_2022", exist_ok=True)

resp = requests.get(URL, headers=HEADERS, timeout=30)
resp.raise_for_status()
soup = BeautifulSoup(resp.text, "html.parser")

links, seen = [], set()
for a in soup.find_all("a", href=True):
    href = a["href"]
    text = a.get_text(" ", strip=True)
    if href.lower().endswith(".pdf") and "2022" in text and "cigarette" not in href.lower():
        full_url = urljoin(URL, href)
        if full_url not in seen:
            seen.add(full_url)
            links.append((text, full_url))

print(f"Found {len(links)} unique 2022 PDFs.")
for i, (text, link) in enumerate(links, start=1):
    fname = link.split("/")[-1].split("?")[0]
    out_path = os.path.join("pdf_2022", fname)
    if os.path.exists(out_path):
        continue
    r = requests.get(link, headers=HEADERS, timeout=30)
    if r.status_code == 200:
        with open(out_path, "wb") as f:
            f.write(r.content)
        print(f"[{i}/{len(links)}] Saved {fname}")
    else:
        print(f"[{i}/{len(links)}] FAILED ({r.status_code}): {link}")