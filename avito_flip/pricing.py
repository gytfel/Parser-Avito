"""Оценка рынка по аналогам и расчёт выгоды перепродажи.

Логика:
1. Аналоги — объявления той же модели/памяти/конфигурации со всех площадок
   (без подделок, «на запчасти» и серьёзных дефектов). Выбросы отсекаются.
2. Цена продажи — квантиль цен аналогов (по умолчанию 40-й: чуть ниже медианы,
   чтобы продать за 1–2 недели), с поправкой на АКБ, комплект, новизну и торг.
3. Дефекты кандидата снижают цену («как есть») либо закладывается ремонт —
   выбирается более выгодный вариант.
4. Прибыль = продажа − покупка − расходы − ремонт.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from statistics import median

from .analysis import Item
from .config import Economics
from .defects import BONUS_RULES
from .text import fmt_rub

BONUS_VALUES = {code: value for code, _, value, _ in BONUS_RULES}


@dataclass
class Comparable:
    item: Item
    similarity: float

    @property
    def price(self) -> int:
        return self.item.listing.price


def weighted_quantile(values: list[float], weights: list[float], q: float) -> float:
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    if not pairs or total <= 0:
        raise ValueError("пустая выборка")
    # Середины «ступенек» накопленного веса — как обычный квантиль при равных весах.
    acc = 0.0
    points = []
    for v, w in pairs:
        points.append(((acc + w / 2) / total, v))
        acc += w
    if q <= points[0][0]:
        return points[0][1]
    for (q0, v0), (q1, v1) in zip(points, points[1:]):
        if q <= q1:
            return v0 + (v1 - v0) * (q - q0) / (q1 - q0)
    return points[-1][1]


@dataclass
class Market:
    comparables: list[Comparable]
    dropped: int = 0

    @property
    def n(self) -> int:
        return len(self.comparables)

    def quantile(self, q: float) -> float:
        return weighted_quantile([c.price for c in self.comparables], [c.similarity for c in self.comparables], q)

    @property
    def median(self) -> float:
        return self.quantile(0.5)

    @property
    def low(self) -> int:
        return min(c.price for c in self.comparables)

    @property
    def high(self) -> int:
        return max(c.price for c in self.comparables)

    @property
    def dispersion(self) -> float:
        """Разброс: (P75 − P25) / медиана."""
        med = self.median
        return (self.quantile(0.75) - self.quantile(0.25)) / med if med else 1.0

    @property
    def by_source(self) -> dict[str, int]:
        return dict(Counter(c.item.listing.source for c in self.comparables))

    @property
    def mean_similarity(self) -> float:
        return sum(c.similarity for c in self.comparables) / self.n


def find_market(target: Item, pool: list[Item], econ: Economics, threshold: float | None = None) -> Market | None:
    threshold = econ.similarity_threshold if threshold is None else threshold
    comps = []
    seen: set[tuple[str, int]] = set()
    for other in pool:
        if other.listing.uid == target.listing.uid or not other.usable_for_market:
            continue
        sim = target.specs.similarity(other.specs)
        if sim < threshold:
            continue
        # Одно и то же объявление, выложенное в нескольких городах или площадках.
        dup = (other.listing.title.strip().lower(), other.listing.price)
        if dup in seen:
            continue
        seen.add(dup)
        comps.append(Comparable(other, sim))

    used = [c for c in comps if not c.item.defects.is_new]
    new = [c for c in comps if c.item.defects.is_new]
    if target.defects.is_new and len(new) >= econ.min_comparables:
        comps = new
    elif len(used) >= 2:
        comps = used
    if len(comps) < 2:
        return None

    total = len(comps)
    med = median(c.price for c in comps)
    comps = [c for c in comps if 0.45 * med <= c.price <= 2.2 * med]
    if len(comps) >= 4:
        prices = sorted(c.price for c in comps)
        q1 = weighted_quantile(prices, [1] * len(prices), 0.25)
        q3 = weighted_quantile(prices, [1] * len(prices), 0.75)
        iqr = q3 - q1
        comps = [c for c in comps if q1 - 1.5 * iqr <= c.price <= q3 + 1.5 * iqr]
    if len(comps) < 2:
        return None
    comps.sort(key=lambda c: (-c.similarity, c.price))
    return Market(comps, dropped=total - len(comps))


VERDICTS = {
    "good": "✅ Выгодно",
    "risky": "⚠️ Выгодно, но риск",
    "maybe": "➖ Малая маржа",
    "bad": "❌ Невыгодно",
    "fake": "🚫 Подделка",
    "nodata": "❔ Мало данных",
}


@dataclass
class Deal:
    item: Item
    market: Market | None
    buy_price: float
    sell_price: float | None = None
    clean_price: float | None = None
    repair_cost: float = 0.0
    costs: float = 0.0
    profit: float | None = None
    roi: float | None = None
    confidence: float = 0.0
    strategy: str = ""
    verdict: str = "nodata"
    notes: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    @property
    def verdict_label(self) -> str:
        return VERDICTS[self.verdict]

    @property
    def confidence_label(self) -> str:
        return "высокая" if self.confidence >= 0.7 else "средняя" if self.confidence >= 0.45 else "низкая"

    @property
    def score(self) -> float:
        if self.profit is None or self.verdict == "fake":
            return float("-inf")
        penalty = 0.5 ** len(self.risks)
        return self.profit * (self.confidence if self.profit > 0 else 1) * penalty


def evaluate(item: Item, market: Market | None, econ: Economics) -> Deal:
    listing, d = item.listing, item.defects
    risks = list(d.risks)
    haggle_share = {True: econ.buy_haggle, None: econ.buy_haggle / 2, False: 0.0}[d.haggle]
    buy = listing.price * (1 - haggle_share)
    deal = Deal(item=item, market=market, buy_price=buy, risks=risks)
    if d.is_fake:
        deal.verdict = "fake"
    if market is None:
        deal.notes.append("Мало похожих объявлений для оценки — увеличьте число страниц или добавьте регион/площадку")
        if not d.is_fake:
            deal.verdict = "nodata"
        return deal

    comps_items = [c.item for c in market.comparables]
    adj = 0.0

    # Аккумулятор относительно аналогов.
    if item.specs.battery:
        batteries = [i.specs.battery for i in comps_items if i.specs.battery]
        ref = median(batteries) if len(batteries) >= 2 else 90
        diff = item.specs.battery - ref
        if diff <= -3:
            delta = max(-0.15, diff * 0.006)
            adj += delta
            deal.notes.append(f"АКБ {item.specs.battery}% хуже, чем у аналогов (≈{ref:.0f}%): {delta * 100:+.1f}%")
        elif diff >= 5:
            delta = min(0.03, diff * 0.003)
            adj += delta
            deal.notes.append(f"АКБ {item.specs.battery}% лучше, чем у аналогов (≈{ref:.0f}%): {delta * 100:+.1f}%")
        if item.specs.battery < 80 and item.specs.profile in {"phones", "tablets", "laptops"}:
            deal.notes.append("АКБ ниже 80% — покупатели торгуются, стоит заложить замену")

    # Комплект/чек/гарантия относительно того, что обычно есть у аналогов.
    have = {b.code for b in d.bonuses}
    for code, value in BONUS_VALUES.items():
        share = sum(1 for i in comps_items if any(b.code == code for b in i.defects.bonuses)) / len(comps_items)
        delta = value * ((1.0 if code in have else 0.0) - share)
        if abs(delta) >= 0.005:
            adj += delta
            label = next(lbl for c, lbl, _, _ in BONUS_RULES if c == code)
            state = "есть" if code in have else "нет"
            deal.notes.append(f"{label}: {state} (у {share:.0%} аналогов) → {delta * 100:+.1f}%")
    if d.is_new and not any(i.defects.is_new for i in comps_items):
        adj += 0.08
        deal.notes.append("Новый / запечатан, а аналоги б/у → +8%")

    adj = max(-0.25, min(adj, 0.12))
    base = market.quantile(econ.sell_quantile)
    clean = base * (1 + adj) * (1 - econ.sell_haggle)
    deal.clean_price = clean

    as_is = clean * (1 - d.discount)
    sell, repair, strategy = as_is, 0.0, "Продать как есть" if d.defects else "Перепродать"
    if d.repairable:
        repair_cost = clean * d.repair_share
        repaired = clean * (1 - d.cosmetic_discount)
        if repaired - repair_cost > as_is:
            sell, repair = repaired, repair_cost
            strategy = f"Починить (≈{fmt_rub(repair_cost)}) и продать"
    deal.sell_price, deal.repair_cost, deal.strategy = sell, repair, strategy
    deal.costs = econ.fixed_costs + sell * econ.fee_percent
    deal.profit = sell - buy - deal.costs - repair
    invested = buy + deal.costs + repair
    deal.roi = deal.profit / invested if invested > 0 else None

    if listing.price < market.median * econ.suspicious_ratio and d.level == 0 and not d.is_new:
        risks.append(
            f"Цена на {1 - listing.price / market.median:.0%} ниже рынка без видимых причин — "
            "возможен обман или скрытый дефект, проверяйте вживую"
        )
    if d.level >= 2 and not listing.detailed:
        deal.notes.append("Дефект найден по короткому описанию — уточните у продавца")

    deal.confidence = _confidence(item, market, risks)
    if d.is_fake:
        deal.verdict = "fake"
    elif deal.profit >= econ.min_profit and (deal.roi or 0) >= econ.min_roi:
        deal.verdict = "good" if deal.confidence >= 0.45 and not risks else "risky"
    elif deal.profit > 0:
        deal.verdict = "maybe"
    else:
        deal.verdict = "bad"
    return deal


def _confidence(item: Item, market: Market, risks: list[str]) -> float:
    """0..1: насколько можно доверять оценке (объём и разброс рынка, точность модели, дефекты)."""
    c_n = min(1.0, market.n / 12)
    c_disp = max(0.0, 1 - market.dispersion / 0.5)
    c_spec = 1.0 if item.specs.model else 0.5
    if item.specs.profile in {"phones", "tablets"} and not item.specs.storage_gb:
        c_spec *= 0.8
    base = 0.45 * c_n + 0.25 * c_disp + 0.15 * c_spec + 0.15 * market.mean_similarity
    # Чем серьёзнее дефект, тем менее предсказуемы ремонт и цена продажи.
    base *= {0: 1.0, 1: 0.95, 2: 0.8, 3: 0.6}[item.defects.level]
    if not item.listing.detailed and len(item.listing.description) < 40:
        base *= 0.85
    if risks:
        base *= 0.6
    return round(base, 3)


def market_overview(items: list[Item]) -> list[dict]:
    """Сводка рынка по моделям: сколько объявлений и по каким ценам."""
    groups: dict[str, list[Item]] = {}
    for it in items:
        if it.usable_for_market and it.specs.model:
            groups.setdefault(it.specs.key, []).append(it)
    rows = []
    for key, group in groups.items():
        prices = sorted(i.listing.price for i in group)
        mid = median(prices) if prices else 0
        prices = [p for p in prices if 0.45 * mid <= p <= 2.2 * mid]
        if len(prices) < 2:
            continue
        w = [1] * len(prices)
        rows.append({
            "key": key,
            "name": group[0].specs.describe(with_battery=False),
            "n": len(prices),
            "min": prices[0],
            "p25": weighted_quantile(prices, w, 0.25),
            "median": weighted_quantile(prices, w, 0.5),
            "max": prices[-1],
            "sources": dict(Counter(i.listing.source for i in group)),
        })
    rows.sort(key=lambda r: -r["n"])
    return rows
