from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator


@dataclass
class SiteConfig:
    slug: str
    title: str
    page_url: str
    # CSS selektori (može ih biti više; skripta će probati redom)
    item_selectors: list[str]
    title_selectors: list[str]
    link_selectors: list[str]
    date_selectors: list[str]


HEADERS = {
    "User-Agent": "RijekaRSSBot/1.0 (GitHub Actions; contact: you@example.com)"
}


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def fetch(url: str, timeout: int = 25) -> str:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text


def find_existing_rss(page_html: str, base_url: str) -> Optional[str]:
    """
    Ako stranica ipak ima RSS link u <head>, iskoristi ga.
    """
    soup = BeautifulSoup(page_html, "lxml")
    link = soup.select_one('link[rel="alternate"][type*="rss"], link[rel="alternate"][type*="xml"]')
    if link and link.get("href"):
        return urljoin(base_url, link["href"])
    return None


def guess_common_rss_endpoints(base: str) -> list[str]:
    """
    Česti RSS URL-ovi (WordPress i sl.).
    """
    return [
        urljoin(base, "/feed/"),
        urljoin(base, "/?feed=rss2"),
        urljoin(base, "/rss"),
        urljoin(base, "/rss.xml"),
        urljoin(base, "/feed.xml"),
    ]


def try_fetch_rss(urls: list[str]) -> Optional[str]:
    for u in urls:
        try:
            xml = fetch(u)
            # vrlo gruba provjera
            if "<rss" in xml or "<feed" in xml:
                return u
        except Exception:
            continue
    return None


def scrape_items(cfg: SiteConfig) -> list[dict]:
    html = fetch(cfg.page_url)
    soup = BeautifulSoup(html, "lxml")

    # 1) probaj pronaći postojeći RSS u <head>
    rss_url = find_existing_rss(html, cfg.page_url)
    if rss_url:
        # Samo “proxy” ideju ne radimo; ali korisno je javiti da postoji.
        print(f"[{cfg.slug}] Found RSS in head: {rss_url}")

    # 2) probaj pogoditi common RSS endpoint (ako postoji, ti ga možeš kasnije koristiti direktno)
    guessed = try_fetch_rss(guess_common_rss_endpoints(cfg.page_url))
    if guessed:
        print(f"[{cfg.slug}] Guessed RSS endpoint works: {guessed}")

    # 3) scraping
    container = None
    for sel in cfg.item_selectors:
        found = soup.select(sel)
        if found:
            container = found
            break

    if not container:
        raise RuntimeError(f"[{cfg.slug}] Ne nalazim elemente. Provjeri item_selectors za: {cfg.page_url}")

    items = []
    for it in container[:30]:
        title_el = None
        for ts in cfg.title_selectors:
            title_el = it.select_one(ts)
            if title_el:
                break

        link_el = None
        for ls in cfg.link_selectors:
            link_el = it.select_one(ls)
            if link_el and link_el.get("href"):
                break

        if not title_el or not link_el:
            continue

        title = clean(title_el.get_text())
        link = urljoin(cfg.page_url, link_el.get("href"))

        # datum (ako ga uspijemo izvući)
        dt = datetime.now(timezone.utc)
        for ds in cfg.date_selectors:
            d_el = it.select_one(ds)
            if d_el:
                raw = clean(d_el.get_text())
                # ovdje je “best effort” – po potrebi ćemo prilagoditi
                parts = re.findall(r"\d+", raw)
                if len(parts) >= 3:
                    d, m, y = map(int, parts[:3])
                    dt = datetime(y, m, d, tzinfo=timezone.utc)
                break

        items.append({"title": title, "link": link, "date": dt})

    if not items:
        raise RuntimeError(f"[{cfg.slug}] Našao sam kontejnere, ali nisam izvukao nijedan item (naslov/link).")

    return items


def build_rss(cfg: SiteConfig, out_dir: Path) -> Path:
    items = scrape_items(cfg)

    fg = FeedGenerator()
    fg.title(cfg.title)
    fg.link(href=cfg.page_url, rel="alternate")
    fg.description(f"Generirani RSS za: {cfg.page_url}")
    fg.language("hr")

    for it in items:
        fe = fg.add_entry()
        fe.id(it["link"])
        fe.title(it["title"])
        fe.link(href=it["link"])
        fe.pubDate(it["date"])

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{cfg.slug}.xml"
    out_path.write_bytes(fg.rss_str(pretty=True))
    return out_path


def main():
    # ⚠️ Selektori su “početne pretpostavke”.
    # Ako ne rade, prilagodit ćemo ih nakon što vidiš HTML strukturu tih stranica.
    configs = [
        SiteConfig(
            slug="cistoca",
            title="Čistoća Rijeka – vijesti (generirano)",
            page_url="https://cistocarijeka.hr/",
            item_selectors=["article", ".post", ".news-item", "main article", "main .post"],
            title_selectors=["h2 a", "h3 a", "h2", "h3", "a"],
            link_selectors=["h2 a", "h3 a", "a"],
            date_selectors=["time", ".date", ".post-date"],
        ),
        SiteConfig(
            slug="autotrolej",
            title="Autotrolej – obavijesti (generirano)",
            page_url="https://www.autotrolej.hr/",
            item_selectors=["article", ".post", ".news-item", "main article", "main .post", "li"],
            title_selectors=["h2 a", "h3 a", "h2", "h3", "a"],
            link_selectors=["h2 a", "h3 a", "a"],
            date_selectors=["time", ".date", ".post-date"],
        ),
        SiteConfig(
            slug="rijeka-plus",
            title="Rijeka plus – novosti (generirano)",
            page_url="https://www.rijeka-plus.hr/",
            item_selectors=["article", ".post", ".news-item", "main article", "main .post"],
            title_selectors=["h2 a", "h3 a", "h2", "h3", "a"],
            link_selectors=["h2 a", "h3 a", "a"],
            date_selectors=["time", ".date", ".post-date"],
        ),
    ]

    out_dir = Path("docs/rss")
    for cfg in configs:
        p = build_rss(cfg, out_dir)
        print(f"OK -> {p}")


if __name__ == "__main__":
    main()
