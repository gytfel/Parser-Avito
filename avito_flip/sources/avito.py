"""Авито: поиск по выдаче и страница объявления.

Страница выдачи разбирается двумя способами:
  1. встроенный JSON состояния (script data-mfe-state / window.__initialData__) —
     в нём полные поля объявления, и он меньше зависит от вёрстки;
  2. HTML-карточки по атрибутам data-marker (item, item-title, item-price...) —
     запасной вариант, если формат JSON изменится.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

from ..models import Listing
from ..text import parse_price
from .base import Source, _has_lxml, json_blobs_from_scripts, walk_json

AVITO = "https://www.avito.ru"
SORTS = {"date": "104", "price_asc": "1", "price_desc": "2", "default": None}


def build_search_url(region: str, path: str, query: str, page: int = 1, sort: str = "date",
                     price_min: int | None = None, price_max: int | None = None) -> str:
    segment = "/" + "/".join(p for p in (region.strip("/"), (path or "").strip("/")) if p)
    params: dict[str, object] = {}
    if query:
        params["q"] = query
    if SORTS.get(sort):
        params["s"] = SORTS[sort]
    if price_min:
        params["pmin"] = int(price_min)
    if price_max:
        params["pmax"] = int(price_max)
    if page > 1:
        params["p"] = page
    return AVITO + segment + ("?" + urlencode(params) if params else "")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml" if _has_lxml() else "html.parser")


def _is_item(d: dict) -> bool:
    return (
        "id" in d and "title" in d and ("urlPath" in d or "url" in d)
        and ("priceDetailed" in d or "price" in d)
        and isinstance(d.get("title"), str)
    )


def _price_from(value) -> int | None:
    if isinstance(value, dict):
        return parse_price(value.get("value")) or parse_price(value.get("string")) or parse_price(value.get("price"))
    return parse_price(value)


def _timestamp(value) -> datetime | None:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:  # миллисекунды
        ts /= 1000
    try:
        return datetime.fromtimestamp(ts)
    except (OverflowError, OSError, ValueError):
        return None


def item_from_json(d: dict) -> Listing | None:
    title = d.get("title", "").strip()
    price = _price_from(d.get("priceDetailed")) or _price_from(d.get("price"))
    url = d.get("urlPath") or d.get("url") or ""
    if not (d.get("id") and title and price and isinstance(url, str)):
        return None
    loc = d.get("location") or {}
    geo = d.get("geo") or {}
    location = (loc.get("name") if isinstance(loc, dict) else "") or (
        geo.get("formattedAddress") if isinstance(geo, dict) else ""
    )
    images = d.get("images")
    return Listing(
        source="avito",
        id=str(d["id"]),
        title=title,
        price=price,
        url=urljoin(AVITO, url),
        description=str(d.get("description") or "").strip(),
        location=location or "",
        published=_timestamp(d.get("sortTimeStamp") or d.get("time")),
        images=len(images) if isinstance(images, list) else 0,
    )


def parse_cards(html: str) -> list[Listing]:
    """HTML-карточки выдачи (data-marker="item")."""
    out = []
    for card in _soup(html).select('[data-marker="item"]'):
        item_id = card.get("data-item-id") or (card.get("id") or "").lstrip("i")
        link = card.select_one('[data-marker="item-title"]') or card.select_one("a[href]")
        name = card.select_one('[itemprop="name"]')
        title = (name.get_text(" ", strip=True) if name else "") or (link.get("title", "") if link else "")
        title = re.sub(r"\s+в\s+[А-ЯЁ][\w\s-]*$", "", title).strip() if not name else title
        price_meta = card.select_one('meta[itemprop="price"]')
        price = parse_price(price_meta.get("content")) if price_meta else None
        if price is None:
            price_el = card.select_one('[data-marker="item-price"]')
            price = parse_price(price_el.get_text()) if price_el else None
        if not (item_id and title and price and link):
            continue
        desc_meta = card.select_one('meta[itemprop="description"]')
        description = desc_meta.get("content", "") if desc_meta else ""
        if not description:
            desc_el = card.select_one('[class*="descriptionStep"]') or card.select_one('[class*="description"]')
            description = desc_el.get_text(" ", strip=True) if desc_el else ""
        specific = card.select_one('[data-marker="item-specific-params"]')
        if specific:
            description = f"{specific.get_text(' ', strip=True)}. {description}".strip(". ")
        addr = card.select_one('[data-marker="item-address"]') or card.select_one('[class*="geo-root"]')
        out.append(Listing(
            source="avito",
            id=str(item_id),
            title=title,
            price=price,
            url=urljoin(AVITO, link.get("href", "")),
            description=description,
            location=addr.get_text(" ", strip=True) if addr else "",
            images=len(card.select("img")),
        ))
    return out


def parse_search_page(html: str) -> list[Listing]:
    found: dict[str, Listing] = {}
    for blob in json_blobs_from_scripts(html):
        for d in walk_json(blob, _is_item):
            listing = item_from_json(d)
            if listing:
                found.setdefault(listing.id, listing)
    cards = parse_cards(html)
    if not found:
        return cards
    # JSON — основа; из HTML дополняем то, чего в нём не оказалось.
    for card in cards:
        base = found.get(card.id)
        if base is None:
            found[card.id] = card
            continue
        if len(card.description) > len(base.description):
            base.description = card.description
        base.location = base.location or card.location
    return list(found.values())


def parse_item_page(html: str) -> dict:
    """Описание, характеристики и продавец со страницы объявления."""
    soup = _soup(html)
    info: dict = {"params": {}}
    desc = soup.select_one('[data-marker="item-view/item-description"]') or soup.select_one('[itemprop="description"]')
    if desc:
        info["description"] = desc.get_text("\n", strip=True)
    for li in soup.select('[data-marker="item-view/item-params"] li') or soup.select('[class*="params-paramsList__item"]'):
        text = li.get_text(" ", strip=True)
        if ":" in text:
            k, v = text.split(":", 1)
            info["params"][k.strip()] = v.strip()
    title = soup.select_one('[data-marker="item-view/title-info"]') or soup.select_one('h1[itemprop="name"]') or soup.h1
    if title:
        info["title"] = title.get_text(" ", strip=True)
    price = soup.select_one('[itemprop="price"]')
    if price:
        info["price"] = parse_price(price.get("content") or price.get_text())
    seller = soup.select_one('[data-marker="seller-info/name"]')
    if seller:
        info["seller"] = seller.get_text(" ", strip=True)
    addr = soup.select_one('[itemprop="address"]') or soup.select_one('[data-marker="item-view/item-address"]')
    if addr:
        info["location"] = addr.get_text(" ", strip=True)
    return info


class AvitoSource(Source):
    name = "avito"
    title = "Авито"

    def __init__(self, fetcher, region: str = "moskva", sort: str = "date", log=None):
        self.fetcher = fetcher
        self.region = region
        self.sort = sort
        self.log = log or (lambda msg: None)

    def search(self, query: str, *, path: str = "", region: str | None = None, pages: int = 1,
               price_min: int | None = None, price_max: int | None = None) -> list[Listing]:
        region = region or self.region
        results: dict[str, Listing] = {}
        referer = AVITO + "/"
        for page in range(1, pages + 1):
            url = build_search_url(region, path, query, page, self.sort, price_min, price_max)
            self.log(f"Авито [{region}] стр. {page}: {url}")
            html = self.fetcher.get(url, referer=referer)
            referer = url
            listings = parse_search_page(html)
            fresh = [x for x in listings if x.id not in results]
            for x in fresh:
                x.location = x.location or region
                results[x.id] = x
            if not fresh:
                break
        return list(results.values())

    def fetch_details(self, listing: Listing) -> Listing:
        if listing.detailed or not listing.url.startswith("http"):
            return listing
        info = parse_item_page(self.fetcher.get(listing.url, referer=AVITO + "/"))
        listing.title = listing.title or info.get("title", "")
        listing.price = listing.price or info.get("price") or 0
        if len(info.get("description", "")) > len(listing.description):
            listing.description = info["description"]
        listing.params.update(info.get("params", {}))
        listing.seller = info.get("seller") or listing.seller
        listing.location = info.get("location") or listing.location
        listing.detailed = True
        return listing
