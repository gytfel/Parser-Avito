"""Общий интерфейс площадки и разбор встроенного в страницу JSON."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator

from ..models import Listing


class Source(ABC):
    name: str = ""
    title: str = ""

    @abstractmethod
    def search(self, query: str, *, path: str = "", region: str | None = None, pages: int = 1,
               price_min: int | None = None, price_max: int | None = None) -> list[Listing]:
        """Объявления по запросу (без дублей)."""

    def fetch_details(self, listing: Listing) -> Listing:
        """Дозагрузить полное описание и характеристики. По умолчанию — как есть."""
        return listing


def walk_json(node, predicate: Callable[[dict], bool]) -> Iterator[dict]:
    """Все словари во вложенной структуре, удовлетворяющие условию."""
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if predicate(cur):
                yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(reversed(cur))


def json_blobs_from_scripts(html: str) -> Iterator[object]:
    """JSON из <script>: application/json, mfe-state, window.__X__ = {...} и URL-кодированные строки."""
    from urllib.parse import unquote

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml") if _has_lxml() else BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        text = text.strip()
        if not text or len(text) < 50:
            continue
        stype = (script.get("type") or "").lower()
        if text[0] in "{[" and (stype in {"application/json", "mime/invalid", "application/ld+json"} or script.get("data-mfe-state")):
            data = _loads(text)
            if data is not None:
                yield data
            continue
        for m in re.finditer(r"window\.(__\w+__)\s*=\s*", text):
            rest = text[m.end():].lstrip()
            if rest.startswith('"'):
                # window.__initialData__ = "%7B%22..."  — JSON, закодированный как URL
                end = rest.find('"', 1)
                while end != -1 and rest[end - 1] == "\\":
                    end = rest.find('"', end + 1)
                if end != -1:
                    data = _loads(unquote(rest[1:end]))
                    if data is not None:
                        yield data
            elif rest[:1] in "{[":
                data = _loads(_balanced(rest))
                if data is not None:
                    yield data


def _has_lxml() -> bool:
    try:
        import lxml  # noqa: F401
        return True
    except ImportError:
        return False


def _loads(text: str):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _balanced(text: str) -> str:
    """Отрезать JSON-объект с начала строки по балансу скобок (с учётом строк)."""
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return text[: i + 1]
    return text
