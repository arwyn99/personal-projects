"""Binny's sale scraper (Playwright).

Selectors live in SELECTORS and START_URLS so they can be tweaked without
touching the parsing logic. First real run against binnys.com will almost
certainly require adjusting these to match the live DOM.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from playwright.async_api import Page, async_playwright

from . import db

BASE_URL = "https://www.binnys.com"

# Entry points to crawl. Add/remove category sale pages as needed.
START_URLS: list[tuple[str, str]] = [
    ("Wine", f"{BASE_URL}/wine/sale/"),
    ("Spirits", f"{BASE_URL}/spirits/sale/"),
    ("Beer", f"{BASE_URL}/beer/sale/"),
]

# CSS selectors — adjust to match Binny's actual markup.
SELECTORS = {
    "product_card": "[class*='product-tile'], [class*='ProductTile'], li.product, div.product-item",
    "name": "[class*='product-name'], [class*='productName'], h2, h3, a[title]",
    "sale_price": "[class*='sale-price'], [class*='salePrice'], [class*='price-sale']",
    "regular_price": "[class*='regular-price'], [class*='regularPrice'], [class*='was-price'], s, del",
    "any_price": "[class*='price']",
    "image": "img",
    "link": "a[href]",
    "next_page": "a[rel='next'], a.pagination-next, a[aria-label='Next']",
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

PRICE_RE = re.compile(r"\$?\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)")


def parse_price(text: str | None) -> float | None:
    if not text:
        return None
    m = PRICE_RE.search(text.replace(",", ""))
    return float(m.group(1)) if m else None


async def extract_card(card, category: str) -> dict | None:
    name_el = await card.query_selector(SELECTORS["name"])
    name = (await name_el.inner_text()).strip() if name_el else None
    if not name:
        title = await card.get_attribute("title")
        name = title.strip() if title else None
    if not name:
        return None

    link_el = await card.query_selector(SELECTORS["link"])
    href = await link_el.get_attribute("href") if link_el else None
    url = urljoin(BASE_URL, href) if href else None
    if not url:
        return None

    sale_el = await card.query_selector(SELECTORS["sale_price"])
    reg_el = await card.query_selector(SELECTORS["regular_price"])
    sale_price = parse_price(await sale_el.inner_text()) if sale_el else None
    regular_price = parse_price(await reg_el.inner_text()) if reg_el else None

    if sale_price is None:
        # Fall back to any price-like node if the layout doesn't split sale/regular.
        any_el = await card.query_selector(SELECTORS["any_price"])
        sale_price = parse_price(await any_el.inner_text()) if any_el else None

    percent_off = None
    if sale_price and regular_price and regular_price > sale_price:
        percent_off = round((regular_price - sale_price) / regular_price * 100, 1)

    img_el = await card.query_selector(SELECTORS["image"])
    image_url = await img_el.get_attribute("src") if img_el else None
    if image_url:
        image_url = urljoin(BASE_URL, image_url)

    return {
        "url": url,
        "name": name,
        "category": category,
        "sale_price": sale_price,
        "regular_price": regular_price,
        "percent_off": percent_off,
        "image_url": image_url,
    }


async def scrape_category(page: Page, category: str, start_url: str, max_pages: int) -> list[dict]:
    items: dict[str, dict] = {}
    url = start_url
    for page_num in range(1, max_pages + 1):
        print(f"[debug] {category} p{page_num}: goto {url}")
        response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        status = response.status if response else "?"
        final_url = page.url
        title = await page.title()
        print(f"[debug]   -> status={status} final_url={final_url} title={title!r}")

        try:
            await page.wait_for_selector(SELECTORS["product_card"], timeout=10000)
        except Exception:
            body_len = len(await page.content())
            print(f"[debug]   product_card selector not found; body_len={body_len}")
            break

        cards = await page.query_selector_all(SELECTORS["product_card"])
        print(f"[debug]   matched {len(cards)} product cards")
        for card in cards:
            try:
                row = await extract_card(card, category)
            except Exception:
                row = None
            if row:
                items[row["url"]] = row

        next_el = await page.query_selector(SELECTORS["next_page"])
        href = await next_el.get_attribute("href") if next_el else None
        if not href:
            break
        url = urljoin(BASE_URL, href)

    return list(items.values())


async def scrape_all(max_pages: int = 5, headless: bool = True) -> int:
    db.init()
    now = datetime.now(timezone.utc).isoformat()
    all_items: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(user_agent=USER_AGENT, locale="en-US")
        page = await context.new_page()
        for category, url in START_URLS:
            try:
                items = await scrape_category(page, category, url, max_pages)
            except Exception as e:
                print(f"[warn] {category} failed: {e}")
                items = []
            for item in items:
                item["scraped_at"] = now
            all_items.extend(items)
            print(f"[info] {category}: {len(items)} items")
        await browser.close()

    return db.replace_all(all_items)


def run_sync(max_pages: int = 5, headless: bool = True) -> int:
    return asyncio.run(scrape_all(max_pages=max_pages, headless=headless))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--no-headless", action="store_true")
    args = parser.parse_args()
    count = run_sync(max_pages=args.max_pages, headless=not args.no_headless)
    print(f"Stored {count} deals.")
