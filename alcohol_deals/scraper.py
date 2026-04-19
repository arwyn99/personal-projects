"""Binny's sale scraper.

Uses Playwright (headless Chromium) to render sale pages, then extracts
product cards with a DOM heuristic that doesn't depend on specific CSS
class names: find every element that contains a $price AND an <a> AND
an <img>, and treat it as a product card.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from playwright.async_api import async_playwright

from . import db

BASE_URL = "https://www.binnys.com"

START_URLS: list[tuple[str, str]] = [
    ("What's on Sale", f"{BASE_URL}/whats-on-sale/"),
    ("Weekly Sale Ad", f"{BASE_URL}/weekly-sale-ad/"),
    ("Finds & Faves", f"{BASE_URL}/finds-faves-and-fabulous-buys/"),
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Runs in the browser. Returns raw product rows for Python to post-process.
EXTRACT_JS = r"""
() => {
  const priceRe = /\$\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?)/g;
  const parsePrice = s => parseFloat(s.replace(/[$,\s]/g, ''));

  // Collect every element whose *own* text contains a $price (not just
  // inherited from children), then walk up to the smallest ancestor
  // that also contains <a href> and <img> — that's the product card.
  const priceHolders = [];
  const all = document.body.querySelectorAll('*');
  for (const el of all) {
    const direct = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3)
      .map(n => n.nodeValue).join('');
    if (/\$\s*\d/.test(direct)) priceHolders.push(el);
  }

  const cards = new Set();
  for (const p of priceHolders) {
    let el = p;
    for (let i = 0; i < 12 && el; i++) {
      const hasLink = el.querySelector && el.querySelector('a[href]');
      const hasImg  = el.querySelector && el.querySelector('img');
      if (hasLink && hasImg) { cards.add(el); break; }
      el = el.parentElement;
    }
  }

  const items = [];
  const seen = new Set();
  for (const card of cards) {
    const link = card.querySelector('a[href]');
    if (!link) continue;
    const href = link.href;
    if (!href || seen.has(href)) continue;

    const text = (card.innerText || '').trim();
    const prices = [...text.matchAll(priceRe)]
      .map(m => parsePrice(m[0]))
      .filter(p => p > 0 && p < 10000);
    if (!prices.length) continue;

    let name = (link.getAttribute('title') || link.innerText || '').trim();
    name = name.split('\n').map(s => s.trim()).filter(Boolean)[0] || '';
    if (name.length < 3) {
      // Fall back to the first heading or image alt inside the card.
      const h = card.querySelector('h1,h2,h3,h4');
      if (h && h.innerText.trim().length >= 3) name = h.innerText.trim();
      else {
        const img = card.querySelector('img[alt]');
        if (img && img.alt.trim().length >= 3) name = img.alt.trim();
      }
    }
    if (!name || name.length < 3) continue;

    const img = card.querySelector('img');
    const sale = Math.min(...prices);
    const regCandidate = Math.max(...prices);
    const regular = prices.length > 1 && regCandidate > sale ? regCandidate : null;

    seen.add(href);
    items.push({
      url: href,
      name,
      sale_price: sale,
      regular_price: regular,
      image_url: img ? img.src : null,
    });
  }
  return items;
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


async def scrape_page(page, category: str, url: str) -> list[dict]:
    log(f"[info] {category}: goto {url}")
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        log(f"[warn] {category}: goto failed: {e}")
        return []

    status = response.status if response else "?"
    log(f"[info] {category}: status={status} final_url={page.url}")
    if status and status >= 400:
        return []

    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    # Scroll to trigger any lazy-loaded content.
    try:
        await page.evaluate(
            "async () => { for (let y = 0; y < document.body.scrollHeight; y += 800)"
            " { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 150)); } }"
        )
    except Exception:
        pass

    raw: list[dict] = await page.evaluate(EXTRACT_JS)
    log(f"[info] {category}: extracted {len(raw)} candidate cards")

    out = []
    for r in raw:
        sale = r.get("sale_price")
        reg = r.get("regular_price")
        pct = None
        if sale and reg and reg > sale:
            pct = round((reg - sale) / reg * 100, 1)
        out.append(
            {
                "url": r["url"],
                "name": r["name"],
                "category": category,
                "sale_price": sale,
                "regular_price": reg,
                "percent_off": pct,
                "image_url": r.get("image_url"),
            }
        )
    return out


async def scrape_all(headless: bool = True) -> int:
    db.init()
    now = datetime.now(timezone.utc).isoformat()
    by_url: dict[str, dict] = {}

    log(f"[info] launching Chromium (headless={headless})")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(user_agent=USER_AGENT, locale="en-US")
        page = await context.new_page()
        for category, url in START_URLS:
            items = await scrape_page(page, category, url)
            for item in items:
                item["scraped_at"] = now
                # Keep the first category we see an item under.
                by_url.setdefault(item["url"], item)
        await browser.close()

    count = db.replace_all(list(by_url.values()))
    log(f"[info] stored {count} deals")
    return count


def run_sync(headless: bool = True) -> int:
    return asyncio.run(scrape_all(headless=headless))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-headless", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)
    log("[info] starting scraper")
    count = run_sync(headless=not args.no_headless)
    log(f"[info] done. stored {count} deals.")
