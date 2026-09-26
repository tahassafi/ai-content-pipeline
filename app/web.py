"""Fetch a car listing page: extract text content + main image for the AI."""
import logging
import re
import time
import httpx
from . import config

log = logging.getLogger("web")

def check_inventory(query: str) -> str:
    """Search the dealer website for a car name; returns text snippet or ''."""
    if not config.INVENTORY_SEARCH_URL or not query:
        return ""
    from urllib.parse import quote
    try:
        url = config.INVENTORY_SEARCH_URL.replace("{query}", quote(query[:80]))
        r = httpx.get(url, timeout=15, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (SocialAgent)"})
        text = re.sub(r"(?is)<(script|style|nav|footer|noscript)[^>]*>.*?</\1>", " ", r.text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()[:3000]
    except Exception:
        log.warning("inventory check failed for %s", query)
        return ""

def fetch_car_page(url: str):
    """Returns (page_text, saved_image_filename_or_empty)."""
    r = httpx.get(url, timeout=20, follow_redirects=True,
                  headers={"User-Agent": "Mozilla/5.0 (SocialAgent caption bot)"})
    r.raise_for_status()
    html = r.text

    # main image via og:image (both attribute orders)
    img_name = ""
    m = (re.search(r'property=["\']og:image["\'][^>]*content=["\']([^"\']+)', html)
         or re.search(r'content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']', html))
    if m:
        try:
            ir = httpx.get(m.group(1), timeout=20, follow_redirects=True,
                           headers={"User-Agent": "Mozilla/5.0"})
            ct = ir.headers.get("content-type", "")
            if ir.status_code == 200 and ct.startswith("image/") and len(ir.content) < 4_500_000:
                ext = ".png" if "png" in ct else ".webp" if "webp" in ct else ".jpg"
                img_name = f"{int(time.time())}_web{ext}"
                (config.UPLOAD_DIR / img_name).write_bytes(ir.content)
        except Exception:
            log.warning("og:image download failed for %s", url)
            img_name = ""

    # strip page down to readable text
    text = re.sub(r"(?is)<(script|style|nav|footer|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:6000]
    return text, img_name
