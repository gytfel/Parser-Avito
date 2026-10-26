"""Нормализация текста объявлений: регистр, «ё», транслит брендов, единицы памяти."""

from __future__ import annotations

import re

# Русские написания брендов/моделей -> латиница, чтобы «айфон 13» и «iPhone 13»
# попадали в одну группу сравнения.
_SYNONYMS: list[tuple[str, str]] = [
    (r"айфон\w*", "iphone"),
    (r"\biphone(?=\d)", "iphone "),
    (r"\b(?:эпл|эппл)\b", "apple"),
    (r"самсунг\w*", "samsung"),
    (r"галакси", "galaxy"),
    (r"(?:сяоми|ксиоми|ксяоми|шаоми|сиоми)\w*", "xiaomi"),
    (r"редми", "redmi"),
    (r"\bпоко\b", "poco"),
    (r"(?:хуавей|хуавэй)\w*", "huawei"),
    (r"хонор\w*", "honor"),
    (r"пиксель", "pixel"),
    (r"макбук\w*", "macbook"),
    (r"айпад\w*", "ipad"),
    (r"(?:эйр|аир|эир)\s*подс\w*|(?:аирподс|эйрподс|эирподс)\w*", "airpods"),
    (r"(?:эпл|apple)\s*(?:вотч|вач|воч)", "apple watch"),
    (r"(?:плейстейшен|плейстейшн|плейстешн|плэйстейшн)\w*|\bплойк\w*", "playstation"),
    (r"\bplaystation\s*(\d)\b", r"ps\1"),
    (r"\b(?:пс|ps)\s*(\d)\b", r"ps\1"),
    (r"иксбокс\w*", "xbox"),
    (r"нинтендо", "nintendo"),
    (r"свитч\w*", "switch"),
    (r"стим\s*дек\w*", "steam deck"),
    (r"(\d)\s*(?:гб|gb|гиг\w*)(?![a-zа-я])", r"\1 gb"),
    (r"(\d)\s*(?:тб|tb|терабайт\w*)(?![a-zа-я])", r"\1 tb"),
    (r"(\d)\s*(?:мм|mm)(?![a-zа-я])", r"\1 mm"),
    (r"(\d)\s*про\b", r"\1 pro"),
    (r"\bпро\s*макс\b", "pro max"),
    (r"(\d|pro)\s*макс\b", r"\1 max"),
    (r"(\d)\s*плюс\b", r"\1 plus"),
    (r"(\d)\s*мини\b", r"\1 mini"),
    (r"(\d)\s*ультра\b", r"\1 ultra"),
]
_SYNONYMS_RE = [(re.compile(p), r) for p, r in _SYNONYMS]

_STOPWORDS = {
    "и", "в", "на", "с", "для", "по", "от", "до", "к", "у", "из", "за", "или",
    "продам", "продаю", "продается", "продаётся", "срочно", "торг", "отличное",
    "хорошее", "состояние", "состоянии", "новый", "новая", "новое", "бу", "б",
    "смартфон", "телефон", "мобильный", "ноутбук", "видеокарта", "приставка",
    "игровая", "консоль", "наушники", "часы", "оригинал", "оригинальный",
    "rus", "ростест", "global", "the",
}


def normalize(text: str) -> str:
    """Нижний регистр, «ё» -> «е», единый вид брендов и единиц памяти."""
    t = (text or "").lower().replace("ё", "е")
    for rx, repl in _SYNONYMS_RE:
        t = rx.sub(repl, t)
    return re.sub(r"\s+", " ", t).strip()


def tokens(text: str) -> frozenset[str]:
    """Значимые слова заголовка для грубого сравнения похожести."""
    return frozenset(
        w for w in re.findall(r"[a-zа-я0-9]+", normalize(text))
        if w not in _STOPWORDS and (len(w) > 1 or w.isdigit())
    )


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def parse_price(value: object) -> int | None:
    """«38 000 ₽», "38000", 38000.0 -> 38000. Возвращает None, если цены нет."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    digits = re.sub(r"[^\d]", "", str(value))
    if not digits:
        return None
    price = int(digits)
    return price if price > 0 else None


def query_matches(query: str, title: str) -> bool:
    """Все слова запроса встречаются в заголовке (с учётом транслита)."""
    q = [w for w in re.findall(r"[a-zа-я0-9]+", normalize(query)) if w not in {"gb", "tb"}]
    t = set(re.findall(r"[a-zа-я0-9]+", normalize(title)))
    return all(w in t for w in q)


def contains_any(text: str, words: list[str]) -> str | None:
    """Первое найденное слово-исключение (по началу слова) или None."""
    norm = normalize(text)
    for w in words:
        if re.search(r"(?<![a-zа-я0-9])" + re.escape(normalize(w)), norm):
            return w
    return None


def fmt_rub(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{int(round(value)):,}".replace(",", " ") + " ₽"
