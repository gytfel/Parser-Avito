"""Юла (youla.ru): вторая крупная барахолка, нужна для сравнения цен и поиска дешёвых лотов.

Разбор «по возможности»: товары ищутся во встроенном JSON состояния страницы
(window.__YOULA_STATE__ / __NEXT_DATA__ / application/json), а если его нет —
в ссылках на карточки товаров. Юла хранит цены в копейках; делитель
определяется сравнением с ценами, видимыми на странице.
"""

from __future__ import annotations

import re
from statistics import median
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

from ..models import Listing
from ..text import parse_price
from .base import Source, _has_lxml, json_blobs_from_scripts, walk_json

YOULA = "https://youla.ru"
_PRODUCT_HREF = re.compile(r"/[\w-]+/[\w-]+/[\w-]+/[\w-]*-[0-9a-f]{20,}")
_VISIBLE_PRICE = re.compile(r"(\d[\d\s   ]{1,12})\s*(?:₽|руб)")


def build_search_url(city: str, query: str, page: int = 1) -> str:
    params: dict[str, object] = {"q": query}
    if page > 1:
        params["page"] = page
    return f"{YOULA}/{city.strip('/')}?{urlencode(params)}"


def _is_product(d: dict) -> bool:
    name = d.get("name") or d.get("title")
    return (
        isinstance(name, str) and bool(name.strip())
        and ("price" in d or "priceText" in d)
        and isinstance(d.get("url") or d.get("slug") or d.get("id"), str)
        and not isinstance(d.get("price"), (list, dict))
    )


def _visible_prices(html: str) -> set[int]:
    text = re.sub(r"<[^>]+>", " ", html)
    return {p for p in (parse_price(m.group(1)) for m in _VISIBLE_PRICE.finditer(text)) if p}


def _divisor(raw_prices: list[int], html: str, setting) -> float:
    if setting not in (None, "auto"):
        return float(setting)
    if not raw_prices:
        return 100.0
    visible = _visible_prices(html)
    if visible:
        as_kopecks = sum(1 for p in raw_prices if p % 100 == 0 and p // 100 in visible)
        as_rubles = sum(1 for p in raw_prices if p in visible)
        if as_kopecks != as_rubles:
            return 100.0 if as_kopecks > as_rubles else 1.0
    # Без подсказок: копейки — штатный формат API Юлы; совсем крупные «рубли» для б/у вещей нереальны.
    return 100.0 if median(raw_prices) >= 50_000 else 1.0


def parse_search_page(html: str, price_divisor="auto") -> list[Listing]:
    products: dict[str, dict] = {}
    for blob in json_blobs_from_scripts(html):
        for d in walk_json(blob, _is_product):
            key = str(d.get("id") or d.get("url") or d.get("slug"))
            products.setdefault(key, d)

    raw = []
    for d in products.values():
        p = d.get("price")
        raw.append(int(p) if isinstance(p, (int, float)) and not isinstance(p, bool) else None)
    divisor = _divisor([p for p in raw if p], html, price_divisor)

    out = []
    for (key, d), raw_price in zip(products.items(), raw):
        price = parse_price(d.get("priceText")) if d.get("priceText") else None
        if price is None and raw_price:
            price = int(round(raw_price / divisor))
        if not price:
            continue
        url = d.get("url") or d.get("slug") or ""
        loc = d.get("location") or {}
        location = ""
        if isinstance(loc, dict):
            location = loc.get("description") or loc.get("city") or loc.get("name") or ""
        elif isinstance(loc, str):
            location = loc
        images = d.get("images")
        out.append(Listing(
            source="youla",
            id=str(d.get("id") or key),
            title=str(d.get("name") or d.get("title")).strip(),
            price=price,
            url=urljoin(YOULA, url) if url else "",
            description=str(d.get("description") or "").strip(),
            location=location,
            images=len(images) if isinstance(images, list) else 0,
        ))
    return out or parse_cards(html)


def parse_cards(html: str) -> list[Listing]:
    """Запасной разбор: ссылки на карточки товаров с ценой внутри."""
    soup = BeautifulSoup(html, "lxml" if _has_lxml() else "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=_PRODUCT_HREF):
        href = a["href"]
        if href in seen:
            continue
        text = a.get_text(" ", strip=True)
        m = _VISIBLE_PRICE.search(text)
        if not m:
            continue
        img = a.find("img")
        title = a.get("title") or (img.get("alt") if img else "") or _VISIBLE_PRICE.sub("", text).strip()
        if not title:
            continue
        seen.add(href)
        out.append(Listing(
            source="youla",
            id=href.rstrip("/").rsplit("-", 1)[-1],
            title=title,
            price=parse_price(m.group(1)),
            url=urljoin(YOULA, href),
        ))
    return out


class YoulaSource(Source):
    name = "youla"
    title = "Юла"

    def __init__(self, fetcher, city: str = "moskva", price_divisor="auto", log=None):
        self.fetcher = fetcher
        self.city = city
        self.price_divisor = price_divisor
        self.log = log or (lambda msg: None)

    def search(self, query: str, *, path: str = "", region: str | None = None, pages: int = 1,
               price_min: int | None = None, price_max: int | None = None) -> list[Listing]:
        results: dict[str, Listing] = {}
        for page in range(1, pages + 1):
            url = build_search_url(self.city, query, page)
            self.log(f"Юла [{self.city}] стр. {page}: {url}")
            html = self.fetcher.get(url, referer=YOULA + "/")
            listings = parse_search_page(html, self.price_divisor)
            if page == 1 and not listings and hasattr(self.fetcher, "save_debug"):
                saved = self.fetcher.save_debug("youla_empty.html", html)
                self.log(f"Юла: на странице не найдено объявлений, она сохранена в {saved}")
            fresh = [x for x in listings if x.id not in results]
            for x in fresh:
                x.location = x.location or self.city
                results[x.id] = x
            if not fresh:
                break
        return [x for x in results.values()
                if (not price_min or x.price >= price_min) and (not price_max or x.price <= price_max)]
