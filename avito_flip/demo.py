"""Демо-режим без интернета: вымышленные объявления, чтобы посмотреть, как работает оценка."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from .models import Listing
from .sources.base import Source


class DemoSource(Source):
    def __init__(self, name: str, title: str, listings: list[Listing]):
        self.name, self.title, self.listings = name, title, listings

    def search(self, query, *, path="", region=None, pages=1, price_min=None, price_max=None):
        return list(self.listings)

    def fetch_details(self, listing: Listing) -> Listing:
        listing.detailed = True
        return listing


_DESCRIPTIONS = [
    "Пользовался аккуратно, всегда в чехле. Всё работает. Коробка, кабель.",
    "Состояние хорошее, мелкие царапины на корпусе. АКБ {bat}%.",
    "Без сколов и царапин, коробка, чек. Аккумулятор {bat}%.",
    "Отличное состояние, Face ID работает, не вскрывался. Ёмкость АКБ {bat}%",
    "Продаю в связи с покупкой нового. Есть потёртости на рамке. АКБ {bat}%",
    "Всё работает, iCloud отвязан. Торг уместен. Батарея {bat}%",
]

_MODELS = [  # модель, память, центр цены рынка
    ("iPhone 13", 128, 41000),
    ("iPhone 13", 256, 46500),
    ("iPhone 13 Pro", 128, 52000),
    ("iPhone 13 Pro", 256, 57000),
    ("iPhone 13 mini", 128, 33000),
]

# Заведомо интересные лоты: видно, как работают дефекты, риски и ремонт.
_SPECIAL = [
    ("avito", "iPhone 13, 128 ГБ", 31500, "Срочно, переезжаю. Без коробки, мелкие царапины на экране. АКБ 86%. Торг."),
    ("avito", "iPhone 13, 256 ГБ", 38000, "Идеальное состояние, коробка, чек, гарантия до 2027. АКБ 95%. Небольшой торг."),
    ("avito", "iPhone 13 Pro, 128 ГБ", 30000, "Разбит экран, сенсор работает, Face ID работает. Остальное без нареканий. АКБ 88%"),
    ("youla", "Айфон 13 про 128гб", 41000, "Царапин нет, полный комплект. Отдам быстро, есть торг. Аккумулятор 91%."),
    ("avito", "iPhone 13, 128 ГБ", 9500, "На запчасти, не включается после падения."),
    ("avito", "iPhone 13 Pro, 256 ГБ", 16000, "Заблокирован на iCloud, продаю как есть."),
    ("youla", "iPhone 13 128gb", 13000, "Реплика 1:1, работает отлично, все функции."),
    ("avito", "iPhone 13, 128 ГБ", 18000, "Новый, идеал. Только предоплата, отправлю СДЭК после оплаты на карту."),
    ("avito", "iPhone 13 mini, 128 ГБ", 24000, "Аккумулятор под замену, быстро садится. В остальном всё работает. Коробка."),
    ("youla", "iPhone 13 256GB", 40500, "Состояние хорошее, без коробки. АКБ 84%."),
]


def demo_listings(seed: int = 13) -> dict[str, list[Listing]]:
    rnd = random.Random(seed)
    now = datetime.now()
    out: dict[str, list[Listing]] = {"avito": [], "youla": []}
    n = 0
    for model, mem, center in _MODELS:
        for _ in range(rnd.randint(9, 14)):
            n += 1
            source = "youla" if rnd.random() < 0.3 else "avito"
            price = int(round(rnd.gauss(center, center * 0.07) / 500) * 500)
            title = f"{model}, {mem} ГБ" if source == "avito" else f"{model} {mem}gb"
            desc = rnd.choice(_DESCRIPTIONS).format(bat=rnd.randint(80, 100))
            out[source].append(_listing(source, n, title, price, desc, now - timedelta(hours=rnd.randint(1, 240))))
    for source, title, price, desc in _SPECIAL:
        n += 1
        out[source].append(_listing(source, n, title, price, desc, now - timedelta(minutes=rnd.randint(5, 600))))
    return out


def _listing(source: str, n: int, title: str, price: int, desc: str, published: datetime) -> Listing:
    return Listing(
        source=source,
        id=f"demo{n}",
        title=title,
        price=price,
        url=f"demo://{source}/{n}",
        description=desc,
        location="Москва (демо)",
        published=published,
    )


def demo_sources() -> dict[str, Source]:
    data = demo_listings()
    return {
        "avito": DemoSource("avito", "Авито (демо)", data["avito"]),
        "youla": DemoSource("youla", "Юла (демо)", data["youla"]),
    }
