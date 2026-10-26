"""Цены с любых других площадок из CSV-файла (Ozon б/у, Я.Маркет, Telegram-барахолки, Drom...).

Колонки (разделитель «;» или «,», первая строка — заголовки):
    title;price;url;description;source;condition
Обязательны title и price. Объявления попадают в оценку рынка; если source
указан в buy_sources, они рассматриваются и как кандидаты на покупку.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from ..models import Listing
from ..text import parse_price, query_matches
from .base import Source

_ALIASES = {
    "title": ("title", "name", "название", "заголовок", "товар"),
    "price": ("price", "цена", "стоимость"),
    "url": ("url", "link", "ссылка"),
    "description": ("description", "описание", "комментарий"),
    "source": ("source", "площадка", "сайт"),
    "condition": ("condition", "состояние"),
    "location": ("location", "город", "регион"),
}


def _column(row: dict, field: str) -> str:
    lower = {(k or "").strip().lower(): v for k, v in row.items()}
    for alias in _ALIASES[field]:
        if lower.get(alias):
            return str(lower[alias]).strip()
    return ""


def read_csv(path: str | Path) -> list[Listing]:
    text = Path(path).read_text(encoding="utf-8-sig")
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=";,\t") if text.strip() else csv.excel
    out = []
    for row in csv.DictReader(text.splitlines(), dialect=dialect):
        title, price = _column(row, "title"), parse_price(_column(row, "price"))
        if not title or not price:
            continue
        url = _column(row, "url")
        condition = _column(row, "condition")
        out.append(Listing(
            source=_column(row, "source") or "csv",
            id=hashlib.sha1(f"{title}|{price}|{url}".encode()).hexdigest()[:12],
            title=title,
            price=price,
            url=url,
            description=_column(row, "description"),
            location=_column(row, "location"),
            params={"Состояние": condition} if condition else {},
            detailed=True,
        ))
    return out


class CsvSource(Source):
    name = "csv"
    title = "CSV"

    def __init__(self, files: list[str]):
        self.listings = [x for f in files for x in read_csv(f)]

    def search(self, query: str, *, path: str = "", region: str | None = None, pages: int = 1,
               price_min: int | None = None, price_max: int | None = None) -> list[Listing]:
        return [x for x in self.listings if not query or query_matches(query, x.title)]
